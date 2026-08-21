"""Validate the rendered production network boundary before deployment."""

from __future__ import annotations

import argparse
import ipaddress
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

EXPECTED_SERVICES = {"postgres", "redis", "migrate", "api", "gw", "web"}
PUBLISHED_SERVICES = {"gw", "web"}
WILDCARD_HOSTS = {"", "0.0.0.0", "::", "[::]"}
WILDCARD_LISTENERS = {"0.0.0.0", "::"}
ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
SECTION_RE = re.compile(r"^## (?P<title>[^\r\n]+)\s*$", re.MULTILINE)
TABLE_ROW_RE = re.compile(r"^\|\s*(?P<key>[^|]+?)\s*\|\s*(?P<value>[^|]*?)\s*\|\s*$")
MAX_PORT = 65535
MIN_QUOTED_VALUE_LENGTH = 2

APPROVAL_FIELDS = (
    "Profile ID / 版本",
    "项目负责人",
    "运维负责人",
    "客户代表",
    "批准时间",
    "安全/合规负责人",
)
NETWORK_FIELDS = (
    "用户网段（CIDR，逗号分隔）",
    "设备网段（CIDR，逗号分隔）",
    "运维/监控网段（CIDR，逗号分隔）",
    "外部服务网段（CIDR，逗号分隔或批准 N/A）",
    "未批准探测源（CIDR，逗号分隔）",
    "Web 宿主绑定（IP:端口）",
    "GW 设备宿主绑定（IP:端口）",
    "GW 管理宿主绑定（IP:端口）",
    "Web 传输模式（LOOPBACK_ONLY / TRUSTED_HTTP / HTTPS_WSS）",
    "TLS 终止、证书及 Web 直连旁路防护",
    "API health/metrics 访问主体和 ACL",
    "GW health/ready/metrics 源 ACL/防火墙",
    "IPv4/IPv6 启用或禁用位置与证据",
    "防火墙平台、配置负责人、复核人及持久化",
    "用户、设备、监控和未批准源探测位置",
)
CIDR_PROFILE_FIELDS = (
    "用户网段（CIDR，逗号分隔）",
    "设备网段（CIDR，逗号分隔）",
    "运维/监控网段（CIDR，逗号分隔）",
    "未批准探测源（CIDR，逗号分隔）",
)
BANNED_NGINX_LOG_VARIABLES = (
    "$request",
    "$request_uri",
    "$args",
    "$query_string",
    "$http_authorization",
    "$http_cookie",
    "$http_referer",
    "$http_user_agent",
    "$remote_user",
)
EVIDENCE_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*$")
EVIDENCE_PLACEHOLDERS = {
    "",
    "change_me",
    "change-me",
    "n/a",
    "na",
    "no",
    "none",
    "provided",
    "true",
    "yes",
}
CERTIFICATE_REFERENCE_RE = re.compile(r"^(?:vault|secret|kms)://[^\s;]+$|^sha256:[0-9a-f]{64}$")
DOMAIN_RE = re.compile(
    r"^(?:\*\.)?(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+"
    r"[A-Za-z]{2,63}$"
)
TERMINATION_REFERENCE_RE = re.compile(r"^[A-Za-z0-9_.-]+(?::[1-9][0-9]{0,4})?$")
FIREWALL_REFERENCE_RE = re.compile(r"^(?:fw|firewall|rule)[-_][A-Za-z0-9_.-]+$")
APPROVAL_PLACEHOLDERS = {"n/a", "na", "批准 n/a", "none", "unknown", "pending"}
APPROVAL_PLACEHOLDERS.update({"change_me", "change-me", "tbd"})


@dataclass(frozen=True)
class Finding:
    message: str
    blocked: bool = False


