"""Spec §4.2 plans: timing_plans / maintain_plans / maintain_actions (v1.3.3)."""

from ruisheng_shared.models.plans import MaintainAction, MaintainPlan, TimingPlan
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex


# ---------------------------------------------------------------------------
# timing_plans
# ---------------------------------------------------------------------------
def test_timing_plans_tablename() -> None:
    assert TimingPlan.__tablename__ == "timing_plans"


def test_timing_plans_primary_key() -> None:
    pk = [c.name for c in TimingPlan.__table__.primary_key.columns]
    assert pk == ["id"]


def test_timing_plans_columns() -> None:
    cols = {c.name for c in TimingPlan.__table__.columns}
    assert cols >= {
        "id",
        "dev_number",
        "action_at",
        "action",
        "repetition",
        "enable",
        "update_flag",
        "usr_group",
        "deleted_at",
        "created_at",
        "updated_at",
    }


def test_timing_plans_dev_number_fk_restrict() -> None:
    col = TimingPlan.__table__.columns["dev_number"]
    fks = list(col.foreign_keys)
    assert len(fks) == 1
    assert fks[0].column.table.name == "devices"
    assert fks[0].column.name == "dev_number"
    assert fks[0].ondelete == "RESTRICT"


def test_timing_plans_usr_group_fk_restrict() -> None:
    col = TimingPlan.__table__.columns["usr_group"]
    fks = list(col.foreign_keys)
    assert len(fks) == 1
    assert fks[0].column.table.name == "wx_groups"
    assert fks[0].column.name == "usr_group"
    assert fks[0].ondelete == "RESTRICT"


def test_timing_plans_index_dev_action() -> None:
    idx = next(ix for ix in TimingPlan.__table__.indexes if ix.name == "ix_timing_plans_dev_action")
    cols = [c.name for c in idx.columns]
    assert cols == ["dev_number", "action_at"]


def test_timing_plans_index_due_partial() -> None:
    idx = next(ix for ix in TimingPlan.__table__.indexes if ix.name == "ix_timing_plans_due")
    ddl = str(CreateIndex(idx).compile(dialect=postgresql.dialect()))
    assert "WHERE" in ddl
    assert "enable = true" in ddl
    assert "deleted_at IS NULL" in ddl


def test_timing_plans_index_usr_group() -> None:
    idx_names = {ix.name for ix in TimingPlan.__table__.indexes}
    assert "ix_timing_plans_usr_group" in idx_names


def test_timing_plans_has_timestamp_and_soft_delete() -> None:
    cols = {c.name for c in TimingPlan.__table__.columns}
    assert "created_at" in cols
    assert "updated_at" in cols
    assert "deleted_at" in cols


# ---------------------------------------------------------------------------
# maintain_plans
# ---------------------------------------------------------------------------
def test_maintain_plans_tablename() -> None:
    assert MaintainPlan.__tablename__ == "maintain_plans"


def test_maintain_plans_primary_key() -> None:
    pk = [c.name for c in MaintainPlan.__table__.primary_key.columns]
    assert pk == ["id"]


def test_maintain_plans_columns() -> None:
    cols = {c.name for c in MaintainPlan.__table__.columns}
    assert cols >= {
        "id",
        "dev_number",
        "plan_name",
        "description",
        "interval_days",
        "next_due_at",
        "enable",
        "usr_group",
        "deleted_at",
        "created_at",
        "updated_at",
    }
    # Spec explicitly has NO update_flag (不下发 gw)
    assert "update_flag" not in cols


def test_maintain_plans_plan_name_collation() -> None:
    col = MaintainPlan.__table__.columns["plan_name"]
    assert col.type.collation == "zh-x-icu"
    assert col.type.length == 100


def test_maintain_plans_ck_interval_days() -> None:
    names = {c.name for c in MaintainPlan.__table__.constraints if c.name}
    assert "ck_maintain_plans_interval_days" in names


def test_maintain_plans_ck_next_due_after_created() -> None:
    names = {c.name for c in MaintainPlan.__table__.constraints if c.name}
    assert "ck_maintain_plans_next_due_after_created" in names


def test_maintain_plans_dev_number_fk_restrict() -> None:
    col = MaintainPlan.__table__.columns["dev_number"]
    fks = list(col.foreign_keys)
    assert len(fks) == 1
    assert fks[0].column.table.name == "devices"
    assert fks[0].ondelete == "RESTRICT"


def test_maintain_plans_usr_group_fk_restrict() -> None:
    col = MaintainPlan.__table__.columns["usr_group"]
    fks = list(col.foreign_keys)
    assert len(fks) == 1
    assert fks[0].column.table.name == "wx_groups"
    assert fks[0].ondelete == "RESTRICT"


def test_maintain_plans_index_dev_number() -> None:
    idx_names = {ix.name for ix in MaintainPlan.__table__.indexes}
    assert "ix_maintain_plans_dev_number" in idx_names


def test_maintain_plans_index_usr_group() -> None:
    idx_names = {ix.name for ix in MaintainPlan.__table__.indexes}
    assert "ix_maintain_plans_usr_group" in idx_names


