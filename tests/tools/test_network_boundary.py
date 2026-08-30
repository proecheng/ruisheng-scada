"""B-04 rendered Compose, ACL, Profile and Nginx boundary checks."""

from __future__ import annotations

import hashlib
from copy import deepcopy
from pathlib import Path

import pytest

import tools.validate_network_boundary as boundary
from tools.validate_network_boundary import validate

ROOT = Path(__file__).parents[2]
MANAGEMENT_DIGEST = "f" * 64
ENV = {
    "APP_NETWORK_SUBNET": "10.254.250.0/24",
    "APP_NETWORK_GATEWAY": "10.254.250.1",
    "WEB_BIND_HOST": "127.0.0.1",
    "WEB_BIND_PORT": "80",
    "WEB_HEALTH_ACL_FILE": "site-health-acl.conf.example",
    "GW_DEVICE_BIND_HOST": "127.0.0.1",
    "GW_DEVICE_BIND_PORT": "5020",
    "GW_HEALTH_BIND_HOST": "127.0.0.1",
    "GW_HEALTH_BIND_PORT": "9090",
    "GW_HEALTH_HOST": "0.0.0.0",
    "GW_HEALTH_ALLOWED_CIDRS": "10.254.250.1/32",
    "MANAGEMENT_TOKEN_SHA256": MANAGEMENT_DIGEST,
}


