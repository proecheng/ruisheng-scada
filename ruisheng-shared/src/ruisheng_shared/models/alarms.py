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
    UniqueConstraint,
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
    """报警事件表（D8 TimescaleDB hypertable；dev_number/point_id 无 FK）。

    PK (id, triggered_at) 复合：D8 转 hypertable 的 TimescaleDB 硬要求
    （TS 2.16.1 规则：PRIMARY KEY / UNIQUE 必须包含分区列）。
    id 自身 BIGSERIAL 唯一，复合只为满足 TS 约束，不改变语义。
    """

    __tablename__ = "alarm_records"
    __table_args__ = (
        Index(
            "idx_alarm_records_dev_triggered",
            "dev_number",
            text("triggered_at DESC"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    alarm_cfg_id: Mapped[int | None] = mapped_column(BigInteger)
    dev_number: Mapped[str] = mapped_column(String(50), nullable=False)
    point_id: Mapped[int | None] = mapped_column(BigInteger)
    alarm_name: Mapped[str | None] = mapped_column(String(100))
    alarm_msg: Mapped[str | None] = mapped_column(String(255))
    alarm_value: Mapped[float | None] = mapped_column(Double)
    alarm_type: Mapped[str | None] = mapped_column(String(4))
    limit_value: Mapped[float | None] = mapped_column(Double)
    channels_sent: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    triggered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, primary_key=True
    )
    reset_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    usr_group: Mapped[str] = mapped_column(String(50), nullable=False)


class AlarmOutbox(Base):
    """报警发布 outbox（spec §3.8.11）。

    alarm_id 去 FK 约束（D8 Plan bug #5）：alarm_records 为 TimescaleDB hypertable，
    TS 2.16.1 拒绝 FK → hypertable。完整性依靠 app 层（publish job 读 alarm_records
    时按 alarm_id 外连，缺失行跳过即可，不影响 outbox 语义）。
    """

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
    alarm_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    published: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AlarmNotificationSubscription(Base, TimestampMixin):
    """A tenant-owned explicit alarm recipient and channel selection."""

    __tablename__ = "alarm_notification_subscriptions"
    __table_args__ = (
        CheckConstraint(
            "channel IN ('wechat','email','sms_custom_http','voice_custom_http')",
            name="channel",
        ),
        Index(
            "uq_alarm_notification_subscriptions_active_target",
            "alarm_cfg_id",
            "user_name",
            "channel",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_alarm_notification_subscriptions_tenant_cfg", "usr_group", "alarm_cfg_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    alarm_cfg_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    user_name: Mapped[str] = mapped_column(String(50), nullable=False)
    channel: Mapped[str] = mapped_column(String(30), nullable=False)
    usr_group: Mapped[str] = mapped_column(String(50), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class NotificationDispatch(Base):
    """Idempotent materialization result for one immutable alarm identity."""

    __tablename__ = "notification_dispatches"
    __table_args__ = (
        CheckConstraint("status IN ('materialized','no_subscription')", name="status"),
        UniqueConstraint(
            "alarm_id",
            "alarm_triggered_at",
            "alarm_cfg_id",
            name="alarm_identity",
        ),
        Index("ix_notification_dispatches_tenant_created", "usr_group", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    alarm_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    alarm_triggered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    alarm_cfg_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    usr_group: Mapped[str] = mapped_column(String(50), nullable=False)
    trace_id: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class NotificationDelivery(Base):
    """A logical notification target with a database-clock lease and fencing version."""

    __tablename__ = "notification_deliveries"
    __table_args__ = (
        CheckConstraint(
            "channel IN ('wechat','email','sms_custom_http','voice_custom_http')",
            name="channel",
        ),
        CheckConstraint(
            "status IN ('pending','retry','leased','sent','failed','skipped')",
            name="status",
        ),
        CheckConstraint("lease_version >= 0", name="lease_version"),
        CheckConstraint("attempt_count >= 0", name="attempt_count"),
        UniqueConstraint(
            "dispatch_id",
            "user_name",
            "channel",
            "contact_ref",
            name="logical_target",
        ),
        Index(
            "ix_notification_deliveries_ready",
            "next_attempt_at",
            "leased_until",
            postgresql_where=text("status IN ('pending','retry','leased')"),
        ),
        Index("ix_notification_deliveries_tenant_created", "usr_group", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    dispatch_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("notification_dispatches.id", ondelete="CASCADE"),
        nullable=False,
    )
    usr_group: Mapped[str] = mapped_column(String(50), nullable=False)
    user_name: Mapped[str] = mapped_column(String(50), nullable=False)
    channel: Mapped[str] = mapped_column(String(30), nullable=False)
    contact_ref: Mapped[str] = mapped_column(String(64), nullable=False)
    contact_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    leased_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_owner: Mapped[str | None] = mapped_column(String(100))
    lease_version: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    last_error_class: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class NotificationDeliveryAttempt(Base):
    """Sanitized delivery audit record; never stores target or provider payload."""

    __tablename__ = "notification_delivery_attempts"
    __table_args__ = (
        CheckConstraint("outcome IN ('sent','retry','failed','skipped','stale')", name="outcome"),
        UniqueConstraint("delivery_id", "attempt_no", name="delivery_attempt"),
        Index("ix_notification_delivery_attempts_tenant_finished", "usr_group", "finished_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    delivery_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("notification_deliveries.id", ondelete="CASCADE"),
        nullable=False,
    )
    usr_group: Mapped[str] = mapped_column(String(50), nullable=False)
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    worker_id: Mapped[str] = mapped_column(String(100), nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    error_class: Mapped[str | None] = mapped_column(String(40))
    http_status: Mapped[int | None] = mapped_column(Integer)
    retry_after_sec: Mapped[int | None] = mapped_column(Integer)
    detail: Mapped[str | None] = mapped_column(String(200))
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
