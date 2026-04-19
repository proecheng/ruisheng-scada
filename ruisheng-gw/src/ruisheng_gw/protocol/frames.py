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
            f"FC3 byte_count={byte_count} but body len={len(body)-MIN_RESPONSE_BODY_LEN}"
        )
    data = body[3:]
    if len(data) % 2 != 0:
        raise ProtocolError("FC3 data length not even")
    registers = tuple((data[i] << 8) | data[i + 1] for i in range(0, len(data), 2))
    return ReadHoldingResponse(slave=slave, byte_count=byte_count, registers=registers)
