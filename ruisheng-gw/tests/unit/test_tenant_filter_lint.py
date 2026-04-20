"""CI tenant-filter lint: fail on SQL against 12 FORCE RLS tables missing usr_group."""

from __future__ import annotations

from pathlib import Path

from ruisheng_gw.ci_lint.tenant_filter_lint import check_file

FORCE_RLS_TABLES = {
    "devices",
    "device_points",
    "wx_groups",
    "users",
    "scene_pages",
    "scene_views",
    "user_emails",
    "user_phone_numbers",
    "user_wx_bindings",
    "user_login_records",
    "user_control_actions",
    "soft_logs",
}


def test_passes_on_query_with_usr_group(tmp_path: Path) -> None:
    f = tmp_path / "ok.py"
    f.write_text("""
from sqlalchemy import text
async def get():
    return await conn.execute(text(
        "SELECT * FROM devices WHERE usr_group = :ug"
    ), {"ug": "x"})
""")
    violations = check_file(f, forbidden_tables=FORCE_RLS_TABLES)
    assert violations == []


def test_fails_on_query_missing_usr_group(tmp_path: Path) -> None:
    f = tmp_path / "bad.py"
    f.write_text("""
from sqlalchemy import text
async def get():
    return await conn.execute(text("SELECT * FROM users"))
""")
    violations = check_file(f, forbidden_tables=FORCE_RLS_TABLES)
    assert len(violations) == 1
    assert "users" in violations[0]


def test_allows_noqa_escape_hatch(tmp_path: Path) -> None:
    f = tmp_path / "ok.py"
    f.write_text("""
from sqlalchemy import text
async def get():
    return await conn.execute(text("SELECT * FROM users"))  # noqa: tenant-lint
""")
    violations = check_file(f, forbidden_tables=FORCE_RLS_TABLES)
    assert violations == []
