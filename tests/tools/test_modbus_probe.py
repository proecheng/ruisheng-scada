"""Regression tests for the authenticated, read-only Modbus RTU probe."""

from __future__ import annotations

import json
import os
import shutil
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


def _extended_config() -> dict[str, object]:
    config = _config()
    config["approval"]["scope_id"] = probe.EXTENDED_SCOPE_ID  # type: ignore[index]
    config["budget"]["max_requests"] = 6  # type: ignore[index]
    config["scope"]["requests"] = [  # type: ignore[index]
        {"start_address": start, "register_count": count, "requires_previous_valid": previous}
        for start, count, previous in probe.EXTENDED_REQUESTS
    ]
    return config


def test_extended_scope_has_exact_read_only_frames(tmp_path: Path) -> None:
    config = probe.load_config(_write_config(tmp_path, _extended_config()))
    assert probe.normalized_plan(config)["frames"] == [
        "010300000006c5c8",
        "0103000600156404",
        "0103001b0009f5cb",
    ]
    assert config.budget.max_requests == 6
    with pytest.raises(probe.ProbeError, match="outside the approved scope"):
        probe.request_frame(1, 3, config.requests[1])


@pytest.mark.parametrize(
    "change", ["old_approval", "unknown_scope", "budget", "range", "order", "dependency"]
)
def test_extended_profile_rejects_scope_mixing_before_io(tmp_path: Path, change: str) -> None:
    config = _extended_config()
    if change == "old_approval":
        config["approval"]["scope_id"] = probe.APPROVED_SCOPE_ID  # type: ignore[index]
    elif change == "unknown_scope":
        config["approval"]["scope_id"] = "arbitrary-range"  # type: ignore[index]
    elif change == "budget":
        config["budget"]["max_requests"] = 7  # type: ignore[index]
    elif change == "range":
        config["scope"]["requests"][1]["register_count"] = 22  # type: ignore[index]
    elif change == "order":
        config["scope"]["requests"].reverse()  # type: ignore[index]
    else:
        config["scope"]["requests"][1]["requires_previous_valid"] = False  # type: ignore[index]
    with pytest.raises(probe.ProbeError):
        probe.load_config(_write_config(tmp_path, config))


@pytest.mark.parametrize("middle_valid", [True, False])
def test_extended_probe_preserves_order_and_stops_after_failed_range(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, middle_valid: bool
) -> None:
    config = probe.load_config(_write_config(tmp_path, _extended_config()))
    responses = iter(
        [_response([3] + [0] * 5)]
        + ([_response(list(range(21))), _response([3] + [0] * 8)] if middle_valid else [b"", b""])
    )
    port = FakeSerial()
    monkeypatch.setattr(probe, "verify_open_file_identity", lambda *_: {"st_rdev": "188"})
    monkeypatch.setattr(probe, "_read_response", lambda *_, **__: next(responses))
    monkeypatch.setattr(probe.time, "sleep", lambda _: None)
    audit = probe.AuditLog(tmp_path / "audit.jsonl")
    outcome = probe.execute_probe(config, audit, serial_factory=lambda _: port)
    audit.close()
    assert outcome.exit_code == (0 if middle_valid else 2)
    assert len(port.writes) == 3
    assert port.writes[1].hex() == "0103000600156404"
    assert port.writes[2].hex() == ("0103001b0009f5cb" if middle_valid else "0103000600156404")


