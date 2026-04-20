"""支付相关 2 张表。对应 spec §4.2 v1.3.5（pay_orders / pay_orders_seen）。

- ``pay_orders``：微信支付订单审计表。PK 例外：``out_trade_no`` 直接作 PK（微信
  协议全链路以 out_trade_no 为键，见 §4.6）；无 ``id BIGSERIAL`` 列。
  继承 ``TimestampMixin``（created_at / updated_at）和 ``SoftDeleteMixin``（deleted_at）。
  ``usr_group`` FK → ``wx_groups.usr_group`` ON DELETE RESTRICT（多租户隔离）。
  3 个 biconditional / time CHECK 约束（类比 user_control_actions.completed_at）：
    (1) total_fee >= 0
    (2) (pay_state = 'paid') = (paid_at IS NOT NULL)
    (3) refund_at IS NULL OR paid_at IS NOT NULL（退款必先于支付）
    (4) refund_at IS NULL OR refund_at >= paid_at（退款时间 ≥ 支付时间）
  3 个 partial index（WHERE deleted_at IS NULL 或 WHERE pay_state = 'pending'）。

  **触发器（Stage D alembic migration 落地）**：
  ``trg_pay_orders_updated BEFORE UPDATE → set_updated_at()``

  **RLS（Stage D 落地）**：
  ``ENABLE ROW LEVEL SECURITY``；``tenant_isolation`` policy（NOT FORCE）；
  ruisheng_gw 回调走 BYPASSRLS；ruisheng_api 走 SET LOCAL 租户上下文。

- ``pay_orders_seen``：微信支付回调幂等守门表（spec §4.2 / §5.10.3）。
  仅继承 ``Base``，无 TimestampMixin / SoftDeleteMixin。
  无 ``usr_group``（回调无 tenant 上下文，类比 soft_logs）；无 RLS。
  BRIN 索引 ``ix_pay_orders_seen_notified_at_brin``（时序追加 + 30d TTL 清理）。
  角色授权：``GRANT INSERT,SELECT,DELETE TO ruisheng_gw``；
           ``GRANT SELECT TO ruisheng_api``（见 §4.2）。
  清理 Job：``pay_orders_seen_cleanup`` 每天 02:00 删除 notified_at < now() - 30d
  （见 §5.10.3）。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, SoftDeleteMixin, TimestampMixin


class PayOrder(Base, TimestampMixin, SoftDeleteMixin):
    """微信支付订单（spec §4.2 pay_orders v1.3.5）。

    PK 例外（§4.6）：out_trade_no 直接作 PK；无 id BIGSERIAL 列。
    openid 无 CHECK（CHECK 交 pydantic，见 spec §4.2 注释 + D3 决策）。
    mark_paid(db, out_trade_no, total_fee, time_end) → §3.5；db 参数为 gw_pool 连接（BYPASSRLS）
    """

    __tablename__ = "pay_orders"
    __table_args__ = (
        # (1) 金额非负
        CheckConstraint(
            "total_fee >= 0",
            name="total_fee_nonneg",  # → ck_pay_orders_total_fee_nonneg
        ),
        # (2) pay_state 枚举 6 值
        # 终态：paid / refund / cancelled / expired（不可再转 paid）
        # 非终态：pending / failed（回调成功后可转 paid）
        CheckConstraint(
            "pay_state IN ('pending','paid','failed','refund','cancelled','expired')",
            name="pay_state",  # → ck_pay_orders_pay_state
        ),
        # (3) biconditional: (pay_state = 'paid') ⇔ (paid_at IS NOT NULL)
        CheckConstraint(
            "(pay_state = 'paid') = (paid_at IS NOT NULL)",
            name="paid_biconditional",  # → ck_pay_orders_paid_biconditional
        ),
        # (4) 退款必先于支付：refund_at IS NULL OR paid_at IS NOT NULL
        CheckConstraint(
            "refund_at IS NULL OR paid_at IS NOT NULL",
            name="refund_requires_paid",  # → ck_pay_orders_refund_requires_paid
        ),
        # (5) 退款时间 ≥ 支付时间
        CheckConstraint(
            "refund_at IS NULL OR refund_at >= paid_at",
            name="refund_after_paid",  # → ck_pay_orders_refund_after_paid
        ),
        # Partial indexes
        Index(
            "ix_pay_orders_usr_group_created",
            "usr_group",
            text("created_at DESC"),
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "ix_pay_orders_openid_created",
            "openid",
            text("created_at DESC"),
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "ix_pay_orders_pending_created",
            "created_at",
            postgresql_where=text("pay_state = 'pending' AND deleted_at IS NULL"),
        ),
    )

    out_trade_no: Mapped[str] = mapped_column(String(50), primary_key=True)
    openid: Mapped[str] = mapped_column(String(100), nullable=False)
    usr_group: Mapped[str] = mapped_column(
        String(50),
        ForeignKey("wx_groups.usr_group", ondelete="RESTRICT"),
        nullable=False,
    )
    total_fee: Mapped[int] = mapped_column(Integer, nullable=False)
    body: Mapped[str | None] = mapped_column(String(255))
    pay_state: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default="pending",
    )
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    refund_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PayOrderSeen(Base):
    """微信支付回调幂等守门表（spec §4.2 pay_orders_seen，§5.10.3）。

    定位：系统级幂等防护，类比 soft_logs；不参与多租户/业务查询。
    无 RLS：回调无 tenant 上下文（类比 soft_logs）。
    清理 Job：每天 02:00 DELETE WHERE notified_at < now() - INTERVAL '30 days'。
    角色授权（Stage D 落地）：
      REVOKE ALL ON pay_orders_seen FROM PUBLIC;
      GRANT INSERT, SELECT, DELETE ON pay_orders_seen TO ruisheng_gw;
      GRANT SELECT ON pay_orders_seen TO ruisheng_api;
    """

    __tablename__ = "pay_orders_seen"
    __table_args__ = (
        Index(
            "ix_pay_orders_seen_notified_at_brin",
            "notified_at",
            postgresql_using="brin",
        ),
        # 时序追加 + 30d TTL 清理，BRIN 压缩率高（vs BTREE 需按时间全量索引）
    )

    out_trade_no: Mapped[str] = mapped_column(String(50), primary_key=True)
    notified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