@dataclass(frozen=True)
class ProfileDecision:
    user_networks: frozenset[ipaddress.IPv4Network | ipaddress.IPv6Network]
    device_networks: frozenset[ipaddress.IPv4Network | ipaddress.IPv6Network]
    monitoring_networks: frozenset[ipaddress.IPv4Network | ipaddress.IPv6Network]
    external_networks: frozenset[ipaddress.IPv4Network | ipaddress.IPv6Network] | None
    web_bind: tuple[str, int]
    gw_device_bind: tuple[str, int]
    gw_health_bind: tuple[str, int]
    web_transport: str


def parse_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line_number, raw in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if not separator or ENV_KEY_RE.fullmatch(key) is None:
            raise ValueError(f"{path}:{line_number}: invalid environment entry")
        if key in values:
            raise ValueError(f"{path}:{line_number}: duplicate environment key {key}")
        if (
            len(value) >= MIN_QUOTED_VALUE_LENGTH
            and value[0] == value[-1]
            and value[0] in {'"', "'"}
        ):
            value = value[1:-1]
        values[key] = value
    return values


def _int_value(values: dict[str, str], key: str, default: int) -> int:
    raw = values.get(key, str(default))
    try:
        value = int(raw)
    except ValueError as error:
        raise ValueError(f"{key} must be an integer") from error
    if not 1 <= value <= MAX_PORT:
        raise ValueError(f"{key} must be between 1 and {MAX_PORT}")
    return value


def _normalize_ip(value: str, *, key: str, allow_wildcard: bool = False) -> str:
    candidate = value.strip()
    if candidate.startswith("[") and candidate.endswith("]"):
        candidate = candidate[1:-1]
    if not allow_wildcard and candidate in WILDCARD_HOSTS:
        raise ValueError(f"{key} uses a wildcard/empty host; approve a concrete bind address")
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError as error:
        raise ValueError(f"{key} must be an IPv4 or IPv6 address: {value!r}") from error


def _host_value(values: dict[str, str], key: str) -> str:
    return _normalize_ip(values.get(key, "127.0.0.1"), key=key)


def _ports(service: dict[str, Any], service_name: str) -> list[dict[str, Any]]:
    ports = service.get("ports", [])
    if not isinstance(ports, list):
        raise ValueError(f"{service_name}.ports must be a list")
    if any(not isinstance(port, dict) for port in ports):
        raise ValueError(f"{service_name}.ports must use rendered object entries")
    return ports


def _check_port(
    findings: list[Finding],
    service: dict[str, Any],
    service_name: str,
    host_key: str,
    published_key: str,
    target: int,
    env: dict[str, str],
    *,
    expected_count: int,
) -> None:
    expected_host = _host_value(env, host_key)
    expected_published = _int_value(env, published_key, target)
    ports = _ports(service, service_name)
    matches: list[bool] = []
    for port in ports:
        try:
            rendered_host = _normalize_ip(str(port.get("host_ip", "")), key=host_key)
            matches.append(
                int(port.get("target", 0)) == target
                and int(str(port.get("published", 0))) == expected_published
                and rendered_host == expected_host
                and str(port.get("protocol", "tcp")) == "tcp"
                and str(port.get("mode", "ingress")) == "ingress"
            )
        except (TypeError, ValueError):
            matches.append(False)
    if matches.count(True) != 1 or len(ports) != expected_count:
        findings.append(
            Finding(
                f"{service_name} must publish only "
                f"{expected_host}:{expected_published}->{target}/tcp"
            )
        )


def _service_environment(service: dict[str, Any], name: str) -> dict[str, Any]:
    environment = service.get("environment", {})
    if not isinstance(environment, dict):
        raise ValueError(f"{name}.environment must be an object")
    return environment


def _same_path(left: str, right: Path) -> bool:
    try:
        return Path(left).resolve() == right.resolve()
    except OSError:
        return False


