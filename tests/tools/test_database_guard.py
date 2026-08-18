from pathlib import Path

import pytest

from conftest import require_test_database


@pytest.mark.parametrize(
    "dsn",
    [
        "postgresql+asyncpg://u:p@127.0.0.1:5432/ruisheng_test",
        "postgresql+asyncpg://u:p@127.0.0.1:5432/test_ruisheng",
    ],
)
def test_destructive_migration_guard_accepts_explicit_test_database(dsn: str) -> None:
    require_test_database(dsn)


@pytest.mark.parametrize(
    "dsn",
    [
        "postgresql+asyncpg://u:p@127.0.0.1:5432/ruisheng",
        "postgresql+asyncpg://u:p@127.0.0.1:5432/postgres",
        "postgresql+asyncpg://u:p@127.0.0.1:5432/production",
    ],
)
def test_destructive_migration_guard_rejects_non_test_database(dsn: str) -> None:
    with pytest.raises(RuntimeError, match="explicit test database"):
        require_test_database(dsn)


def test_root_integration_suite_applies_database_guard() -> None:
    source = (Path(__file__).parents[1] / "integration" / "conftest.py").read_text(encoding="utf-8")

    assert "def require_test_database_target()" in source
    assert "require_test_database(_DEV_DSN)" in source
    assert "require_test_database_target, dev_database_ready" in source


def test_default_integration_target_is_a_test_database() -> None:
    source = (Path(__file__).parents[2] / "conftest.py").read_text(encoding="utf-8")

    assert "127.0.0.1:5432/ruisheng_test" in source
