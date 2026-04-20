"""ModBus RTU 帧构造工具 + CRC16。对应 spec §A.3 + §A.4。"""

from __future__ import annotations

from ruisheng_shared.constants.protocol import CRC16_INIT, CRC16_POLYNOMIAL


def crc16(data: bytes) -> int:
    """ModBus RTU 标准 CRC16（多项式 0xA001，初始 0xFFFF）。返回 16 位整数。"""
    reg = CRC16_INIT
    for byte in data:
        reg ^= byte
        for _ in range(8):
            if reg & 0x0001:
                reg = (reg >> 1) ^ CRC16_POLYNOMIAL
            else:
                reg >>= 1
    return reg


def _append_crc(body: bytes) -> bytes:
    crc = crc16(body)
    return body + bytes([crc & 0xFF, (crc >> 8) & 0xFF])


def encode_register_frame(*, dev_ser_number: str, fw: str = "1.0.0", hw: str = "1.0") -> bytes:
    """FC 21 注册帧：[0xFE][15][DevSerNumber(24)][FwVer(5)][HwVer(3)][CRC]。"""
    ser = dev_ser_number.encode("ascii").ljust(24, b"\x00")[:24]
    fw_b = fw.encode("ascii").ljust(5, b"\x00")[:5]
    hw_b = hw.encode("ascii").ljust(3, b"\x00")[:3]
    body = b"\xfe\x15" + ser + fw_b + hw_b
    return _append_crc(body)


def encode_read_holding(*, slave: int, start: int, count: int) -> bytes:
    body = bytes([slave, 3]) + start.to_bytes(2, "big") + count.to_bytes(2, "big")
    return _append_crc(body)


def encode_read_holding_response(*, slave: int, values: list[int]) -> bytes:
    data = b"".join(v.to_bytes(2, "big", signed=False) for v in values)
    body = bytes([slave, 3, len(data)]) + data
    return _append_crc(body)


def encode_write_single_register(*, slave: int, reg: int, value: int) -> bytes:
    body = bytes([slave, 6]) + reg.to_bytes(2, "big") + value.to_bytes(2, "big", signed=False)
    return _append_crc(body)


def encode_heartbeat(*, slave: int, token: int) -> bytes:
    """FC 0x19 心跳帧（新约定）。"""
    body = bytes([slave, 0x19]) + token.to_bytes(4, "big")
    return _append_crc(body)