def _validate_acl_path(path: Path) -> list[Finding]:
    """Reject replaceable/non-regular ACL inputs before comparing their contents."""
    findings: list[Finding] = []
    try:
        if path.is_symlink():
            return [Finding(f"health ACL must not be a symbolic link: {path}", blocked=True)]
        stat_result = path.stat()
    except OSError:
        return [Finding(f"health ACL is missing or unreadable: {path}", blocked=True)]
    if not path.is_file():
        findings.append(Finding(f"health ACL must be a regular file: {path}", blocked=True))
    # Windows ACLs are enforced by NTFS and are not represented by mode bits.
    # On POSIX, group/other write permission allows a post-validation policy swap.
    elif sys.platform != "win32" and stat_result.st_mode & 0o022:
        findings.append(Finding("health ACL must not be group- or world-writable", blocked=True))
    return findings


def validate_model(  # noqa: PLR0912, PLR0915
    model: dict[str, Any], env: dict[str, str], acl_path: Path | None = None
) -> list[Finding]:
    findings: list[Finding] = []
    services = model.get("services")
    if not isinstance(services, dict):
        return [Finding("rendered Compose model has no services object")]
    names = set(services)
    if names != EXPECTED_SERVICES:
        findings.append(
            Finding(
                "rendered Compose service set must be exactly "
                f"{sorted(EXPECTED_SERVICES)}; got {sorted(names)}"
            )
        )
    networks = model.get("networks", {})
    if networks is not None and not isinstance(networks, dict):
        findings.append(Finding("rendered Compose networks must be an object"))
        networks = {}
    if "default" not in networks:
        findings.append(
            Finding("rendered Compose must retain the controlled default internal network")
        )
    for network_name, network in networks.items():
        if not isinstance(network, dict):
            findings.append(Finding(f"network {network_name} must be an object"))
            continue
        if network.get("external") is True or network.get("driver") in {
            "host",
            "macvlan",
            "ipvlan",
        }:
            findings.append(
                Finding(f"network {network_name} uses an unapproved external/direct driver")
            )
    for name, raw_service in services.items():
        if not isinstance(raw_service, dict):
            findings.append(Finding(f"service {name} must be an object"))
            continue
        if raw_service.get("network_mode") not in (None, ""):
            findings.append(Finding(f"{name} must not set network_mode"))
        if raw_service.get("expose") not in (None, []):
            findings.append(Finding(f"{name} must not declare expose"))
        if name not in PUBLISHED_SERVICES and _ports(raw_service, name):
            findings.append(Finding(f"{name} must not publish host ports"))
        attached = raw_service.get("networks", {})
        if isinstance(attached, list | dict):
            attached_names = set(attached)
        elif attached is None:
            attached_names = set()
        else:
            findings.append(Finding(f"{name}.networks must be a list or object"))
            attached_names = set()
        unknown_networks = attached_names - set(networks)
        if unknown_networks:
            findings.append(
                Finding(f"{name} attaches unknown networks: {sorted(unknown_networks)}")
            )
    gw = services.get("gw")
    if isinstance(gw, dict):
        _check_port(
            findings,
            gw,
            "gw device",
            "GW_DEVICE_BIND_HOST",
            "GW_DEVICE_BIND_PORT",
            5020,
            env,
            expected_count=2,
        )
        _check_port(
            findings,
            gw,
            "gw health",
            "GW_HEALTH_BIND_HOST",
            "GW_HEALTH_BIND_PORT",
            9090,
            env,
            expected_count=2,
        )
        gw_env = _service_environment(gw, "gw")
        for host_key in ("GW_LISTEN_HOST", "GW_HEALTH_HOST"):
            raw_host = str(gw_env.get(host_key, ""))
            try:
                listener = _normalize_ip(raw_host, key=host_key, allow_wildcard=True)
            except ValueError as error:
                findings.append(Finding(str(error)))
            else:
                if listener not in WILDCARD_LISTENERS:
                    findings.append(
                        Finding(f"{host_key} must listen on a Docker-reachable wildcard address")
                    )
        for port_key, expected in (("GW_LISTEN_PORT", 5020), ("GW_HEALTH_PORT", 9090)):
            try:
                actual = int(str(gw_env.get(port_key, 0)))
            except ValueError:
                actual = 0
            if actual != expected:
                findings.append(Finding(f"{port_key} must match container target {expected}"))
        rendered_allowed = str(gw_env.get("GW_HEALTH_ALLOWED_CIDRS", ""))
        rendered_networks: frozenset[ipaddress.IPv4Network | ipaddress.IPv6Network] | None
        env_networks: frozenset[ipaddress.IPv4Network | ipaddress.IPv6Network] | None
        try:
            rendered_networks = _parse_networks(
                rendered_allowed, "rendered GW_HEALTH_ALLOWED_CIDRS"
            )
            env_networks = _parse_networks(
                env.get("GW_HEALTH_ALLOWED_CIDRS", ""), "GW_HEALTH_ALLOWED_CIDRS"
            )
        except ValueError:
            rendered_networks = None
            env_networks = None
        if rendered_networks != env_networks:
            findings.append(
                Finding("rendered GW_HEALTH_ALLOWED_CIDRS does not match the approved env value")
            )
    web = services.get("web")
    if isinstance(web, dict):
        _check_port(
            findings,
            web,
            "web",
            "WEB_BIND_HOST",
            "WEB_BIND_PORT",
            80,
            env,
            expected_count=1,
        )
        volumes = web.get("volumes", [])
        if not isinstance(volumes, list):
            volumes = []
        acl_mounts = [
            volume
            for volume in volumes
            if isinstance(volume, dict)
            and volume.get("target") == "/etc/nginx/site-health-acl.conf"
        ]
        if len(acl_mounts) != 1:
            findings.append(Finding("web must mount exactly one site health ACL"))
        else:
            mount = acl_mounts[0]
            if mount.get("type") != "bind" or mount.get("read_only") is not True:
                findings.append(Finding("web health ACL must be a read-only bind mount"))
            source = mount.get("source")
            if acl_path is not None and (
                not isinstance(source, str) or not _same_path(source, acl_path)
            ):
                findings.append(Finding("validated ACL file is not the source mounted by web"))
    return findings


