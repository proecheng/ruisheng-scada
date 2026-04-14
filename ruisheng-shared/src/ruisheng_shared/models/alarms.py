"""报警相关 3 张表。对应 spec §3.8 / §4（device_waring_cfgs / alarm_records / alarm_outbox）。

- alarm_records 将来会升级为 TimescaleDB hypertable，故 dev_number/point_id
  不设 FK（spec DDL 原文即不含 REFERENCES）。
- alarm_outbox 用作 alarm 事件发布 outbox，partial index 仅对 published=false 建索引。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Double,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin


class DeviceWaringCfg(Base, TimestampMixin):
    __tablename__ = "device_waring_cfgs"
    __table_args__ = (
        CheckConstraint(
            "alarm_type IN ('>','<','=','!=','LX')",
            name="alarm_type",  # → ck_device_waring_cfgs_alarm_type
        ),
        CheckConstraint(
            "'NaN' != limit_value::text AND 'Infinity' != abs(limit_value)::text",
            name="limit_value",  # → ck_device_waring_cfgs_limit_value
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    dev_number: Mapped[str] = mapped_column(
        String(50),
        ForeignKey("devices.dev_number", ondelete="CASCADE"),
        nullable=False,
    )
    point_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("device_points.id", ondelete="CASCADE"),
        nullable=False,
    )
    reg_bit: Mapped[int | None] = mapped_column(SmallInteger)
    alarm_name: Mapped[str] = mapped_column(String(100), nullable=False)
    alarm_type: Mapped[str] = mapped_column(String(4), nullable=False)
    limit_value: Mapped[float] = mapped_column(Double, nullable=False)
    relation_point_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("device_points.id")
    )
    relation_reg_bit: Mapped[int | None] = mapped_column(SmallInteger)
    relation_alarm_type: Mapped[str | None] = mapped_column(String(4))
    relation_limit_value: Mapped[float | None] = mapped_column(Double)
    enable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    phone_alarm: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reset_remind: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    dev_sync_flag: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    waring_flag: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    alarm_msg: Mapped[str | None] = mapped_column(String(255))


class AlarmRecord(Base):
    """报警事件表（将升级为 TimescaleDB hypertable，故 dev_number/point_id 无 FK）。"""

    __tablename__ = "alarm_records"
    __table_args__ = (
        Index(
            "idx_alarm_records_dev_triggered",
            "dev_number",
            text("triggered_at DESC"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    dev_number: Mapped[str] = mapped_column(String(50), nullable=False)
    point_id: Mapped[int | None] = mapped_column(BigInteger)
    alarm_name: Mapped[str | None] = mapped_column(String(100))
    alarm_msg: Mapped[str | None] = mapped_column(String(255))
    alarm_value: Mapped[float | None] = mapped_column(Double)
    channels_sent: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    triggered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reset_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    usr_group: Mapped[str] = mapped_column(String(50), nullable=False)


class AlarmOutbox(Base):
    """报警发布 outbox（spec §3.8.11）。"""

    __tablename__ = "alarm_outbox"
    __table_args__ = (
        Index(
            "idx_alarm_outbox_unpublished",
            "published",
            "created_at",
            postgresql_where="published = false",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    alarm_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("alarm_records.id"), nullable=False
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    published: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
