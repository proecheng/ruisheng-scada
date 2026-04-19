"""FC 5 / 6 / 16 write codecs."""

from __future__ import annotations

from ruisheng_gw.protocol.frames import (
    WriteMultipleHoldingRequest,
    WriteSingleCoilRequest,
    WriteSingleHoldingRequest,
    encode_write_multiple_holding,
    encode_write_single_coil,
    encode_write_single_holding,
)

FC_WRITE_SINGLE_HOLDING = 0x06
FC_WRITE_MULTIPLE_HOLDING = 0x10
SLAVE_ID = 0x01
REGISTER_COUNT_2 = 0x02
BYTE_COUNT_4 = 0x04


def test_fc5_write_coil_on() -> None:
    req = WriteSingleCoilRequest(slave=1, addr=0, value=True)
    raw = encode_write_single_coil(req)
    # slave=01 fc=05 addr=0000 val=FF00 → 01 05 00 00 FF 00 + CRC
    assert raw[:6] == bytes.fromhex("01050000FF00")


def test_fc5_write_coil_off_uses_0x0000() -> None:
    req = WriteSingleCoilRequest(slave=1, addr=0, value=False)
    raw = encode_write_single_coil(req)
    assert raw[4:6] == bytes([0x00, 0x00])


def test_fc6_write_single_holding() -> None:
    req = WriteSingleHoldingRequest(slave=1, addr=0, value=10)
    raw = encode_write_single_holding(req)
    # slave=01 fc=06 addr=0000 val=000A → 01 06 00 00 00 0A + CRC
    assert raw[:6] == bytes.fromhex("01060000000A")


def test_fc16_write_multiple_holding_2_values() -> None:
    req = WriteMultipleHoldingRequest(slave=1, start_addr=0, values=(10, 20))
    raw = encode_write_multiple_holding(req)
    # slave=01 fc=10 start=0000 count=0002 byte_count=04 data=[00 0A 00 14] + CRC
    body = raw[:-2]
    assert body[0] == SLAVE_ID
    assert body[1] == FC_WRITE_MULTIPLE_HOLDING
    assert body[5] == REGISTER_COUNT_2  # count_lo = 2
    assert body[6] == BYTE_COUNT_4  # byte_count = 4
    assert body[7:11] == bytes([0x00, 0x0A, 0x00, 0x14])