def _strip_nginx_comments(text: str) -> str:
    return "\n".join(line.split("#", 1)[0] for line in text.splitlines())


def _location_block(text: str, selector: str) -> str | None:
    match = re.search(rf"location\s+{selector}\s*\{{(?P<body>[^{{}}]*)\}}", text, re.DOTALL)
    return match.group("body") if match else None


def validate_nginx(text: str) -> list[Finding]:
    findings: list[Finding] = []
    clean = _strip_nginx_comments(text)
    if len(re.findall(r"\bserver\s*\{", clean)) != 1:
        findings.append(Finding("nginx config must define exactly one active server block"))
    format_match = re.search(r"log_format\s+ruisheng_safe\s+(?P<body>.*?);", clean, re.DOTALL)
    if format_match is None or "$uri" not in format_match.group("body"):
        findings.append(Finding("nginx safe access log format must use $uri"))
    for variable in BANNED_NGINX_LOG_VARIABLES:
        if re.search(re.escape(variable) + r"(?![A-Za-z_])", clean):
            findings.append(
                Finding(f"nginx logging must not reference sensitive variable {variable}")
            )
    access_logs = re.findall(r"access_log\s+([^;]+);", clean)
    if access_logs != ["/dev/stdout ruisheng_safe"]:
        findings.append(Finding("nginx must use only /dev/stdout ruisheng_safe access logging"))
    required_locations = {
        r"=\s+/api/health": "http://api:8000/api/health",
        r"\^~\s+/api/health/": "http://api:8000/api/health/",
    }
    for selector, upstream in required_locations.items():
        block = _location_block(clean, selector)
        if block is None:
            findings.append(Finding(f"nginx is missing protected health location {selector}"))
        elif (
            "include /etc/nginx/site-health-acl.conf;" not in block
            or f"proxy_pass {upstream};" not in block
        ):
            findings.append(
                Finding(f"nginx health location {selector} must apply the site ACL before proxying")
            )
    api_block = _location_block(clean, r"/api/")
    if api_block is None or "proxy_pass http://api:8000/api/;" not in api_block:
        findings.append(Finding("nginx must retain the ordinary /api/ application proxy"))
    ws_block = _location_block(clean, r"/ws")
    server_match = re.search(r"\bserver\s*\{(?P<body>.*)\}\s*$", clean, re.DOTALL)
    server_body = server_match.group("body") if server_match else ""
    first_location = server_body.find("location")
    server_error_log = re.search(r"\berror_log\s+/dev/null\s+crit\s*;", server_body)
    error_at_server_scope = server_error_log is not None and (
        first_location < 0 or server_error_log.start() < first_location
    )
    if ws_block is None or not error_at_server_scope:
        findings.append(
            Finding("nginx config must suppress request-bearing error logs at server scope")
        )
    return findings


