"""PhoneAlarm 位域解码。对应 spec §F.4。

PhoneAlarm 是 16 位整数：
- bit 0 (0x0001): 触发电话报警
- bit 1 (0x0002): 恢复电话报警
- bit 8-11:      触发时动作码（0–4）
- bit 12-15:     恢复时动作码（0–4）
"""

from __future__ import annotations

from enum import IntEnum


class AlarmAction(IntEnum):
    """触发/恢复时的继电器动作码（0–4）。"""

    NONE = 0
    ALL_ON = 1
    ALL_OFF = 2
    CHANNEL_ON = 3
    CHANNEL_OFF = 4

    @classmethod
    def decode_phone_alarm(cls, phone_alarm: int) -> tuple[AlarmAction, AlarmAction]:
        """返回 (trigger_action, reset_action)。"""
        trig = cls((phone_alarm >> 8) & 0xF)
        reset = cls((phone_alarm >> 12) & 0xF)
        return trig, reset

    @staticmethod
    def decode_flags(phone_alarm: int) -> tuple[bool, bool]:
        """返回 (call_on_trigger, call_on_reset)。"""
        return bool(phone_alarm & 0x0001), bool(phone_alarm & 0x0002)

    @staticmethod
    def encode_phone_alarm(
        *,
        call_on_trigger: bool,
        call_on_reset: bool,
        trigger_action: AlarmAction,
        reset_action: AlarmAction,
    ) -> int:
        v = 0
        if call_on_trigger:
            v |= 0x0001
        if call_on_reset:
            v |= 0x0002
        v |= (int(trigger_action) & 0xF) << 8
        v |= (int(reset_action) & 0xF) << 12
        return v
