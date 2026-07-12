"""Spec §4.2 logs: soft_logs / user_login_records (v1.3.6)."""

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.schema import CreateIndex
from sqlalchemy.types import Boolean, DateTime, String

# ---------------------------------------------------------------------------
# SoftLog
# ---------------------------------------------------------------------------


def test_soft_logs_tablename() -> None:
    from ruisheng_shared.models.logs import SoftLog

    assert SoftLog.__tablename__ == "soft_logs"


def test_soft_logs_primary_key() -> None:
    # D8: composite PK (id, recorded_at) — TimescaleDB 2.16.1 硬要求 PK 含分区列。
    # id 自身 BIGSERIAL 唯一，复合只为满足 TS 约束。
    from ruisheng_shared.models.logs import SoftLog

    pk = [c.name for c in SoftLog.__table__.primary_key.columns]
    assert pk == ["id", "recorded_at"]


def test_soft_logs_columns() -> None:
    from ruisheng_shared.models.logs import SoftLog

    cols = {c.name for c in SoftLog.__table__.columns}
    assert cols >= {"id", "level", "source", "msg", "context", "recorded_at"}


def test_soft_logs_level_not_null() -> None:
    from ruisheng_shared.models.logs import SoftLog

    col = SoftLog.__table__.columns["level"]
    assert col.nullable is False
    assert isinstance(col.type, String)
    assert col.type.length == 10


def test_soft_logs_source_not_null() -> None:
    from ruisheng_shared.models.logs import SoftLog

    col = SoftLog.__table__.columns["source"]
    assert col.nullable is False
    assert isinstance(col.type, String)
    assert col.type.length == 20


def test_soft_logs_msg_not_null() -> None:
    from ruisheng_shared.models.logs import SoftLog

    col = SoftLog.__table__.columns["msg"]
    assert col.nullable is False
    assert isinstance(col.type, String)
    assert col.type.length == 500


def test_soft_logs_context_jsonb_nullable() -> None:
    from ruisheng_shared.models.logs import SoftLog

    col = SoftLog.__table__.columns["context"]
    assert isinstance(col.type, JSONB)
    assert col.nullable is True


def test_soft_logs_recorded_at_timezone_not_null() -> None:
    from ruisheng_shared.models.logs import SoftLog

    col = SoftLog.__table__.columns["recorded_at"]
    assert col.nullable is False
    assert isinstance(col.type, DateTime)
    assert col.type.timezone is True


def test_soft_logs_level_check_contains_warn() -> None:
    from ruisheng_shared.models.logs import SoftLog

    for c in SoftLog.__table__.constraints:
        if hasattr(c, "sqltext"):
            sql = str(c.sqltext)
            if "level" in sql:
                assert "WARN" in sql
                return
    pytest.fail("No CHECK constraint found for level")


def test_soft_logs_level_check_contains_error_and_critical() -> None:
    from ruisheng_shared.models.logs import SoftLog

    for c in SoftLog.__table__.constraints:
        if hasattr(c, "sqltext"):
            sql = str(c.sqltext)
            if "level" in sql:
                assert "ERROR" in sql
                assert "CRITICAL" in sql
                return
    pytest.fail("No CHECK constraint found for level")


def test_soft_logs_source_check_contains_gw_api_worker() -> None:
    from ruisheng_shared.models.logs import SoftLog

    for c in SoftLog.__table__.constraints:
        if hasattr(c, "sqltext"):
            sql = str(c.sqltext)
            if "source" in sql and "gw" in sql:
                assert "gw" in sql
                assert "api" in sql
                assert "worker" in sql
                return
    pytest.fail("No CHECK constraint found for source")


def test_soft_logs_index_level_recorded_at() -> None:
    from ruisheng_shared.models.logs import SoftLog

    idx = next(
        (ix for ix in SoftLog.__table__.indexes if ix.name == "ix_soft_logs_level_recorded_at"),
        None,
    )
    assert idx is not None, "Missing index ix_soft_logs_level_recorded_at"
    ddl = str(CreateIndex(idx).compile(dialect=postgresql.dialect()))
    assert "level" in ddl
    assert "recorded_at DESC" in ddl


