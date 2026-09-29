"""Check production readiness against PostgreSQL's real temporary initdb server."""

from __future__ import annotations

import json
import os
import subprocess
import time
from ipaddress import ip_network
from pathlib import Path
from uuid import uuid4

import pytest

from conftest import _skip_if_docker_unavailable

ROOT = Path(__file__).parents[2]
IMAGE = "timescale/timescaledb:2.16.1-pg15"


@pytest.fixture(autouse=True)
def require_test_database_target() -> None:
    """This module creates its own isolated fresh database."""


@pytest.fixture(autouse=True)
def require_dev_database() -> None:
    """Do not require or connect to the external development database."""


def docker(*args: str, timeout: int = 60) -> str:
    result = subprocess.run(
        ["docker", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=timeout,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return result.stdout.strip()


def _available_test_subnet() -> str:
    # Retained test networks must not exhaust Docker's small default address pool.
    ids = docker("network", "ls", "--quiet").splitlines()
    occupied = [
        ip_network(config["Subnet"])
        for network in json.loads(docker("network", "inspect", *ids))
        for config in network["IPAM"].get("Config") or []
        if config.get("Subnet")
    ]
    for subnet in ip_network("10.254.0.0/16").subnets(new_prefix=28):
        if not any(subnet.overlaps(existing) for existing in occupied):
            return str(subnet)
    raise RuntimeError("No unused subnet available for isolated readiness tests")


@pytest.mark.integration
@pytest.mark.parametrize("directory", (".", "deploy"))
def test_postgres_health_waits_for_final_tcp_server(tmp_path: Path, directory: str) -> None:
    _skip_if_docker_unavailable()
    project = "ruisheng-tcp-readiness-" + uuid4().hex
    template = ROOT / directory / ".env.prod.example"
    # Use only example/test settings, independent of the developer's environment.
    environment = os.environ.copy()
    for line in template.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            environment[key] = value
    environment.update(
        POSTGRES_IMAGE=IMAGE,
        POSTGRES_USER="readiness_test",
        POSTGRES_PASSWORD="readiness-test-password-2026",
    )
    rendered = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            str(template),
            "-f",
            str(ROOT / directory / "docker-compose.prod.yml"),
            "config",
            "--format",
            "json",
        ],
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=30,
    )
    postgres = json.loads(rendered.stdout)["services"]["postgres"]
    postgres.update(
        container_name=project, restart="no", labels={"ruisheng.readiness-test": project}
    )
    init_script = tmp_path / "hold-init.sh"
    init_script.write_text(
        "#!/bin/sh\nset -eu\ntouch /tmp/readiness-initializing\n"
        "while ! test -f /tmp/readiness-release; do sleep 1; done\n",
        encoding="ascii",
        newline="\n",
    )
    postgres["volumes"].append(
        {
            "type": "bind",
            "source": str(init_script),
            "target": "/docker-entrypoint-initdb.d/99-readiness.sh",
            "read_only": True,
        }
    )
    model = {
        "name": project,
        "services": {"postgres": postgres},
        "volumes": {"ruisheng-pgdata": {"labels": {"ruisheng.readiness-test": project}}},
        "networks": {
            "default": {
                "internal": True,
                "ipam": {"config": [{"subnet": _available_test_subnet()}]},
            }
        },
    }
    compose_file = tmp_path / "compose.json"
    compose_file.write_text(json.dumps(model), encoding="utf-8")
    compose = ("compose", "-p", project, "-f", str(compose_file))
    try:
        docker(*compose, "up", "-d", "--pull", "never", "postgres")
        docker(
            "exec",
            project,
            "sh",
            "-c",
            "until test -f /tmp/readiness-initializing; do sleep 1; done",
            timeout=40,
        )
        initial = json.loads(docker("inspect", project))[0]
        previous_probes = {
            entry["Start"] for entry in initial["State"].get("Health", {}).get("Log", [])
        }
        deadline = time.monotonic() + 50
        while time.monotonic() < deadline:
            # Two actual production health probes cover the socket-only init phase.
            info = json.loads(docker("inspect", project))[0]
            logs = [
                entry
                for entry in info["State"].get("Health", {}).get("Log", [])
                if entry["Start"] not in previous_probes
            ]
            if len(logs) >= 2:
                break
            time.sleep(1)
        else:
            pytest.fail("PostgreSQL did not produce two health probes during initdb")
        docker("exec", project, "test", "-f", "/tmp/readiness-initializing")
        docker("exec", project, "pg_isready", "-U", "readiness_test", "-d", "ruisheng")
        (tmp_path / "init-health.json").write_text(json.dumps(info["State"]), encoding="utf-8")
        assert info["State"]["Health"]["Status"] != "healthy", (
            "temporary socket server admitted migrations"
        )
        assert all(entry["ExitCode"] != 0 for entry in logs)
        docker("exec", project, "touch", "/tmp/readiness-release")
        docker(*compose, "up", "-d", "--wait", "--wait-timeout", "60", "postgres", timeout=90)
        docker(
            "exec",
            project,
            "pg_isready",
            "-h",
            "127.0.0.1",
            "-U",
            "readiness_test",
            "-d",
            "ruisheng",
        )
        info = json.loads(docker("inspect", project))[0]
        assert info["State"]["Health"]["Status"] == "healthy"
        assert not info["HostConfig"]["PortBindings"]
    finally:
        # Retain database storage and logs for diagnosis; stop only this unique project.
        docker(*compose, "stop", "--timeout", "10")
        (tmp_path / "postgres.log").write_text(
            docker(*compose, "logs", "--no-color"), encoding="utf-8"
        )
        ids = docker("ps", "--all", "--quiet", "--filter", f"name=^/{project}$").splitlines()
        state = docker("inspect", *ids) if ids else "[]"
        (tmp_path / "final-state.json").write_text(state, encoding="utf-8")
