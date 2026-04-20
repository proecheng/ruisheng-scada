"""时序数据 3 张表。对应 spec §4.2（point_data_realtime / point_data_history / waveform_history）。

- ``point_data_realtime``：设备实时测量值覆盖写表（UPDATE-heavy）。
  复合 PK (dev_number, point_id)；无 id BIGSERIAL 列。
  fillfactor=70 + autovacuum 参数强化，防止 UPDATE-heavy 造成表膨胀（spec §4.2）。
  无 usr_group / 无 RLS / 无 TimestampMixin / 无 SoftDeleteMixin。
  角色授权：GRANT SELECT, INSERT, UPDATE ON ALL TABLES TO ruisheng_gw（§4.1.1）。

- ``point_data_history``：设备历史测量值（TimescaleDB hypertable，INSERT-only）。
  复合 PK (dev_number, point_id, recorded_at)；TimescaleDB hypertable PK 必须含时间列。
  降序复合索引：ix_point_data_history_dev_number_point_id_recorded_at。
  无 usr_group / 无 RLS / 无 TimestampMixin / 无 SoftDeleteMixin。
  Stage D alembic: hypertable chunk_time_interval=1 month + retention 1 year +
  compression segmentby='dev_number, point_id' compress_after='7 days' 在 Stage D 落地。

- ``waveform_history``：波形 BLOB 历史（TimescaleDB hypertable，INSERT-only）。
  复合 PK (dev_number, point_id, recorded_at)；TimescaleDB hypertable PK 必须含时间列。
  data_array BYTEA NOT NULL；tz_data_array BYTEA nullable；
  sample_time_decisec / packet_count SMALLINT NOT NULL。
  无 usr_group / 无 RLS / 无 TimestampMixin / 无 SoftDeleteMixin。
  Stage D alembic: hypertable chunk_time_interval=1 month + retention 1 year +
  compression segmentby='dev_number' compress_after='7 days' 在 Stage D 落地。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Double, Index, LargeBinary, SmallInteger, String, text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class PointDataRealtime(Base):
    """设备实时测量值覆盖写（spec §4.2 point_data_realtime）。

    UPDATE-heavy；复合 PK (dev_number, point_id)；无 id 列。
    fillfactor=70 + autovacuum 强化防膨胀（见 spec §4.2 WITH 子句）。
    无 usr_group / 无 RLS：访问控制见 §4.1.1 角色级 GRANT。
    Stage D alembic: point_data_realtime fillfactor + autovacuum tuning 在 Stage D 落地。
    """

    __tablename__ = "point_data_realtime"
    # postgresql_with parameters are stored as table metadata for Stage D Alembic migration.
    # They cannot be emitted by SQLAlchemy 2.0 DDL directly (not supported as dialect_option
    # on Table); Stage D alembic applies: ALTER TABLE point_data_realtime SET (fillfactor=70, ...).
    __table_args__ = {
        "info": {
            "postgresql_with": {
                "fillfactor": "70",
                "autovacuum_vacuum_scale_factor": "0.05",
                "autovacuum_analyze_scale_factor": "0.02",
                "autovacuum_vacuum_cost_limit": "1000",
                "autovacuum_vacuum_insert_scale_factor": "0.1",
            }
        }
    }

    dev_number: Mapped[str] = mapped_column(String(50), primary_key=True, nullable=False)
    point_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, nullable=False)
    org_value: Mapped[float | None] = mapped_column(Double, nullable=True)
    rt_value: Mapped[float | None] = mapped_column(Double, nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PointDataHistory(Base):
    """设备历史测量值（spec §4.2 point_data_history，TimescaleDB hypertable）。

    INSERT-only；复合 PK (dev_number, point_id, recorded_at)。
    TimescaleDB hypertable PK 必须包含时间列（recorded_at）。
    降序复合索引 ix_point_data_history_dev_number_point_id_recorded_at。
    无 usr_group / 无 RLS：访问控制见 §4.1.1 角色级 GRANT。
    Stage D alembic: hypertable chunk_time_interval=1 month + retention 1 year +
    compression segmentby='dev_number, point_id' compress_after='7 days' 在 Stage D 落地。
    """

    __tablename__ = "point_data_history"
    __table_args__ = (
        Index(
            "ix_point_data_history_dev_number_point_id_recorded_at",
            "dev_number",
            "point_id",
            text("recorded_at DESC"),
        ),
    )

    dev_number: Mapped[str] = mapped_column(String(50), primary_key=True, nullable=False)
    point_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, nullable=False)
    org_value: Mapped[float | None] = mapped_column(Double, nullable=True)
    rt_value: Mapped[float | None] = mapped_column(Double, nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True, nullable=False
    )


class WaveformHistory(Base):
    """波形 BLOB 历史（spec §4.2 waveform_history，TimescaleDB hypertable）。

    INSERT-only；复合 PK (dev_number, point_id, recorded_at)。
    TimescaleDB hypertable PK 必须包含时间列（recorded_at）。
    data_array BYTEA NOT NULL；tz_data_array BYTEA nullable。
    sample_time_decisec / packet_count SMALLINT NOT NULL。
    无 usr_group / 无 RLS：访问控制见 §4.1.1 角色级 GRANT。
    Stage D alembic: hypertable chunk_time_interval=1 month + retention 1 year +
    compression segmentby='dev_number' compress_after='7 days' 在 Stage D 落地。
    """

    __tablename__ = "waveform_history"

    dev_number: Mapped[str] = mapped_column(String(50), primary_key=True, nullable=False)
    point_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, nullable=False)
    data_array: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    tz_data_array: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    sample_time_decisec: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    packet_count: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True, nullable=False
    )