@pytest.mark.parametrize("shell", ["powershell.exe", "pwsh.exe"])
def test_runner_extended_profile_audit_enforces_order(tmp_path: Path, shell: str) -> None:
    executable = shutil.which(shell)
    if executable is None:
        pytest.skip(f"{shell} unavailable")
    script = (ROOT / "tools/run_modbus_probe.ps1").read_text(encoding="utf-8-sig")
    functions = script.split("if ($SelfTest) {", 1)[0]
    harness = (
        functions
        + r"""
Set-ProbeScope $ExtendedScope
$Events = @([pscustomobject]@{event='run_started'}, [pscustomobject]@{event='port_verified'})
for ($i=0; $i -lt $ExpectedRequests.Count; $i++) {
    $r = $ExpectedRequests[$i]
    $Events += [pscustomobject]@{event='request_tx';request_index=$i;attempt=0;tx_number=($i+1);function_code=3;start_address=$r.address;register_count=$r.count;tx_hex=$r.frame}
    $Events += [pscustomobject]@{event='response_rx';request_index=$i;attempt=0;tx_number=($i+1);latency_ms=1;rx_hex='00';classification='valid';crc_valid=$true;registers=@(0)*$r.count;conclusion='仅证明区间可读，型号/点名/倍率未决'}
}
$Events += [pscustomobject]@{event='completed';result='valid';completed_tx_count=3;attempted_write_bytes=24;tx_count_known=$true}
Assert-ProbeAuditSequence $Events
$terminal=[pscustomobject]@{exit_code=0;result='valid';completed_tx_count=3;attempted_write_bytes=24;tx_count_known=$true;audit_complete=$true}
Assert-ProbeTerminalMatches $Events $terminal 0
$Events[4].request_index = 2
$rejected=$false
try { Assert-ProbeAuditSequence $Events } catch {$rejected=$true}
if (-not $rejected) { throw 'out-of-order audit accepted' }
Set-ProbeScope 'b06-9600-8n1-unit1-fc3-r0-5-r27-35'
if ($MaxRequests -ne 4 -or $ExpectedRequests.Count -ne 2) { throw 'legacy scope changed' }
Write-Output 'PASS'
"""
    )
    path = tmp_path / "runner-profile-test.ps1"
    path.write_text(harness, encoding="utf-8-sig")
    result = subprocess.run(
        [executable, "-NoProfile", "-File", str(path)],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "PASS"


def _correlation_config() -> dict[str, object]:
    config = _config()
    config["approval"]["scope_id"] = probe.CORRELATION_SCOPE_ID  # type: ignore[index]
    config["budget"]["max_requests"] = 12  # type: ignore[index]
    config["scope"]["requests"] = [  # type: ignore[index]
        {"start_address": start, "register_count": count, "requires_previous_valid": previous}
        for start, count, previous in probe.CORRELATION_REQUESTS
    ]
    return config


def test_correlation_profile_is_fixed_bounded_and_defaults_to_zero_io(tmp_path: Path) -> None:
    path = _write_config(tmp_path, _correlation_config())
    config = probe.load_config(path)
    expected = [
        "01030000001b05c1",
        "0103000600156404",
        "01030000001b05c1",
        "01030012001265c2",
        "0103001b0009f5cb",
        "01030012001265c2",
    ]
    assert probe.normalized_plan(config)["frames"] == expected
    assert config.budget.max_requests == 12
    assert max(5 + 2 * request.register_count for request in config.requests) == 59
    for request in config.requests:
        assert (
            0 <= request.start_address <= request.start_address + request.register_count - 1 <= 35
        )
    for scope in (probe.APPROVED_SCOPE_ID, probe.EXTENDED_SCOPE_ID):
        with pytest.raises(probe.ProbeError, match="outside the approved scope"):
            probe.request_frame(1, 3, config.requests[0], scope_id=scope)
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools/probe_modbus_rtu.py"), "--config", str(path)],
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["mode"] == "dry-run"
    assert output["plan"]["frames"] == expected


@pytest.mark.parametrize(
    "change", ["old_scope", "budget", "range", "order", "dependency", "missing_return", "write"]
)
def test_correlation_profile_rejects_changed_plan(tmp_path: Path, change: str) -> None:
    config = _correlation_config()
    if change == "old_scope":
        config["approval"]["scope_id"] = probe.EXTENDED_SCOPE_ID  # type: ignore[index]
    elif change == "budget":
        config["budget"]["max_requests"] = 13  # type: ignore[index]
    elif change == "range":
        config["scope"]["requests"][3]["register_count"] = 19  # type: ignore[index]
    elif change == "order":
        config["scope"]["requests"].reverse()  # type: ignore[index]
    elif change == "dependency":
        config["scope"]["requests"][2]["requires_previous_valid"] = False  # type: ignore[index]
    elif change == "missing_return":
        config["scope"]["requests"].pop()  # type: ignore[index]
    else:
        config["scope"]["function_code"] = 6  # type: ignore[index]
    with pytest.raises(probe.ProbeError):
        probe.load_config(_write_config(tmp_path, config))


@pytest.mark.parametrize("failed_index", [None, 0, 1, 2, 3, 4, 5])
def test_correlation_probe_orders_repeated_ranges_and_stops_on_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, failed_index: int | None
) -> None:
    config = probe.load_config(_write_config(tmp_path, _correlation_config()))
    count = 6 if failed_index is None else failed_index
    responses = iter(
        [_response(list(range(request.register_count))) for request in config.requests[:count]]
        + ([] if failed_index is None else [b"", b""])
    )
    port = FakeSerial()
    monkeypatch.setattr(probe, "verify_open_file_identity", lambda *_: {"st_rdev": "188"})
    monkeypatch.setattr(probe, "_read_response", lambda *_, **__: next(responses))
    monkeypatch.setattr(probe.time, "sleep", lambda _: None)
    audit = probe.AuditLog(tmp_path / "audit.jsonl")
    try:
        outcome = probe.execute_probe(config, audit, serial_factory=lambda _: port)
    finally:
        audit.close()
    expected_frames = probe.normalized_plan(config)["frames"]
    expected = (
        expected_frames
        if failed_index is None
        else (expected_frames[:failed_index] + [expected_frames[failed_index]] * 2)
    )
    assert [frame.hex() for frame in port.writes] == expected
    assert outcome.exit_code == (0 if failed_index is None else 2)
    assert outcome.completed_tx_count == len(expected)
    assert all(frame[1] == 3 for frame in port.writes)


