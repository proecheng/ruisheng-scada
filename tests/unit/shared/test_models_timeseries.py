"""Spec §4.2 timeseries models: PointDataRealtime / PointDataHistory / WaveformHistory."""

from __future__ import annotations

import pytest
from ruisheng_shared.models.timeseries import (
    PointDataHistory,
    PointDataRealtime,
    WaveformHistory,
)

# ---------------------------------------------------------------------------
# PointDataRealtime — tablename & PK
# ---------------------------------------------------------------------------


def test_realtime_tablename() -> None:
    assert PointDataRealtime.__tablename__ == "point_data_realtime"


def test_realtime_pk_columns() -> None:
    """复合 PK (dev_number, point_id)，无 id 列。"""
    pk = [c.name for c in PointDataRealtime.__table__.primary_key.columns]
    assert pk == ["dev_number", "point_id"]


def test_realtime_no_id_column() -> None:
    cols = {c.name for c in PointDataRealtime.__table__.columns}
    assert "id" not in cols


# ---------------------------------------------------------------------------
# PointDataRealtime — all 5 columns present
# ---------------------------------------------------------------------------


def test_realtime_columns_complete() -> None:
    cols = {c.name for c in PointDataRealtime.__table__.columns}
    assert cols == {"dev_number", "point_id", "org_value", "rt_value", "recorded_at"}


# ---------------------------------------------------------------------------
# PointDataRealtime — column types & nullable
# ---------------------------------------------------------------------------


def test_realtime_dev_number_string50_not_null() -> None:
    from sqlalchemy import String

    col = PointDataRealtime.__table__.columns["dev_number"]
    assert isinstance(col.type, String)
    assert col.type.length == 50
    assert col.nullable is False


def test_realtime_point_id_biginteger_not_null() -> None:
    from sqlalchemy import BigInteger

    col = PointDataRealtime.__table__.columns["point_id"]
    assert isinstance(col.type, BigInteger)
    assert col.nullable is False


def test_realtime_org_value_double_nullable() -> None:
    from sqlalchemy import Double

    col = PointDataRealtime.__table__.columns["org_value"]
    assert isinstance(col.type, Double)
    assert col.nullable is True


def test_realtime_rt_value_double_nullable() -> None:
    from sqlalchemy import Double

    col = PointDataRealtime.__table__.columns["rt_value"]
    assert isinstance(col.type, Double)
    assert col.nullable is True


def test_realtime_recorded_at_timestamptz_not_null() -> None:
    from sqlalchemy import DateTime

    col = PointDataRealtime.__table__.columns["recorded_at"]
    assert isinstance(col.type, DateTime)
    assert col.type.timezone is True
    assert col.nullable is False


# ---------------------------------------------------------------------------
# PointDataRealtime — postgresql_with storage params
# ---------------------------------------------------------------------------


def test_realtime_postgresql_with_fillfactor() -> None:
    """table.info['postgresql_with'] must include fillfactor=70.

    Storage params are stored as table metadata (not emitted by SQLAlchemy DDL directly).
    Stage D alembic applies: ALTER TABLE point_data_realtime SET (fillfactor=70, ...).
    """
    pg_with = PointDataRealtime.__table__.info.get("postgresql_with", {})
    assert "fillfactor" in pg_with
    assert str(pg_with["fillfactor"]) == "70"


def test_realtime_postgresql_with_autovacuum_params() -> None:
    """All 5 autovacuum params present in table.info['postgresql_with']."""
    pg_with = PointDataRealtime.__table__.info.get("postgresql_with", {})
    expected_keys = {
        "autovacuum_vacuum_scale_factor",
        "autovacuum_analyze_scale_factor",
        "autovacuum_vacuum_cost_limit",
        "autovacuum_vacuum_insert_scale_factor",
    }
    for key in expected_keys:
        assert key in pg_with, f"Missing postgresql_with key: {key}"


# ---------------------------------------------------------------------------
# PointDataRealtime — no mixin columns
# ---------------------------------------------------------------------------


def test_realtime_no_updated_at() -> None:
    cols = {c.name for c in PointDataRealtime.__table__.columns}
    assert "updated_at" not in cols


def test_realtime_no_created_at() -> None:
    cols = {c.name for c in PointDataRealtime.__table__.columns}
    assert "created_at" not in cols


def test_realtime_no_deleted_at() -> None:
    cols = {c.name for c in PointDataRealtime.__table__.columns}
    assert "deleted_at" not in cols


def test_realtime_no_usr_group() -> None:
    cols = {c.name for c in PointDataRealtime.__table__.columns}
    assert "usr_group" not in cols