def validate_acl(
    text: str,
) -> tuple[frozenset[ipaddress.IPv4Network | ipaddress.IPv6Network], list[Finding]]:
    findings: list[Finding] = []
    allowed: set[ipaddress.IPv4Network | ipaddress.IPv6Network] = set()
    lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if not lines or lines[-1] != "deny all;":
        findings.append(Finding("health ACL must keep deny all; as its final rule"))
    if "UNRESOLVED" in text or "CHANGE_ME" in text:
        findings.append(
            Finding("health ACL contains unresolved or placeholder values", blocked=True)
        )
    for index, line in enumerate(lines):
        match = re.fullmatch(r"(allow|deny)\s+([^;]+);", line)
        if match is None:
            findings.append(Finding(f"unsupported health ACL directive: {line}"))
            continue
        directive, subject = match.groups()
        subject = subject.strip()
        if directive == "deny":
            if index != len(lines) - 1 or subject != "all":
                findings.append(Finding("health ACL may only use deny all as its final deny rule"))
            continue
        if subject == "all":
            findings.append(Finding("health ACL must not allow all"))
            continue
        try:
            network = ipaddress.ip_network(subject, strict=False)
        except ValueError:
            findings.append(Finding(f"health ACL subject is not an IP/CIDR: {subject!r}"))
            continue
        if network.prefixlen == 0:
            findings.append(Finding(f"health ACL must not allow a default route: {subject}"))
            continue
        allowed.add(network)
    if not allowed:
        findings.append(
            Finding("health ACL must allow at least one approved monitoring CIDR", blocked=True)
        )
    return frozenset(allowed), findings


def _section(text: str, title: str) -> str | None:
    matches = list(SECTION_RE.finditer(text))
    for index, match in enumerate(matches):
        if match.group("title").strip() == title:
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            return text[match.end() : end]
    return None


