"""B-04 rendered Compose, ACL, Profile and Nginx boundary checks."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

import tools.validate_network_boundary as boundary
from tools.validate_network_boundary import validate

ROOT = Path(__file__).parents[2]
ENV = {
    "WEB_BIND_HOST": "127.0.0.1",
    "WEB_BIND_PORT": "80",
    "WEB_HEALTH_ACL_FILE": "site-health-acl.conf.example",
    "GW_DEVICE_BIND_HOST": "127.0.0.1",
    "GW_DEVICE_BIND_PORT": "5020",
    "GW_HEALTH_BIND_HOST": "127.0.0.1",
    "GW_HEALTH_BIND_PORT": "9090",
    "GW_HEALTH_HOST": "0.0.0.0",
    "GW_HEALTH_ALLOWED_CIDRS": "127.0.0.1/32,::1/128",
}


def _model() -> dict[str, object]:
    return {
        "networks": {"default": {}},
        "services": {
            "api": {"ports": []},
            "postgres": {"ports": []},
            "redis": {"ports": []},
            "migrate": {"ports": []},
            "gw": {
                "environment": {
                    "GW_LISTEN_HOST": "0.0.0.0",
                    "GW_LISTEN_PORT": "5020",
                    "GW_HEALTH_HOST": "0.0.0.0",
                    "GW_HEALTH_PORT": "9090",
                    "GW_HEALTH_ALLOWED_CIDRS": "127.0.0.1/32,::1/128",
                },
                "ports": [
                    {
                        "host_ip": "127.0.0.1",
                        "target": 5020,
                        "published": "5020",
                        "protocol": "tcp",
                    },
                    {
                        "host_ip": "127.0.0.1",
                        "target": 9090,
                        "published": "9090",
                        "protocol": "tcp",
                    },
                ],
            },
            "web": {
                "ports": [
                    {"host_ip": "127.0.0.1", "target": 80, "published": "80", "protocol": "tcp"}
                ],
                "volumes": [
                    {
                        "type": "bind",
                        "source": "site-health-acl.conf.example",
                        "target": "/etc/nginx/site-health-acl.conf",
                        "read_only": True,
                    }
                ],
            },
        },
    }


def _nginx() -> str:
    return (ROOT / "ruisheng-web" / "nginx.conf").read_text(encoding="utf-8")


def _acl() -> str:
    return (ROOT / "deploy" / "site-health-acl.conf.example").read_text(encoding="utf-8")


def _approved_profile() -> str:
    return """# 站点验收参数

## 审批

| 字段 | 决定 |
|---|---|
| Profile ID / 版本 | b04-test/1 |
| 项目负责人 | project-owner |
| 运维负责人 | ops-owner |
| 客户代表 | customer-representative |
| 批准时间 | 2026-08-20T12:00:00+08:00 |
| 安全/合规负责人 | security-owner |

## 网络与安全

