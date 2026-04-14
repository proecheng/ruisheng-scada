"""Spec §A.3 + §B.1 + §D.1 — 协议常量。"""

from __future__ import annotations

from ruisheng_shared.constants.protocol import (
    CRC16_INIT,
    CRC16_POLYNOMIAL,
    DEVICE_REGISTER_TCP_PORT,
    DEVICE_TELEMETRY_TCP_PORT,
    FRAME_MAX_LENGTH,
    FRAME_SILENCE_MS,
    HEARTBEAT_INTERVAL_S,
    MODBUS_BROADCAST_ADDR,
    MODBUS_MAX_SLAVE_ADDR,
    MODBUS_MIN_SLAVE_ADDR,
)


def test_crc16_standard_polynomial() -> None:
    assert CRC16_POLYNOMIAL == 0xA001
    assert CRC16_INIT == 0xFFFF


def test_port_assignments() -> None:
    assert DEVICE_REGISTER_TCP_PORT == 6000
    assert DEVICE_TELEMETRY_TCP_PORT == 6020


def test_frame_limits() -> None:
    assert FRAME_MAX_LENGTH == 4096
    assert FRAME_SILENCE_MS == 200


def test_heartbeat_default_period() -> None:
    assert HEARTBEAT_INTERVAL_S == 30


def test_modbus_address_range() -> None:
    assert MODBUS_BROADCAST_ADDR == 0
    assert MODBUS_MIN_SLAVE_ADDR == 1
    assert MODBUS_MAX_SLAVE_ADDR == 247
