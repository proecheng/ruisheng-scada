"""ModBus RTU frames dataclasses + encode/decode.

FC 支持：3/5/6/16 (标准) + 25(0x19) heartbeat (私有 §A.5) + 20/21/22
(低功耗/文件) + 100 (注册) + ExceptionResponse (fc|0x80).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from ruisheng_gw.protocol.exceptions import PrivateCodeNotImplemented, ProtocolError
from ruisheng_gw.protocol.modbus_codec import (
    append_crc_to_frame,
    verify_crc16,
)

FC_READ_HOLDING_REGISTERS = 0x03
FC_READ_COILS = 0x01
FC_READ_DISCRETE_INPUTS = 0x02
FC_READ_INPUT_REGISTERS = 0x04
FC_HEARTBEAT = 0x19
FC_DEVICE_REGISTER = 0x15
FC_LOW_POWER_REGISTER = 0x16
FC_PRIVATE_13 = 0x0D
FC_PRIVATE_26 = 0x1A
MIN_RESPONSE_BODY_LEN = 3
MIN_FRAME_BODY_LEN = 2
MIN_EXCEPTION_BODY_LEN = 3
EXCEPTION_BIT = 0x80
FC_MASK = 0x7F
REGISTER_BODY_LEN = 34
REGISTER_SER_LEN = 24
REGISTER_FW_LEN = 5
REGISTER_HW_LEN = 3


@dataclass(frozen=True)
class ReadHoldingRequest:
    """Modbus read request for FC 1/2/3/4."""

    slave: int
    start_addr: int
    register_count: int
    fun_code: int = FC_READ_HOLDING_REGISTERS


@dataclass(frozen=True)
class ReadHoldingResponse:
    """Modbus read response for FC 1/2/3/4.

    `registers` is populated for FC3/FC4. `bits` is populated for FC1/FC2.
    """

    slave: int
    byte_count: int
    registers: tuple[int, ...] = ()
    bits: tuple[int, ...] = ()
    fun_code: int = FC_READ_HOLDING_REGISTERS


def encode_read_holding_request(req: ReadHoldingRequest) -> bytes:
    """Encode FC 1/2/3/4 read request as 8-byte RTU frame."""
    if req.fun_code not in (
        FC_READ_COILS,
        FC_READ_DISCRETE_INPUTS,
        FC_READ_HOLDING_REGISTERS,
        FC_READ_INPUT_REGISTERS,
    ):
        raise ProtocolError(f"unsupported read FC 0x{req.fun_code:02X}")
    body = bytes(
        [
            req.slave & 0xFF,
            req.fun_code & 0xFF,
            (req.start_addr >> 8) & 0xFF,
            req.start_addr & 0xFF,
            (req.register_count >> 8) & 0xFF,
            req.register_count & 0xFF,
        ]
    )
    return append_crc_to_frame(body)


def decode_read_holding_response(raw: bytes) -> ReadHoldingResponse:
    """Decode FC 1/2/3/4 response frame; raises on bad input."""
    verify_crc16(raw)
    body = raw[:-2]
    if len(body) < MIN_RESPONSE_BODY_LEN:
        raise ProtocolError("read response too short")
    slave = body[0]
    fc = body[1]
    if fc not in (
        FC_READ_COILS,
        FC_READ_DISCRETE_INPUTS,
        FC_READ_HOLDING_REGISTERS,
        FC_READ_INPUT_REGISTERS,
    ):
        raise ProtocolError(f"expected read FC 0x01/0x02/0x03/0x04, got 0x{fc:02X}")
    byte_count = body[2]
    if len(body) != MIN_RESPONSE_BODY_LEN + byte_count:
        raise ProtocolError(f"read byte_count={byte_count} but body len={len(body) - 3}")
    data = body[3:]
    if fc in (FC_READ_COILS, FC_READ_DISCRETE_INPUTS):
        bits = tuple((byte >> bit) & 0x01 for byte in data for bit in range(8))
        return ReadHoldingResponse(slave=slave, fun_code=fc, byte_count=byte_count, bits=bits)
    if len(data) % 2 != 0:
        raise ProtocolError("read register data length not even")
    registers = tuple((data[i] << 8) | data[i + 1] for i in range(0, len(data), 2))
    return ReadHoldingResponse(
        slave=slave,
        fun_code=fc,
        byte_count=byte_count,
        registers=registers,
    )


@dataclass(frozen=True)
class WriteSingleCoilRequest:
    """FC 5 request: write single coil."""

    slave: int
    addr: int
    value: bool


@dataclass(frozen=True)
class WriteSingleHoldingRequest:
    """FC 6 request: write single holding register."""

    slave: int
    addr: int
    value: int


@dataclass(frozen=True)
class WriteMultipleHoldingRequest:
    """FC 16 (0x10) request: write multiple holding registers."""

    slave: int
    start_addr: int
    values: tuple[int, ...]


def encode_write_single_coil(req: WriteSingleCoilRequest) -> bytes:
    """Encode FC 5 request: bool → 0xFF00 (ON) or 0x0000 (OFF)."""
    data = bytes([0xFF, 0x00]) if req.value else bytes([0x00, 0x00])
    body = (
        bytes(
            [
                req.slave & 0xFF,
                0x05,
                (req.addr >> 8) & 0xFF,
                req.addr & 0xFF,
            ]
        )
        + data
    )
    return append_crc_to_frame(body)


def encode_write_single_holding(req: WriteSingleHoldingRequest) -> bytes:
    """Encode FC 6 request: single register as big-endian uint16."""
    body = bytes(
        [
            req.slave & 0xFF,
            0x06,
            (req.addr >> 8) & 0xFF,
            req.addr & 0xFF,
            (req.value >> 8) & 0xFF,
            req.value & 0xFF,
        ]
    )
    return append_crc_to_frame(body)


def encode_write_multiple_holding(req: WriteMultipleHoldingRequest) -> bytes:
    """Encode FC 16 (0x10) request: count + byte_count + N×2B big-endian data."""
    count = len(req.values)
    byte_count = count * 2
    data = bytearray()
    for v in req.values:
        data.append((v >> 8) & 0xFF)
        data.append(v & 0xFF)
    body = bytes(
        [
            req.slave & 0xFF,
            0x10,
            (req.start_addr >> 8) & 0xFF,
            req.start_addr & 0xFF,
            (count >> 8) & 0xFF,
            count & 0xFF,
            byte_count & 0xFF,
        ]
    ) + bytes(data)
    return append_crc_to_frame(body)


@dataclass(frozen=True)
class ExceptionResponse:
    """Slave returned fc|0x80 + 1-byte error code (§ModBus spec)."""

    slave: int
    original_fc: int  # fc without 0x80 bit
    error_code: int  # 01 illegal-fc / 02 illegal-addr / 03 illegal-val / 04 slave-fail / ...


@dataclass(frozen=True)
class HeartbeatFrame:
    """FC 0x19 (25) heartbeat per spec §A.5. Mandatory for online detection."""

    slave: int


@dataclass(frozen=True)
class LowPowerRegisterFrame:
    """FC 22 (0x16) 低功耗注册（私有） per spec §A.4. NOT file records."""

    slave: int
    payload: bytes


@dataclass(frozen=True)
class RegisterFrame:
    """FC 21 (0x15) TCP device registration frame."""

    slave: int
    dev_ser_number: str
    fw_version: str
    hw_version: str


AnyFrame: TypeAlias = (
    ExceptionResponse | ReadHoldingResponse | HeartbeatFrame | LowPowerRegisterFrame | RegisterFrame
)


def encode_exception_response(resp: ExceptionResponse) -> bytes:
    """Encode ExceptionResponse as 5-byte RTU frame."""
    body = bytes([resp.slave & 0xFF, (resp.original_fc | 0x80) & 0xFF, resp.error_code & 0xFF])
    return append_crc_to_frame(body)


def encode_heartbeat(slave: int) -> bytes:
    """Encode FC 0x19 heartbeat request (4 bytes: slave + fc + CRC)."""
    body = bytes([slave & 0xFF, 0x19])
    return append_crc_to_frame(body)


def _decode_ascii_field(raw: bytes) -> str:
    return raw.split(b"\x00", 1)[0].decode("ascii", errors="ignore").strip()


def decode_register_frame(raw: bytes) -> RegisterFrame:
    """Decode FC 21 registration: [0xFE][0x15][ser24][fw5][hw3][CRC]."""
    verify_crc16(raw)
    body = raw[:-2]
    if len(body) != REGISTER_BODY_LEN:
        raise ProtocolError(f"FC21 register body len={len(body)}, expected {REGISTER_BODY_LEN}")
    slave = body[0]
    fc = body[1]
    if fc != FC_DEVICE_REGISTER:
        raise ProtocolError(f"expected FC 0x15, got 0x{fc:02X}")
    ser_start = 2
    fw_start = ser_start + REGISTER_SER_LEN
    hw_start = fw_start + REGISTER_FW_LEN
    return RegisterFrame(
        slave=slave,
        dev_ser_number=_decode_ascii_field(body[ser_start:fw_start]),
        fw_version=_decode_ascii_field(body[fw_start:hw_start]),
        hw_version=_decode_ascii_field(body[hw_start : hw_start + REGISTER_HW_LEN]),
    )


def decode_frame_by_funcode(
    raw: bytes,
) -> AnyFrame:
    """Top-level dispatch by FC. ExceptionResponse check (fc & 0x80) comes first."""
    verify_crc16(raw)
    body = raw[:-2]
    if len(body) < MIN_FRAME_BODY_LEN:
        raise ProtocolError("frame too short (no slave+fc)")
    slave = body[0]
    fc = body[1]
    # v2 A2 — Exception response (high bit set); check BEFORE specific FC dispatch
    if fc & EXCEPTION_BIT:
        if len(body) < MIN_EXCEPTION_BODY_LEN:
            raise ProtocolError("exception response missing error code")
        return ExceptionResponse(
            slave=slave,
            original_fc=fc & FC_MASK,
            error_code=body[2],
        )
    if fc in (
        FC_READ_COILS,
        FC_READ_DISCRETE_INPUTS,
        FC_READ_HOLDING_REGISTERS,
        FC_READ_INPUT_REGISTERS,
    ):
        return decode_read_holding_response(raw)
    if fc == FC_HEARTBEAT:
        return HeartbeatFrame(slave=slave)
    if fc == FC_DEVICE_REGISTER:
        return decode_register_frame(raw)
    if fc == FC_LOW_POWER_REGISTER:
        return LowPowerRegisterFrame(slave=slave, payload=bytes(body[2:]))
    # FC 13 (0x0D) / 26 (0x1A) — B5 task placeholder
    if fc in (FC_PRIVATE_13, FC_PRIVATE_26):
        raise PrivateCodeNotImplemented(f"private fc 0x{fc:02X} not yet implemented; see B5")
    raise ProtocolError(f"unknown FC 0x{fc:02X}")
