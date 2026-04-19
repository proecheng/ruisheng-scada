"""ModBus RTU-on-TCP codec: CRC16 + wire byte order.

RTU polynomial 0xA001 (bit-reversed 0x8005). Wire byte order: LO first, HI second.

NOTE: This module is named `modbus_codec` (not `modbus_rtu`) because over
DTU transparent-mode TCP there is no silent-period framing — framer.py
performs length-aware dispatch instead.
"""

from __future__ import annotations

from ruisheng_gw.protocol.exceptions import CRCMismatchError

MIN_FRAME_LENGTH = 3


def compute_crc16(data: bytes) -> int:
    """Compute ModBus RTU CRC16(poly=0xA001) over `data`. Return as integer."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc & 0xFFFF


def append_crc_to_frame(frame_no_crc: bytes) -> bytes:
    """Append 2-byte CRC in LO-HI wire order."""
    crc = compute_crc16(frame_no_crc)
    return frame_no_crc + bytes([crc & 0xFF, (crc >> 8) & 0xFF])


def verify_crc16(frame_with_crc: bytes) -> None:
    """Verify a frame including 2-byte trailing CRC (wire LO-HI). Raise on mismatch."""
    if len(frame_with_crc) < MIN_FRAME_LENGTH:
        raise CRCMismatchError("frame too short")
    body = frame_with_crc[:-2]
    wire_lo, wire_hi = frame_with_crc[-2], frame_with_crc[-1]
    expected = compute_crc16(body)
    got = wire_lo | (wire_hi << 8)
    if got != expected:
        raise CRCMismatchError(f"CRC mismatch: expected 0x{expected:04X}, got 0x{got:04X}")
