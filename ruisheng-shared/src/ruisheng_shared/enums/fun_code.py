"""ModBus FunCode 枚举。对应 spec §A.4。"""
from __future__ import annotations

from enum import IntEnum


class FunCode(IntEnum):
    """ModBus RTU/TCP 功能码（保留成员，非保留的已砍）。"""

    READ_COILS = 1
    READ_DISCRETE = 2
    READ_HOLDING = 3
    READ_INPUT = 4
    WRITE_SINGLE_COIL = 5
    WRITE_SINGLE_REGISTER = 6
    WRITE_MULTIPLE_REGISTERS = 16
    ICCID_REPORT = 20
    REGISTER = 21
    REGISTER_LOW_POWER = 22
    HEARTBEAT = 0x19
    GENERIC_RESPONSE = 100

    @classmethod
    def normalize(cls, raw: int) -> FunCode:
        """归一化：FC13→FC3、FC26→FC6；未知码抛 ValueError。"""
        aliases = {13: cls.READ_HOLDING.value, 26: cls.WRITE_SINGLE_REGISTER.value}
        value = aliases.get(raw, raw)
        try:
            return cls(value)
        except ValueError as exc:
            raise ValueError(f"unknown FunCode {raw} (normalized to {value})") from exc
