"""ExceptionResponse (fc|0x80) + FC 0x19 heartbeat + FC 22 (0x16) low-power register."""

from __future__ import annotations

from ruisheng_gw.protocol.frames import (
    ExceptionResponse,
    HeartbeatFrame,
    LowPowerRegisterFrame,
    decode_frame_by_funcode,
    encode_heartbeat,
)
from ruisheng_gw.protocol.modbus_codec import append_crc_to_frame

SLAVE_ID = 1
FC_READ_HOLDING = 0x03
ERROR_CODE_ILLEGAL_ADDRESS = 0x02
EXCEPTION_FC = FC_READ_HOLDING | 0x80
FC_HEARTBEAT = 0x19
FC_LOW_POWER = 0x16
PAYLOAD_LEN = 10


def test_exception_response_decoded_when_high_bit_set() -> None:
    # slave=1 fc=0x83 (FC 3 + 0x80 = exception for FC 3) errcode=0x02 + CRC
    body = bytes([SLAVE_ID, EXCEPTION_FC, ERROR_CODE_ILLEGAL_ADDRESS])
    frame = append_crc_to_frame(body)
    decoded = decode_frame_by_funcode(frame)
    assert isinstance(decoded, ExceptionResponse)
    assert decoded.slave == SLAVE_ID
    assert decoded.original_fc == FC_READ_HOLDING
    assert decoded.error_code == ERROR_CODE_ILLEGAL_ADDRESS


def test_heartbeat_fc_0x19_round_trip() -> None:
    frame = encode_heartbeat(slave=SLAVE_ID)
    decoded = decode_frame_by_funcode(frame)
    assert isinstance(decoded, HeartbeatFrame)
    assert decoded.slave == SLAVE_ID


def test_fc_22_low_power_register_decoded() -> None:
    # FC 22 = 0x16, per spec §A.4 this is 低功耗注册 (NOT file records)
    # placeholder body shape; actual vendor-specific may vary
    body = bytes([SLAVE_ID, FC_LOW_POWER]) + bytes(PAYLOAD_LEN)
    frame = append_crc_to_frame(body)
    decoded = decode_frame_by_funcode(frame)
    assert isinstance(decoded, LowPowerRegisterFrame)
    assert decoded.slave == SLAVE_ID
    assert decoded.payload == bytes(PAYLOAD_LEN)