| 字段 | 决定 |
|---|---|
| 用户网段（CIDR，逗号分隔） | 10.0.0.0/24 |
| 设备网段（CIDR，逗号分隔） | 10.0.1.0/24 |
| 运维/监控网段（CIDR，逗号分隔） | 127.0.0.1/32, ::1/128 |
| 外部服务网段（CIDR，逗号分隔或批准 N/A） | N/A |
| 未批准探测源（CIDR，逗号分隔） | 192.0.2.0/24 |
| Web 宿主绑定（IP:端口） | 127.0.0.1:80 |
| GW 设备宿主绑定（IP:端口） | 127.0.0.1:5020 |
| GW 管理宿主绑定（IP:端口） | 127.0.0.1:9090 |
| Web 传输模式（LOOPBACK_ONLY / TRUSTED_HTTP / HTTPS_WSS） | LOOPBACK_ONLY |
| TLS 终止、证书及 Web 直连旁路防护 | LOOPBACK_ONLY: no shared-network exposure |
| API health/metrics 访问主体和 ACL | local monitoring agent |
| GW health/ready/metrics 源 ACL/防火墙 | local monitoring agent and host firewall |
| IPv4/IPv6 启用或禁用位置与证据 | IPv4 and IPv6 loopback only |
| 防火墙平台、配置负责人、复核人及持久化 | host firewall / ops-owner / customer-representative / persistent |
| 用户、设备、监控和未批准源探测位置 | test fixture namespaces |
"""


def test_default_boundary_is_valid_with_approved_profile() -> None:
    assert validate(_model(), ENV, _nginx(), _acl(), _approved_profile()) == []


def test_missing_profile_keeps_b04_blocked() -> None:
    findings = validate(_model(), ENV, _nginx(), _acl(), None)
    assert any(finding.blocked for finding in findings)
    assert any("profile" in finding.message for finding in findings)


def test_wildcard_bind_and_internal_port_are_rejected() -> None:
    model = _model()
    services = model["services"]
    assert isinstance(services, dict)
    services["web"]["ports"][0]["host_ip"] = "0.0.0.0"
    services["api"]["ports"] = [{"target": 8000, "published": "8000", "host_ip": "0.0.0.0"}]
    findings = validate(model, ENV, _nginx(), _acl(), _approved_profile())
    messages = [finding.message for finding in findings]
    assert any("web must publish" in message for message in messages)
    assert any("api must not publish" in message for message in messages)


def test_container_health_listener_must_be_docker_reachable() -> None:
    model = _model()
    services = model["services"]
    assert isinstance(services, dict)
    services["gw"]["environment"]["GW_HEALTH_HOST"] = "127.0.0.1"
    findings = validate(model, ENV, _nginx(), _acl(), _approved_profile())
    assert any("GW_HEALTH_HOST must listen" in finding.message for finding in findings)


def test_ipv6_bracketed_site_values_match_rendered_addresses() -> None:
    env = {
        **ENV,
        "WEB_BIND_HOST": "[::1]",
        "GW_DEVICE_BIND_HOST": "[::1]",
        "GW_HEALTH_BIND_HOST": "[::1]",
    }
    model = _model()
    services = model["services"]
    assert isinstance(services, dict)
    services["web"]["ports"][0]["host_ip"] = "::1"
    services["gw"]["ports"][0]["host_ip"] = "::1"
    services["gw"]["ports"][1]["host_ip"] = "::1"
    assert boundary.validate_model(model, env) == []


def test_nginx_and_acl_are_checked_for_query_string_leaks() -> None:
    findings = validate(
        _model(), ENV, _nginx().replace("$uri", "$request_uri"), _acl(), _approved_profile()
    )
    assert any("sensitive variable" in finding.message for finding in findings)


def test_nginx_does_not_log_user_agent_or_remote_user() -> None:
    mutated = _nginx().replace("$body_bytes_sent", "$body_bytes_sent $http_user_agent $remote_user")
    findings = validate(_model(), ENV, mutated, _acl(), _approved_profile())
    messages = [finding.message for finding in findings]
    assert any("$http_user_agent" in message for message in messages)
    assert any("$remote_user" in message for message in messages)


def test_nginx_error_log_must_be_at_server_scope() -> None:
    mutated = (
        _nginx()
        .replace("    error_log /dev/null crit;\n", "", 1)
        .replace("    location /ws {", "    location /ws {\n        error_log /dev/null crit;")
    )
    findings = validate(_model(), ENV, mutated, _acl(), _approved_profile())
    assert any("server scope" in finding.message for finding in findings)


def test_missing_or_unreadable_acl_is_blocked(tmp_path: Path) -> None:
    profile = tmp_path / "profile.md"
    profile.write_text(_approved_profile(), encoding="utf-8")
    env = tmp_path / ".env"
    env.write_text("\n".join(f"{key}={value}" for key, value in ENV.items()), encoding="utf-8")
    code = boundary.main(
        [
            "--compose",
            str(ROOT / "deploy" / "docker-compose.prod.yml"),
            "--compose",
            str(ROOT / "deploy" / "site-network.override.yml"),
            "--env-file",
            str(env),
            "--profile",
            str(profile),
            "--nginx-config",
            str(ROOT / "ruisheng-web" / "nginx.conf"),
            "--acl-file",
            str(tmp_path / "missing-acl.conf"),
        ]
    )
    assert code == 2


def test_broad_acl_is_rejected() -> None:
    acl = "allow 0.0.0.0/0;\ndeny all;\n"
    findings = validate(_model(), ENV, _nginx(), acl, _approved_profile())
    assert any("default route" in finding.message for finding in findings)


def test_acl_must_match_approved_monitoring_networks() -> None:
    acl = "allow 10.99.0.0/24;\ndeny all;\n"
    findings = validate(_model(), ENV, _nginx(), acl, _approved_profile())
    assert any("exactly match" in finding.message for finding in findings)


def test_empty_profile_section_is_blocked() -> None:
    findings = validate(_model(), ENV, _nginx(), _acl(), "## 网络与安全\n")
    assert any(finding.blocked for finding in findings)


def test_all_profile_cidr_fields_are_validated() -> None:
    profile = _approved_profile().replace("10.0.0.0/24", "not-a-cidr", 1)
    findings = validate(_model(), ENV, _nginx(), _acl(), profile)
    assert any(finding.blocked and "invalid CIDR" in finding.message for finding in findings)


def test_non_loopback_https_profile_requires_real_termination_evidence() -> None:
    profile = (
        _approved_profile()
        .replace("127.0.0.1:80", "10.0.0.10:80")
        .replace(
            "| Web 传输模式（LOOPBACK_ONLY / TRUSTED_HTTP / HTTPS_WSS） | LOOPBACK_ONLY |",
            "| Web 传输模式（LOOPBACK_ONLY / TRUSTED_HTTP / HTTPS_WSS） | HTTPS_WSS |",
        )
        .replace("LOOPBACK_ONLY: no shared-network exposure", "yes")
    )
    findings = validate(_model(), {**ENV, "WEB_BIND_HOST": "10.0.0.10"}, _nginx(), _acl(), profile)
    assert any(finding.blocked and "HTTPS/WSS" in finding.message for finding in findings)


def test_non_loopback_https_profile_accepts_only_structured_evidence() -> None:
    profile = (
        _approved_profile()
        .replace("127.0.0.1:80", "10.0.0.10:80")
        .replace(
            "| Web 传输模式（LOOPBACK_ONLY / TRUSTED_HTTP / HTTPS_WSS） | LOOPBACK_ONLY |",
            "| Web 传输模式（LOOPBACK_ONLY / TRUSTED_HTTP / HTTPS_WSS） | HTTPS_WSS |",
        )
        .replace(
            "LOOPBACK_ONLY: no shared-network exposure",
            "termination=edge-proxy:443; certificate=vault://b04/web; "
            "domain=web.example.test; firewall=fw-rule-b04; direct_http=deny; direct_ws=deny",
        )
    )
    model = _model()
    model["services"]["web"]["ports"][0]["host_ip"] = "10.0.0.10"
    assert validate(model, {**ENV, "WEB_BIND_HOST": "10.0.0.10"}, _nginx(), _acl(), profile) == []


def test_non_loopback_trusted_http_requires_explicit_allow_lists() -> None:
    profile = (
        _approved_profile()
        .replace("127.0.0.1:80", "10.0.0.10:80")
        .replace(
            "| Web 传输模式（LOOPBACK_ONLY / TRUSTED_HTTP / HTTPS_WSS） | LOOPBACK_ONLY |",
            "| Web 传输模式（LOOPBACK_ONLY / TRUSTED_HTTP / HTTPS_WSS） | TRUSTED_HTTP |",
        )
        .replace(
            "LOOPBACK_ONLY: no shared-network exposure",
            "isolation=trusted-segment-b04; firewall=fw-rule-b04; "
            "direct_http=trusted-only; direct_ws=trusted-only",
        )
    )
    model = _model()
    model["services"]["web"]["ports"][0]["host_ip"] = "10.0.0.10"
    assert validate(model, {**ENV, "WEB_BIND_HOST": "10.0.0.10"}, _nginx(), _acl(), profile) == []


def test_https_evidence_rejects_fake_references() -> None:
    profile = (
        _approved_profile()
        .replace("127.0.0.1:80", "10.0.0.10:80")
        .replace(
            "| Web 传输模式（LOOPBACK_ONLY / TRUSTED_HTTP / HTTPS_WSS） | LOOPBACK_ONLY |",
            "| Web 传输模式（LOOPBACK_ONLY / TRUSTED_HTTP / HTTPS_WSS） | HTTPS_WSS |",
        )
        .replace(
            "LOOPBACK_ONLY: no shared-network exposure",
            "termination=baz; certificate=foo; domain=bar; firewall=x; "
            "direct_http=deny; direct_ws=deny",
        )
    )
    model = _model()
    model["services"]["web"]["ports"][0]["host_ip"] = "10.0.0.10"
    findings = validate(model, {**ENV, "WEB_BIND_HOST": "10.0.0.10"}, _nginx(), _acl(), profile)
    assert any(finding.blocked and "certificate" in finding.message for finding in findings)


def test_approval_na_is_not_a_real_approval() -> None:
    profile = _approved_profile().replace("| 项目负责人 | project-owner |", "| 项目负责人 | N/A |")
    findings = validate(_model(), ENV, _nginx(), _acl(), profile)
    assert any(finding.blocked and "项目负责人" in finding.message for finding in findings)


def test_gateway_health_acl_must_match_profile_monitoring_cidrs() -> None:
    findings = validate(
        _model(),
        {**ENV, "GW_HEALTH_ALLOWED_CIDRS": "10.99.0.0/24"},
        _nginx(),
        _acl(),
        _approved_profile(),
    )
    assert any("GW health source ACL" in finding.message for finding in findings)


def test_unknown_service_exposure_is_rejected() -> None:
    model = _model()
    services = model["services"]
    assert isinstance(services, dict)
    services["unexpected"] = {"ports": [{"target": 9000, "published": "9000"}]}
    services["migrate"]["expose"] = ["8000"]
    findings = validate(model, ENV, _nginx(), _acl(), _approved_profile())
    messages = [finding.message for finding in findings]
    assert any("service set must be exactly" in message for message in messages)
    assert any("migrate must not declare expose" in message for message in messages)


def test_missing_security_approval_keeps_profile_blocked() -> None:
    profile = _approved_profile().replace(
        "| 安全/合规负责人 | security-owner |", "| 安全/合规负责人 | UNRESOLVED |"
    )
    findings = validate(_model(), ENV, _nginx(), _acl(), profile)
    assert any(finding.blocked and "安全/合规负责人" in finding.message for finding in findings)


def test_cli_renders_compose_and_does_not_accept_rendered_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    acl = tmp_path / "site-health-acl.conf"
    acl.write_text(_acl(), encoding="utf-8")
    profile = tmp_path / "profile.md"
    profile.write_text(_approved_profile(), encoding="utf-8")
    env = tmp_path / ".env"
    env.write_text("\n".join(f"{key}={value}" for key, value in ENV.items()), encoding="utf-8")
    model = deepcopy(_model())
    model["services"]["web"]["volumes"][0]["source"] = str(acl)
    monkeypatch.setattr(boundary, "_render_compose", lambda compose, env_file: model)
    code = boundary.main(
        [
            "--compose",
            str(ROOT / "deploy" / "docker-compose.prod.yml"),
            "--env-file",
            str(env),
            "--profile",
            str(profile),
            "--nginx-config",
            str(ROOT / "ruisheng-web" / "nginx.conf"),
            "--acl-file",
            str(acl),
        ]
    )
    assert code == 0
    with pytest.raises(SystemExit):
        boundary.main(
            [
                "--compose",
                "x",
                "--env-file",
                "x",
                "--rendered-json",
                "x",
                "--nginx-config",
                "x",
                "--acl-file",
                "x",
            ]
        )
