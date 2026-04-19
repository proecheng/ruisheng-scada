"""ModBus RTU frames dataclasses + encode/decode.

FC 支持：3/5/6/16 (标准) + 19(0x13) heartbeat (私有 §A.5) + 20/21/22
(低功耗/文件) + 100 (注册) + ExceptionResponse (fc|0x80).
"""

from __future__ import annotations

from dataclasses import dataclass

from ruisheng_gw.protocol.exceptions import ProtocolError
from ruisheng_gw.protocol.modbus_codec import (
    append_crc_to_frame,
    verify_crc16,
)

FC_READ_HOLDING_REGISTERS = 0x03
MIN_RESPONSE_BODY_LEN = 3


@dataclass(frozen=True)
class ReadHoldingRequest:
    """FC 3 request: read holding registers."""

    slave: int
    start_addr: int
    register_count: int


@dataclass(frozen=True)
class ReadHoldingResponse:
    """FC 3 response."""

    slave: int
    byte_count: int
    registers: tuple[int, ...]


def encode_read_holding_request(req: ReadHoldingRequest) -> bytes:
    """Encode FC 3 request as 8-byte RTU frame (6 body + 2 CRC)."""
    body = bytes(
        [
            req.slave & 0xFF,
            FC_READ_HOLDING_REGISTERS,
            (req.start_addr >> 8) & 0xFF,
            req.start_addr & 0xFF,
            (req.register_count >> 8) & 0xFF,
            req.register_count & 0xFF,
        ]
    )
    return append_crc_to_frame(body)


def decode_read_holding_response(raw: bytes) -> ReadHoldingResponse:
    """Decode FC 3 response frame; raises CRCMismatchError or ProtocolError on bad input."""
    verify_crc16(raw)
    body = raw[:-2]
    if len(body) < MIN_RESPONSE_BODY_LEN:
        raise ProtocolError("FC3 resp too short")
    slave = body[0]
    fc = body[1]
    if fc != FC_READ_HOLDING_REGISTERS:
        raise ProtocolError(f"expected FC 0x03, got 0x{fc:02X}")
    byte_count = body[2]
    if len(body) != MIN_RESPONSE_BODY_LEN + byte_count:
        raise ProtocolError(
            f"FC3 byte_count={byte_count} but body len={len(body) - MIN_RESPONSE_BODY_LEN}"
        )
    data = body[3:]
    if len(data) % 2 != 0:
        raise ProtocolError("FC3 data length not even")
    registers = tuple((data[i] << 8) | data[i + 1] for i in range(0, len(data), 2))
    return ReadHoldingResponse(slave=slave, byte_count=byte_count, registers=registers)


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
