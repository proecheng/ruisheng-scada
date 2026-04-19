"""FunCode 3 (read holding registers) request + response encode/decode."""

from __future__ import annotations

import pytest
from ruisheng_gw.protocol.exceptions import CRCMismatchError
from ruisheng_gw.protocol.frames import (
    ReadHoldingRequest,
    decode_read_holding_response,
    encode_read_holding_request,
)
from ruisheng_gw.protocol.modbus_codec import append_crc_to_frame


def test_encode_read_holding_request_bytes() -> None:
    # slave=1, start=0, count=2 → 01 03 00 00 00 02 + CRC
    req = ReadHoldingRequest(slave=1, start_addr=0, register_count=2)
    raw = encode_read_holding_request(req)
    assert raw[:6] == bytes.fromhex("010300000002")
    assert len(raw) == 8  # noqa: PLR2004 # 6 body + 2 CRC


def test_decode_read_holding_response_2_registers() -> None:
    # slave=1 fc=3 bytecount=4 data=[0x00,0x0A,0x00,0x14] + CRC
    body = bytes.fromhex("010304000A0014")
    frame = append_crc_to_frame(body)
    resp = decode_read_holding_response(frame)
    assert resp.slave == 1
    assert resp.byte_count == 4  # noqa: PLR2004
    assert resp.registers == [10, 20]


def test_decode_response_crc_mismatch_raises() -> None:
    bad = bytes.fromhex("010304000A0014") + bytes([0x00, 0x00])
    with pytest.raises(CRCMismatchError):
        decode_read_holding_response(bad)
