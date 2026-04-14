"""计划相关 3 张表。对应 spec §4.2 v1.3.3（timing_plans / maintain_plans / maintain_actions）。

- ``timing_plans``：v1.3.3 扩展版，新增 ``usr_group`` / ``deleted_at`` / ``updated_at``；
  保留 ``update_flag`` 供 gw 感知配置变更。
- ``maintain_plans``：保养计划模板；不下发 gw 故无 ``update_flag``；``plan_name``
  COLLATE ``zh-x-icu`` 以支持中文排序；partial UNIQUE ``(dev_number, plan_name)``
  仅在 ``deleted_at IS NULL`` 时生效（允许软删后同名重建）。
- ``maintain_actions``：保养执行审计表，一次写入；``action_uuid`` 使用 ULID（前端
  生成，弱网幂等）；``plan_id`` / ``dev_number`` **无 FK**（弱引用，设备或计划删除
  后仍永久留痕，类比 ``user_control_actions`` / ``alarm_records``）。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, SoftDeleteMixin, TimestampMixin


class TimingPlan(Base, TimestampMixin, SoftDeleteMixin):
    """定时计划（spec §4.2 timing_plans，v1.3.3 扩展）。"""

    __tablename__ = "timing_plans"
    __table_args__ = (
        Index("ix_timing_plans_dev_action", "dev_number", "action_at"),
        Index(
            "ix_timing_plans_due",
            "action_at",
            postgresql_where=text("enable = true AND deleted_at IS NULL"),
        ),
        Index("ix_timing_plans_usr_group", "usr_group"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    dev_number: Mapped[str] = mapped_column(
        String(50),
        ForeignKey("devices.dev_number", ondelete="RESTRICT"),
        nullable=False,
    )
    action_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    action: Mapped[int] = mapped_column(Integer, nullable=False)
    repetition: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    enable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    update_flag: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    usr_group: Mapped[str] = mapped_column(
        String(50),
        ForeignKey("wx_groups.usr_group", ondelete="RESTRICT"),
        nullable=False,
    )


class MaintainPlan(Base, TimestampMixin, SoftDeleteMixin):
    """保养计划模板（spec §4.2 maintain_plans，v1.3.3 新增）。"""

    __tablename__ = "maintain_plans"
    __table_args__ = (
        CheckConstraint(
            "interval_days BETWEEN 1 AND 3650",
            name="interval_days",  # → ck_maintain_plans_interval_days
        ),
        # 60s 容差，见 spec §4.1：允许 next_due_at 相对 created_at 早 1 分钟，
        # 用于抗时钟漂移场景（前端 -> 服务端写入时 now() 可能稍晚）。
        CheckConstraint(
            "next_due_at >= created_at - INTERVAL '1 minute'",
            name="next_due_after_created",  # → ck_maintain_plans_next_due_after_created
        ),
        Index("ix_maintain_plans_dev_number", "dev_number"),
        Index("ix_maintain_plans_usr_group", "usr_group"),
        Index(
            "ix_maintain_plans_next_due_active",
            "next_due_at",
            postgresql_where=text("enable = true AND deleted_at IS NULL"),
        ),
        # partial UNIQUE：仅活跃行（未软删）内强制 (dev_number, plan_name) 唯一。
        # SQLAlchemy 的 UniqueConstraint 不支持 WHERE 子句，故用 Index(unique=True)。
        Index(
            "ux_maintain_plans_dev_plan_name",
            "dev_number",
            "plan_name",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    dev_number: Mapped[str] = mapped_column(
        String(50),
        ForeignKey("devices.dev_number", ondelete="RESTRICT"),
        nullable=False,
    )
    plan_name: Mapped[str] = mapped_column(String(100, collation="zh-x-icu"), nullable=False)
    description: Mapped[str | None] = mapped_column(String(255))
    interval_days: Mapped[int] = mapped_column(Integer, nullable=False)
    next_due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    enable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    usr_group: Mapped[str] = mapped_column(
        String(50),
        ForeignKey("wx_groups.usr_group", ondelete="RESTRICT"),
        nullable=False,
    )


class MaintainAction(Base):
    """保养执行审计（spec §4.2 maintain_actions，v1.3.3 新增）。

    一次写入，无 TimestampMixin / 无软删；``plan_id`` / ``dev_number`` 无 FK
    (弱引用)，类比 ``user_control_actions`` / ``alarm_records``。
    """

    __tablename__ = "maintain_actions"
    __table_args__ = (
        UniqueConstraint("action_uuid", name="action_uuid"),  # → uq_maintain_actions_action_uuid
        Index(
            "ix_maintain_actions_plan_acted",
            "plan_id",
            text("acted_at DESC"),
        ),
        Index(
            "ix_maintain_actions_dev_acted",
            "dev_number",
            text("acted_at DESC"),
        ),
        Index(
            "ix_maintain_actions_user_acted",
            "user_name",
            text("acted_at DESC"),
        ),
        Index("ix_maintain_actions_usr_group", "usr_group"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    action_uuid: Mapped[str] = mapped_column(String(26), nullable=False)
    plan_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    dev_number: Mapped[str] = mapped_column(String(50), nullable=False)
    acted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    user_name: Mapped[str] = mapped_column(
        String(50),
        ForeignKey("users.user_name", ondelete="RESTRICT"),
        nullable=False,
    )
    note: Mapped[str | None] = mapped_column(String(1000))
    usr_group: Mapped[str] = mapped_column(
        String(50),
        ForeignKey("wx_groups.usr_group", ondelete="RESTRICT"),
        nullable=False,
    )
