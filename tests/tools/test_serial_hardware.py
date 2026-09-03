"""Regression tests for the Windows/WSL serial hardware deployment boundary."""

from __future__ import annotations

import base64
import json
import re
import shutil
import subprocess
import sys
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from subprocess import CompletedProcess

import pytest

from tools.validate_serial_hardware import (
    SerialHardwareError,
    load_site_config,
    main,
    parse_env_files,
    validate_base_compose,
    validate_database_device,
    validate_hardware_attestation,
    validate_serial_compose,
    validate_serial_env,
    validate_serial_env_file,
)

ROOT = Path(__file__).parents[2]


def _config(*, approved: bool = True) -> dict[str, object]:
    return {
        "schema_version": 1,
        "adapter": {
            "vendor_id": "0403",
            "product_id": "6001",
            "serial_number": "AI06JYFW",
            "stable_path": "/dev/ruisheng-rs485",
            "wsl_distribution": "docker-desktop",
            "retry_seconds": 5,
        },
        "device": {
            "dev_number": "RS485-001",
            "brand": "ApprovedBrand",
            "model": "ApprovedModel-1",
            "protocol": "MODBUS_RTU",
            "point_map_reference": "manual://approved-map-v1",
        },
        "serial": {
            "baud_rate": 9600,
            "data_bits": 8,
            "parity": "N",
            "stop_bits": 1,
            "modbus_addr": 1,
        },
        "approval": {
            "polling_approved": approved,
            "approved_by": "site-owner",
            "approved_at": "2026-08-24T16:30:00+08:00",
        },
    }


def _write_config(tmp_path: Path, config: dict[str, object]) -> Path:
    path = tmp_path / "serial-hardware.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    return path


def _base_compose() -> dict[str, object]:
    return {
        "services": {
            "postgres": {"environment": {}},
            "redis": {"environment": {}},
            "migrate": {"environment": {}},
            "api": {"environment": {}},
            "gw": {"environment": {"GW_ENV": "prod"}},
            "web": {"environment": {}},
        }
    }


def _serial_compose() -> dict[str, object]:
    compose = _base_compose()
    compose["services"]["gw"].update(  # type: ignore[index,union-attr]
        {
            "devices": [
                {
                    "source": "/dev/ruisheng-rs485",
                    "target": "/dev/ruisheng-rs485",
                    "permissions": "rw",
                }
            ],
            "environment": {
                "GW_ENV": "prod",
                "GW_SERIAL_PORTS": '[{"port":"/dev/ruisheng-rs485","baud_rate":9600}]',
            },
        }
    )
    return compose


def test_unapproved_config_is_valid_for_attachment_but_blocks_polling(tmp_path: Path) -> None:
    config = _config(approved=False)
    config["device"] = {
        "dev_number": "UNRESOLVED",
        "brand": "UNRESOLVED",
        "model": "UNRESOLVED",
        "protocol": "UNRESOLVED",
        "point_map_reference": "UNRESOLVED",
    }
    config["serial"] = {
        "baud_rate": None,
        "data_bits": None,
        "parity": "UNRESOLVED",
        "stop_bits": None,
        "modbus_addr": None,
    }
    config["approval"] = {
        "polling_approved": False,
        "approved_by": "UNRESOLVED",
        "approved_at": "UNRESOLVED",
    }
    path = _write_config(tmp_path, config)

    site = load_site_config(path)

    assert site.adapter.serial_number == "AI06JYFW"
    assert site.polling is None
    assert main(["--config", str(path)]) == 2


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("data_bits", 7, "only 8N1"),
        ("parity", "E", "only 8N1"),
        ("stop_bits", 2, "only 8N1"),
        ("modbus_addr", 0, "between 1 and 247"),
        ("baud_rate", 12345, "not supported"),
    ),
)
def test_approved_config_rejects_unsupported_serial_parameters(
    tmp_path: Path, field: str, value: object, message: str
) -> None:
    config = _config()
    config["serial"][field] = value  # type: ignore[index]

    with pytest.raises(SerialHardwareError, match=message):
        load_site_config(_write_config(tmp_path, config))


def test_config_rejects_busid_as_persistent_identity(tmp_path: Path) -> None:
    config = _config()
    config["adapter"]["bus_id"] = "2-9"  # type: ignore[index]

    with pytest.raises(SerialHardwareError, match="extra=.*bus_id"):
        load_site_config(_write_config(tmp_path, config))


def test_config_requires_unique_non_placeholder_usb_serial(tmp_path: Path) -> None:
    config = _config()
    config["adapter"]["serial_number"] = "CHANGE_ME"  # type: ignore[index]

    with pytest.raises(SerialHardwareError, match="serial_number is unresolved"):
        load_site_config(_write_config(tmp_path, config))


