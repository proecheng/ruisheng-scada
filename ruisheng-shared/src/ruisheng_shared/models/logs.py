"""日志相关 2 张表。对应 spec §4.2 v1.3.6（soft_logs / user_login_records）。

- ``soft_logs``：系统级软件日志审计表（WARN/ERROR/CRITICAL）。
  仅继承 ``Base``，无 TimestampMixin / SoftDeleteMixin。
  无 usr_group / 无 RLS：系统级审计（类比 pay_orders_seen）。
  INSERT-only；retention policy 1 年清理（见 spec §4.2）。
  角色授权（Stage D alembic 落地）：
    REVOKE ALL ON soft_logs FROM PUBLIC;
    GRANT INSERT ON soft_logs TO ruisheng_gw;
    GRANT INSERT, SELECT ON soft_logs TO ruisheng_api;

- ``user_login_records``：Web 端登录审计表（v1.3.6 新增）。
  仅继承 ``Base``，无 TimestampMixin / SoftDeleteMixin。
  user_name 弱引用无 FK（审计永久留痕，用户软删后记录保留，类比 maintain_actions.dev_number）。
  usr_group FK → wx_groups(usr_group) ON DELETE RESTRICT（多租户隔离）。
  INSERT-only；retention policy 3 年清理（见 spec §4.2）。
  角色授权（Stage D alembic 落地）：
    REVOKE ALL ON user_login_records FROM PUBLIC;
    GRANT INSERT, SELECT ON user_login_records TO ruisheng_api;
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class SoftLog(Base):
    """系统软件日志审计（spec §4.2 soft_logs v1.3.6）。

    无 usr_group / 无 RLS：系统级审计，类比 pay_orders_seen。
    INSERT-only；retention policy 1 year。
    Stage D alembic: hypertable + retention + compression 在 Stage D alembic 落地。
    REVOKE/GRANT 见 spec §4.2。
    """

    __tablename__ = "soft_logs"
    __table_args__ = (
        CheckConstraint(
            "level IN ('WARN','ERROR','CRITICAL')",
            name="level",  # → ck_soft_logs_level
        ),
        CheckConstraint(
            "source IN ('gw','api','worker')",
            name="source",  # → ck_soft_logs_source
        ),
        Index("ix_soft_logs_level_recorded_at", "level", text("recorded_at DESC")),
        Index("ix_soft_logs_source_recorded_at", "source", text("recorded_at DESC")),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    level: Mapped[str] = mapped_column(String(10), nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    msg: Mapped[str] = mapped_column(String(500), nullable=False)
    context: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class UserLoginRecord(Base):
    """Web 端登录审计（spec §4.2 user_login_records v1.3.6）。

    user_name 弱引用无 FK：审计永久留痕，用户软删/注销后记录保留
    （类比 maintain_actions.dev_number；见 spec §4.6）。
    usr_group FK → wx_groups(usr_group) ON DELETE RESTRICT（多租户隔离）。
    INSERT-only；Stage D alembic: hypertable + retention 3 years + compression
    segmentby=usr_group 在 Stage D alembic 落地。见 spec §4.2 v1.3.6。
    """

    __tablename__ = "user_login_records"
    __table_args__ = (
        Index("ix_user_login_records_usr_group_logged_at", "usr_group", text("logged_at DESC")),
        Index("ix_user_login_records_user_name_logged_at", "user_name", text("logged_at DESC")),
        Index(
            "ix_user_login_records_ip_fail",
            "ip_addr",
            text("logged_at DESC"),
            postgresql_where=text("NOT success"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_name: Mapped[str] = mapped_column(String(50), nullable=False)
    logged_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    ip_addr: Mapped[str] = mapped_column(INET, nullable=False)
    city: Mapped[str | None] = mapped_column(String(100), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(500), nullable=True)
    success: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
    )
    usr_group: Mapped[str] = mapped_column(
        String(50),
        ForeignKey("wx_groups.usr_group", ondelete="RESTRICT"),
        nullable=False,
    )