# ---------------------------------------------------------------------------
# PointDataHistory — tablename & PK
# ---------------------------------------------------------------------------


def test_history_tablename() -> None:
    assert PointDataHistory.__tablename__ == "point_data_history"


def test_history_pk_includes_recorded_at() -> None:
    """Hypertable PK (dev_number, point_id, recorded_at)."""
    pk = {c.name for c in PointDataHistory.__table__.primary_key.columns}
    assert "recorded_at" in pk
    assert "dev_number" in pk
    assert "point_id" in pk


# ---------------------------------------------------------------------------
# PointDataHistory — all 5 columns present
# ---------------------------------------------------------------------------


def test_history_columns_complete() -> None:
    cols = {c.name for c in PointDataHistory.__table__.columns}
    assert cols == {"dev_number", "point_id", "org_value", "rt_value", "recorded_at"}


# ---------------------------------------------------------------------------
# PointDataHistory — index
# ---------------------------------------------------------------------------


def test_history_index_exists() -> None:
    idx = next(
        (
            ix
            for ix in PointDataHistory.__table__.indexes
            if ix.name == "ix_point_data_history_dev_number_point_id_recorded_at"
        ),
        None,
    )
    assert idx is not None, "ix_point_data_history_dev_number_point_id_recorded_at not found"


def test_history_index_contains_dev_number() -> None:
    idx = next(
        ix
        for ix in PointDataHistory.__table__.indexes
        if ix.name == "ix_point_data_history_dev_number_point_id_recorded_at"
    )
    expr_strs = [str(e) for e in idx.expressions]
    assert any("dev_number" in s for s in expr_strs)


def test_history_index_contains_point_id() -> None:
    idx = next(
        ix
        for ix in PointDataHistory.__table__.indexes
        if ix.name == "ix_point_data_history_dev_number_point_id_recorded_at"
    )
    expr_strs = [str(e) for e in idx.expressions]
    assert any("point_id" in s for s in expr_strs)


def test_history_index_contains_recorded_at_desc() -> None:
    idx = next(
        ix
        for ix in PointDataHistory.__table__.indexes
        if ix.name == "ix_point_data_history_dev_number_point_id_recorded_at"
    )
    expr_strs = [str(e) for e in idx.expressions]
    assert any("recorded_at" in s and "DESC" in s for s in expr_strs)


# ---------------------------------------------------------------------------
# PointDataHistory — no mixin columns
# ---------------------------------------------------------------------------


def test_history_no_updated_at() -> None:
    cols = {c.name for c in PointDataHistory.__table__.columns}
    assert "updated_at" not in cols


def test_history_no_created_at() -> None:
    cols = {c.name for c in PointDataHistory.__table__.columns}
    assert "created_at" not in cols


def test_history_no_deleted_at() -> None:
    cols = {c.name for c in PointDataHistory.__table__.columns}
    assert "deleted_at" not in cols


def test_history_no_usr_group() -> None:
    cols = {c.name for c in PointDataHistory.__table__.columns}
    assert "usr_group" not in cols


# ---------------------------------------------------------------------------
# PointDataHistory — docstring mentions hypertable and Stage D
# ---------------------------------------------------------------------------


def test_history_docstring_mentions_hypertable() -> None:
    assert PointDataHistory.__doc__ is not None
    assert "hypertable" in PointDataHistory.__doc__


def test_history_docstring_mentions_stage_d() -> None:
    assert PointDataHistory.__doc__ is not None
    assert "Stage D alembic" in PointDataHistory.__doc__


# ---------------------------------------------------------------------------
# WaveformHistory — tablename & PK
# ---------------------------------------------------------------------------


def test_waveform_tablename() -> None:
    assert WaveformHistory.__tablename__ == "waveform_history"


def test_waveform_pk_includes_recorded_at() -> None:
    """Hypertable PK (dev_number, point_id, recorded_at)."""
    pk = {c.name for c in WaveformHistory.__table__.primary_key.columns}
    assert "recorded_at" in pk
    assert "dev_number" in pk
    assert "point_id" in pk


# ---------------------------------------------------------------------------
# WaveformHistory — all 7 columns present
# ---------------------------------------------------------------------------


def test_waveform_columns_complete() -> None:
    cols = {c.name for c in WaveformHistory.__table__.columns}
    assert cols == {
        "dev_number",
        "point_id",
        "data_array",
        "tz_data_array",
        "sample_time_decisec",
        "packet_count",
        "recorded_at",
    }


