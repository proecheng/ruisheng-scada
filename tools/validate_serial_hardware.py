"""Validate an external Windows/WSL RS485 site configuration and Compose override."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

HEX_ID_RE = re.compile(r"^[0-9A-Fa-f]{4}$")
SERIAL_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
DEVICE_NUMBER_RE = re.compile(r"^[A-Za-z0-9._-]{1,50}$")
LINUX_DEVICE_RE = re.compile(r"^/dev/ruisheng-[A-Za-z0-9._-]+$")
TTY_DEVICE_RE = re.compile(r"^/dev/ttyUSB[0-9]+$")
REFERENCE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/#@+-]{2,255}$")
PLACEHOLDER_RE = re.compile(r"(?:unresolved|change[_-]?me|pending|tbd)", re.IGNORECASE)
ALLOWED_BAUD_RATES = {1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200}
MIN_RETRY_SECONDS = 2
MAX_RETRY_SECONDS = 300
MIN_MODBUS_ADDRESS = 1
MAX_MODBUS_ADDRESS = 247
EXPECTED_ROOT_KEYS = {"schema_version", "adapter", "device", "serial", "approval"}
EXPECTED_ADAPTER_KEYS = {
    "vendor_id",
    "product_id",
    "serial_number",
    "stable_path",
    "wsl_distribution",
    "retry_seconds",
}
EXPECTED_DEVICE_KEYS = {"dev_number", "brand", "model", "protocol", "point_map_reference"}
EXPECTED_SERIAL_KEYS = {"baud_rate", "data_bits", "parity", "stop_bits", "modbus_addr"}
EXPECTED_APPROVAL_KEYS = {"polling_approved", "approved_by", "approved_at"}
SERIAL_ENV_KEYS = {"GW_SERIAL_DEVICE_PATH", "GW_SERIAL_BAUD_RATE"}
ATTESTATION_KEYS = {
    "schema_version",
    "result",
    "timestamp",
    "vendor_id",
    "product_id",
    "serial_number",
    "stable_path",
    "device_path",
    "bus_id",
}
MAX_ATTESTATION_AGE_SECONDS = 300


class SerialHardwareError(ValueError):
    """Raised when serial hardware input violates the deployment contract."""


@dataclass(frozen=True)
class AdapterConfig:
    vendor_id: str
    product_id: str
    serial_number: str
    stable_path: str
    wsl_distribution: str
    retry_seconds: int


@dataclass(frozen=True)
class PollingConfig:
    dev_number: str
    brand: str
    model: str
    protocol: str
    point_map_reference: str
    baud_rate: int
    data_bits: int
    parity: str
    stop_bits: int
    modbus_addr: int
    approved_by: str
    approved_at: str


@dataclass(frozen=True)
class SiteConfig:
    adapter: AdapterConfig
    polling: PollingConfig | None


def _object(value: object, name: str, expected_keys: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SerialHardwareError(f"{name} must be an object")
    keys = set(value)
    if keys != expected_keys:
        missing = sorted(expected_keys - keys)
        extra = sorted(keys - expected_keys)
        raise SerialHardwareError(f"{name} keys mismatch: missing={missing}, extra={extra}")
    return value


def _text(value: object, name: str, pattern: re.Pattern[str] | None = None) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SerialHardwareError(f"{name} must be a non-empty string")
    result = value.strip()
    if PLACEHOLDER_RE.search(result) is not None:
        raise SerialHardwareError(f"{name} is unresolved")
    if pattern is not None and pattern.fullmatch(result) is None:
        raise SerialHardwareError(f"{name} has an invalid format")
    return result


def _approved_text(value: object, name: str, pattern: re.Pattern[str] | None = None) -> str:
    return _text(value, name, pattern)


def _integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SerialHardwareError(f"{name} must be an integer")
    return value


def _load_adapter(raw: object) -> AdapterConfig:
    adapter = _object(raw, "adapter", EXPECTED_ADAPTER_KEYS)
    vendor_id = _text(adapter["vendor_id"], "adapter.vendor_id", HEX_ID_RE).upper()
    product_id = _text(adapter["product_id"], "adapter.product_id", HEX_ID_RE).upper()
    serial_number = _text(adapter["serial_number"], "adapter.serial_number", SERIAL_RE)
    stable_path = _text(adapter["stable_path"], "adapter.stable_path", LINUX_DEVICE_RE)
    wsl_distribution = _text(adapter["wsl_distribution"], "adapter.wsl_distribution", SERIAL_RE)
    if wsl_distribution != "docker-desktop":
        raise SerialHardwareError("adapter.wsl_distribution must be docker-desktop")
    retry_seconds = _integer(adapter["retry_seconds"], "adapter.retry_seconds")
    if not MIN_RETRY_SECONDS <= retry_seconds <= MAX_RETRY_SECONDS:
        raise SerialHardwareError("adapter.retry_seconds must be between 2 and 300")
    return AdapterConfig(
        vendor_id=vendor_id,
        product_id=product_id,
        serial_number=serial_number,
        stable_path=stable_path,
        wsl_distribution=wsl_distribution,
        retry_seconds=retry_seconds,
    )


def _load_polling(
    device_raw: object, serial_raw: object, approval_raw: object
) -> PollingConfig | None:
    device = _object(device_raw, "device", EXPECTED_DEVICE_KEYS)
    serial = _object(serial_raw, "serial", EXPECTED_SERIAL_KEYS)
    approval = _object(approval_raw, "approval", EXPECTED_APPROVAL_KEYS)
    polling_approved = approval["polling_approved"]
    if not isinstance(polling_approved, bool):
        raise SerialHardwareError("approval.polling_approved must be boolean")
    if not polling_approved:
        return None

    dev_number = _approved_text(device["dev_number"], "device.dev_number", DEVICE_NUMBER_RE)
    brand = _approved_text(device["brand"], "device.brand", REFERENCE_RE)
    model = _approved_text(device["model"], "device.model", REFERENCE_RE)
    protocol = _approved_text(device["protocol"], "device.protocol", REFERENCE_RE).upper()
    if protocol != "MODBUS_RTU":
        raise SerialHardwareError("device.protocol must be MODBUS_RTU")
    point_map_reference = _approved_text(
        device["point_map_reference"], "device.point_map_reference", REFERENCE_RE
    )
    baud_rate = _integer(serial["baud_rate"], "serial.baud_rate")
    if baud_rate not in ALLOWED_BAUD_RATES:
        raise SerialHardwareError("serial.baud_rate is not supported")
    data_bits = _integer(serial["data_bits"], "serial.data_bits")
    parity = _approved_text(serial["parity"], "serial.parity").upper()
    stop_bits = _integer(serial["stop_bits"], "serial.stop_bits")
    if (data_bits, parity, stop_bits) != (8, "N", 1):
        raise SerialHardwareError("current gateway supports only 8N1")
    modbus_addr = _integer(serial["modbus_addr"], "serial.modbus_addr")
    if not MIN_MODBUS_ADDRESS <= modbus_addr <= MAX_MODBUS_ADDRESS:
        raise SerialHardwareError("serial.modbus_addr must be between 1 and 247")
    approved_by = _approved_text(approval["approved_by"], "approval.approved_by")
    approved_at = _approved_text(approval["approved_at"], "approval.approved_at")
    try:
        approved_time = datetime.fromisoformat(approved_at.replace("Z", "+00:00"))
    except ValueError as error:
        raise SerialHardwareError("approval.approved_at must be ISO-8601") from error
    if approved_time.tzinfo is None:
        raise SerialHardwareError("approval.approved_at must include a timezone")
    return PollingConfig(
        dev_number=dev_number,
        brand=brand,
        model=model,
        protocol=protocol,
        point_map_reference=point_map_reference,
        baud_rate=baud_rate,
        data_bits=data_bits,
        parity=parity,
        stop_bits=stop_bits,
        modbus_addr=modbus_addr,
        approved_by=approved_by,
        approved_at=approved_at,
    )


def load_site_config(path: Path) -> SiteConfig:
    try:
        raw = json.loads(
            path.read_text(encoding="utf-8-sig"), object_pairs_hook=_reject_duplicate_keys
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise SerialHardwareError(f"cannot read serial hardware config: {error}") from error
    root = _object(raw, "root", EXPECTED_ROOT_KEYS)
    if type(root["schema_version"]) is not int or root["schema_version"] != 1:
        raise SerialHardwareError("unsupported schema_version")
    return SiteConfig(
        adapter=_load_adapter(root["adapter"]),
        polling=_load_polling(root["device"], root["serial"], root["approval"]),
    )


def parse_env_files(paths: list[Path]) -> dict[str, str]:
    values: dict[str, str] = {}
    for path in paths:
        try:
            lines = path.read_text(encoding="utf-8-sig").splitlines()
        except (OSError, UnicodeError) as error:
            raise SerialHardwareError(f"cannot read environment file {path}: {error}") from error
        for line_number, raw in enumerate(lines, 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            key, separator, value = line.partition("=")
            if not separator or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
                raise SerialHardwareError(f"{path}:{line_number}: invalid environment entry")
            if key in values and key in SERIAL_ENV_KEYS:
                raise SerialHardwareError(f"{path}:{line_number}: duplicate environment key {key}")
            values[key] = value
    return values


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SerialHardwareError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def validate_serial_env_file(values: dict[str, str], site: SiteConfig) -> None:
    if set(values) != SERIAL_ENV_KEYS:
        extra = sorted(set(values) - SERIAL_ENV_KEYS)
        missing = sorted(SERIAL_ENV_KEYS - set(values))
        raise SerialHardwareError(
            f"serial environment keys mismatch: missing={missing}, extra={extra}"
        )
    validate_serial_env(values, site)


def _load_json_object(path: Path, expected_keys: set[str], name: str) -> dict[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8-sig"), object_pairs_hook=_reject_duplicate_keys
        )
    except (OSError, UnicodeError, json.JSONDecodeError, SerialHardwareError) as error:
        raise SerialHardwareError(f"cannot read {name}: {error}") from error
    if not isinstance(value, dict) or set(value) != expected_keys:
        raise SerialHardwareError(f"{name} keys mismatch")
    return value


def validate_hardware_attestation(path: Path, site: SiteConfig) -> None:
    value = _load_json_object(path, ATTESTATION_KEYS, "hardware attestation")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise SerialHardwareError("hardware attestation schema is unsupported")
    if value["result"] != "ready":
        raise SerialHardwareError("hardware attestation is not ready")
    for key, expected in (
        ("vendor_id", site.adapter.vendor_id),
        ("product_id", site.adapter.product_id),
        ("serial_number", site.adapter.serial_number),
        ("stable_path", site.adapter.stable_path),
    ):
        if str(value[key]).upper() != expected.upper():
            raise SerialHardwareError(f"hardware attestation {key} does not match approval")
    if (
        not isinstance(value["device_path"], str)
        or TTY_DEVICE_RE.fullmatch(value["device_path"]) is None
    ):
        raise SerialHardwareError("hardware attestation device_path is invalid")
    if re.fullmatch(r"[0-9]+-[0-9]+", str(value["bus_id"])) is None:
        raise SerialHardwareError("hardware attestation bus_id is invalid")
    try:
        observed = datetime.fromisoformat(str(value["timestamp"]).replace("Z", "+00:00"))
    except ValueError as error:
        raise SerialHardwareError("hardware attestation timestamp must be ISO-8601") from error
    if observed.tzinfo is None:
        raise SerialHardwareError("hardware attestation timestamp must include a timezone")
    age = (datetime.now(observed.tzinfo) - observed).total_seconds()
    if age < 0 or age > MAX_ATTESTATION_AGE_SECONDS:
        raise SerialHardwareError("hardware attestation is stale")


def validate_database_device(site: SiteConfig, postgres_user: str) -> None:  # noqa: PLR0912
    if site.polling is None:
        raise SerialHardwareError("polling approval is required before device record validation")
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]{0,62}", postgres_user) is None:
        raise SerialHardwareError("POSTGRES_USER has an invalid format")
    query = (
        "SELECT json_build_object("
        "'dev_number', dev_number, 'transport_type', transport_type, "
        "'serial_port', serial_port, 'modbus_addr', modbus_addr, "
        "'baud_rate', baud_rate, 'is_enabled', is_enabled)::text "
        "FROM devices WHERE deleted_at IS NULL AND dev_number = "
        f"'{site.polling.dev_number}';"
    )
    try:
        result = subprocess.run(
            [
                "docker",
                "exec",
                "ruisheng-postgres",
                "psql",
                "-X",
                "-A",
                "-t",
                "-v",
                "ON_ERROR_STOP=1",
                "-U",
                postgres_user,
                "-d",
                "ruisheng",
                "-c",
                query,
            ],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )
    except FileNotFoundError as error:
        raise SerialHardwareError("docker command is unavailable") from error
    except subprocess.TimeoutExpired as error:
        raise SerialHardwareError("database device validation timed out") from error
    except subprocess.CalledProcessError as error:
        details = (error.stderr or error.stdout or "no output").strip()
        raise SerialHardwareError(f"database device validation failed: {details}") from error
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if len(lines) != 1:
        raise SerialHardwareError("database must contain exactly one approved device record")
    try:
        value = json.loads(lines[0], object_pairs_hook=_reject_duplicate_keys)
    except (json.JSONDecodeError, SerialHardwareError) as error:
        raise SerialHardwareError("database device record is invalid JSON") from error
    if not isinstance(value, dict):
        raise SerialHardwareError("database device record must be an object")
    if value.get("dev_number") != site.polling.dev_number:
        raise SerialHardwareError("database device dev_number does not match approval")
    if value["transport_type"] != "serial":
        raise SerialHardwareError("database device transport_type must be serial")
    if value["serial_port"] != site.adapter.stable_path:
        raise SerialHardwareError("database device serial_port does not match stable path")
    if value["modbus_addr"] != site.polling.modbus_addr:
        raise SerialHardwareError("database device modbus_addr does not match approval")
    if value.get("baud_rate") != site.polling.baud_rate:
        raise SerialHardwareError("database device baud_rate does not match approval")
    if value.get("is_enabled") is not True:
        raise SerialHardwareError("database device must be enabled")


def render_compose(compose_files: list[Path], env_files: list[Path]) -> dict[str, Any]:
    command = ["docker", "compose"]
    for env_file in env_files:
        command.extend(("--env-file", str(env_file)))
    for compose_file in compose_files:
        command.extend(("-f", str(compose_file)))
    command.extend(("config", "--format", "json"))
    try:
        result = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
        )
    except FileNotFoundError as error:
        raise SerialHardwareError("docker command is unavailable") from error
    except subprocess.TimeoutExpired as error:
        raise SerialHardwareError("docker compose rendering timed out") from error
    except subprocess.CalledProcessError as error:
        details = (error.stderr or error.stdout or "no output").strip()
        raise SerialHardwareError(f"docker compose rendering failed: {details}") from error
    try:
        rendered = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise SerialHardwareError("docker compose returned invalid JSON") from error
    if not isinstance(rendered, dict):
        raise SerialHardwareError("docker compose returned a non-object")
    return rendered


def validate_base_compose(rendered: dict[str, Any]) -> None:
    services = rendered.get("services")
    if not isinstance(services, dict) or "gw" not in services:
        raise SerialHardwareError("base Compose must contain gw")
    for service_name, service in services.items():
        if not isinstance(service, dict):
            raise SerialHardwareError(f"service {service_name} must be an object")
        if service.get("devices"):
            raise SerialHardwareError("signed base/network Compose must not map hardware devices")
    gw_environment = services["gw"].get("environment", {})
    if "GW_SERIAL_PORTS" in gw_environment:
        raise SerialHardwareError("signed base/network Compose must not set GW_SERIAL_PORTS")


def validate_serial_compose(  # noqa: PLR0912
    rendered: dict[str, Any], site: SiteConfig, base_rendered: dict[str, Any] | None = None
) -> None:
    if site.polling is None:
        raise SerialHardwareError("polling approval is required before serial override validation")
    services = rendered.get("services")
    if not isinstance(services, dict) or "gw" not in services:
        raise SerialHardwareError("rendered Compose must contain gw")
    for service_name, service in services.items():
        if not isinstance(service, dict):
            raise SerialHardwareError(f"service {service_name} must be an object")
        if service_name != "gw" and service.get("devices"):
            raise SerialHardwareError(f"only gw may receive hardware devices: {service_name}")

    gw = services["gw"]
    devices = gw.get("devices")
    if not isinstance(devices, list) or len(devices) != 1 or not isinstance(devices[0], dict):
        raise SerialHardwareError("gw must have exactly one structured device mapping")
    device = devices[0]
    expected_path = site.adapter.stable_path
    if device.get("source") != expected_path or device.get("target") != expected_path:
        raise SerialHardwareError("gw device mapping must use the approved stable path")
    if device.get("permissions") != "rw":
        raise SerialHardwareError("gw device mapping permissions must be rw")

    environment = gw.get("environment")
    if not isinstance(environment, dict):
        raise SerialHardwareError("gw environment must be an object")
    raw_ports = environment.get("GW_SERIAL_PORTS")
    if not isinstance(raw_ports, str):
        raise SerialHardwareError("GW_SERIAL_PORTS must be a JSON string")
    try:
        ports = json.loads(raw_ports)
    except json.JSONDecodeError as error:
        raise SerialHardwareError("GW_SERIAL_PORTS is not valid JSON") from error
    expected = [{"port": expected_path, "baud_rate": site.polling.baud_rate}]
    if ports != expected:
        raise SerialHardwareError("GW_SERIAL_PORTS does not match approved site parameters")
    if base_rendered is not None:
        _validate_allowed_compose_delta(base_rendered, rendered)


def _validate_allowed_compose_delta(
    base_rendered: dict[str, Any], full_rendered: dict[str, Any]
) -> None:
    before = deepcopy_json(base_rendered)
    after = deepcopy_json(full_rendered)
    try:
        gw = after["services"]["gw"]
        gw.pop("devices")
        gw["environment"].pop("GW_SERIAL_PORTS")
    except (KeyError, TypeError, AttributeError) as error:
        raise SerialHardwareError("serial override delta is malformed") from error
    if before != after:
        raise SerialHardwareError("serial override changes an unapproved Compose field")


def deepcopy_json(value: Any) -> Any:
    return json.loads(json.dumps(value, sort_keys=True))


def validate_serial_env(values: dict[str, str], site: SiteConfig) -> None:
    if site.polling is None:
        raise SerialHardwareError(
            "polling approval is required before serial environment validation"
        )
    expected = {
        "GW_SERIAL_DEVICE_PATH": site.adapter.stable_path,
        "GW_SERIAL_BAUD_RATE": str(site.polling.baud_rate),
    }
    actual = {key: values.get(key) for key in expected}
    if actual != expected:
        raise SerialHardwareError("serial environment does not match approved site parameters")


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--compose", action="append", type=Path, default=[])
    parser.add_argument("--serial-override", type=Path)
    parser.add_argument("--env-file", action="append", type=Path, default=[])
    parser.add_argument("--serial-env-file", type=Path)
    parser.add_argument("--candidate-root", type=Path)
    parser.add_argument("--hardware-attestation", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        site = load_site_config(args.config)
        if site.polling is None:
            if args.serial_override or args.serial_env_file:
                raise SerialHardwareError(
                    "serial override and serial environment are forbidden without polling approval"
                )
            if args.compose or args.env_file:
                if not args.compose or not args.env_file:
                    raise SerialHardwareError(
                        "unapproved polling Compose inspection requires compose and env files"
                    )
                validate_base_compose(render_compose(args.compose, args.env_file))
            print(
                "[serial-hardware] BLOCKED: polling approval and device protocol parameters are unresolved"
            )
            return 2
        if (
            not args.compose
            or args.serial_override is None
            or not args.env_file
            or args.serial_env_file is None
            or args.candidate_root is None
            or args.hardware_attestation is None
        ):
            raise SerialHardwareError(
                "approved polling requires compose, serial override/env, candidate root, and fresh evidence"
            )
        candidate_root = args.candidate_root.resolve()
        if not candidate_root.is_dir():
            raise SerialHardwareError("candidate root must be an existing directory")
        if any(not _inside(compose, candidate_root) for compose in args.compose):
            raise SerialHardwareError("all candidate Compose files must be inside candidate root")
        for site_path, label in (
            (args.config, "serial config"),
            (args.serial_override, "serial override"),
            (args.serial_env_file, "serial environment"),
            (args.hardware_attestation, "hardware attestation"),
        ):
            if _inside(site_path, candidate_root):
                raise SerialHardwareError(f"{label} must be outside the signed candidate directory")
        base_values = parse_env_files(args.env_file)
        if set(base_values) & SERIAL_ENV_KEYS:
            raise SerialHardwareError("base environment must not define serial environment keys")
        serial_values = parse_env_files([args.serial_env_file])
        validate_serial_env_file(serial_values, site)
        base_rendered = render_compose(args.compose, args.env_file)
        validate_base_compose(base_rendered)
        full_rendered = render_compose(
            [*args.compose, args.serial_override], [*args.env_file, args.serial_env_file]
        )
        validate_serial_compose(full_rendered, site, base_rendered)
        validate_hardware_attestation(args.hardware_attestation, site)
        postgres_user = base_values.get("POSTGRES_USER", "")
        validate_database_device(site, postgres_user)
    except SerialHardwareError as error:
        print(f"[serial-hardware] FAIL: {error}", file=sys.stderr)
        return 1
    print("[serial-hardware] PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
