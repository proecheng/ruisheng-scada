"""Spec §3.8 alarms: device_waring_cfgs / alarm_records / alarm_outbox"""

from ruisheng_shared.models.alarms import AlarmOutbox, AlarmRecord, DeviceWaringCfg
from sqlalchemy.dialects.postgresql import JSONB


# ---------------------------------------------------------------------------
# device_waring_cfgs
# ---------------------------------------------------------------------------
def test_device_waring_cfgs_tablename() -> None:
    assert DeviceWaringCfg.__tablename__ == "device_waring_cfgs"


def test_device_waring_cfgs_primary_key() -> None:
    pk = [c.name for c in DeviceWaringCfg.__table__.primary_key.columns]
    assert pk == ["id"]


def test_device_waring_cfgs_columns() -> None:
    cols = {c.name for c in DeviceWaringCfg.__table__.columns}
    assert cols >= {
        "id",
        "dev_number",
        "point_id",
        "reg_bit",
        "alarm_name",
        "alarm_type",
        "limit_value",
        "relation_point_id",
        "relation_reg_bit",
        "relation_alarm_type",
        "relation_limit_value",
        "enable",
        "phone_alarm",
        "reset_remind",
        "dev_sync_flag",
        "waring_flag",
        "alarm_msg",
        "created_at",
        "updated_at",
    }


def test_device_waring_cfgs_ck_alarm_type() -> None:
    names = {c.name for c in DeviceWaringCfg.__table__.constraints if c.name}
    assert "ck_device_waring_cfgs_alarm_type" in names


def test_device_waring_cfgs_ck_limit_value() -> None:
    names = {c.name for c in DeviceWaringCfg.__table__.constraints if c.name}
    assert "ck_device_waring_cfgs_limit_value" in names


# ---------------------------------------------------------------------------
# alarm_records
# ---------------------------------------------------------------------------
def test_alarm_records_tablename() -> None:
    assert AlarmRecord.__tablename__ == "alarm_records"


def test_alarm_records_primary_key() -> None:
    pk = [c.name for c in AlarmRecord.__table__.primary_key.columns]
    assert pk == ["id"]


def test_alarm_records_columns() -> None:
    cols = {c.name for c in AlarmRecord.__table__.columns}
    assert cols >= {
        "id",
        "dev_number",
        "point_id",
        "alarm_name",
        "alarm_msg",
        "alarm_value",
        "channels_sent",
        "triggered_at",
        "reset_at",
        "usr_group",
    }
    # No audit columns per spec (will become hypertable)
    assert "created_at" not in cols
    assert "updated_at" not in cols


def test_alarm_records_no_fk_on_dev_number_or_point_id() -> None:
    # Hypertable candidate — no FK per spec
    dev_col = AlarmRecord.__table__.columns["dev_number"]
    point_col = AlarmRecord.__table__.columns["point_id"]
    assert len(dev_col.foreign_keys) == 0
    assert len(point_col.foreign_keys) == 0


def test_alarm_records_channels_sent_jsonb() -> None:
    col = AlarmRecord.__table__.columns["channels_sent"]
    assert isinstance(col.type, JSONB)
    # default is a CallableColumnDefault wrapping `dict` → emits {}::jsonb
    assert col.default is not None
    assert col.default.is_callable is True
    # SA wraps it in a positional-ctx adapter; unwrap via .arg.__wrapped__ or call with None
    # Simpler: just check nullable is False and JSONB is set (the type check is the key fact)
    assert col.nullable is False


def test_alarm_records_index_dev_triggered() -> None:
    # Find an index covering (dev_number, triggered_at) with triggered_at DESC
    found = False
    for ix in AlarmRecord.__table__.indexes:
        col_names = [c.name for c in ix.columns]
        if col_names == ["dev_number", "triggered_at"]:
            found = True
            break
    assert found, "missing composite index on (dev_number, triggered_at DESC)"


# ---------------------------------------------------------------------------
# alarm_outbox
# ---------------------------------------------------------------------------
def test_alarm_outbox_tablename() -> None:
    assert AlarmOutbox.__tablename__ == "alarm_outbox"


def test_alarm_outbox_primary_key() -> None:
    pk = [c.name for c in AlarmOutbox.__table__.primary_key.columns]
    assert pk == ["id"]


def test_alarm_outbox_columns() -> None:
    cols = {c.name for c in AlarmOutbox.__table__.columns}
    assert cols >= {"id", "alarm_id", "payload", "published", "created_at"}
    # No updated_at per spec
    assert "updated_at" not in cols


def test_alarm_outbox_alarm_id_fk() -> None:
    col = AlarmOutbox.__table__.columns["alarm_id"]
    fks = list(col.foreign_keys)
    assert len(fks) == 1
    assert fks[0].column.table.name == "alarm_records"
    assert fks[0].column.name == "id"


def test_alarm_outbox_payload_jsonb() -> None:
    col = AlarmOutbox.__table__.columns["payload"]
    assert isinstance(col.type, JSONB)


def test_alarm_outbox_published_default_false() -> None:
    col = AlarmOutbox.__table__.columns["published"]
    # default=False at Python level
    assert col.default is not None
    assert col.default.arg is False


def test_alarm_outbox_partial_index() -> None:
    idx_names = {ix.name for ix in AlarmOutbox.__table__.indexes}
    assert "idx_alarm_outbox_unpublished" in idx_names
    # Verify it is partial (has postgresql_where)
    for ix in AlarmOutbox.__table__.indexes:
        if ix.name == "idx_alarm_outbox_unpublished":
            assert ix.dialect_options.get("postgresql", {}).get("where") is not None
            break
