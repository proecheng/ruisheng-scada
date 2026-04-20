"""Spec §3.6 — 四级 RBAC Authority 枚举 + 分级比较。"""

from __future__ import annotations

import pytest
from ruisheng_shared.enums import Authority


def test_values() -> None:
    assert Authority.USER == "User"
    assert Authority.COMPANY == "Company"
    assert Authority.GROUP_COMPANY == "GroupCompany"
    assert Authority.ADMIN == "Administrators"


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        (Authority.USER, Authority.COMPANY, True),
        (Authority.COMPANY, Authority.GROUP_COMPANY, True),
        (Authority.ADMIN, Authority.USER, False),
        (Authority.ADMIN, Authority.ADMIN, False),
    ],
)
def test_is_below(a: Authority, b: Authority, expected: bool) -> None:
    """权限等级比较：USER < COMPANY < GROUP_COMPANY < ADMIN"""
    assert a.is_below(b) is expected
