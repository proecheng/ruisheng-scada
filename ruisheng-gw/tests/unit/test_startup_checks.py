"""启动检查：schema_version + alembic head + config。"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys

import pytest
from ruisheng_gw.main import (
    check_alembic_head,
    check_shared_schema_version,
)

# Monorepo root — four levels up from this test file (unit → tests → ruisheng-gw → root)
_REPO_ROOT = pathlib.Path(__file__).parent.parent.parent.parent

_EXIT_CONFIG_INVALID = 3


def _subprocess_env() -> dict[str, str]:
    """Return os.environ augmented with PYTHONPATH so subprocesses find the src layout."""
    src_paths = [
        str(_REPO_ROOT / "ruisheng-gw" / "src"),
        str(_REPO_ROOT / "ruisheng-shared" / "src"),
    ]
    existing = os.environ.get("PYTHONPATH", "")
    combined = os.pathsep.join(filter(None, src_paths + [existing]))
    return {**os.environ, "PYTHONPATH": combined}


def test_schema_version_match_returns_none() -> None:
    # shared 0.1.0 当前是 20260415；REQUIRED=20260415 应 pass
    assert check_shared_schema_version(required=20260415) is None


def test_schema_version_mismatch_raises() -> None:
    with pytest.raises(RuntimeError, match="shared mismatch"):
        check_shared_schema_version(required=99999999)


def test_alembic_head_check_signature_exists() -> None:
    # 真实 check 需要 DB，这里只验函数可调用（E10 integration 再验 live）
    assert callable(check_alembic_head)


def test_check_only_exit_0_on_success(monkeypatch):
    monkeypatch.setenv("GW_LISTEN_HOST", "0.0.0.0")
    monkeypatch.setenv("GW_LISTEN_PORT", "5020")
    monkeypatch.setenv("GW_DATABASE_URL", "postgresql+asyncpg://u:p@h:5432/d")
    monkeypatch.setenv("GW_REDIS_URL", "redis://h:6379/0")
    result = subprocess.run(
        [sys.executable, "-m", "ruisheng_gw", "--check-only"],
        capture_output=True,
        text=True,
        env=_subprocess_env(),
        check=False,
    )
    assert result.returncode == 0


def test_print_config_exit_3_on_invalid_env(monkeypatch):
    # 清所有 GW_* → 缺必填 → config invalid → exit 3
    env = {k: v for k, v in _subprocess_env().items() if not k.startswith("GW_")}
    result = subprocess.run(
        [sys.executable, "-m", "ruisheng_gw", "--print-config"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert result.returncode == _EXIT_CONFIG_INVALID
    assert "config invalid" in result.stderr.lower()
