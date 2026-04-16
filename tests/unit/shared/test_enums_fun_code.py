"""Spec §A.4 — FunCode 枚举必须覆盖新系统保留的全部码，且值为 int。"""

from __future__ import annotations

import pytest
from ruisheng_shared.enums import FunCode


def test_funcode_standard_values() -> None:
    assert FunCode.READ_COILS == 1
    assert FunCode.READ_DISCRETE == 2
    assert FunCode.READ_HOLDING == 3
    assert FunCode.READ_INPUT == 4
    assert FunCode.WRITE_SINGLE_COIL == 5
    assert FunCode.WRITE_SINGLE_REGISTER == 6
    assert FunCode.WRITE_MULTIPLE_REGISTERS == 16


def test_funcode_private_values() -> None:
    # 私有扩展，spec §A.4
    assert FunCode.ICCID_REPORT == 20
    assert FunCode.REGISTER == 21
    assert FunCode.REGISTER_LOW_POWER == 22
    assert FunCode.HEARTBEAT == 0x19  # 25
    assert FunCode.GENERIC_RESPONSE == 100


def test_funcode_aliases_collapsed_to_parents() -> None:
    """FC 13 / 26 是 3 / 6 的变种别名，不单独成员（§11.1）"""
    assert not hasattr(FunCode, "READ_HOLDING_VARIANT")
    assert not hasattr(FunCode, "WRITE_SINGLE_REGISTER_VARIANT")


def test_funcode_removed() -> None:
    """FC 7 / 12 已砍（§11.1 D7）"""
    assert not hasattr(FunCode, "REQUEST_SERVICE")
    assert not hasattr(FunCode, "REGISTER_SYNC")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (3, FunCode.READ_HOLDING),
        (13, FunCode.READ_HOLDING),  # 别名合并
        (6, FunCode.WRITE_SINGLE_REGISTER),
        (26, FunCode.WRITE_SINGLE_REGISTER),  # 别名合并
    ],
)
def test_funcode_normalize_aliases(raw: int, expected: FunCode) -> None:
    """FunCode.normalize(raw_byte) 把 13 映射为 READ_HOLDING，26 映射为 WRITE_SINGLE_REGISTER"""
    assert FunCode.normalize(raw) is expected


def test_funcode_normalize_unknown_raises() -> None:
    with pytest.raises(ValueError, match="unknown FunCode"):
        FunCode.normalize(7)  # 已砍 → 拒绝