def _table(section: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in section.splitlines():
        match = TABLE_ROW_RE.fullmatch(line.strip())
        if match is None:
            continue
        key = match.group("key").strip()
        if key not in {"字段", "---"}:
            values[key] = match.group("value").strip()
    return values


def _unresolved(value: str | None) -> bool:
    return value is None or not value.strip() or "UNRESOLVED" in value


def _parse_networks(
    value: str, key: str
) -> frozenset[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    result: set[ipaddress.IPv4Network | ipaddress.IPv6Network] = set()
    for item in re.split(r"[,，、;；\s]+", value.strip()):
        if not item:
            continue
        try:
            network = ipaddress.ip_network(item, strict=False)
        except ValueError as error:
            raise ValueError(f"profile {key} contains invalid CIDR {item!r}") from error
        if network.prefixlen == 0:
            raise ValueError(f"profile {key} must not approve a default route")
        result.add(network)
    if not result:
        raise ValueError(f"profile {key} must contain at least one CIDR")
    return frozenset(result)


def _parse_endpoint(value: str, key: str) -> tuple[str, int]:
    match = re.fullmatch(r"(?:\[(?P<ipv6>[^]]+)\]|(?P<ipv4>[^:]+)):(?P<port>\d+)", value.strip())
    if match is None:
        raise ValueError(f"profile {key} must use IP:port or [IPv6]:port")
    host = _normalize_ip(match.group("ipv6") or match.group("ipv4"), key=key)
    port = int(match.group("port"))
    if not 1 <= port <= MAX_PORT:
        raise ValueError(f"profile {key} port must be between 1 and {MAX_PORT}")
    return host, port


def _parse_transport_evidence(value: str) -> dict[str, str] | None:
    """Parse explicit key=value evidence; prose/checkboxes are deliberately rejected."""
    fields: dict[str, str] = {}
    chunks = [chunk.strip() for chunk in re.split(r"[;\n]+", value.casefold()) if chunk.strip()]
    if not chunks:
        return None
    for chunk in chunks:
        key, separator, raw_value = chunk.partition("=")
        key = key.strip()
        raw_value = raw_value.strip()
        if separator != "=" or EVIDENCE_KEY_RE.fullmatch(key) is None or not raw_value:
            return None
        if key in fields:
            return None
        fields[key] = raw_value
    return fields


def _evidence_value(fields: dict[str, str], key: str) -> str | None:
    value = fields.get(key, "").strip()
    if not value or value in EVIDENCE_PLACEHOLDERS:
        return None
    return value


def _validate_non_loopback_transport(transport: str, evidence: str) -> str | None:  # noqa: PLR0911
    fields = _parse_transport_evidence(evidence)
    if fields is None:
        label = "HTTPS/WSS" if transport == "HTTPS_WSS" else transport
        return (
            f"{label} evidence must use explicit key=value fields; "
            "free-form prose is not approval evidence"
        )
    required = {"firewall", "direct_http", "direct_ws"}
    if transport == "TRUSTED_HTTP":
        required.add("isolation")
        if any(_evidence_value(fields, key) is None for key in required):
            return "TRUSTED_HTTP requires isolation, firewall, direct_http and direct_ws evidence"
        if fields["direct_http"] not in {"trusted-only", "isolated-only", "allow-listed"}:
            return (
                "TRUSTED_HTTP direct_http evidence must explicitly restrict to trusted-only access"
            )
        if fields["direct_ws"] not in {"trusted-only", "isolated-only", "allow-listed"}:
            return "TRUSTED_HTTP direct_ws evidence must explicitly restrict to trusted-only access"
        return None
    required.update({"termination", "certificate", "domain"})
    if any(_evidence_value(fields, key) is None for key in required):
        return (
            "HTTPS_WSS requires termination, certificate, domain, firewall, "
            "direct_http and direct_ws evidence"
        )
    if transport == "HTTPS_WSS":
        if not TERMINATION_REFERENCE_RE.fullmatch(fields["termination"]):
            return "HTTPS_WSS termination must be a named endpoint, optionally with a port"
        if not CERTIFICATE_REFERENCE_RE.fullmatch(fields["certificate"]):
            return (
                "HTTPS_WSS certificate must be a vault/secret/KMS reference or SHA-256 fingerprint"
            )
        if not DOMAIN_RE.fullmatch(fields["domain"]):
            return "HTTPS_WSS domain must be a fully qualified DNS name"
        if not FIREWALL_REFERENCE_RE.fullmatch(fields["firewall"]):
            return "HTTPS_WSS firewall must be a named rule reference"
    if fields["direct_http"] != "deny" or fields["direct_ws"] != "deny":
        return "HTTPS_WSS direct_http and direct_ws evidence must both be deny"
    return None


def validate_profile(text: str | None) -> tuple[ProfileDecision | None, list[Finding]]:  # noqa: PLR0911
    if text is None:
        return None, [Finding("approved site-acceptance-profile.md is required", blocked=True)]
    approval_section = _section(text, "审批")
    network_section = _section(text, "网络与安全")
    if approval_section is None or network_section is None:
        return None, [
            Finding("site profile requires anchored 审批 and 网络与安全 sections", blocked=True)
        ]
    approval = _table(approval_section)
    network = _table(network_section)
    all_fields = approval | network
    missing = [
        key
        for key in (*APPROVAL_FIELDS, *NETWORK_FIELDS)
        if _unresolved(all_fields.get(key))
        or (
            key in APPROVAL_FIELDS
            and all_fields.get(key, "").strip().casefold() in APPROVAL_PLACEHOLDERS
            and not (
                key == "安全/合规负责人"
                and all_fields.get(key, "").strip().casefold() in {"n/a", "na", "批准 n/a"}
            )
        )
    ]
    if missing:
        return None, [
            Finding(f"site profile has unresolved required fields: {missing}", blocked=True)
        ]
    try:
        user_networks = _parse_networks(network["用户网段（CIDR，逗号分隔）"], "user CIDR")
        device_networks = _parse_networks(network["设备网段（CIDR，逗号分隔）"], "device CIDR")
        monitoring = _parse_networks(network["运维/监控网段（CIDR，逗号分隔）"], "monitoring CIDR")
        _parse_networks(network["未批准探测源（CIDR，逗号分隔）"], "unapproved CIDR")
        external_value = network.get("外部服务网段（CIDR，逗号分隔或批准 N/A）", "")
        external = (
            None
            if external_value.strip() in {"N/A", "NA", "批准 N/A"}
            else _parse_networks(external_value, "external CIDR")
        )
        web_bind = _parse_endpoint(network["Web 宿主绑定（IP:端口）"], "Web bind")
        device_bind = _parse_endpoint(network["GW 设备宿主绑定（IP:端口）"], "GW device bind")
        health_bind = _parse_endpoint(network["GW 管理宿主绑定（IP:端口）"], "GW health bind")
    except (KeyError, ValueError) as error:
        return None, [Finding(str(error), blocked=True)]
    transport = network["Web 传输模式（LOOPBACK_ONLY / TRUSTED_HTTP / HTTPS_WSS）"]
    if transport not in {"LOOPBACK_ONLY", "TRUSTED_HTTP", "HTTPS_WSS"}:
        return None, [Finding("site profile Web transport must use an approved enum", blocked=True)]
    if not ipaddress.ip_address(web_bind[0]).is_loopback and transport == "LOOPBACK_ONLY":
        return None, [
            Finding(
                "non-loopback Web binding requires TRUSTED_HTTP or HTTPS_WSS approval", blocked=True
            )
        ]
    tls_evidence = network["TLS 终止、证书及 Web 直连旁路防护"]
    if not ipaddress.ip_address(web_bind[0]).is_loopback:
        evidence_error = _validate_non_loopback_transport(transport, tls_evidence)
        if evidence_error is not None:
            return None, [Finding(evidence_error, blocked=True)]
    return ProfileDecision(
        user_networks,
        device_networks,
        monitoring,
        external,
        web_bind,
        device_bind,
        health_bind,
        transport,
    ), []


def _expected_bindings(
    env: dict[str, str],
) -> tuple[tuple[str, int], tuple[str, int], tuple[str, int]]:
    return (
        (_host_value(env, "WEB_BIND_HOST"), _int_value(env, "WEB_BIND_PORT", 80)),
        (_host_value(env, "GW_DEVICE_BIND_HOST"), _int_value(env, "GW_DEVICE_BIND_PORT", 5020)),
        (_host_value(env, "GW_HEALTH_BIND_HOST"), _int_value(env, "GW_HEALTH_BIND_PORT", 9090)),
    )


def validate(
    model: dict[str, Any],
    env: dict[str, str],
    nginx_text: str,
    acl_text: str,
    profile_text: str | None,
    acl_path: Path | None = None,
) -> list[Finding]:
    findings = validate_model(model, env, acl_path)
    if acl_path is not None:
        findings.extend(_validate_acl_path(acl_path))
        try:
            current_acl = acl_path.read_text(encoding="utf-8-sig")
        except OSError as error:
            findings.append(
                Finding(f"health ACL changed or became unreadable: {error}", blocked=True)
            )
        else:
            if current_acl != acl_text:
                findings.append(
                    Finding(
                        "health ACL changed during validation; rerun the boundary check",
                        blocked=True,
                    )
                )
    findings.extend(validate_nginx(nginx_text))
    allowed, acl_findings = validate_acl(acl_text)
    findings.extend(acl_findings)
    profile, profile_findings = validate_profile(profile_text)
    findings.extend(profile_findings)
    if profile is not None:
        if allowed != profile.monitoring_networks:
            findings.append(
                Finding("health ACL allow CIDRs must exactly match approved monitoring CIDRs")
            )
        try:
            expected = _expected_bindings(env)
        except ValueError as error:
            findings.append(Finding(str(error)))
        else:
            approved = (profile.web_bind, profile.gw_device_bind, profile.gw_health_bind)
            if expected != approved:
                findings.append(
                    Finding("environment bindings do not match the approved site profile")
                )
        try:
            health_allowed = _parse_networks(
                env.get("GW_HEALTH_ALLOWED_CIDRS", ""), "GW_HEALTH_ALLOWED_CIDRS"
            )
        except ValueError as error:
            findings.append(Finding(str(error)))
        else:
            if health_allowed != profile.monitoring_networks:
                findings.append(
                    Finding("GW health source ACL must exactly match approved monitoring CIDRs")
                )
    return findings


def _render_compose(compose_files: list[Path], env_file: Path) -> dict[str, Any]:
    command = ["docker", "compose", "--env-file", str(env_file)]
    for compose in compose_files:
        command.extend(("-f", str(compose)))
    command.extend(("config", "--format", "json"))
    try:
        result = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        raise RuntimeError(f"cannot render Compose: {error}") from error
    value = json.loads(result.stdout)
    if not isinstance(value, dict):
        raise RuntimeError("Compose rendering did not return an object")
    return value


def _read_required(path: Path, label: str) -> tuple[str | None, Finding | None]:
    try:
        if path.is_symlink():
            return None, Finding(f"{label} must not be a symbolic link: {path}", blocked=True)
        return path.read_text(encoding="utf-8-sig"), None
    except OSError:
        return None, Finding(f"{label} is missing or unreadable: {path}", blocked=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compose", type=Path, required=True, action="append")
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--profile", type=Path)
    parser.add_argument("--nginx-config", type=Path, required=True)
    parser.add_argument("--acl-file", type=Path, required=True)
    args = parser.parse_args(argv)
    blocked: list[Finding] = []
    acl_text, acl_finding = _read_required(args.acl_file, "site health ACL")
    profile_text: str | None = None
    if acl_finding is not None:
        blocked.append(acl_finding)
    if args.profile is None:
        blocked.append(Finding("approved site-acceptance-profile.md is required", blocked=True))
    else:
        profile_text, profile_finding = _read_required(args.profile, "site profile")
        if profile_finding is not None:
            blocked.append(profile_finding)
    if blocked:
        for finding in blocked:
            print(f"[network] BLOCKED: {finding.message}", file=sys.stderr)
        return 2
    try:
        env = parse_env(args.env_file)
        model = _render_compose(args.compose, args.env_file)
        if args.nginx_config.is_symlink():
            raise RuntimeError(f"nginx config must not be a symbolic link: {args.nginx_config}")
        nginx_text = args.nginx_config.read_text(encoding="utf-8-sig")
        findings = validate(model, env, nginx_text, acl_text or "", profile_text, args.acl_file)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError, RuntimeError) as error:
        print(f"[network] FAIL: {error}", file=sys.stderr)
        return 1
    if findings:
        for finding in findings:
            prefix = "BLOCKED" if finding.blocked else "FAIL"
            print(f"[network] {prefix}: {finding.message}", file=sys.stderr)
        return 2 if any(finding.blocked for finding in findings) else 1
    print("[network] PASS: rendered boundary, ACL, profile and log policy are valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
