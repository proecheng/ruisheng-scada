"""trg_<table>_updated triggers (13 tables)

Revision ID: 89a9dfebe138
Revises: 09676586bfbd
Create Date: 2026-04-16 22:38:09.122236

Spec 依据：
- §4.1   L964 通用约定（命名 trg_<table>_updated）
- §4.1.1 (1) L978-979 — 引用 D4 部署的 set_updated_at() 函数
- §4.2   各表 DDL 示例（L1269 / L1300 / L1360 / L1485 / L1526 等）展示 BEFORE UPDATE 触发器约定

Plan：§Task D5 (post Plan-bug-#3-fix in master commit 1490ef5)

13 张表清单（仅含 TimestampMixin 的实体表，与 ORM Base.metadata 严格对齐）：
- 业务核心：wx_groups, users
- 设备域：devices, device_points, device_static_data, sim_cards,
          device_templates, device_waring_cfgs
- 计划域：timing_plans, maintain_plans
- 场景域：scene_pages, scene_views
- 支付域：pay_orders

故意排除 4 类（共 11 张表无 updated_at 列）：
- 时序覆盖写：point_data_realtime（覆盖写语义，自带 update_time，不走 TimestampMixin）
- 审计/日志：soft_logs, user_login_records, maintain_actions,
              user_control_actions, pay_orders_seen, alarm_outbox
- 关联/不变表（仅 created_at 或域时间戳）：
  user_wx_bindings (bound_at), user_phone_numbers, user_emails,
  alarm_records (triggered_at + reset_at)

幂等性：upgrade 走 DROP IF EXISTS + CREATE 配对（防 prod 偶发重跑）。
downgrade 仅 DROP TRIGGER —— set_updated_at() 函数由 D4 拥有，本迁移不删。
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "89a9dfebe138"
down_revision: str | Sequence[str] | None = "09676586bfbd"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# 必须与 ORM Base.metadata.tables 中含 updated_at 列的表完全一致（13 张）
# pre-dispatch ORM scan 已确认（master commit 1490ef5 修订 Plan bug #3）
UPDATED_AT_TABLES: list[str] = [
    "wx_groups",
    "users",
    "devices",
    "device_points",
    "device_static_data",
    "sim_cards",
    "device_templates",
    "device_waring_cfgs",
    "timing_plans",
    "maintain_plans",
    "scene_pages",
    "scene_views",
    "pay_orders",
]


def upgrade() -> None:
    """Upgrade schema."""
    # --- drift detection: 列表 vs ORM metadata ---
    # lazy import：避免在 module import 阶段触发 ruisheng_shared 解析
    from ruisheng_shared.models import Base

    orm_updated = {t.name for t in Base.metadata.tables.values() if "updated_at" in t.columns}
    plan_updated = set(UPDATED_AT_TABLES)
    missing = orm_updated - plan_updated
    extra = plan_updated - orm_updated
    if missing or extra:
        raise RuntimeError(
            f"UPDATED_AT_TABLES drift from ORM:\n"
            f"  ORM 有但迁移没列: {sorted(missing)}\n"
            f"  迁移列了但 ORM 没: {sorted(extra)}\n"
            f"  修法：对齐列表或调整 ORM TimestampMixin"
        )

    # --- 主循环：DROP + CREATE 保证幂等 ---
    for t in UPDATED_AT_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS trg_{t}_updated ON {t};")
        op.execute(
            f"CREATE TRIGGER trg_{t}_updated "
            f"BEFORE UPDATE ON {t} FOR EACH ROW "
            f"EXECUTE FUNCTION set_updated_at();"
        )


def downgrade() -> None:
    """Downgrade schema."""
    for t in UPDATED_AT_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS trg_{t}_updated ON {t};")
    # 注：函数 set_updated_at() 由 D4 管理，本迁移不删