def test_config_rejects_boolean_schema_and_duplicate_json_keys(tmp_path: Path) -> None:
    config = _config()
    config["schema_version"] = True
    with pytest.raises(SerialHardwareError, match="unsupported schema_version"):
        load_site_config(_write_config(tmp_path, config))

    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text(
        json.dumps(_config()).replace(
            '"schema_version": 1', '"schema_version": 1, "schema_version": 2'
        ),
        encoding="utf-8",
    )
    with pytest.raises(SerialHardwareError, match="duplicate JSON key"):
        load_site_config(duplicate)


def test_base_compose_must_remain_hardware_free() -> None:
    compose = _base_compose()
    validate_base_compose(compose)
    compose["services"]["gw"]["devices"] = [  # type: ignore[index]
        {"source": "/dev/ttyUSB0", "target": "/dev/ttyUSB0", "permissions": "rw"}
    ]

    with pytest.raises(SerialHardwareError, match="must not map hardware"):
        validate_base_compose(compose)


def test_serial_compose_matches_approved_stable_path_and_baud(tmp_path: Path) -> None:
    site = load_site_config(_write_config(tmp_path, _config()))

    validate_serial_env(
        {"GW_SERIAL_DEVICE_PATH": "/dev/ruisheng-rs485", "GW_SERIAL_BAUD_RATE": "9600"},
        site,
    )
    validate_serial_compose(_serial_compose(), site)


def test_serial_env_rejects_duplicate_keys(tmp_path: Path) -> None:
    env_a = tmp_path / "a.env"
    env_b = tmp_path / "b.env"
    env_a.write_text("GW_SERIAL_DEVICE_PATH=/dev/ruisheng-rs485\n", encoding="utf-8")
    env_b.write_text("GW_SERIAL_DEVICE_PATH=/dev/ttyUSB0\n", encoding="utf-8")

    with pytest.raises(SerialHardwareError, match="duplicate environment key"):
        parse_env_files([env_a, env_b])


def test_serial_env_file_rejects_release_variable_override(tmp_path: Path) -> None:
    site = load_site_config(_write_config(tmp_path, _config()))

    with pytest.raises(SerialHardwareError, match="extra=.*GW_IMAGE"):
        validate_serial_env_file(
            {
                "GW_SERIAL_DEVICE_PATH": "/dev/ruisheng-rs485",
                "GW_SERIAL_BAUD_RATE": "9600",
                "GW_IMAGE": "attacker/image:latest",
            },
            site,
        )


def test_non_serial_env_keys_follow_compose_override_order(tmp_path: Path) -> None:
    env_a = tmp_path / "a.env"
    env_b = tmp_path / "b.env"
    env_a.write_text("WEB_BIND_PORT=80\n", encoding="utf-8")
    env_b.write_text("WEB_BIND_PORT=8080\n", encoding="utf-8")

    assert parse_env_files([env_a, env_b])["WEB_BIND_PORT"] == "8080"


def test_approved_at_requires_timezone(tmp_path: Path) -> None:
    config = _config()
    config["approval"]["approved_at"] = "2026-08-24T16:30:00"  # type: ignore[index]

    with pytest.raises(SerialHardwareError, match="include a timezone"):
        load_site_config(_write_config(tmp_path, config))


@pytest.mark.parametrize("mutation", ("source", "target", "permissions", "baud"))
def test_serial_compose_rejects_drift(tmp_path: Path, mutation: str) -> None:
    site = load_site_config(_write_config(tmp_path, _config()))
    compose = deepcopy(_serial_compose())
    gw = compose["services"]["gw"]  # type: ignore[index]
    if mutation == "baud":
        gw["environment"]["GW_SERIAL_PORTS"] = (  # type: ignore[index]
            '[{"port":"/dev/ruisheng-rs485","baud_rate":19200}]'
        )
    else:
        gw["devices"][0][mutation] = (  # type: ignore[index]
            "rwm" if mutation == "permissions" else "/dev/ttyUSB0"
        )

    with pytest.raises(SerialHardwareError):
        validate_serial_compose(compose, site)


