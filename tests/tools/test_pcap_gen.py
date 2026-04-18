"""Spec §A.3 — CRC16 验证向量 + §A.4 注册帧。"""

from __future__ import annotations

import pytest
from pcap_gen.modbus_frames import crc16, encode_read_holding, encode_register_frame


@pytest.mark.parametrize(
    ("body", "expected_lo_hi"),
    [
        # Plan bug #14 fix (v1.1)：原 "0103000000020" + "2" = 14 hex chars = 7 bytes (01 03 00 00 00 02 02)，
        # CRC=0x528B 与断言 (0xC4, 0x0B) 不符。改为 12 hex chars = 6 bytes 的标准 ModBus 向量。
        (
            bytes.fromhex("010300000002"),
            (0xC4, 0x0B),
        ),  # 01 03 00 00 00 02 -> CRC=0x0BC4, wire lo=C4 hi=0B
    ],
)
def test_crc16_standard_vectors(body: bytes, expected_lo_hi: tuple[int, int]) -> None:
    crc = crc16(body)
    assert crc & 0xFF == expected_lo_hi[0]
    assert (crc >> 8) & 0xFF == expected_lo_hi[1]


def test_encode_register_frame_length() -> None:
    frame = encode_register_frame(dev_ser_number="DEMO-SN-0001")
    # 0xFE + 0x15 + 24B + 5B + 3B + CRC(2) = 36 bytes
    assert len(frame) == 2 + 24 + 5 + 3 + 2


def test_encode_read_holding() -> None:
    frame = encode_read_holding(slave=1, start=0, count=2)
    assert frame[0] == 1
    assert frame[1] == 3  # FC
    assert int.from_bytes(frame[2:4], "big") == 0
    assert int.from_bytes(frame[4:6], "big") == 2
    assert len(frame) == 8