def test_soft_logs_index_source_recorded_at() -> None:
    from ruisheng_shared.models.logs import SoftLog

    idx = next(
        (ix for ix in SoftLog.__table__.indexes if ix.name == "ix_soft_logs_source_recorded_at"),
        None,
    )
    assert idx is not None, "Missing index ix_soft_logs_source_recorded_at"
    ddl = str(CreateIndex(idx).compile(dialect=postgresql.dialect()))
    assert "source" in ddl
    assert "recorded_at DESC" in ddl


def test_soft_logs_no_timestamp_mixin() -> None:
    """SoftLog must NOT inherit TimestampMixin — no updated_at."""
    from ruisheng_shared.models.logs import SoftLog

    cols = {c.name for c in SoftLog.__table__.columns}
    assert "updated_at" not in cols
    assert "created_at" not in cols


def test_soft_logs_no_soft_delete_mixin() -> None:
    """SoftLog must NOT inherit SoftDeleteMixin — no deleted_at."""
    from ruisheng_shared.models.logs import SoftLog

    cols = {c.name for c in SoftLog.__table__.columns}
    assert "deleted_at" not in cols


def test_soft_logs_no_usr_group() -> None:
    """soft_logs is system-level audit — no usr_group (no RLS)."""
    from ruisheng_shared.models.logs import SoftLog

    cols = {c.name for c in SoftLog.__table__.columns}
    assert "usr_group" not in cols


def test_soft_logs_docstring_stage_d_hint() -> None:
    from ruisheng_shared.models.logs import SoftLog

    doc = SoftLog.__doc__ or ""
    assert "Stage D alembic" in doc
    assert "hypertable" in doc


# ---------------------------------------------------------------------------
# UserLoginRecord
# ---------------------------------------------------------------------------


def test_user_login_records_tablename() -> None:
    from ruisheng_shared.models.logs import UserLoginRecord

    assert UserLoginRecord.__tablename__ == "user_login_records"


def test_user_login_records_primary_key() -> None:
    # D8: composite PK (id, logged_at) — TimescaleDB 2.16.1 硬要求 PK 含分区列。
    # id 自身 BIGSERIAL 唯一，复合只为满足 TS 约束。
    from ruisheng_shared.models.logs import UserLoginRecord

    pk = [c.name for c in UserLoginRecord.__table__.primary_key.columns]
    assert pk == ["id", "logged_at"]


def test_user_login_records_columns() -> None:
    from ruisheng_shared.models.logs import UserLoginRecord

    cols = {c.name for c in UserLoginRecord.__table__.columns}
    assert cols >= {
        "id",
        "user_name",
        "logged_at",
        "ip_addr",
        "city",
        "user_agent",
        "success",
        "usr_group",
    }


def test_user_login_records_user_name_not_null() -> None:
    from ruisheng_shared.models.logs import UserLoginRecord

    col = UserLoginRecord.__table__.columns["user_name"]
    assert col.nullable is False
    assert isinstance(col.type, String)
    assert col.type.length == 50


def test_user_login_records_logged_at_timezone_not_null() -> None:
    from ruisheng_shared.models.logs import UserLoginRecord

    col = UserLoginRecord.__table__.columns["logged_at"]
    assert col.nullable is False
    assert isinstance(col.type, DateTime)
    assert col.type.timezone is True


def test_user_login_records_ip_addr_inet_not_null() -> None:
    from ruisheng_shared.models.logs import UserLoginRecord

    col = UserLoginRecord.__table__.columns["ip_addr"]
    assert col.nullable is False
    assert isinstance(col.type, INET)


def test_user_login_records_city_nullable() -> None:
    from ruisheng_shared.models.logs import UserLoginRecord

    col = UserLoginRecord.__table__.columns["city"]
    assert col.nullable is True
    assert isinstance(col.type, String)
    assert col.type.length == 100


def test_user_login_records_user_agent_nullable() -> None:
    from ruisheng_shared.models.logs import UserLoginRecord

    col = UserLoginRecord.__table__.columns["user_agent"]
    assert col.nullable is True
    assert isinstance(col.type, String)
    assert col.type.length == 500


def test_user_login_records_success_bool_not_null() -> None:
    from ruisheng_shared.models.logs import UserLoginRecord

    col = UserLoginRecord.__table__.columns["success"]
    assert col.nullable is False
    assert isinstance(col.type, Boolean)


def test_user_login_records_success_default_true() -> None:
    """success should default to True (server_default or default)."""
    from ruisheng_shared.models.logs import UserLoginRecord

    col = UserLoginRecord.__table__.columns["success"]
    # Either Python-level default or server-side default is acceptable.
    has_default = (col.default is not None) or (col.server_default is not None)
    assert has_default, "success must have a default value of True"