def test_only_gateway_may_receive_serial_device(tmp_path: Path) -> None:
    site = load_site_config(_write_config(tmp_path, _config()))
    compose = _serial_compose()
    compose["services"]["api"]["devices"] = [  # type: ignore[index]
        {
            "source": "/dev/ruisheng-rs485",
            "target": "/dev/ruisheng-rs485",
            "permissions": "rw",
        }
    ]

    with pytest.raises(SerialHardwareError, match="only gw"):
        validate_serial_compose(compose, site)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("image", "attacker/image:latest"),
        ("command", ["sh", "-c", "id"]),
        ("privileged", True),
        ("volumes", [{"type": "bind", "source": "/", "target": "/host"}]),
    ),
)
def test_serial_override_rejects_unapproved_gateway_delta(
    tmp_path: Path, field: str, value: object
) -> None:
    site = load_site_config(_write_config(tmp_path, _config()))
    base = _base_compose()
    full = _serial_compose()
    full["services"]["gw"][field] = value  # type: ignore[index]

    with pytest.raises(SerialHardwareError, match="unapproved Compose field"):
        validate_serial_compose(full, site, base)


def test_serial_override_rejects_service_and_top_level_changes(tmp_path: Path) -> None:
    site = load_site_config(_write_config(tmp_path, _config()))
    base = _base_compose()
    full = _serial_compose()
    full["services"]["attacker"] = {"image": "attacker/image:latest"}  # type: ignore[index]
    with pytest.raises(SerialHardwareError, match="unapproved Compose field"):
        validate_serial_compose(full, site, base)

    full = _serial_compose()
    full["networks"] = {"default": {"driver": "host"}}
    with pytest.raises(SerialHardwareError, match="unapproved Compose field"):
        validate_serial_compose(full, site, base)


def test_hardware_attestation_requires_fresh_matching_ready_identity(tmp_path: Path) -> None:
    site = load_site_config(_write_config(tmp_path, _config()))
    attestation = {
        "schema_version": 1,
        "result": "ready",
        "timestamp": datetime.now(UTC).isoformat(),
        "vendor_id": "0403",
        "product_id": "6001",
        "serial_number": "AI06JYFW",
        "stable_path": "/dev/ruisheng-rs485",
        "device_path": "/dev/ttyUSB0",
        "bus_id": "2-9",
    }
    path = tmp_path / "hardware-state.json"
    path.write_text(json.dumps(attestation), encoding="utf-8")
    validate_hardware_attestation(path, site)

    attestation["timestamp"] = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
    path.write_text(json.dumps(attestation), encoding="utf-8")
    with pytest.raises(SerialHardwareError, match="stale"):
        validate_hardware_attestation(path, site)


def test_database_device_must_match_approved_endpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    site = load_site_config(_write_config(tmp_path, _config()))
    output = json.dumps(
        {
            "dev_number": "RS485-001",
            "transport_type": "serial",
            "serial_port": "/dev/ruisheng-rs485",
            "modbus_addr": 1,
            "baud_rate": 9600,
            "is_enabled": True,
        }
    )

    def fake_run(*args: object, **kwargs: object) -> CompletedProcess[str]:
        return CompletedProcess(args=[], returncode=0, stdout=output + "\n", stderr="")

    monkeypatch.setattr("tools.validate_serial_hardware.subprocess.run", fake_run)
    validate_database_device(site, "ruisheng_admin")

    stale = json.loads(output)
    stale["modbus_addr"] = 2
    output = json.dumps(stale)
    with pytest.raises(SerialHardwareError, match="modbus_addr"):
        validate_database_device(site, "ruisheng_admin")


def test_unapproved_config_rejects_hidden_serial_override(tmp_path: Path) -> None:
    path = _write_config(tmp_path, _config(approved=False))

    assert main(["--config", str(path), "--serial-override", "serial.yml"]) == 1


def test_attach_script_uses_usb_identity_and_never_opens_serial_port() -> None:
    script = (ROOT / "tools" / "serial_hardware_attach.ps1").read_text(encoding="utf-8")

    assert "InstanceId" in script
    assert "VendorId" in script
    assert "ProductId" in script
    assert "SerialNumber" in script
    assert "modprobe ftdi_sio" in script
    assert 'rm -f -- "$alias_path"' in script
    assert 'ln -s "$device_node" "$alias_path"' in script
    assert "Remove-StableAlias" in script
    assert "serial-hardware-state.json" in script
    assert '"sh", "-s", "--"' in script
    assert "sh -c $linuxScript" not in script
    assert "^/dev/ruisheng-" in script
    assert '"bus_id"' not in (ROOT / "deploy" / "site-serial-hardware.json.example").read_text(
        encoding="utf-8"
    )
    assert "System.IO.Ports.SerialPort" not in script
    assert "open_serial_connection" not in script
    assert "Get-TargetDevice" in script
    assert '"bind", "--busid", [string]$device.BusId' in script
    assert '"attach", "--wsl"' in script
    assert "native_command_timeout" in script
    assert "audit_paths_are_fixed" in script
    assert "wsl_missing" in script
    assert "StandardInputEncoding" not in script
    assert "$process.StandardInput.BaseStream" in script
    assert "[Text.UTF8Encoding]::new($false).GetBytes($StandardInput)" in script
    assert "$inputStream.Close()" in script


