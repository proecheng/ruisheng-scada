"""启动检查：schema_version + alembic head + config。"""

from __future__ import annotations

import pytest
from ruisheng_gw.main import (
    check_alembic_head,
    check_shared_schema_version,
)


def test_schema_version_match_returns_none() -> None:
    # shared 0.1.0 当前是 20260415；REQUIRED=20260415 应 pass
    assert check_shared_schema_version(required=20260415) is None


def test_schema_version_mismatch_raises() -> None:
    with pytest.raises(RuntimeError, match="shared mismatch"):
        check_shared_schema_version(required=99999999)


def test_alembic_head_check_signature_exists() -> None:
    # 真实 check 需要 DB，这里只验函数可调用（E10 integration 再验 live）
    assert callable(check_alembic_head)
