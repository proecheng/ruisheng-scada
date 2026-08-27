"""Regression tests for the authenticated, read-only Modbus RTU probe."""

from __future__ import annotations

import json
import subprocess
import sys
import uuid
from collections.abc import Iterator
from copy import deepcopy
from pathlib import Path

import pytest

from tools import probe_modbus_rtu as probe

ROOT = Path(__file__).parents[2]


def _config() -> dict[str, object]:
    return {
        "schema_version": 1,
        "adapter": {
            "vendor_id": "0403",
            "product_id": "6001",
            "serial_number": "AI06JYFW",
            "device_path": "/dev/ruisheng-rs485",
        },
        "serial": {
            "baud_rate": 9600,
            "data_bits": 8,
            "parity": "N",
            "stop_bits": 1,
            "timeout_ms": 400,
        },
        "scope": {
            "unit_id": 1,
            "function_code": 3,
            "requests": [
                {
                    "start_address": 0,
                    "register_count": 6,
                    "requires_previous_valid": False,
                },
                {
                    "start_address": 27,
                    "register_count": 9,
                    "requires_previous_valid": True,
                },
            ],
        },
        "budget": {
            "max_requests": 4,
            "max_retries_per_request": 1,
            "min_interval_ms": 500,
            "max_response_bytes": 64,
        },
        "approval": {
            "scope_id": probe.APPROVED_SCOPE_ID,
            "approved_by": "release-approver",
            "approved_at": "2026-08-25T12:00:00+08:00",
        },
    }


def _write_config(tmp_path: Path, value: dict[str, object] | None = None) -> Path:
    path = tmp_path / "probe.json"
    path.write_text(json.dumps(value or _config()), encoding="utf-8")
    return path


def _response(registers: list[int], *, unit: int = 1, function_code: int = 3) -> bytes:
    data = b"".join(value.to_bytes(2, "big") for value in registers)
    body = bytes((unit, function_code, len(data))) + data
    crc = probe.compute_crc16(body)
    return body + bytes((crc & 0xFF, crc >> 8))


def _exception(code: int = 2) -> bytes:
    body = bytes((1, 0x83, code))
    crc = probe.compute_crc16(body)
    return body + bytes((crc & 0xFF, crc >> 8))


