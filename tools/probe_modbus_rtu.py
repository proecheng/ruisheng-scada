"""Budgeted, read-only Modbus RTU protocol probe.

The executable scope is intentionally narrower than the general Modbus codec.  It
can only emit the two approved FC3 requests and defaults to a zero-I/O dry run.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import sys
import time
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Protocol, cast

APPROVED_BAUD_RATE = 9600
APPROVED_VENDOR_ID = "0403"
APPROVED_PRODUCT_ID = "6001"
APPROVED_SERIAL_NUMBER = "AI06JYFW"
APPROVED_UNIT_ID = 1
APPROVED_FUNCTION_CODE = 3
APPROVED_REQUESTS = ((0, 6, False), (27, 9, True))
APPROVED_SCOPE_ID = "b06-9600-8n1-unit1-fc3-r0-5-r27-35"
READ_FUNCTION_CODES = frozenset({1, 2, 3, 4})
STANDARD_EXCEPTION_CODES = frozenset({1, 2, 3, 4, 5, 6, 8, 10, 11})
CONCLUSION = "仅证明区间可读，型号/点名/倍率未决"
SHA256_PREFIX = "sha256:"
SHA256_HEX_LENGTH = 64
USB_ID_HEX_LENGTH = 4
APPROVED_TIMEOUT_MS = 400
APPROVED_MAX_REQUESTS = 4
APPROVED_RETRIES = 1
APPROVED_INTERVAL_MS = 500
APPROVED_MAX_RESPONSE_BYTES = 64
MIN_RTU_FRAME_BYTES = 3
EXCEPTION_FRAME_BYTES = 5
INTER_BYTE_QUIET_SECONDS = 0.05
TERMINAL_PREFIX = "RUISHENG_PROBE_TERMINAL="


class ProbeError(RuntimeError):
    """Raised when the probe cannot safely continue."""


class SerialLike(Protocol):
    in_waiting: int

    def fileno(self) -> int: ...

    def reset_input_buffer(self) -> None: ...

    def write(self, value: bytes) -> int: ...

    def flush(self) -> None: ...

    def read(self, size: int = 1) -> bytes: ...

    def close(self) -> None: ...


@dataclass(frozen=True)
class Adapter:
    vendor_id: str
    product_id: str
    serial_number: str
    device_path: str


@dataclass(frozen=True)
class SerialSettings:
    baud_rate: int
    data_bits: int
    parity: str
    stop_bits: int
    timeout_ms: int


@dataclass(frozen=True)
class Request:
    start_address: int
    register_count: int
    requires_previous_valid: bool


@dataclass(frozen=True)
class Budget:
    max_requests: int
    max_retries_per_request: int
    min_interval_ms: int
    max_response_bytes: int


@dataclass(frozen=True)
class Approval:
    scope_id: str
    approved_by: str
    approved_at: str


@dataclass(frozen=True)
class ProbeConfig:
    adapter: Adapter
    serial: SerialSettings
    unit_id: int
    function_code: int
    requests: tuple[Request, ...]
    budget: Budget
    approval: Approval


@dataclass(frozen=True)
class ProbeOutcome:
    exit_code: int
    result: str
    completed_tx_count: int | None
    attempted_write_bytes: int | None
    tx_count_known: bool
    audit_complete: bool


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ProbeError(f"duplicate configuration key: {key}")
        result[key] = value
    return result


def _object(value: object, label: str, keys: set[str]) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise ProbeError(f"{label} must be an object")
    actual = set(value)
    if actual != keys:
        raise ProbeError(
            f"{label} keys mismatch: missing={sorted(keys - actual)}, extra={sorted(actual - keys)}"
        )
    return cast(Mapping[str, object], value)


def _integer(value: object, label: str) -> int:
    if type(value) is not int:
        raise ProbeError(f"{label} must be an integer")
    return value


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProbeError(f"{label} must be a non-empty string")
    return value.strip()


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _validate_digest(value: str, label: str, *, prefixed: bool = False) -> str:
    expected_length = SHA256_HEX_LENGTH + (len(SHA256_PREFIX) if prefixed else 0)
    if len(value) != expected_length:
        raise ProbeError(f"{label} must be an immutable SHA-256")
    digest = value.removeprefix(SHA256_PREFIX) if prefixed else value
    if prefixed and not value.startswith(SHA256_PREFIX):
        raise ProbeError(f"{label} must be an immutable sha256: image ID")
    if any(character not in "0123456789abcdef" for character in digest):
        raise ProbeError(f"{label} must be lowercase hexadecimal")
    return value


def _load_json(path: Path) -> Mapping[str, object]:
    try:
        raw = json.loads(
            path.read_text(encoding="utf-8-sig"), object_pairs_hook=_reject_duplicate_keys
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ProbeError(f"cannot read probe config: {error}") from error
    return _object(
        raw,
        "root",
        {"schema_version", "adapter", "serial", "scope", "budget", "approval"},
    )


def load_config(path: Path) -> ProbeConfig:  # noqa: PLR0912, PLR0915
    root = _load_json(path)
    if _integer(root["schema_version"], "schema_version") != 1:
        raise ProbeError("unsupported schema_version")
    adapter_raw = _object(
        root["adapter"],
        "adapter",
        {"vendor_id", "product_id", "serial_number", "device_path"},
    )
    serial_raw = _object(
        root["serial"],
        "serial",
        {"baud_rate", "data_bits", "parity", "stop_bits", "timeout_ms"},
    )
    scope_raw = _object(root["scope"], "scope", {"unit_id", "function_code", "requests"})
    budget_raw = _object(
        root["budget"],
        "budget",
        {"max_requests", "max_retries_per_request", "min_interval_ms", "max_response_bytes"},
    )
    approval_raw = _object(root["approval"], "approval", {"scope_id", "approved_by", "approved_at"})

    adapter = Adapter(
        vendor_id=_text(adapter_raw["vendor_id"], "adapter.vendor_id").lower(),
        product_id=_text(adapter_raw["product_id"], "adapter.product_id").lower(),
        serial_number=_text(adapter_raw["serial_number"], "adapter.serial_number"),
        device_path=_text(adapter_raw["device_path"], "adapter.device_path"),
    )
    if (adapter.vendor_id, adapter.product_id, adapter.serial_number) != (
        APPROVED_VENDOR_ID,
        APPROVED_PRODUCT_ID,
        APPROVED_SERIAL_NUMBER,
    ):
        raise ProbeError("adapter identity must be approved 0403:6001/AI06JYFW")
    if adapter.device_path != "/dev/ruisheng-rs485":
        raise ProbeError("adapter.device_path is outside the approved scope")

    settings = SerialSettings(
        baud_rate=_integer(serial_raw["baud_rate"], "serial.baud_rate"),
        data_bits=_integer(serial_raw["data_bits"], "serial.data_bits"),
        parity=_text(serial_raw["parity"], "serial.parity").upper(),
        stop_bits=_integer(serial_raw["stop_bits"], "serial.stop_bits"),
        timeout_ms=_integer(serial_raw["timeout_ms"], "serial.timeout_ms"),
    )
    if (settings.baud_rate, settings.data_bits, settings.parity, settings.stop_bits) != (
        APPROVED_BAUD_RATE,
        8,
        "N",
        1,
    ):
        raise ProbeError("only approved 9600/8N1 serial settings are allowed")
    if settings.timeout_ms != APPROVED_TIMEOUT_MS:
        raise ProbeError("serial.timeout_ms must remain 400")

    unit_id = _integer(scope_raw["unit_id"], "scope.unit_id")
    function_code = _integer(scope_raw["function_code"], "scope.function_code")
    if function_code not in READ_FUNCTION_CODES or function_code != APPROVED_FUNCTION_CODE:
        raise ProbeError("scope.function_code must be approved read-only FC3")
    if unit_id != APPROVED_UNIT_ID:
        raise ProbeError("scope.unit_id is outside the approved scope")
    requests_raw = scope_raw["requests"]
    if not isinstance(requests_raw, list):
        raise ProbeError("scope.requests must be an array")
    requests = tuple(
        Request(
            start_address=_integer(
                request["start_address"], f"scope.requests[{index}].start_address"
            ),
            register_count=_integer(
                request["register_count"], f"scope.requests[{index}].register_count"
            ),
            requires_previous_valid=cast(bool, request["requires_previous_valid"]),
        )
        for index, value in enumerate(requests_raw)
        for request in (
            _object(
                value,
                f"scope.requests[{index}]",
                {"start_address", "register_count", "requires_previous_valid"},
            ),
        )
    )
    if any(type(request["requires_previous_valid"]) is not bool for request in requests_raw):
        raise ProbeError("requires_previous_valid must be boolean")
    if (
        tuple(
            (request.start_address, request.register_count, request.requires_previous_valid)
            for request in requests
        )
        != APPROVED_REQUESTS
    ):
        raise ProbeError("scope.requests do not match the approved ordered register ranges")

    budget = Budget(
        max_requests=_integer(budget_raw["max_requests"], "budget.max_requests"),
        max_retries_per_request=_integer(
            budget_raw["max_retries_per_request"], "budget.max_retries_per_request"
        ),
        min_interval_ms=_integer(budget_raw["min_interval_ms"], "budget.min_interval_ms"),
        max_response_bytes=_integer(budget_raw["max_response_bytes"], "budget.max_response_bytes"),
    )
    if budget.max_requests != APPROVED_MAX_REQUESTS:
        raise ProbeError("budget.max_requests must remain 4")
    if budget.max_retries_per_request != APPROVED_RETRIES:
        raise ProbeError("budget.max_retries_per_request must remain 1")
    if budget.min_interval_ms != APPROVED_INTERVAL_MS:
        raise ProbeError("budget.min_interval_ms must remain 500")
    largest_response = max(5 + 2 * request.register_count for request in requests)
    if largest_response > APPROVED_MAX_RESPONSE_BYTES:
        raise ProbeError("approved response does not fit the fixed receive budget")
    if budget.max_response_bytes != APPROVED_MAX_RESPONSE_BYTES:
        raise ProbeError("budget.max_response_bytes must remain 64")

    approval = Approval(
        scope_id=_text(approval_raw["scope_id"], "approval.scope_id"),
        approved_by=_text(approval_raw["approved_by"], "approval.approved_by"),
        approved_at=_text(approval_raw["approved_at"], "approval.approved_at"),
    )
    if approval.scope_id != APPROVED_SCOPE_ID:
        raise ProbeError("approval.scope_id does not match the executable scope")
    if "CHANGE_ME" in approval.approved_by.upper():
        raise ProbeError("approval.approved_by is unresolved")
    try:
        approved_at = datetime.fromisoformat(approval.approved_at.replace("Z", "+00:00"))
    except ValueError as error:
        raise ProbeError("approval.approved_at must be ISO-8601") from error
    if approved_at.tzinfo is None:
        raise ProbeError("approval.approved_at must include a timezone")
    if approved_at.astimezone(UTC) > datetime.now(UTC):
        raise ProbeError("approval.approved_at cannot be in the future")
    return ProbeConfig(adapter, settings, unit_id, function_code, requests, budget, approval)


def compute_crc16(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def request_frame(unit_id: int, function_code: int, request: Request) -> bytes:
    if unit_id != APPROVED_UNIT_ID or function_code != APPROVED_FUNCTION_CODE:
        raise ProbeError("request is outside the approved read-only scope")
    if (request.start_address, request.register_count, request.requires_previous_valid) not in (
        APPROVED_REQUESTS
    ):
        raise ProbeError("request range is outside the approved scope")
    body = bytes(
        (
            unit_id,
            function_code,
            request.start_address >> 8,
            request.start_address & 0xFF,
            request.register_count >> 8,
            request.register_count & 0xFF,
        )
    )
    crc = compute_crc16(body)
    return body + bytes((crc & 0xFF, crc >> 8))


def _crc_valid(frame: bytes) -> bool:
    if len(frame) < MIN_RTU_FRAME_BYTES:
        return False
    expected = compute_crc16(frame[:-2])
    return frame[-2:] == bytes((expected & 0xFF, expected >> 8))


def _frame_candidates(
    raw: bytes, request: Request, unit_id: int, function_code: int
) -> Iterator[tuple[int, int, bytes]]:
    expected_length = 5 + 2 * request.register_count
    for offset in range(len(raw)):
        for length in (EXCEPTION_FRAME_BYTES, expected_length):
            end = offset + length
            if end <= len(raw):
                yield offset, end, raw[offset:end]


def classify_response(  # noqa: PLR0911
    raw: bytes, request: Request, unit_id: int, function_code: int
) -> dict[str, object]:
    base: dict[str, object] = {"rx_hex": raw.hex(), "rx_bytes": len(raw), "crc_valid": False}
    if not raw:
        return {**base, "classification": "timeout"}
    matching: list[tuple[int, int, bytes, str]] = []
    crc_frames: list[bytes] = []
    for start, end, frame in _frame_candidates(raw, request, unit_id, function_code):
        if not _crc_valid(frame):
            continue
        crc_frames.append(frame)
        if frame[0] != unit_id:
            continue
        if frame[1] == function_code | 0x80 and len(frame) == EXCEPTION_FRAME_BYTES:
            matching.append((start, end, frame, "modbus_exception"))
        elif (
            frame[1] == function_code
            and len(frame) == 5 + 2 * request.register_count
            and frame[2] == 2 * request.register_count
        ):
            matching.append((start, end, frame, "valid"))
    if matching:
        start, end, frame, classification = matching[0]
        if start != 0 or end != len(raw) or len(matching) != 1:
            return {
                **base,
                "classification": "noise",
                "crc_valid": True,
                "embedded_frame_hex": frame.hex(),
                "response_crc_hex": frame[-2:].hex(),
                "noise_prefix_hex": raw[:start].hex(),
                "noise_suffix_hex": raw[end:].hex(),
            }
        if classification == "modbus_exception":
            if frame[2] not in STANDARD_EXCEPTION_CODES:
                return {
                    **base,
                    "classification": "invalid_exception",
                    "crc_valid": True,
                    "exception_code": frame[2],
                    "response_crc_hex": frame[-2:].hex(),
                }
            return {
                **base,
                "classification": classification,
                "crc_valid": True,
                "exception_code": frame[2],
                "response_crc_hex": frame[-2:].hex(),
            }
        registers = [
            int.from_bytes(frame[index : index + 2], "big") for index in range(3, len(frame) - 2, 2)
        ]
        return {
            **base,
            "classification": "valid",
            "crc_valid": True,
            "registers": registers,
            "response_crc_hex": frame[-2:].hex(),
            "conclusion": CONCLUSION,
        }
    if crc_frames:
        return {**base, "classification": "mismatch", "crc_valid": True}
    if len(raw) < EXCEPTION_FRAME_BYTES:
        return {**base, "classification": "truncated"}
    return {**base, "classification": "crc_error"}


def _read_usb_value(start: Path, name: str) -> str | None:
    current = start
    for _ in range(10):
        candidate = current / name
        try:
            if candidate.is_file():
                return candidate.read_text(encoding="ascii").strip()
        except OSError:
            return None
        if current.parent == current:
            break
        current = current.parent
    return None


def _linux_device_numbers(device: int) -> tuple[int, int]:
    major = ((device >> 8) & 0xFFF) | ((device >> 32) & ~0xFFF)
    minor = (device & 0xFF) | ((device >> 12) & ~0xFF)
    return major, minor


def verify_open_file_identity(serial_port: SerialLike, adapter: Adapter) -> dict[str, str]:
    try:
        opened_rdev = int(getattr(os.fstat(serial_port.fileno()), "st_rdev", 0))
    except OSError as error:
        raise ProbeError(f"cannot inspect opened serial descriptor: {error}") from error
    if opened_rdev == 0:
        raise ProbeError("opened serial descriptor is not a device")
    major, minor = _linux_device_numbers(opened_rdev)
    sys_device = Path("/sys/dev/char") / f"{major}:{minor}"
    try:
        resolved = sys_device.resolve(strict=True)
    except OSError as error:
        raise ProbeError("opened serial descriptor has no sysfs device identity") from error
    identity = {
        "vendor_id": (_read_usb_value(resolved, "idVendor") or "").lower(),
        "product_id": (_read_usb_value(resolved, "idProduct") or "").lower(),
        "serial_number": _read_usb_value(resolved, "serial") or "",
        "sys_device": str(sys_device),
        "st_rdev": str(opened_rdev),
    }
    if (
        identity["vendor_id"] != adapter.vendor_id
        or identity["product_id"] != adapter.product_id
        or identity["serial_number"] != adapter.serial_number
    ):
        raise ProbeError("opened serial descriptor USB identity does not match approval")
    return identity


class AuditLog:
    def __init__(self, path: Path, run_id: str | None = None) -> None:
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except OSError as error:
            raise ProbeError(f"cannot create unique audit file: {error}") from error
        self._stream: IO[str] = os.fdopen(descriptor, "w", encoding="utf-8", newline="\n")
        self._run_id = run_id
        try:
            _fsync_parent_directory(path)
        except OSError as error:
            self._stream.close()
            raise ProbeError(f"cannot persist audit directory entry: {error}") from error

    def write(self, event: Mapping[str, object]) -> None:
        record = dict(event)
        if self._run_id is not None:
            record["run_id"] = self._run_id
        self._stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        self._stream.flush()
        os.fsync(self._stream.fileno())

    def close(self) -> None:
        self._stream.flush()
        os.fsync(self._stream.fileno())
        self._stream.close()


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _fsync_parent_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def normalized_plan(config: ProbeConfig) -> dict[str, object]:
    return {
        "adapter": asdict(config.adapter),
        "serial": asdict(config.serial),
        "scope": {
            "unit_id": config.unit_id,
            "function_code": config.function_code,
            "requests": [asdict(request) for request in config.requests],
        },
        "budget": asdict(config.budget),
        "approval": asdict(config.approval),
        "frames": [
            request_frame(config.unit_id, config.function_code, request).hex()
            for request in config.requests
        ],
    }


def _default_serial_factory(config: ProbeConfig) -> SerialLike:
    try:
        serial = importlib.import_module("serial")
    except ImportError as error:  # pragma: no cover - candidate image supplies pyserial
        raise ProbeError("pyserial is not installed in the authenticated runtime image") from error
    try:
        return cast(
            SerialLike,
            serial.Serial(
                port=config.adapter.device_path,
                baudrate=config.serial.baud_rate,
                bytesize=config.serial.data_bits,
                parity=config.serial.parity,
                stopbits=config.serial.stop_bits,
                timeout=0.05,
                write_timeout=config.serial.timeout_ms / 1000,
                exclusive=True,
            ),
        )
    except (OSError, ValueError, serial.SerialException) as error:
        raise ProbeError(f"cannot open serial port exclusively: {error}") from error


def _read_response(port: SerialLike, *, timeout_ms: int, max_bytes: int) -> bytes:
    deadline = time.monotonic() + timeout_ms / 1000
    last_byte_at: float | None = None
    received = bytearray()
    while time.monotonic() < deadline:
        waiting = min(max(port.in_waiting, 1), max_bytes - len(received))
        if waiting <= 0:
            break
        chunk = port.read(waiting)
        if chunk:
            received.extend(chunk)
            last_byte_at = time.monotonic()
            if len(received) >= max_bytes:
                break
        elif (
            last_byte_at is not None and time.monotonic() - last_byte_at >= INTER_BYTE_QUIET_SECONDS
        ):
            break
    return bytes(received)


def execute_probe(  # noqa: PLR0912, PLR0915
    config: ProbeConfig,
    audit: AuditLog,
    *,
    serial_factory: Callable[[ProbeConfig], SerialLike] | None = None,
) -> ProbeOutcome:
    port: SerialLike | None = None
    completed_tx = 0
    attempted_write_bytes = 0
    tx_count_known = True
    exit_code = 0
    result_name = "valid"
    failure: BaseException | None = None
    try:
        selected_serial_factory = serial_factory or _default_serial_factory
        port = selected_serial_factory(config)
        usb_identity = verify_open_file_identity(port, config.adapter)
        audit.write(
            {"event": "port_verified", "timestamp": _utc_now(), "usb_identity": usb_identity}
        )
        previous_valid = False
        last_response_at: float | None = None
        stop_plan = False
        for request_index, request in enumerate(config.requests):
            if request.requires_previous_valid and not previous_valid:
                break
            previous_valid = False
            for attempt in range(config.budget.max_retries_per_request + 1):
                if completed_tx >= config.budget.max_requests:
                    raise ProbeError("request budget exhausted")
                if last_response_at is not None:
                    remaining = config.budget.min_interval_ms / 1000 - (
                        time.monotonic() - last_response_at
                    )
                    if remaining > 0:
                        time.sleep(remaining)
                frame = request_frame(config.unit_id, config.function_code, request)
                port.reset_input_buffer()
                try:
                    written = port.write(frame)
                except BaseException:
                    tx_count_known = False
                    raise
                attempted_write_bytes += max(written, 0)
                if written != len(frame):
                    raise ProbeError(f"short serial write: wrote {written} of {len(frame)} bytes")
                completed_tx += 1
                audit.write(
                    {
                        "event": "request_tx",
                        "timestamp": _utc_now(),
                        "request_index": request_index,
                        "attempt": attempt,
                        "tx_number": completed_tx,
                        "function_code": config.function_code,
                        "start_address": request.start_address,
                        "register_count": request.register_count,
                        "tx_hex": frame.hex(),
                    }
                )
                port.flush()
                started = time.monotonic()
                raw = _read_response(
                    port,
                    timeout_ms=config.serial.timeout_ms,
                    max_bytes=config.budget.max_response_bytes,
                )
                last_response_at = time.monotonic()
                result = classify_response(raw, request, config.unit_id, config.function_code)
                audit.write(
                    {
                        "event": "response_rx",
                        "timestamp": _utc_now(),
                        "request_index": request_index,
                        "attempt": attempt,
                        "tx_number": completed_tx,
                        "latency_ms": round((last_response_at - started) * 1000, 3),
                        **result,
                    }
                )
                classification = result["classification"]
                if classification == "valid":
                    previous_valid = True
                    break
                if classification == "modbus_exception":
                    exit_code = 3
                    result_name = "modbus_exception"
                    stop_plan = True
                    break
            if stop_plan:
                break
            if not previous_valid:
                exit_code = 2
                result_name = "no_valid_response"
                break
    except BaseException as error:
        failure = error
    if port is not None:
        try:
            port.close()
        except BaseException as error:
            failure = error
    if failure is None:
        terminal: dict[str, object] = {
            "event": "completed",
            "timestamp": _utc_now(),
            "result": result_name,
            "completed_tx_count": completed_tx,
            "attempted_write_bytes": attempted_write_bytes,
            "tx_count_known": True,
        }
        if result_name == "valid":
            terminal["conclusion"] = CONCLUSION
        try:
            audit.write(terminal)
        except BaseException as error:
            failure = error
        else:
            return ProbeOutcome(
                exit_code, result_name, completed_tx, attempted_write_bytes, tx_count_known, True
            )
    assert failure is not None
    aborted_written = False
    try:
        audit.write(
            {
                "event": "aborted",
                "timestamp": _utc_now(),
                "reason": type(failure).__name__,
                "detail": str(failure),
                "completed_tx_count": completed_tx if tx_count_known else None,
                "attempted_write_bytes": attempted_write_bytes if tx_count_known else None,
                "tx_count_known": tx_count_known,
            }
        )
        aborted_written = True
    except BaseException:
        pass
    return ProbeOutcome(
        130 if isinstance(failure, KeyboardInterrupt | SystemExit) else 1,
        "aborted",
        completed_tx if tx_count_known else None,
        attempted_write_bytes if tx_count_known else None,
        tx_count_known,
        aborted_written,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument(
        "--execute", action="store_true", help="perform the approved read-only probe"
    )
    parser.add_argument("--audit-path", type=Path)
    parser.add_argument("--expected-config-sha256")
    parser.add_argument("--expected-script-sha256")
    parser.add_argument("--image-id")
    parser.add_argument("--approval-scope")
    parser.add_argument("--receipt-sha256")
    parser.add_argument("--run-id")
    return parser


def _print_terminal(outcome: ProbeOutcome) -> None:
    print(TERMINAL_PREFIX + json.dumps(asdict(outcome), sort_keys=True))


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    executing = bool(args.execute)
    outcome = ProbeOutcome(1, "blocked", 0, 0, True, False)
    audit: AuditLog | None = None
    try:
        config = load_config(args.config)
        config_hash = _sha256(args.config.read_bytes())
        script_hash = _sha256(Path(__file__).read_bytes())
        plan = normalized_plan(config)
        if not args.execute:
            print(
                json.dumps(
                    {
                        "mode": "dry-run",
                        "config_sha256": config_hash,
                        "script_sha256": script_hash,
                        "plan": plan,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            return 0
        if args.audit_path is None:
            raise ProbeError("--audit-path is required with --execute")
        expected_config = _validate_digest(
            _text(args.expected_config_sha256, "expected config hash"), "expected config hash"
        )
        expected_script = _validate_digest(
            _text(args.expected_script_sha256, "expected script hash"), "expected script hash"
        )
        image_id = _validate_digest(_text(args.image_id, "image ID"), "image ID", prefixed=True)
        receipt_sha256 = _validate_digest(
            _text(args.receipt_sha256, "receipt hash"), "receipt hash"
        )
        approval_scope = _text(args.approval_scope, "approval scope")
        run_id = _text(args.run_id, "run ID")
        try:
            parsed_run_id = uuid.UUID(run_id)
        except ValueError as error:
            raise ProbeError("run ID must be a canonical UUID") from error
        if str(parsed_run_id) != run_id:
            raise ProbeError("run ID must be a canonical UUID")
        if config_hash != expected_config or script_hash != expected_script:
            raise ProbeError("authenticated script/config SHA-256 mismatch")
        if approval_scope != config.approval.scope_id:
            raise ProbeError("runner approval scope does not match config")
        audit = AuditLog(args.audit_path, run_id)
        try:
            audit.write(
                {
                    "event": "run_started",
                    "timestamp": _utc_now(),
                    "mode": "execute",
                    "config_sha256": config_hash,
                    "script_sha256": script_hash,
                    "image_id": image_id,
                    "receipt_sha256": receipt_sha256,
                    "approval_scope": approval_scope,
                    "plan": plan,
                }
            )
            outcome = execute_probe(config, audit)
        except BaseException:
            raise
        try:
            audit.close()
        except BaseException:
            outcome = ProbeOutcome(
                1,
                "audit_close_failed",
                outcome.completed_tx_count,
                outcome.attempted_write_bytes,
                outcome.tx_count_known,
                False,
            )
        return outcome.exit_code
    except BaseException as error:
        print(f"[modbus-probe] BLOCKED: {error}", file=sys.stderr)
        return 1
    finally:
        if executing:
            _print_terminal(outcome)


if __name__ == "__main__":
    raise SystemExit(main())