def test_user_login_records_usr_group_not_null() -> None:
    from ruisheng_shared.models.logs import UserLoginRecord

    col = UserLoginRecord.__table__.columns["usr_group"]
    assert col.nullable is False
    assert isinstance(col.type, String)
    assert col.type.length == 50


def test_user_login_records_usr_group_fk_wx_groups_restrict() -> None:
    from ruisheng_shared.models.logs import UserLoginRecord

    col = UserLoginRecord.__table__.columns["usr_group"]
    fks = list(col.foreign_keys)
    assert len(fks) == 1
    assert fks[0].column.table.name == "wx_groups"
    assert fks[0].column.name == "usr_group"
    assert fks[0].ondelete == "RESTRICT"


def test_user_login_records_user_name_no_fk() -> None:
    """user_name is a weak reference — no FK to users (audit permanence)."""
    from ruisheng_shared.models.logs import UserLoginRecord

    col = UserLoginRecord.__table__.columns["user_name"]
    assert len(col.foreign_keys) == 0


def test_user_login_records_index_usr_group_logged_at() -> None:
    from ruisheng_shared.models.logs import UserLoginRecord

    idx = next(
        (
            ix
            for ix in UserLoginRecord.__table__.indexes
            if ix.name == "ix_user_login_records_usr_group_logged_at"
        ),
        None,
    )
    assert idx is not None, "Missing index ix_user_login_records_usr_group_logged_at"
    ddl = str(CreateIndex(idx).compile(dialect=postgresql.dialect()))
    assert "usr_group" in ddl
    assert "logged_at DESC" in ddl


def test_user_login_records_index_user_name_logged_at() -> None:
    from ruisheng_shared.models.logs import UserLoginRecord

    idx = next(
        (
            ix
            for ix in UserLoginRecord.__table__.indexes
            if ix.name == "ix_user_login_records_user_name_logged_at"
        ),
        None,
    )
    assert idx is not None, "Missing index ix_user_login_records_user_name_logged_at"
    ddl = str(CreateIndex(idx).compile(dialect=postgresql.dialect()))
    assert "user_name" in ddl
    assert "logged_at DESC" in ddl


def test_user_login_records_index_ip_fail_partial() -> None:
    """Partial index on ip_addr/logged_at DESC WHERE NOT success."""
    from ruisheng_shared.models.logs import UserLoginRecord

    idx = next(
        (
            ix
            for ix in UserLoginRecord.__table__.indexes
            if ix.name == "ix_user_login_records_ip_fail"
        ),
        None,
    )
    assert idx is not None, "Missing index ix_user_login_records_ip_fail"
    ddl = str(CreateIndex(idx).compile(dialect=postgresql.dialect()))
    assert "ip_addr" in ddl
    assert "logged_at DESC" in ddl
    assert "NOT success" in ddl


def test_user_login_records_no_timestamp_mixin() -> None:
    """UserLoginRecord must NOT inherit TimestampMixin."""
    from ruisheng_shared.models.logs import UserLoginRecord

    cols = {c.name for c in UserLoginRecord.__table__.columns}
    assert "created_at" not in cols
    assert "updated_at" not in cols


def test_user_login_records_no_soft_delete_mixin() -> None:
    """UserLoginRecord must NOT inherit SoftDeleteMixin."""
    from ruisheng_shared.models.logs import UserLoginRecord

    cols = {c.name for c in UserLoginRecord.__table__.columns}
    assert "deleted_at" not in cols


def test_user_login_records_docstring_stage_d_hint() -> None:
    from ruisheng_shared.models.logs import UserLoginRecord

    doc = UserLoginRecord.__doc__ or ""
    assert "Stage D alembic" in doc
    assert "hypertable" in doc
    assert "3" in doc  # "3 years" or "3年"


# ---------------------------------------------------------------------------
# Stage D placeholder skip-tests
# ---------------------------------------------------------------------------


@pytest.mark.skip(
    reason="Stage D alembic integration: soft_logs + user_login_records hypertable + retention + compression"
)
def test_logs_hypertable_stage_d() -> None: ...


@pytest.mark.skip(reason="Stage D alembic integration: user_login_records 3-year retention")
def test_user_login_records_retention_stage_d() -> None: ...