def _events(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


class FakeSerial:
    def __init__(
        self, *, short_write: bool = False, close_error: bool = False, write_error: bool = False
    ) -> None:
        self.short_write = short_write
        self.close_error = close_error
        self.write_error = write_error
        self.writes: list[bytes] = []
        self.in_waiting = 0

    def fileno(self) -> int:
        return 10

    def reset_input_buffer(self) -> None:
        return None

    def write(self, value: bytes) -> int:
        self.writes.append(value)
        if self.write_error:
            raise OSError("write outcome is indeterminate")
        return len(value) - 1 if self.short_write else len(value)

    def flush(self) -> None:
        return None

    def read(self, size: int = 1) -> bytes:
        return b""

    def close(self) -> None:
        if self.close_error:
            raise OSError("injected close failure")


class FailingAudit:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    def write(self, event: dict[str, object]) -> None:
        self.events.append(event)
        if event["event"] == "request_tx":
            raise OSError("injected fsync failure")


@pytest.mark.parametrize(
    ("section", "field", "value", "error"),
    (
        ("serial", "baud_rate", 19200, "9600/8N1"),
        ("scope", "unit_id", 2, "unit_id"),
        ("scope", "function_code", 6, "read-only FC3"),
        ("budget", "max_requests", 5, "must remain 4"),
        ("budget", "min_interval_ms", 499, "must remain 500"),
        ("budget", "max_response_bytes", 22, "must remain 64"),
        ("budget", "max_response_bytes", 256, "must remain 64"),
        ("serial", "timeout_ms", 2000, "must remain 400"),
        ("adapter", "vendor_id", "1234", "approved 0403:6001"),
    ),
)
def test_config_rejects_out_of_scope_values_before_io(
    tmp_path: Path, section: str, field: str, value: object, error: str
) -> None:
    config = deepcopy(_config())
    config[section][field] = value  # type: ignore[index]

    with pytest.raises(probe.ProbeError, match=error):
        probe.load_config(_write_config(tmp_path, config))


def test_only_approved_read_frames_can_be_generated(tmp_path: Path) -> None:
    config = probe.load_config(_write_config(tmp_path))

    frames = [
        probe.request_frame(config.unit_id, config.function_code, request)
        for request in config.requests
    ]

    assert [frame.hex() for frame in frames] == ["010300000006c5c8", "0103001b0009f5cb"]
    assert {frame[1] for frame in frames} <= {1, 2, 3, 4}
    with pytest.raises(probe.ProbeError, match="read-only"):
        probe.request_frame(1, 6, config.requests[0])


def test_valid_response_records_registers_and_conservative_conclusion(tmp_path: Path) -> None:
    config = probe.load_config(_write_config(tmp_path))

    result = probe.classify_response(_response([3, 0, 0, 0, 0, 0]), config.requests[0], 1, 3)

    assert result["classification"] == "valid"
    assert result["crc_valid"] is True
    assert result["registers"] == [3, 0, 0, 0, 0, 0]
    assert result["response_crc_hex"] == _response([3, 0, 0, 0, 0, 0])[-2:].hex()
    assert result["conclusion"] == probe.CONCLUSION


def test_exception_and_valid_frame_with_extra_noise_are_not_success(tmp_path: Path) -> None:
    config = probe.load_config(_write_config(tmp_path))
    request = config.requests[0]

    exception = probe.classify_response(_exception(), request, 1, 3)
    noisy = probe.classify_response(_response([3, 0, 0, 0, 0, 0]) + b"\xff", request, 1, 3)

    assert exception["classification"] == "modbus_exception"
    assert exception["exception_code"] == 2
    assert noisy["classification"] == "noise"
    assert noisy["noise_suffix_hex"] == "ff"


def _run_with_responses(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    responses: Iterator[bytes],
    *,
    serial: FakeSerial | None = None,
) -> tuple[probe.ProbeOutcome, FakeSerial, Path]:
    config = probe.load_config(_write_config(tmp_path))
    port = serial or FakeSerial()
    audit_path = tmp_path / "audit.jsonl"
    audit = probe.AuditLog(audit_path)
    monkeypatch.setattr(probe, "verify_open_file_identity", lambda *_: {"st_rdev": "188"})
    monkeypatch.setattr(probe, "_read_response", lambda *_, **__: next(responses))
    monkeypatch.setattr(probe.time, "sleep", lambda _: None)
    result = probe.execute_probe(config, audit, serial_factory=lambda _: port)
    audit.close()
    return result, port, audit_path


def test_second_range_requires_first_valid_and_all_tx_are_read_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    responses = iter((_response([3, 0, 0, 0, 0, 0]), _response([3] + [0] * 8)))

    result, port, audit_path = _run_with_responses(monkeypatch, tmp_path, responses)

    assert result.exit_code == 0
    assert [frame[1] for frame in port.writes] == [3, 3]
    assert [event["event"] for event in _events(audit_path)].count("request_tx") == 2
    assert _events(audit_path)[-1]["conclusion"] == probe.CONCLUSION


def test_no_valid_first_response_retries_then_stops_without_second_range(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    responses = iter((b"", b"\x01\x03\x00"))

    result, port, audit_path = _run_with_responses(monkeypatch, tmp_path, responses)

    assert result.exit_code == 2
    assert len(port.writes) == 2
    assert _events(audit_path)[-1]["result"] == "no_valid_response"


@pytest.mark.parametrize(
    ("serial", "reason"),
    ((FakeSerial(short_write=True), "ProbeError"), (FakeSerial(close_error=True), "close_failed")),
)
def test_transport_failures_end_with_aborted_and_nonzero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, serial: FakeSerial, reason: str
) -> None:
    responses = iter((_response([3, 0, 0, 0, 0, 0]), _response([3] + [0] * 8)))

    result, _port, audit_path = _run_with_responses(monkeypatch, tmp_path, responses, serial=serial)

    assert result.exit_code != 0
    assert _events(audit_path)[-1]["event"] == "aborted"
    if reason == "close_failed":
        assert "close failure" in _events(audit_path)[-1]["detail"]
    else:
        assert _events(audit_path)[-1]["reason"] == reason


def test_audit_write_failure_aborts_before_any_followup_request(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = probe.load_config(_write_config(tmp_path))
    port = FakeSerial()
    audit = FailingAudit()
    monkeypatch.setattr(probe, "verify_open_file_identity", lambda *_: {"st_rdev": "188"})

    result = probe.execute_probe(
        config,
        audit,  # type: ignore[arg-type]
        serial_factory=lambda _: port,
    )

    assert result.exit_code == 1
    assert len(port.writes) == 1
    assert audit.events[-1]["event"] == "aborted"


def test_busy_port_is_zero_tx_and_audited_as_aborted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = probe.load_config(_write_config(tmp_path))
    audit_path = tmp_path / "audit.jsonl"
    audit = probe.AuditLog(audit_path)

    def busy(_: probe.ProbeConfig) -> probe.SerialLike:
        raise probe.ProbeError("cannot open serial port exclusively: busy")

    result = probe.execute_probe(config, audit, serial_factory=busy)
    audit.close()

    assert result.exit_code == 1
    assert _events(audit_path)[-1]["event"] == "aborted"
    assert _events(audit_path)[-1]["completed_tx_count"] == 0


def test_real_audit_log_surfaces_fsync_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    audit = probe.AuditLog(tmp_path / "audit.jsonl")
    original_fsync = probe.os.fsync
    monkeypatch.setattr(
        probe.os, "fsync", lambda _descriptor: (_ for _ in ()).throw(OSError("disk"))
    )

    with pytest.raises(OSError, match="disk"):
        audit.write({"event": "test"})

    monkeypatch.setattr(probe.os, "fsync", original_fsync)
    audit.close()


def test_persistent_audit_failure_uses_independent_terminal_summary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class PersistentFailure:
        failed = False

        def write(self, event: dict[str, object]) -> None:
            if event["event"] == "request_tx":
                self.failed = True
            if self.failed:
                raise OSError("persistent disk failure")

    config = probe.load_config(_write_config(tmp_path))
    port = FakeSerial()
    monkeypatch.setattr(probe, "verify_open_file_identity", lambda *_: {"st_rdev": "188"})

    outcome = probe.execute_probe(
        config,
        PersistentFailure(),
        serial_factory=lambda _: port,  # type: ignore[arg-type]
    )

    assert outcome.exit_code == 1
    assert outcome.completed_tx_count == 1
    assert outcome.attempted_write_bytes == 8
    assert outcome.audit_complete is False


def test_serial_write_exception_reports_unknown_tx_count(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = probe.load_config(_write_config(tmp_path))
    port = FakeSerial(write_error=True)
    audit_path = tmp_path / "audit.jsonl"
    audit = probe.AuditLog(audit_path)
    monkeypatch.setattr(probe, "verify_open_file_identity", lambda *_: {"st_rdev": "188"})

    outcome = probe.execute_probe(config, audit, serial_factory=lambda _: port)
    audit.close()

    assert outcome.exit_code == 1
    assert outcome.completed_tx_count is None
    assert outcome.attempted_write_bytes is None
    assert outcome.tx_count_known is False
    assert _events(audit_path)[-1]["tx_count_known"] is False


def test_audit_creation_persists_parent_directory_entry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[Path] = []
    monkeypatch.setattr(probe, "_fsync_parent_directory", calls.append)

    audit_path = tmp_path / "audit.jsonl"
    audit = probe.AuditLog(audit_path)
    audit.close()

    assert calls == [audit_path]


def test_fd_identity_uses_sys_dev_char_without_tty_node(monkeypatch: pytest.MonkeyPatch) -> None:
    class Metadata:
        st_rdev = 48128  # Linux makedev(188, 0)

    monkeypatch.setattr(probe.os, "fstat", lambda _: Metadata())
    monkeypatch.setattr(
        probe.Path, "resolve", lambda self, strict=True: probe.Path("/sys/devices/usb/ttyUSB0")
    )
    values = {"idVendor": "0403", "idProduct": "6001", "serial": "AI06JYFW"}
    monkeypatch.setattr(probe, "_read_usb_value", lambda _path, name: values[name])

    identity = probe.verify_open_file_identity(
        FakeSerial(), probe.Adapter("0403", "6001", "AI06JYFW", "/dev/ruisheng-rs485")
    )

    assert identity["sys_device"].replace("\\", "/") == "/sys/dev/char/188:0"


def test_inter_request_delay_starts_after_response_end(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = probe.load_config(_write_config(tmp_path))
    port = FakeSerial()
    audit = probe.AuditLog(tmp_path / "audit.jsonl")
    responses = iter((_response([3, 0, 0, 0, 0, 0]), _response([3] + [0] * 8)))
    clocks = iter((10.0, 10.4, 10.5, 20.0, 20.1))
    sleeps: list[float] = []
    monkeypatch.setattr(probe, "verify_open_file_identity", lambda *_: {"st_rdev": "188"})
    monkeypatch.setattr(probe, "_read_response", lambda *_, **__: next(responses))
    monkeypatch.setattr(probe.time, "monotonic", lambda: next(clocks))
    monkeypatch.setattr(probe.time, "sleep", sleeps.append)

    outcome = probe.execute_probe(config, audit, serial_factory=lambda _: port)
    audit.close()

    assert outcome.exit_code == 0
    assert sleeps == pytest.approx([0.4])


def test_nonstandard_exception_code_is_not_protocol_evidence(tmp_path: Path) -> None:
    config = probe.load_config(_write_config(tmp_path))
    result = probe.classify_response(_exception(7), config.requests[0], 1, 3)

    assert result["classification"] == "invalid_exception"


def test_audit_file_is_unique_and_fsynced(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    calls: list[int] = []
    monkeypatch.setattr(probe.os, "fsync", calls.append)
    audit = probe.AuditLog(path)
    audit.write({"event": "test"})
    audit.close()

    assert calls
    with pytest.raises(probe.ProbeError, match="unique audit"):
        probe.AuditLog(path)


def test_real_cli_defaults_to_dry_run_and_execute_metadata_is_mandatory(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)
    command = [
        sys.executable,
        str(ROOT / "tools" / "probe_modbus_rtu.py"),
        "--config",
        str(config_path),
    ]

    dry_run = subprocess.run(command, capture_output=True, text=True, check=False)
    blocked = subprocess.run([*command, "--execute"], capture_output=True, text=True, check=False)

    assert dry_run.returncode == 0
    assert json.loads(dry_run.stdout)["mode"] == "dry-run"
    assert blocked.returncode != 0
    assert "--audit-path is required" in blocked.stderr


def test_real_cli_execute_writes_bound_closed_audit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config_path = _write_config(tmp_path)
    audit_path = tmp_path / "execute.jsonl"
    port = FakeSerial()
    responses = iter((_response([3, 0, 0, 0, 0, 0]), _response([3] + [0] * 8)))
    monkeypatch.setattr(probe, "verify_open_file_identity", lambda *_: {"st_rdev": "188"})
    monkeypatch.setattr(probe, "_default_serial_factory", lambda _: port)
    monkeypatch.setattr(probe, "_read_response", lambda *_, **__: next(responses))
    monkeypatch.setattr(probe.time, "sleep", lambda _: None)
    run_id = str(uuid.uuid4())
    config_hash = probe._sha256(config_path.read_bytes())
    script_hash = probe._sha256(Path(probe.__file__).read_bytes())

    result = probe.main(
        [
            "--config",
            str(config_path),
            "--execute",
            "--audit-path",
            str(audit_path),
            "--expected-config-sha256",
            config_hash,
            "--expected-script-sha256",
            script_hash,
            "--image-id",
            "sha256:" + "a" * 64,
            "--approval-scope",
            probe.APPROVED_SCOPE_ID,
            "--receipt-sha256",
            "b" * 64,
            "--run-id",
            run_id,
        ]
    )

    assert result == 0
    assert probe.TERMINAL_PREFIX in capsys.readouterr().out
    events = _events(audit_path)
    assert events[0]["run_id"] == run_id
    assert events[-1]["event"] == "completed"


def test_runner_hard_gates_gateway_before_device_mapping() -> None:
    script = (ROOT / "tools" / "run_modbus_probe.ps1").read_text(encoding="utf-8")

    assert "modbus-probe-release.json" in script
    assert ".Config.Image" not in script
    assert "GW_SERIAL_" in script
    assert "Assert-SafeProductionState $Before" in script
    assert script.index("Assert-SafeProductionState $Before") < script.index(
        '"--device", "${DevicePath}:${DevicePath}:rwm"'
    )
    assert "probe audit path must be a new JSONL file" in script
    assert "rejected_zero_tx" in script
    assert "database_counts_raw" in script
    assert "production state changed" in script
    assert '"--entrypoint", "python"' in script
    assert "DeviceCgroupRules" in script
    assert "$Value.HostConfig.Devices | Where-Object { $null -ne $_ }" in script
    assert "$Value.HostConfig.DeviceCgroupRules | Where-Object { $null -ne $_ }" in script
    assert "Privileged" in script
    assert "RUISHENG_PROBE_TERMINAL=" in script
    assert "Test-ProbeAudit" in script
    assert '$DockerHost = "npipe:////./pipe/docker_engine"' in script
    assert '@("--host", $DockerHost) + $Arguments' in script
    assert '"--pull", "never"' in script
    assert '"DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH", "DOCKER_API_VERSION"' in script
    assert "Assert-ProbeContainerNameAvailable $ContainerName" in script
    assert "Remove-ProbeContainerAndConfirm $ContainerName" in script
    assert '"container", "create", "--name", $ContainerName' in script
    assert '"container", "start", "--attach", $CreatedContainerId' in script
    assert "created probe container identity mismatch" in script
    assert "[string[]]$Ids = if ($Result.exit_code -eq 0)" in script
    assert "$Observation.ElapsedMilliseconds -ge $MinimumObservationMilliseconds" in script
    assert "Remove-ProbeContainerAndConfirm $ContainerName $CleanupWindow" in script
    assert "probe container cleanup could not be confirmed" in script
    assert "$Terminal.exit_code -ne $ExitCode" in script
    assert "process_exit_code = $RawProbeExitCode" in script
    assert "runner must execute from the authenticated installation path" in script
    assert "dry_run = $DryRun" in script
    assert "Write-Output ($DryRun | ConvertTo-Json" in script
    assert "source=$ProbeAuditStagingRoot,target=/audit" in script
    assert "source=$AuditRoot,target=/audit" not in script
    assert "production_state_after = $After" in script
    assert "if ($null -ne $Before -and $null -eq $After)" in script
    assert "production_state_after_error = $AfterCaptureError" in script
    assert 'Write-Host "[modbus-runner] audit:' in script


def test_runner_does_not_persist_raw_docker_inspection_output() -> None:
    script = (ROOT / "tools" / "run_modbus_probe.ps1").read_text(encoding="utf-8")

    assert '.out"' not in script
    assert '.err"' not in script
    assert "WriteAllText($OutPath" not in script
    assert "WriteAllText($ErrPath" not in script
    assert "DeleteSubdirectoriesAndFiles" in script
    assert "ancestor permits replacement by" in script


def test_runner_powershell_self_test_executes_path_guards() -> None:
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-File",
            str(ROOT / "tools" / "run_modbus_probe.ps1"),
            "-SelfTest",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value == {
        "exact_dev": True,
        "child_dev": True,
        "root_exposes_dev": True,
        "similar_path": False,
        "quoted_argument": '"SELECT 1"',
        "embedded_quote": '"a\\"b"',
        "trailing_slash": '"C:\\path with space\\\\"',
        "empty_argument": '""',
        "docker_host": "npipe:////./pipe/docker_engine",
        "audit_terminal_valid": True,
        "exit_mismatch_rejected": True,
        "incomplete_audit_rejected": True,
        "tmpfs_rejected": True,
        "process_timeout_bounded": True,
        "single_container_id_array": True,
    }


def test_probe_files_are_in_every_authenticated_release_allowlist() -> None:
    expected = {
        "site-modbus-probe.json.example",
        "probe_modbus_rtu.py",
        "run_modbus_probe.ps1",
    }
    paths = (
        ROOT / "tools" / "release_artifacts.py",
        ROOT / "tools" / "release_trust" / "verify-publisher.ps1",
        ROOT / "tools" / "release_trust" / "verify-publisher.sh",
        ROOT / "deploy" / "verify-candidate.ps1",
        ROOT / "deploy" / "verify-candidate.sh",
    )
    for path in paths:
        contents = path.read_text(encoding="utf-8")
        assert expected <= {name for name in expected if name in contents}, path

    publisher = paths[1].read_text(encoding="utf-8")
    assert "Join-Path $AuthenticatedRoot $TemplateRelative" in publisher
    assert '"probe_modbus_rtu.py"' in publisher
    assert '"run_modbus_probe.ps1"' in publisher