def test_maintain_plans_index_next_due_active_partial() -> None:
    idx = next(
        ix
        for ix in MaintainPlan.__table__.indexes
        if ix.name == "ix_maintain_plans_next_due_active"
    )
    ddl = str(CreateIndex(idx).compile(dialect=postgresql.dialect()))
    assert "WHERE" in ddl
    assert "enable = true" in ddl
    assert "deleted_at IS NULL" in ddl


def test_maintain_plans_unique_partial_dev_plan_name() -> None:
    idx = next(
        ix for ix in MaintainPlan.__table__.indexes if ix.name == "ux_maintain_plans_dev_plan_name"
    )
    assert idx.unique is True
    ddl = str(CreateIndex(idx).compile(dialect=postgresql.dialect()))
    assert "UNIQUE" in ddl
    assert "WHERE" in ddl
    assert "deleted_at IS NULL" in ddl
    cols = [c.name for c in idx.columns]
    assert cols == ["dev_number", "plan_name"]


def test_maintain_plans_has_timestamp_and_soft_delete() -> None:
    cols = {c.name for c in MaintainPlan.__table__.columns}
    assert "created_at" in cols
    assert "updated_at" in cols
    assert "deleted_at" in cols


# ---------------------------------------------------------------------------
# maintain_actions
# ---------------------------------------------------------------------------
def test_maintain_actions_tablename() -> None:
    assert MaintainAction.__tablename__ == "maintain_actions"


def test_maintain_actions_primary_key() -> None:
    pk = [c.name for c in MaintainAction.__table__.primary_key.columns]
    assert pk == ["id"]


def test_maintain_actions_columns() -> None:
    cols = {c.name for c in MaintainAction.__table__.columns}
    assert cols >= {
        "id",
        "action_uuid",
        "plan_id",
        "dev_number",
        "acted_at",
        "user_name",
        "note",
        "usr_group",
    }


def test_maintain_actions_no_timestamp_mixin_no_soft_delete() -> None:
    # 审计表 = 一次写入，spec DDL 未定义 created_at/updated_at/deleted_at。
    cols = {c.name for c in MaintainAction.__table__.columns}
    assert "created_at" not in cols
    assert "updated_at" not in cols
    assert "deleted_at" not in cols


def test_maintain_actions_action_uuid_unique() -> None:
    names = {c.name for c in MaintainAction.__table__.constraints if c.name}
    assert "uq_maintain_actions_action_uuid" in names


def test_maintain_actions_action_uuid_length_26() -> None:
    col = MaintainAction.__table__.columns["action_uuid"]
    assert col.type.length == 26
    assert col.nullable is False


def test_maintain_actions_no_fk_on_plan_id_or_dev_number() -> None:
    # 弱引用：审计永久留痕，设备/计划删除后仍保留（类比 alarm_records / user_control_actions）。
    plan_col = MaintainAction.__table__.columns["plan_id"]
    dev_col = MaintainAction.__table__.columns["dev_number"]
    assert len(plan_col.foreign_keys) == 0
    assert len(dev_col.foreign_keys) == 0


def test_maintain_actions_user_name_fk_restrict() -> None:
    col = MaintainAction.__table__.columns["user_name"]
    fks = list(col.foreign_keys)
    assert len(fks) == 1
    assert fks[0].column.table.name == "users"
    assert fks[0].column.name == "user_name"
    assert fks[0].ondelete == "RESTRICT"


def test_maintain_actions_usr_group_fk_restrict() -> None:
    col = MaintainAction.__table__.columns["usr_group"]
    fks = list(col.foreign_keys)
    assert len(fks) == 1
    assert fks[0].column.table.name == "wx_groups"
    assert fks[0].ondelete == "RESTRICT"


def test_maintain_actions_index_plan_acted_desc() -> None:
    idx = next(
        ix for ix in MaintainAction.__table__.indexes if ix.name == "ix_maintain_actions_plan_acted"
    )
    ddl = str(CreateIndex(idx).compile(dialect=postgresql.dialect()))
    assert "plan_id" in ddl
    assert "acted_at DESC" in ddl


def test_maintain_actions_index_dev_acted_desc() -> None:
    idx = next(
        ix for ix in MaintainAction.__table__.indexes if ix.name == "ix_maintain_actions_dev_acted"
    )
    ddl = str(CreateIndex(idx).compile(dialect=postgresql.dialect()))
    assert "dev_number" in ddl
    assert "acted_at DESC" in ddl


def test_maintain_actions_index_user_acted_desc() -> None:
    idx = next(
        ix for ix in MaintainAction.__table__.indexes if ix.name == "ix_maintain_actions_user_acted"
    )
    ddl = str(CreateIndex(idx).compile(dialect=postgresql.dialect()))
    assert "user_name" in ddl
    assert "acted_at DESC" in ddl


def test_maintain_actions_index_usr_group() -> None:
    idx_names = {ix.name for ix in MaintainAction.__table__.indexes}
    assert "ix_maintain_actions_usr_group" in idx_names


def test_maintain_actions_acted_at_server_default_now() -> None:
    col = MaintainAction.__table__.columns["acted_at"]
    assert col.server_default is not None
    assert col.nullable is False