# ---------------------------------------------------------------------------
# WaveformHistory — column types & nullable
# ---------------------------------------------------------------------------


def test_waveform_data_array_largebinary_not_null() -> None:
    from sqlalchemy import LargeBinary

    col = WaveformHistory.__table__.columns["data_array"]
    assert isinstance(col.type, LargeBinary)
    assert col.nullable is False


def test_waveform_tz_data_array_largebinary_nullable() -> None:
    from sqlalchemy import LargeBinary

    col = WaveformHistory.__table__.columns["tz_data_array"]
    assert isinstance(col.type, LargeBinary)
    assert col.nullable is True


def test_waveform_sample_time_decisec_smallinteger_not_null() -> None:
    from sqlalchemy import SmallInteger

    col = WaveformHistory.__table__.columns["sample_time_decisec"]
    assert isinstance(col.type, SmallInteger)
    assert col.nullable is False


def test_waveform_packet_count_smallinteger_not_null() -> None:
    from sqlalchemy import SmallInteger

    col = WaveformHistory.__table__.columns["packet_count"]
    assert isinstance(col.type, SmallInteger)
    assert col.nullable is False


def test_waveform_recorded_at_timestamptz_not_null() -> None:
    from sqlalchemy import DateTime

    col = WaveformHistory.__table__.columns["recorded_at"]
    assert isinstance(col.type, DateTime)
    assert col.type.timezone is True
    assert col.nullable is False


def test_waveform_dev_number_string50_not_null() -> None:
    from sqlalchemy import String

    col = WaveformHistory.__table__.columns["dev_number"]
    assert isinstance(col.type, String)
    assert col.type.length == 50
    assert col.nullable is False


def test_waveform_point_id_biginteger_not_null() -> None:
    from sqlalchemy import BigInteger

    col = WaveformHistory.__table__.columns["point_id"]
    assert isinstance(col.type, BigInteger)
    assert col.nullable is False


# ---------------------------------------------------------------------------
# WaveformHistory — no mixin columns
# ---------------------------------------------------------------------------


def test_waveform_no_updated_at() -> None:
    cols = {c.name for c in WaveformHistory.__table__.columns}
    assert "updated_at" not in cols


def test_waveform_no_created_at() -> None:
    cols = {c.name for c in WaveformHistory.__table__.columns}
    assert "created_at" not in cols


def test_waveform_no_deleted_at() -> None:
    cols = {c.name for c in WaveformHistory.__table__.columns}
    assert "deleted_at" not in cols


def test_waveform_no_usr_group() -> None:
    cols = {c.name for c in WaveformHistory.__table__.columns}
    assert "usr_group" not in cols


# ---------------------------------------------------------------------------
# WaveformHistory — docstring mentions hypertable and Stage D
# ---------------------------------------------------------------------------


def test_waveform_docstring_mentions_hypertable() -> None:
    assert WaveformHistory.__doc__ is not None
    assert "hypertable" in WaveformHistory.__doc__


def test_waveform_docstring_mentions_stage_d() -> None:
    assert WaveformHistory.__doc__ is not None
    assert "Stage D alembic" in WaveformHistory.__doc__


# ---------------------------------------------------------------------------
# __init__.py exports
# ---------------------------------------------------------------------------


def test_init_exports_point_data_realtime() -> None:
    from ruisheng_shared import models as reexported_models

    assert reexported_models.PointDataRealtime is PointDataRealtime


def test_init_exports_point_data_history() -> None:
    from ruisheng_shared import models as reexported_models

    assert reexported_models.PointDataHistory is PointDataHistory


def test_init_exports_waveform_history() -> None:
    from ruisheng_shared import models as reexported_models

    assert reexported_models.WaveformHistory is WaveformHistory


def test_init_all_contains_timeseries() -> None:
    from ruisheng_shared.models import __all__

    assert "PointDataRealtime" in __all__
    assert "PointDataHistory" in __all__
    assert "WaveformHistory" in __all__


# ---------------------------------------------------------------------------
# Stage D placeholder skip-tests
# ---------------------------------------------------------------------------


@pytest.mark.skip(
    reason="Stage D alembic: point_data_history + waveform_history hypertable + retention + compression"
)
def test_timeseries_hypertable_stage_d() -> None:
    """占位：Stage D 验证 hypertable + retention + compression 落地。"""
    ...


@pytest.mark.skip(reason="Stage D alembic: point_data_realtime fillfactor + autovacuum tuning")
def test_realtime_autovacuum_stage_d() -> None:
    """占位：Stage D 验证 point_data_realtime fillfactor=70 + autovacuum 参数生效。"""
    ...