def _model() -> dict[str, object]:
    return {
        "networks": {
            "default": {
                "driver": "bridge",
                "ipam": {"config": [{"subnet": "10.254.250.0/24", "gateway": "10.254.250.1"}]},
            }
        },
        "services": {
            "api": {
                "environment": {
                    "API_ENV": "prod",
                    "API_MANAGEMENT_TOKEN_SHA256": MANAGEMENT_DIGEST,
                },
                "ports": [],
            },
            "postgres": {"ports": []},
            "redis": {"ports": []},
            "migrate": {"ports": []},
            "gw": {
                "environment": {
                    "GW_LISTEN_HOST": "0.0.0.0",
                    "GW_LISTEN_PORT": "5020",
                    "GW_ENV": "prod",
                    "GW_HEALTH_HOST": "0.0.0.0",
                    "GW_HEALTH_PORT": "9090",
                    "GW_HEALTH_ALLOWED_CIDRS": "10.254.250.1/32",
                    "GW_HEALTH_TOKEN_SHA256": MANAGEMENT_DIGEST,
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
| Docker 应用网络子网（CIDR） | 10.254.250.0/24 |
| Docker 应用网络网关（IP） | 10.254.250.1 |
| 管理端点容器观察来源（CIDR，逗号分隔） | 10.254.250.1/32 |
| 外部服务网段（CIDR，逗号分隔或批准 N/A） | N/A |
| 未批准探测源（CIDR，逗号分隔） | 192.0.2.0/24 |
| Web 宿主绑定（IP:端口） | 127.0.0.1:80 |
| GW 设备宿主绑定（IP:端口） | 127.0.0.1:5020 |
| GW 管理宿主绑定（IP:端口） | 127.0.0.1:9090 |
| Web 传输模式（LOOPBACK_ONLY / TRUSTED_HTTP / HTTPS_WSS） | LOOPBACK_ONLY |
| TLS 终止、证书及 Web 直连旁路防护 | LOOPBACK_ONLY: no shared-network exposure |
| API health/metrics 访问主体和 ACL | local monitoring agent |
| GW health/ready/metrics 源 ACL/防火墙 | local monitoring agent and host firewall |
| 管理端点认证方案（固定 BEARER_SHA256） | BEARER_SHA256 |
| 管理端点令牌 SHA-256（64 位小写十六进制） | ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff |
| 管理端点凭据生成、保管、轮换和恢复负责人 | ops-owner / vault://site/monitoring-token / quarterly |
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


@pytest.mark.parametrize(
    "digest",
    ("", "CHANGE_ME_MANAGEMENT_TOKEN_SHA256", "A" * 64, "a" * 63, "g" * 64),
)
def test_management_digest_must_be_real_lowercase_sha256(digest: str) -> None:
    findings = validate(
        _model(),
        {**ENV, "MANAGEMENT_TOKEN_SHA256": digest},
        _nginx(),
        _acl(),
        _approved_profile(),
    )
    assert any("MANAGEMENT_TOKEN_SHA256" in finding.message for finding in findings)


def test_management_digest_must_match_compose_and_profile() -> None:
    model = _model()
    model["services"]["api"]["environment"]["API_MANAGEMENT_TOKEN_SHA256"] = "e" * 64
    model["services"]["gw"]["environment"]["GW_HEALTH_TOKEN_SHA256"] = "d" * 64
    findings = validate(model, ENV, _nginx(), _acl(), _approved_profile())
    messages = [finding.message for finding in findings]
    assert any("API_MANAGEMENT_TOKEN_SHA256" in message for message in messages)
    assert any("GW_HEALTH_TOKEN_SHA256" in message for message in messages)

    profile = _approved_profile().replace(MANAGEMENT_DIGEST, "e" * 64)
    findings = validate(_model(), ENV, _nginx(), _acl(), profile)
    assert any("approved site profile" in finding.message for finding in findings)


@pytest.mark.parametrize(("service", "key"), (("api", "API_ENV"), ("gw", "GW_ENV")))
def test_production_mode_must_be_explicit(service: str, key: str) -> None:
    model = _model()
    model["services"][service]["environment"][key] = "test"
    findings = validate(model, ENV, _nginx(), _acl(), _approved_profile())
    assert any(key in finding.message and "prod" in finding.message for finding in findings)


def test_plaintext_management_token_must_not_enter_any_service() -> None:
    model = _model()
    model["services"]["web"]["environment"] = {"MANAGEMENT_TOKEN": "reusable-secret"}
    findings = validate(model, ENV, _nginx(), _acl(), _approved_profile())
    messages = [finding.message for finding in findings]
    assert any("environment keys" in message for message in messages)
    assert any("plaintext management token" in message for message in messages)


def test_plaintext_management_token_is_detected_under_unrelated_key() -> None:
    token = "b04-review-" + "x" * 48
    digest = hashlib.sha256(token.encode("ascii")).hexdigest()
    env = {**ENV, "MANAGEMENT_TOKEN_SHA256": digest}
    model = _model()
    model["services"]["api"]["environment"]["API_MANAGEMENT_TOKEN_SHA256"] = digest
    model["services"]["gw"]["environment"]["GW_HEALTH_TOKEN_SHA256"] = digest
    model["services"]["web"]["environment"] = {"OPS_BEARER": token}
    profile = _approved_profile().replace(MANAGEMENT_DIGEST, digest)
    findings = validate(model, env, _nginx(), _acl(), profile)
    assert any("plaintext management token" in finding.message for finding in findings)


@pytest.mark.parametrize("resource", ["secrets", "configs"])
def test_compose_credentials_and_configs_are_rejected(resource: str) -> None:
    model = _model()
    model[resource] = {"monitoring": {"file": "./credential"}}
    model["services"]["web"][resource] = [{"source": "monitoring"}]
    findings = validate(model, ENV, _nginx(), _acl(), _approved_profile())
    assert any(resource in finding.message for finding in findings)


def test_unapproved_credential_mount_is_rejected() -> None:
    model = _model()
    model["services"]["api"]["volumes"] = [
        {"type": "bind", "source": "./credential", "target": "/run/monitoring-token"}
    ]
    findings = validate(model, ENV, _nginx(), _acl(), _approved_profile())
    assert any("unapproved volume mount" in finding.message for finding in findings)


def test_profile_auth_scheme_is_fixed() -> None:
    profile = _approved_profile().replace(
        "| 管理端点认证方案（固定 BEARER_SHA256） | BEARER_SHA256 |",
        "| 管理端点认证方案（固定 BEARER_SHA256） | BASIC |",
    )
    _decision, findings = boundary.validate_profile(profile)
    assert any(finding.blocked and "auth scheme" in finding.message for finding in findings)


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


def test_expanded_ipv6_unspecified_host_is_rejected() -> None:
    env = {**ENV, "WEB_BIND_HOST": "0:0:0:0:0:0:0:0"}
    findings = validate(_model(), env, _nginx(), _acl(), _approved_profile())
    assert any("wildcard/empty host" in finding.message for finding in findings)


def test_ipv4_mapped_ipv6_unspecified_host_is_rejected() -> None:
    env = {**ENV, "WEB_BIND_HOST": "::ffff:0.0.0.0"}
    findings = validate(_model(), env, _nginx(), _acl(), _approved_profile())
    assert any("wildcard/empty host" in finding.message for finding in findings)


def test_rendered_ipam_must_match_env_and_profile() -> None:
    model = _model()
    model["networks"]["default"]["ipam"]["config"][0]["gateway"] = "10.254.250.2"
    findings = validate(model, ENV, _nginx(), _acl(), _approved_profile())
    assert any("rendered application network IPAM" in finding.message for finding in findings)


def test_container_observed_acl_rejects_entire_bridge() -> None:
    profile = _approved_profile().replace(
        "| 管理端点容器观察来源（CIDR，逗号分隔） | 10.254.250.1/32 |",
        "| 管理端点容器观察来源（CIDR，逗号分隔） | 10.254.250.0/24 |",
    )
    findings = validate(
        _model(),
        {**ENV, "GW_HEALTH_ALLOWED_CIDRS": "10.254.250.0/24"},
        _nginx(),
        "allow 10.254.250.0/24;\ndeny all;\n",
        profile,
    )
    assert any(
        finding.blocked and "exact rendered bridge gateway" in finding.message
        for finding in findings
    )


def test_untranslated_approved_monitoring_cidr_can_be_observed_directly() -> None:
    profile = _approved_profile().replace(
        "| 管理端点容器观察来源（CIDR，逗号分隔） | 10.254.250.1/32 |",
        "| 管理端点容器观察来源（CIDR，逗号分隔） | 127.0.0.1/32 |",
    )
    env = {**ENV, "GW_HEALTH_ALLOWED_CIDRS": "127.0.0.1/32"}
    model = _model()
    model["services"]["gw"]["environment"]["GW_HEALTH_ALLOWED_CIDRS"] = "127.0.0.1/32"
    assert validate(model, env, _nginx(), "allow 127.0.0.1/32;\ndeny all;\n", profile) == []


def test_monitoring_cidrs_must_not_overlap_unapproved_sources() -> None:
    findings = validate(
        _model(),
        ENV,
        _nginx(),
        _acl(),
        _approved_profile().replace("192.0.2.0/24", "::1/128", 1),
    )
    assert any(finding.blocked and "unapproved" in finding.message for finding in findings)


def test_user_and_device_cidrs_must_not_overlap_unapproved_sources() -> None:
    for approved in ("10.0.0.0/24", "10.0.1.0/24"):
        profile = _approved_profile().replace("192.0.2.0/24", approved, 1)
        findings = validate(_model(), ENV, _nginx(), _acl(), profile)
        assert any(finding.blocked and "unapproved" in finding.message for finding in findings)


def test_ipv4_mapped_ipv6_cidrs_are_rejected_consistently() -> None:
    mapped = "::ffff:192.0.2.10/128"
    with pytest.raises(ValueError, match="IPv4-mapped"):
        boundary._parse_networks(mapped, "mapped")  # noqa: SLF001
    _allowed, acl_findings = boundary.validate_acl(f"allow {mapped};\ndeny all;\n")
    assert any("IPv4-mapped" in finding.message for finding in acl_findings)


def test_container_observed_host_route_may_be_inside_approved_monitoring_subnet() -> None:
    profile = (
        _approved_profile()
        .replace("127.0.0.1/32, ::1/128", "10.40.0.0/24")
        .replace(
            "| 管理端点容器观察来源（CIDR，逗号分隔） | 10.254.250.1/32 |",
            "| 管理端点容器观察来源（CIDR，逗号分隔） | 10.40.0.12/32 |",
        )
    )
    env = {**ENV, "GW_HEALTH_ALLOWED_CIDRS": "10.40.0.12/32"}
    model = _model()
    model["services"]["gw"]["environment"]["GW_HEALTH_ALLOWED_CIDRS"] = "10.40.0.12/32"
    assert validate(model, env, _nginx(), "allow 10.40.0.12/32;\ndeny all;\n", profile) == []


def test_profile_rejects_duplicate_section_and_table_key() -> None:
    duplicate_section = _approved_profile() + "\n## 审批\n\n| 字段 | 决定 |\n"
    section_findings = validate(_model(), ENV, _nginx(), _acl(), duplicate_section)
    assert any(
        finding.blocked and "duplicate sections" in finding.message for finding in section_findings
    )

    duplicate_key = _approved_profile().replace(
        "| 项目负责人 | project-owner |",
        "| 项目负责人 | project-owner |\n| 项目负责人 | second-owner |",
    )
    key_findings = validate(_model(), ENV, _nginx(), _acl(), duplicate_key)
    assert any(
        finding.blocked and "duplicate table key" in finding.message for finding in key_findings
    )


def test_lowercase_placeholders_are_blocked() -> None:
    profile = _approved_profile().replace(
        "| 项目负责人 | project-owner |", "| 项目负责人 | pending |"
    )
    findings = validate(_model(), ENV, _nginx(), "allow pending;\ndeny all;\n", profile)
    assert any(finding.blocked and "unresolved" in finding.message for finding in findings)


def test_nginx_rejects_extra_health_acl_include_and_inline_allow() -> None:
    mutated = _nginx().replace(
        "include /etc/nginx/site-health-acl.conf;",
        "include /etc/nginx/site-health-acl.conf;\n        include /etc/nginx/extra.conf;\n        allow all;",
        1,
    )
    findings = validate(_model(), ENV, mutated, _acl(), _approved_profile())
    messages = [finding.message for finding in findings]
    assert any("include only" in message for message in messages)
    assert any("must not add ACL rules" in message for message in messages)


def test_nginx_rejects_real_ip_and_proxy_protocol_source_rewrites() -> None:
    for directive in (
        "set_real_ip_from 0.0.0.0/0;\nreal_ip_header X-Forwarded-For;",
        "real_ip_recursive on;",
        "listen 80 proxy_protocol;",
    ):
        mutated = _nginx().replace("server {", f"server {{\n    {directive}", 1)
        findings = validate(_model(), ENV, mutated, _acl(), _approved_profile())
        assert any("must not rewrite ACL source" in finding.message for finding in findings)


def test_nginx_rejects_arg_logging_variables() -> None:
    mutated = _nginx().replace("$body_bytes_sent", "$body_bytes_sent $arg_token")
    findings = validate(_model(), ENV, mutated, _acl(), _approved_profile())
    assert any("$arg_*" in finding.message for finding in findings)


def test_nginx_allows_non_logging_arg_variables() -> None:
    mutated = _nginx().replace("location / {", "location / {\n        set $cache_hint $arg_page;")
    findings = validate(_model(), ENV, mutated, _acl(), _approved_profile())
    assert not any("$arg_*" in finding.message for finding in findings)


def test_https_termination_port_must_be_in_range() -> None:
    profile = (
        _approved_profile()
        .replace("127.0.0.1:80", "10.0.0.10:80")
        .replace(
            "| Web 传输模式（LOOPBACK_ONLY / TRUSTED_HTTP / HTTPS_WSS） | LOOPBACK_ONLY |",
            "| Web 传输模式（LOOPBACK_ONLY / TRUSTED_HTTP / HTTPS_WSS） | HTTPS_WSS |",
        )
        .replace(
            "LOOPBACK_ONLY: no shared-network exposure",
            "termination=edge-proxy:70000; certificate=vault://b04/web; "
            "domain=web.example.test; firewall=fw-rule-b04; direct_http=deny; direct_ws=deny",
        )
    )
    findings = validate(_model(), {**ENV, "WEB_BIND_HOST": "10.0.0.10"}, _nginx(), _acl(), profile)
    assert any(finding.blocked and "termination port" in finding.message for finding in findings)


def test_three_host_bindings_must_not_conflict() -> None:
    profile = _approved_profile().replace("127.0.0.1:5020", "127.0.0.1:80")
    findings = validate(_model(), ENV, _nginx(), _acl(), profile)
    assert any(finding.blocked and "must not conflict" in finding.message for finding in findings)


def test_invalid_utf8_acl_and_profile_are_controlled_blocked(tmp_path: Path) -> None:
    acl = tmp_path / "site-health-acl.conf"
    acl.write_bytes(b"allow \xff;\n")
    profile = tmp_path / "profile.md"
    profile.write_bytes(b"# profile\n\xff")
    env = tmp_path / ".env"
    env.write_text("\n".join(f"{key}={value}" for key, value in ENV.items()), encoding="utf-8")
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
    assert code == 2


@pytest.mark.parametrize(
    ("subnet", "gateway"),
    (
        ("0.0.0.0/0", "10.254.250.1"),
        ("10.254.250.0/29", "10.254.250.1"),
        ("127.0.0.0/24", "127.0.0.1"),
        ("169.254.40.0/24", "169.254.40.1"),
        ("2001:db8::/64", "2001:db8::1"),
    ),
)
def test_application_network_rejects_unusable_ipam(subnet: str, gateway: str) -> None:
    with pytest.raises(ValueError):
        boundary._application_network(  # noqa: SLF001
            {"APP_NETWORK_SUBNET": subnet, "APP_NETWORK_GATEWAY": gateway}
        )


def test_application_network_must_not_overlap_site_cidrs() -> None:
    profile = (
        _approved_profile()
        .replace("10.254.250.0/24", "10.0.0.0/24")
        .replace("10.254.250.1", "10.0.0.1")
    )
    _decision, findings = boundary.validate_profile(profile)
    assert any(finding.blocked and "must not overlap" in finding.message for finding in findings)


@pytest.mark.parametrize(
    ("key", "value"),
    (
        ("driver", "overlay"),
        ("internal", True),
        ("attachable", True),
        ("enable_ipv6", True),
        ("driver_opts", {"com.docker.network.bridge.gateway_mode_ipv4": "routed"}),
    ),
)
def test_rendered_application_bridge_rejects_non_nat_options(key: str, value: object) -> None:
    model = _model()
    model["networks"]["default"][key] = value
    findings = boundary.validate_model(model, ENV)
    assert any("bridge" in finding.message for finding in findings)


@pytest.mark.parametrize(
    ("key", "value"),
    (("driver", "custom-ipam"), ("options", {"arbitrary": "value"})),
)
def test_rendered_application_bridge_rejects_custom_ipam(key: str, value: object) -> None:
    model = _model()
    model["networks"]["default"]["ipam"][key] = value
    findings = boundary.validate_model(model, ENV)
    assert any("default IPAM" in finding.message for finding in findings)


def test_placeholder_tokens_cannot_hide_inside_longer_values() -> None:
    profile = _approved_profile().replace(
        "| 项目负责人 | project-owner |", "| 项目负责人 | owner_pending_review |"
    )
    findings = validate(_model(), ENV, _nginx(), _acl(), profile)
    assert any(finding.blocked and "unresolved" in finding.message for finding in findings)
