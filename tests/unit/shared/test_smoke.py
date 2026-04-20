"""最小 smoke 测试：ruisheng_shared 包可导入且 SHARED_SCHEMA_VERSION 为正整数。"""

from __future__ import annotations


def test_shared_importable() -> None:
    import ruisheng_shared

    assert hasattr(ruisheng_shared, "SHARED_SCHEMA_VERSION")


def test_schema_version_positive_int() -> None:
    from ruisheng_shared import SHARED_SCHEMA_VERSION

    assert isinstance(SHARED_SCHEMA_VERSION, int)
    assert SHARED_SCHEMA_VERSION > 20250000  # sanity: 不早于 2025 年