@pytest.mark.parametrize("shell", ["powershell.exe", "pwsh.exe"])
def test_runner_correlation_profile_binds_repeated_ranges_and_budget(
    tmp_path: Path, shell: str
) -> None:
    executable = shutil.which(shell)
    if executable is None:
        pytest.skip(f"{shell} unavailable")
    source = (ROOT / "tools/run_modbus_probe.ps1").read_text(encoding="utf-8-sig")
    harness = (
        source.split("if ($SelfTest) {", 1)[0]
        + r"""
Set-ProbeScope $CorrelationScope
if ($MaxRequests -ne 12 -or $ExpectedRequests.Count -ne 6) { throw 'scope budget changed' }
$frames = @('01030000001b05c1','0103000600156404','01030000001b05c1','01030012001265c2','0103001b0009f5cb','01030012001265c2')
$Events = @([pscustomobject]@{event='run_started'}, [pscustomobject]@{event='port_verified'})
for ($i=0; $i -lt $ExpectedRequests.Count; $i++) {
    $r = $ExpectedRequests[$i]
    if ($r.frame -cne $frames[$i]) { throw 'unexpected frame' }
    $Events += [pscustomobject]@{event='request_tx';request_index=$i;attempt=0;tx_number=($i+1);function_code=3;start_address=$r.address;register_count=$r.count;tx_hex=$r.frame}
    $Events += [pscustomobject]@{event='response_rx';request_index=$i;attempt=0;tx_number=($i+1);latency_ms=1;rx_hex='00';classification='valid';crc_valid=$true;registers=@(0)*$r.count;conclusion='仅证明区间可读，型号/点名/倍率未决'}
}
$Events += [pscustomobject]@{event='completed';result='valid';completed_tx_count=6;attempted_write_bytes=48;tx_count_known=$true}
Assert-ProbeAuditSequence $Events
$terminal=[pscustomobject]@{exit_code=0;result='valid';completed_tx_count=6;attempted_write_bytes=48;tx_count_known=$true;audit_complete=$true}
Assert-ProbeTerminalMatches $Events $terminal 0
$Events[6].request_index = 0
$rejected=$false
try { Assert-ProbeAuditSequence $Events } catch { $rejected=$true }
if (-not $rejected) { throw 'repeated-frame replay accepted' }
Set-ProbeScope $ExtendedScope
if ($MaxRequests -ne 6 -or $ExpectedRequests.Count -ne 3) { throw 'extended scope changed' }
Set-ProbeScope 'b06-9600-8n1-unit1-fc3-r0-5-r27-35'
if ($MaxRequests -ne 4 -or $ExpectedRequests.Count -ne 2) { throw 'legacy scope changed' }
Write-Output 'PASS'
"""
    )
    path = tmp_path / "runner-correlation-test.ps1"
    path.write_text(harness, encoding="utf-8-sig")
    result = subprocess.run(
        [executable, "-NoProfile", "-File", str(path)],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "PASS"


def _zero_origin_config() -> dict[str, object]:
    config = _config()
    config["approval"]["scope_id"] = probe.ZERO_ORIGIN_SCOPE_ID  # type: ignore[index]
    config["budget"]["max_requests"] = 10  # type: ignore[index]
    config["budget"]["max_response_bytes"] = 80  # type: ignore[index]
    config["scope"]["requests"] = [  # type: ignore[index]
        {"start_address": start, "register_count": count, "requires_previous_valid": previous}
        for start, count, previous in probe.ZERO_ORIGIN_REQUESTS
    ]
    return config


def test_zero_origin_profile_has_exact_frames_and_zero_io_default(tmp_path: Path) -> None:
    path = _write_config(tmp_path, _zero_origin_config())
    config = probe.load_config(path)
    expected = [
        "01030000002445d1",
        "01030000001b05c1",
        "01030000002445d1",
        "010300000006c5c8",
        "01030000002445d1",
    ]
    assert probe.normalized_plan(config)["frames"] == expected
    assert config.budget.max_requests == 10
    assert config.budget.max_response_bytes == 80
    assert max(5 + 2 * request.register_count for request in config.requests) == 77
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools/probe_modbus_rtu.py"), "--config", str(path)],
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["mode"] == "dry-run"
    assert json.loads(result.stdout)["plan"]["frames"] == expected


@pytest.mark.parametrize(
    "change",
    ["scope", "count", "start", "order", "dependency", "missing", "fc", "tx", "rx_low", "rx_high"],
)
def test_zero_origin_rejects_any_changed_scope(tmp_path: Path, change: str) -> None:
    config = _zero_origin_config()
    if change == "scope":
        config["approval"]["scope_id"] = probe.CORRELATION_SCOPE_ID  # type: ignore[index]
    elif change in {"count", "start", "dependency"}:
        field, value = {
            "count": ("register_count", 37),
            "start": ("start_address", 1),
            "dependency": ("requires_previous_valid", False),
        }[change]
        config["scope"]["requests"][2][field] = value  # type: ignore[index]
    elif change == "order":
        config["scope"]["requests"].reverse()  # type: ignore[index]
    elif change == "missing":
        config["scope"]["requests"].pop()  # type: ignore[index]
    elif change == "fc":
        config["scope"]["function_code"] = 6  # type: ignore[index]
    else:
        field, value = {
            "tx": ("max_requests", 11),
            "rx_low": ("max_response_bytes", 64),
            "rx_high": ("max_response_bytes", 81),
        }[change]
        config["budget"][field] = value  # type: ignore[index]
    with pytest.raises(probe.ProbeError):
        probe.load_config(_write_config(tmp_path, config))


@pytest.mark.parametrize("factory", [_config, _extended_config, _correlation_config])
def test_old_profiles_cannot_use_zero_origin_receive_budget(tmp_path: Path, factory) -> None:
    config = factory()
    assert probe.load_config(_write_config(tmp_path, config)).budget.max_response_bytes == 64
    config["budget"]["max_response_bytes"] = 80
    with pytest.raises(probe.ProbeError, match="must remain 64"):
        probe.load_config(_write_config(tmp_path, config))
    with pytest.raises(probe.ProbeError, match="outside the approved scope"):
        probe.request_frame(
            1, 3, probe.Request(0, 36, False), scope_id=config["approval"]["scope_id"]
        )


@pytest.mark.parametrize("failed_index", [None, 0, 1, 2, 3, 4])
def test_zero_origin_stops_at_each_failed_step(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, failed_index: int | None
) -> None:
    config = probe.load_config(_write_config(tmp_path, _zero_origin_config()))
    count = 5 if failed_index is None else failed_index
    responses = iter(
        [_response(list(range(request.register_count))) for request in config.requests[:count]]
        + ([] if failed_index is None else [b"", b""])
    )
    limits = []

    def read_response(*_, **kwargs):
        limits.append(kwargs["max_bytes"])
        return next(responses)

    port = FakeSerial()
    monkeypatch.setattr(probe, "verify_open_file_identity", lambda *_: {"st_rdev": "188"})
    monkeypatch.setattr(probe, "_read_response", read_response)
    monkeypatch.setattr(probe.time, "sleep", lambda _: None)
    audit = probe.AuditLog(tmp_path / "audit.jsonl")
    try:
        outcome = probe.execute_probe(config, audit, serial_factory=lambda _: port)
    finally:
        audit.close()
    frames = probe.normalized_plan(config)["frames"]
    expected = frames if failed_index is None else frames[:count] + [frames[count]] * 2
    assert [frame.hex() for frame in port.writes] == expected
    assert outcome.exit_code == (0 if failed_index is None else 2)
    assert outcome.completed_tx_count == len(expected)
    assert outcome.attempted_write_bytes == 8 * len(expected)
    assert limits == [80] * len(expected)


@pytest.mark.parametrize("extra", [b"", b"\xff", b"\xff" * 10])
def test_zero_origin_reads_77_bytes_but_never_exceeds_80(
    monkeypatch: pytest.MonkeyPatch, extra: bytes
) -> None:
    values = [0, 32767, 32768, 65535] + list(range(32))
    wire = _response(values) + extra

    class ChunkedSerial(FakeSerial):
        received = 0
        in_waiting = 1000

        def read(self, size: int = 1) -> bytes:
            chunk = wire[self.received : self.received + min(size, 9)]
            self.received += len(chunk)
            return chunk

    clock = iter(index / 1000 for index in range(10000))
    monkeypatch.setattr(probe.time, "monotonic", lambda: next(clock))
    port = ChunkedSerial()
    raw = probe._read_response(port, timeout_ms=400, max_bytes=80)
    assert raw == wire[:80]
    assert port.received <= 80
    result = probe.classify_response(raw, probe.Request(0, 36, False), 1, 3)
    assert result["classification"] == ("noise" if extra else "valid")
    if not extra:
        assert result["registers"] == values


@pytest.mark.parametrize("mutation", ["truncated", "byte_count", "crc", "unit", "function"])
def test_zero_origin_invalid_full_frames_are_not_accepted(mutation: str) -> None:
    raw = bytearray(_response(list(range(36))))
    if mutation == "truncated":
        raw = raw[:64]
    elif mutation == "crc":
        raw[-1] ^= 1
    else:
        offset, value = {"byte_count": (2, 70), "unit": (0, 2), "function": (1, 4)}[mutation]
        raw[offset] = value
        raw[-2:] = probe.compute_crc16(raw[:-2]).to_bytes(2, "little")
    assert (
        probe.classify_response(bytes(raw), probe.Request(0, 36, False), 1, 3)["classification"]
        != "valid"
    )


@pytest.mark.parametrize("shell", ["powershell.exe", "pwsh.exe"])
def test_runner_zero_origin_enforces_frames_lengths_and_restores_legacy_budget(
    tmp_path: Path, shell: str
) -> None:
    executable = shutil.which(shell)
    if executable is None:
        pytest.skip(f"{shell} unavailable")
    source = (ROOT / "tools/run_modbus_probe.ps1").read_text(encoding="utf-8-sig")
    responses = [_response([0] * count).hex() for _, count, _ in probe.ZERO_ORIGIN_REQUESTS]
    harness = (
        source.split("if ($SelfTest) {", 1)[0]
        + "\n$wire = '"
        + json.dumps(responses)
        + "' | ConvertFrom-Json\n"
        + r"""
Set-ProbeScope $ZeroOriginScope
if ($MaxRequests -ne 10 -or $MaxResponseBytes -ne 80 -or $ExpectedRequests.Count -ne 5) { throw 'wrong budget' }
$frames = @('01030000002445d1','01030000001b05c1','01030000002445d1','010300000006c5c8','01030000002445d1')
$Events = @([pscustomobject]@{event='run_started'},[pscustomobject]@{event='port_verified'})
for ($i=0; $i -lt $ExpectedRequests.Count; $i++) {
    $r=$ExpectedRequests[$i]
    if ($r.frame -cne $frames[$i] -or $r.address -ne 0) { throw 'wrong frame' }
    $Events += [pscustomobject]@{event='request_tx';request_index=$i;attempt=0;tx_number=($i+1);function_code=3;start_address=0;register_count=$r.count;tx_hex=$r.frame}
    $Events += [pscustomobject]@{event='response_rx';request_index=$i;attempt=0;tx_number=($i+1);latency_ms=1;rx_hex=$wire[$i];rx_bytes=(5+2*$r.count);classification='valid';crc_valid=$true;registers=@(0)*$r.count;conclusion='仅证明区间可读，型号/点名/倍率未决'}
}
$Events += [pscustomobject]@{event='completed';result='valid';completed_tx_count=5;attempted_write_bytes=40;tx_count_known=$true}
Assert-ProbeAuditSequence $Events
$terminal=[pscustomobject]@{exit_code=0;result='valid';completed_tx_count=5;attempted_write_bytes=40;tx_count_known=$true;audit_complete=$true}
Assert-ProbeTerminalMatches $Events $terminal 0
foreach ($bad in @('00',('ff'*81))) {
    $Events[3].rx_hex=$bad
    $rejected=$false
    try { Assert-ProbeAuditSequence $Events } catch { $rejected=$true }
    if (-not $rejected) { throw 'bad response length accepted' }
}
$Events[3].rx_hex=$wire[0]
$Events[6].request_index=0
$rejected=$false
try { Assert-ProbeAuditSequence $Events } catch { $rejected=$true }
if (-not $rejected) { throw 'replayed full block accepted' }
foreach ($scope in @($CorrelationScope,$ExtendedScope,'b06-9600-8n1-unit1-fc3-r0-5-r27-35')) {
    Set-ProbeScope $scope
    if ($MaxResponseBytes -ne 64) { throw 'old receive budget increased' }
}
Write-Output 'PASS'
"""
    )
    path = tmp_path / "runner-zero-origin-test.ps1"
    path.write_text(harness, encoding="utf-8-sig")
    result = subprocess.run(
        [executable, "-NoProfile", "-File", str(path)],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "PASS"


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


@pytest.mark.skipif(
    os.name != "nt" or shutil.which("powershell.exe") is None,
    reason="Windows Desktop PowerShell self-test is Windows-only",
)
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
