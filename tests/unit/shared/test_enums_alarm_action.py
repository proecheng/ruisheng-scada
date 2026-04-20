"""Spec §F.4 — PhoneAlarm 高 8/12 位的动作码。"""

from __future__ import annotations

from ruisheng_shared.enums import AlarmAction


def test_values() -> None:
    assert AlarmAction.NONE == 0
    assert AlarmAction.ALL_ON == 1
    assert AlarmAction.ALL_OFF == 2
    assert AlarmAction.CHANNEL_ON == 3
    assert AlarmAction.CHANNEL_OFF == 4


def test_decode_phone_alarm() -> None:
    """0x0103 = trigger call + trigger all-on"""
    trig, reset = AlarmAction.decode_phone_alarm(0x0103)
    assert trig == AlarmAction.ALL_ON
    assert reset == AlarmAction.NONE


def test_encode_phone_alarm() -> None:
    # 触发电话 + 恢复电话 + 触发全开 + 恢复全关 → 0x2103
    v = AlarmAction.encode_phone_alarm(
        call_on_trigger=True,
        call_on_reset=True,
        trigger_action=AlarmAction.ALL_ON,
        reset_action=AlarmAction.ALL_OFF,
    )
    assert v == 0x2103
