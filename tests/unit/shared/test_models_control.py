"""Spec §3.5 control: user_control_actions"""

from ruisheng_shared.models.control import UserControlAction
from sqlalchemy.dialects.postgresql import JSONB


def test_user_control_actions_tablename() -> None:
    assert UserControlAction.__tablename__ == "user_control_actions"


def test_user_control_actions_primary_key() -> None:
    pk = [c.name for c in UserControlAction.__table__.primary_key.columns]
    assert pk == ["id"]


def test_user_control_actions_columns() -> None:
    cols = {c.name for c in UserControlAction.__table__.columns}
    assert cols >= {
        "id",
        "dev_number",
        "user_name",
        "action",
        "cmd_id",
        "result",
        "acted_at",
        "completed_at",
        "usr_group",
    }


def test_user_control_actions_no_timestamp_mixin() -> None:
    # Spec only has acted_at / completed_at — no created_at / updated_at.
    cols = {c.name for c in UserControlAction.__table__.columns}
    assert "created_at" not in cols
    assert "updated_at" not in cols


def test_user_control_actions_action_jsonb() -> None:
    col = UserControlAction.__table__.columns["action"]
    assert isinstance(col.type, JSONB)
    assert col.nullable is False


def test_user_control_actions_result_ck() -> None:
    names = {c.name for c in UserControlAction.__table__.constraints if c.name}
    assert "ck_user_control_actions_result" in names


def test_user_control_actions_result_completed_ck() -> None:
    names = {c.name for c in UserControlAction.__table__.constraints if c.name}
    assert "ck_user_control_actions_result_completed_consistency" in names


def test_user_control_actions_cmd_id_unique() -> None:
    names = {c.name for c in UserControlAction.__table__.constraints if c.name}
    assert "uq_user_control_actions_cmd_id" in names


def test_user_control_actions_index_dev_acted() -> None:
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.schema import CreateIndex

    idx = next(
        ix
        for ix in UserControlAction.__table__.indexes
        if ix.name == "idx_user_control_actions_dev_acted"
    )
    ddl = str(CreateIndex(idx).compile(dialect=postgresql.dialect()))
    assert "dev_number" in ddl
    assert "acted_at DESC" in ddl


def test_user_control_actions_index_user_acted() -> None:
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.schema import CreateIndex

    idx = next(
        ix
        for ix in UserControlAction.__table__.indexes
        if ix.name == "idx_user_control_actions_user_acted"
    )
    ddl = str(CreateIndex(idx).compile(dialect=postgresql.dialect()))
    assert "user_name" in ddl
    assert "acted_at DESC" in ddl


def test_user_control_actions_no_fk_on_dev_or_user() -> None:
    # Hypertable candidate (like alarm_records) — spec DDL has no FK.
    dev_col = UserControlAction.__table__.columns["dev_number"]
    user_col = UserControlAction.__table__.columns["user_name"]
    assert len(dev_col.foreign_keys) == 0
    assert len(user_col.foreign_keys) == 0