def test_native_stdin_is_utf8_without_bom_under_windows_powershell_51(tmp_path: Path) -> None:
    powershell = shutil.which("powershell.exe")
    if powershell is None:
        pytest.skip("Windows PowerShell is unavailable")
    source = (ROOT / "tools" / "serial_hardware_attach.ps1").read_text(encoding="utf-8")
    match = re.search(
        r"(function Invoke-NativeCommand\(.*?\n\})\r?\n\r?\nfunction Invoke-Usbipd",
        source,
        flags=re.DOTALL,
    )
    assert match is not None
    reader = tmp_path / "read_stdin.py"
    reader.write_text(
        "import base64, sys\n"
        "sys.stdout.write(base64.b64encode(sys.stdin.buffer.read()).decode('ascii'))\n",
        encoding="utf-8",
    )
    python_literal = "'" + sys.executable.replace("'", "''") + "'"
    reader_literal = "'" + str(reader).replace("'", "''") + "'"
    invocation = f"""
{match.group(1)}
$inputText = "set -eu`n中文设备`n"
$result = Invoke-NativeCommand {python_literal} @({reader_literal}) $inputText 30
[ordered]@{{
    version = $PSVersionTable.PSVersion.ToString()
    exit_code = $result.ExitCode
    stdin_base64 = $result.Output
    stderr = $result.Error
}} | ConvertTo-Json -Compress
"""
    completed = subprocess.run(
        [powershell, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", invocation],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    result = json.loads(completed.stdout.strip())
    assert result["version"].startswith("5.1.")
    assert result["exit_code"] == 0
    assert result["stderr"] == ""
    stdin_bytes = base64.b64decode(result["stdin_base64"])
    assert stdin_bytes == "set -eu\n中文设备\n".encode()
    assert not stdin_bytes.startswith(b"\xef\xbb\xbf")


def test_wsl_script_normalizes_crlf_and_removes_utf8_bom_before_stdin() -> None:
    powershell = shutil.which("powershell.exe")
    if powershell is None:
        pytest.skip("Windows PowerShell is unavailable")
    source = (ROOT / "tools" / "serial_hardware_attach.ps1").read_text(encoding="utf-8")
    match = re.search(
        r"(function Invoke-WslScript.*?\n\})\r?\n\r?\nfunction Remove-StableAlias",
        source,
        flags=re.DOTALL,
    )
    assert match is not None
    invocation = f"""
function Invoke-NativeCommand {{
    param($FilePath, $ArgumentList, $StandardInput, $TimeoutSeconds)
    [Convert]::ToBase64String([Text.UTF8Encoding]::new($false).GetBytes($StandardInput))
}}
$script:WslDistribution = 'docker-desktop'
$script:WslPath = 'wsl.exe'
{match.group(1)}
$inputText = ([string][char]0xFEFF) + "set -eu`r`n中文`rsecond`n"
Invoke-WslScript $inputText @() 30
"""
    completed = subprocess.run(
        [powershell, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", invocation],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert base64.b64decode(completed.stdout.strip()).decode("utf-8") == ("set -eu\n中文\nsecond\n")


def test_task_installer_registers_retry_when_device_is_temporarily_absent() -> None:
    script = (ROOT / "tools" / "install_serial_hardware_task.ps1").read_text(encoding="utf-8")

    assert "$initialExitCode -notin @(0, 2)" in script
    assert '"device_unavailable"' in script
    assert "Register-ScheduledTask" in script
    assert "Assert-ProtectedPath" in script
    assert "interactive_docker_desktop_owner_required" in script


def test_windows_publisher_installs_serial_tools_from_authenticated_snapshot() -> None:
    script = (ROOT / "tools" / "release_trust" / "verify-publisher.ps1").read_text(encoding="utf-8")

    assert "function Install-AuthenticatedSerialTools" in script
    assert "if ($InstallSerialTools)" in script
    assert "Install-AuthenticatedSerialTools $PackageRoot" in script
    assert "Join-Path $AuthenticatedRoot $Relative" in script
    assert "Set-ProtectedSnapshotAcl $Directory" in script
    assert "Assert-ProtectedAcl $Entry.destination" in script


def test_serial_override_is_not_part_of_signed_base_compose() -> None:
    for path in (ROOT / "docker-compose.prod.yml", ROOT / "deploy" / "docker-compose.prod.yml"):
        compose = path.read_text(encoding="utf-8")
        assert "GW_SERIAL_PORTS" not in compose
        assert "site-serial.override.yml" not in compose
