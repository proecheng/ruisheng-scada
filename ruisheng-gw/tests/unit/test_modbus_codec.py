"""CRC16(0xA001) + wire byte order (lo first, hi second)."""

from __future__ import annotations

import pytest
from ruisheng_gw.protocol.exceptions import CRCMismatchError, FramingError
from ruisheng_gw.protocol.modbus_codec import (
    append_crc_to_frame,
    compute_crc16,
    verify_crc16,
)


def test_crc_vector_01_read_holding() -> None:
    # Official Modbus Appendix B vector:
    # 01 03 00 00 00 02 → CRC = 0x0BC4 (wire: C4 0B)
    assert compute_crc16(bytes.fromhex("010300000002")) == 0x0BC4  # noqa: PLR2004


def test_crc_empty_bytes_equals_0x_ffff() -> None:  # noqa: N802
    assert compute_crc16(b"") == 0xFFFF  # noqa: PLR2004


def test_crc_single_byte_00() -> None:
    assert compute_crc16(b"\x00") == 0x40BF  # noqa: PLR2004


def test_wire_byte_order_is_lo_then_hi() -> None:
    # CRC 0x0BC4 on wire should be [0xC4, 0x0B]
    frame_no_crc = bytes.fromhex("010300000002")
    with_crc = append_crc_to_frame(frame_no_crc)
    assert with_crc[-2:] == bytes([0xC4, 0x0B])


def test_verify_crc_ok() -> None:
    frame = bytes.fromhex("010300000002") + bytes([0xC4, 0x0B])
    verify_crc16(frame)  # no raise


def test_verify_crc_mismatch_raises() -> None:
    frame = bytes.fromhex("010300000002") + bytes([0x00, 0x00])
    with pytest.raises(CRCMismatchError):
        verify_crc16(frame)


def test_verify_crc_frame_too_short_raises_framing_error() -> None:
    with pytest.raises(FramingError):
        verify_crc16(b"\x01\x03")  # 2 bytes, below MIN_FRAME_LENGTH=3
