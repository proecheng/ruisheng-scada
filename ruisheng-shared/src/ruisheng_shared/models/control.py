"""用户控制指令审计表。对应 spec §3.5 / §4（user_control_actions）。

- dev_number / user_name 无 FK：与 alarm_records 一致，属于将来可能升级为
  TimescaleDB hypertable 的事件流表，spec DDL 原文即不含 REFERENCES。
- result CHECK 5 值 ∈ {pending, success, failed, timeout, cancelled}；
  cmd_id 使用显式 ``UniqueConstraint`` 以便测试断言 Alembic 命名模板生成的
  ``uq_user_control_actions_cmd_id``。
- 额外 CheckConstraint ``result_completed_consistency``：保证 ``result='pending'``
  与 ``completed_at IS NULL`` 同时成立或同时不成立（类比 pay_orders 的
  pay_state/paid_at 一致性约束）。spec DDL 未写死此约束，但 spec §3.5 控制
  状态机及 line 766 "result='cancelled', completed_at=now()" 都隐含该语义，
  controller 已批准将其作为安全网写入 schema。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Index,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class UserControlAction(Base):
    """用户控制指令审计（spec §3.5 / §4 user_control_actions）。"""

    __tablename__ = "user_control_actions"
    __table_args__ = (
        CheckConstraint(
            "result IN ('pending','success','failed','timeout','cancelled')",
            name="result",  # → ck_user_control_actions_result
        ),
        # biconditional: (result = 'pending') ⇔ (completed_at IS NULL)
        # terminal states (success/failed/timeout/cancelled) must have completed_at set;
        # pending must not. See spec §3.5 control state machine.
        CheckConstraint(
            "(result = 'pending') = (completed_at IS NULL)",
            name="result_completed_consistency",
            # → ck_user_control_actions_result_completed_consistency
        ),
        UniqueConstraint("cmd_id", name="cmd_id"),  # → uq_user_control_actions_cmd_id
        Index(
            "idx_user_control_actions_dev_acted",
            "dev_number",
            text("acted_at DESC"),
        ),
        Index(
            "idx_user_control_actions_user_acted",
            "user_name",
            text("acted_at DESC"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    dev_number: Mapped[str] = mapped_column(String(50), nullable=False)
    user_name: Mapped[str] = mapped_column(String(50), nullable=False)
    action: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    cmd_id: Mapped[str | None] = mapped_column(String(32))
    result: Mapped[str | None] = mapped_column(String(20))
    acted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    usr_group: Mapped[str] = mapped_column(String(50), nullable=False)
