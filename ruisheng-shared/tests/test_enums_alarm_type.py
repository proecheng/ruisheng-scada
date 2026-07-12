"""Spec §F + §4.2 — AlarmType 5 种类型，用字符串值存 DB CHECK。"""

from __future__ import annotations

import pytest
from ruisheng_shared.enums import AlarmType


def test_all_five_members() -> None:
    assert {t.value for t in AlarmType} == {">", "<", "=", "!=", "LX"}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (">", AlarmType.GT),
        ("<", AlarmType.LT),
        ("=", AlarmType.EQ),
        ("!=", AlarmType.NE),
        ("LX", AlarmType.LX),
    ],
)
def test_from_symbol(raw: str, expected: AlarmType) -> None:
    assert AlarmType(raw) is expected


def test_invalid_symbol_raises() -> None:
    with pytest.raises(ValueError):
        AlarmType(">=")  # 不在规范内
