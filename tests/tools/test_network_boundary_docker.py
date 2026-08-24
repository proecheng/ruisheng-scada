"""Opt-in production Compose regression for B-04 management-source ACLs."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import socket
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
RUN_DOCKER_E2E = os.environ.get("B04_DOCKER_E2E") == "1"
SUBNET = "10.254.250.0/24"
GATEWAY = "10.254.250.1"


def _run(command: list[str], *, timeout: int = 180) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=timeout,
    )


def _best_effort_cleanup(command: list[str]) -> None:
    with contextlib.suppress(OSError, subprocess.TimeoutExpired):
        subprocess.run(
            command,
            cwd=ROOT,
            check=False,
            capture_output=True,
            timeout=30,
        )


def _wait_for_stable_health(
    container_name: str, *, stable_seconds: float, timeout: float = 120.0
) -> None:
    deadline = time.monotonic() + timeout
    healthy_since: float | None = None
    while time.monotonic() < deadline:
        result = subprocess.run(
            [
                "docker",
                "inspect",
                "--format",
                "{{.State.Health.Status}}",
                container_name,
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip() == "healthy":
            healthy_since = healthy_since or time.monotonic()
            if time.monotonic() - healthy_since >= stable_seconds:
                return
        else:
            healthy_since = None
        time.sleep(0.5)
    raise AssertionError(f"container did not remain healthy: {container_name}")


def _reserve_ports(count: int) -> tuple[list[int], list[socket.socket]]:
    listeners: list[socket.socket] = []
    ports: list[int] = []
    for _index in range(count):
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listeners.append(listener)
        ports.append(int(listener.getsockname()[1]))
    return ports, listeners


def _write_env(path: Path, replacements: dict[str, str]) -> None:
    source = (ROOT / ".env.prod.example").read_text(encoding="utf-8")
    lines: list[str] = []
    seen: set[str] = set()
    for source_line in source.splitlines():
        key, separator, _value = source_line.partition("=")
        if separator and key in replacements:
            output_line = f"{key}={replacements[key]}"
            seen.add(key)
        else:
            output_line = source_line
        lines.append(output_line)
    if seen != set(replacements):
        raise AssertionError(f"missing E2E env keys: {sorted(set(replacements) - seen)}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def _wait_for_status(url: str, expected: int = 200, token: str | None = None) -> bytes:
    last_error: Exception | None = None
    for _attempt in range(120):
        try:
            headers = {"X-Forwarded-For": GATEWAY, "X-Real-IP": GATEWAY}
            if token is not None:
                headers["Authorization"] = f"Bearer {token}"
            request = urllib.request.Request(  # noqa: S310
                url,
                headers=headers,
            )
            with urllib.request.urlopen(request, timeout=2) as response:  # noqa: S310
                if response.status == expected:
                    return response.read()
        except urllib.error.HTTPError as error:
            if error.code == expected:
                return error.read()
            last_error = error
        except (OSError, urllib.error.URLError) as error:
            last_error = error
        time.sleep(0.5)
    raise AssertionError(f"{url} did not return {expected}: {last_error}")


def _assert_host_management_paths(
    web_port: int, gw_port: int, token: str, token_digest: str
) -> None:
    urls = {
        "gw_health": f"http://127.0.0.1:{gw_port}/health",
        "gw_ready": f"http://127.0.0.1:{gw_port}/ready",
        "gw_metrics": f"http://127.0.0.1:{gw_port}/metrics",
        "api_live": f"http://127.0.0.1:{web_port}/api/health/live",
        "api_ready": f"http://127.0.0.1:{web_port}/api/health/ready",
        "api_metrics": f"http://127.0.0.1:{web_port}/api/health/metrics",
    }
    for url in urls.values():
        _wait_for_status(url, expected=403)
        _wait_for_status(url, expected=403, token=token_digest)
    expected_fragments = {
        "gw_health": b"alive",
        "gw_ready": b"true",
        "gw_metrics": b"ruisheng_gw_build_info",
        "api_live": b'"status":"live"',
        "api_ready": b'"status":"ready"',
        "api_metrics": b"ruisheng_api_notification_pending",
    }
    for name, url in urls.items():
        assert expected_fragments[name] in _wait_for_status(url, token=token)


@pytest.mark.integration
@pytest.mark.skipif(not RUN_DOCKER_E2E, reason="set B04_DOCKER_E2E=1")
def test_production_compose_nat_acl_survives_restart(tmp_path: Path) -> None:  # noqa: PLR0915
    token = uuid.uuid4().hex[:12]
    project = f"ruisheng-b04-e2e-{token}"
    api_image = f"ruisheng-b04-e2e/api:{token}"
    gw_image = f"ruisheng-b04-e2e/gw:{token}"
    web_image = f"ruisheng-b04-e2e/web:{token}"
    postgres_container = f"ruisheng-b04-postgres-{token}"
    redis_container = f"ruisheng-b04-redis-{token}"
    migrate_container = f"ruisheng-b04-migrate-{token}"
    gw_container = f"ruisheng-b04-gw-{token}"
    api_container = f"ruisheng-b04-api-{token}"
    web_container = f"ruisheng-b04-web-{token}"
    (gw_port, device_port, web_port), port_reservations = _reserve_ports(3)
    management_token = f"b04-e2e-{uuid.uuid4().hex}-{uuid.uuid4().hex}"
    management_digest = hashlib.sha256(management_token.encode("ascii")).hexdigest()
    acl = tmp_path / "site-health-acl.conf"
    acl.write_text(f"allow {GATEWAY}/32;\ndeny all;\n", encoding="utf-8", newline="\n")
    env_file = tmp_path / ".env.prod"
    _write_env(
        env_file,
        {
            "POSTGRES_PASSWORD": "b04_e2e_postgres_password_48_chars_safe_value",
            "RUISHENG_GW_PASSWORD": "b04_e2e_gw_role_password_48_chars_safe_value",
            "RUISHENG_API_PASSWORD": "b04_e2e_api_role_password_48_chars_safe_value",
            "REDIS_PASSWORD": "b04_e2e_redis_password_48_chars_safe_value",
            "JWT_SECRET": "b04_e2e_jwt_secret_at_least_48_chars_safe_value",
            "APP_NETWORK_SUBNET": SUBNET,
            "APP_NETWORK_GATEWAY": GATEWAY,
            "WEB_BIND_PORT": str(web_port),
            "WEB_HEALTH_ACL_FILE": acl.as_posix(),
            "GW_DEVICE_BIND_PORT": str(device_port),
            "GW_HEALTH_BIND_PORT": str(gw_port),
            "GW_HEALTH_ALLOWED_CIDRS": f"{GATEWAY}/32",
            "MANAGEMENT_TOKEN_SHA256": management_digest,
            "POSTGRES_IMAGE": "timescale/timescaledb:2.16.1-pg15",
            "REDIS_IMAGE": "redis:7-alpine",
            "API_IMAGE": api_image,
            "GW_IMAGE": gw_image,
            "WEB_IMAGE": web_image,
        },
    )
    overlay = tmp_path / "e2e.override.json"
    overlay.write_text(
        json.dumps(
            {
                "services": {
                    "postgres": {"container_name": postgres_container},
                    "redis": {"container_name": redis_container},
                    "migrate": {"container_name": migrate_container},
                    "gw": {"container_name": gw_container},
                    "api": {
                        "container_name": api_container,
                        "extra_hosts": ["host.docker.internal:host-gateway"],
                    },
                    "web": {"container_name": web_container},
                }
            }
        ),
        encoding="utf-8",
    )
    compose = [
        "docker",
        "compose",
        "--project-name",
        project,
        "--env-file",
        str(env_file),
        "--file",
        str(ROOT / "docker-compose.prod.yml"),
        "--file",
        str(ROOT / "deploy" / "site-network.override.yml"),
        "--file",
        str(overlay),
    ]
    peer_code = "\n".join(
        (
            "import json, sys, urllib.error, urllib.request",
            f"headers = {{'X-Forwarded-For': '{GATEWAY}', 'X-Real-IP': '{GATEWAY}'}}",
            f"wrong_headers = {{**headers, 'Authorization': 'Bearer {management_digest}'}}",
            "def status(url, request_headers):",
            " try:",
            "  return urllib.request.urlopen(urllib.request.Request(url, headers=request_headers), "
            "timeout=5).status",
            " except urllib.error.HTTPError as error:",
            "  return error.code",
            " except urllib.error.URLError:",
            "  return 0",
            "endpoints = {",
            " 'gw_health_direct': 'http://gw:9090/health',",
            " 'gw_ready_direct': 'http://gw:9090/ready',",
            " 'gw_metrics_direct': 'http://gw:9090/metrics',",
            " 'api_live_direct': 'http://web:80/api/health/live',",
            " 'api_ready_direct': 'http://web:80/api/health/ready',",
            " 'api_metrics_direct': 'http://web:80/api/health/metrics',",
            f" 'gw_health_hairpin': 'http://host.docker.internal:{gw_port}/health',",
            f" 'gw_ready_hairpin': 'http://host.docker.internal:{gw_port}/ready',",
            f" 'gw_metrics_hairpin': 'http://host.docker.internal:{gw_port}/metrics',",
            f" 'api_live_hairpin': 'http://host.docker.internal:{web_port}/api/health/live',",
            f" 'api_ready_hairpin': 'http://host.docker.internal:{web_port}/api/health/ready',",
            f" 'api_metrics_hairpin': 'http://host.docker.internal:{web_port}/api/health/metrics',",
            "}",
            "statuses = {}",
            "for name, url in endpoints.items():",
            " statuses[name + '_missing'] = status(url, headers)",
            " statuses[name + '_wrong'] = status(url, wrong_headers)",
            "print(json.dumps(statuses, sort_keys=True))",
            "checks = [value == 403 for value in statuses.values()]",
            "raise SystemExit(0 if all(checks) else 3)",
        )
    )

    def assert_peer_denied() -> None:
        command = compose + [
            "run",
            "--rm",
            "--no-deps",
            "--entrypoint",
            "python",
            "api",
            "-c",
            peer_code,
        ]
        result = subprocess.run(
            command,
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=180,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    try:
        # Docker Desktop can corrupt the shared-session header when two services
        # concurrently expose the same non-ASCII Windows build context.
        for service in ("api", "gw", "web"):
            _run(compose + ["build", service], timeout=900)
        for reservation in port_reservations:
            reservation.close()
        port_reservations.clear()
        _run(
            compose + ["up", "--detach", "--no-build", "postgres", "redis"],
            timeout=180,
        )
        # The TimescaleDB image briefly accepts connections during first-time
        # initialization, then restarts PostgreSQL after tuning. Require a stable
        # healthy window before allowing the one-shot migration to start.
        _wait_for_stable_health(postgres_container, stable_seconds=6.0)
        _wait_for_stable_health(redis_container, stable_seconds=1.0)
        startup = subprocess.run(
            compose + ["up", "--detach", "--no-build"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=300,
        )
        if startup.returncode != 0:
            diagnostics = subprocess.run(
                compose + ["logs", "--no-color", "migrate", "postgres"],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=30,
            )
            raise AssertionError(
                startup.stdout + startup.stderr + diagnostics.stdout + diagnostics.stderr
            )
        network = json.loads(_run(["docker", "network", "inspect", f"{project}_default"]).stdout)
        assert network[0]["IPAM"]["Config"] == [{"Subnet": SUBNET, "Gateway": GATEWAY}]
        api_inspect = json.loads(_run(["docker", "inspect", api_container]).stdout)[0]
        gw_inspect = json.loads(_run(["docker", "inspect", gw_container]).stdout)[0]
        assert api_inspect["Config"]["Image"] == api_image
        assert gw_inspect["Config"]["Image"] == gw_image
        gw_env = gw_inspect["Config"]["Env"]
        assert f"GW_HEALTH_ALLOWED_CIDRS={GATEWAY}/32" in gw_env
        assert f"GW_HEALTH_TOKEN_SHA256={management_digest}" in gw_env
        assert not any(value.startswith("GW_HEALTH_TOKEN=") for value in gw_env)
        api_env = api_inspect["Config"]["Env"]
        assert f"API_MANAGEMENT_TOKEN_SHA256={management_digest}" in api_env
        assert not any(value.startswith("API_MANAGEMENT_TOKEN=") for value in api_env)
        web_mounts = json.loads(_run(["docker", "inspect", web_container]).stdout)[0]["Mounts"]
        acl_mount = next(
            mount
            for mount in web_mounts
            if mount["Destination"] == "/etc/nginx/site-health-acl.conf"
        )
        assert acl_mount["RW"] is False

        _assert_host_management_paths(web_port, gw_port, management_token, management_digest)
        assert_peer_denied()
        _run(compose + ["restart", "gw", "api", "web"])
        _assert_host_management_paths(web_port, gw_port, management_token, management_digest)
        assert_peer_denied()
    finally:
        for reservation in port_reservations:
            reservation.close()
        _best_effort_cleanup(compose + ["down", "--volumes", "--remove-orphans"])
        _best_effort_cleanup(["docker", "image", "rm", "--force", api_image, gw_image, web_image])
