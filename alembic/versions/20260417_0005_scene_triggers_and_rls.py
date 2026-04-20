"""scene tenant triggers + RLS tenant_isolation (12 tables)

Revision ID: da5852f072d5
Revises: 89a9dfebe138
Create Date: 2026-04-17 16:34:25.415353

Spec 依据：
- §3.7   L670-722 — RLS 三层防护 + gw BYPASSRLS + scene_* 租户一致性
- §4.1.1 (4)(5) — PL/pgSQL 函数 enforce_scene_tenant_consistency / fill_scene_views_snapshot
                   （D4 已部署，本迁移只加触发器，不重建函数）
- §4.2   L1487-1495 — scene_pages BEFORE INSERT OR UPDATE OF owner_user_name, usr_group
- §4.2   L1529-1541 — scene_views BEFORE INSERT OR UPDATE OF
                      owner_user_name, usr_group, scene_page_id, dev_number
                      + BEFORE INSERT fill_scene_views_snapshot
- §3.7   L686 / §4.1.1 L989 — RLS 变量名权威：`app.tenant_id` + `app.role`
                              （严禁 app.current_usr_group 等旧命名）

Plan：§Task D6（Plan v1.2，controller 已反向 fix Plan bug #4 —— RLS_TABLES 19→12）

========== 块 A：3 个 scene 专用触发器 ==========

字母序约定 (enforce < fill < updated)：
  PostgreSQL 同一表 / 同一 timing (BEFORE) / 同一 event (INSERT|UPDATE) / 同一 level (ROW)
  的多个触发器按 *触发器名字母序* 触发（PG 官方文档 CREATE TRIGGER Notes）。
  本项目约定命名：
      trg_<table>_enforce_tenant   先跑：校验/修正 usr_group & owner_user_name
      trg_<table>_fill_snapshot    再跑：填充冗余快照列（scene_views 专属）
      trg_<table>_updated          最后：D5 已部署的 set_updated_at()
  三者字母序正好等于期望执行顺序。**禁止**未来把任一触发器重命名打乱字母序
  （例如改成 trg_scene_views_zzz_enforce_tenant 会让 updated 抢先跑；
   维持 enforce → fill → updated 前缀即可自动保序）。

- trg_scene_pages_enforce_tenant：scene_pages 只有 enforce（无 scene_page_id/dev_number）
- trg_scene_views_enforce_tenant + trg_scene_views_fill_snapshot：scene_views 两段
  （fill_snapshot 仅 BEFORE INSERT —— 快照列一次性固化，UPDATE 不刷）

========== 块 B：12 张表 ENABLE + FORCE + tenant_isolation policy ==========

RLS_TABLES 权威清单（12 张，带 usr_group 列 & 非 wx_groups 本身）：
  业务表：users, user_wx_bindings, devices, alarm_records,
           timing_plans, maintain_plans, scene_pages, scene_views, pay_orders
  审计表：user_control_actions, maintain_actions, user_login_records

**为什么 satellite 表不入 RLS_TABLES**（spec §3.7 L676 权威判据）：
  user_emails / user_phone_numbers / device_points / device_waring_cfgs /
  sim_cards / alarm_outbox / soft_logs 等 satellite 表通过 FK→父表 继承租户，
  **没有自身 usr_group 列**。如果把它们列入 RLS_TABLES，policy 的
  `usr_group = current_setting('app.tenant_id', true)` 会在 CREATE POLICY 阶段
  炸 `column does not exist`。spec §3.7 L676 判据："对所有业务表
  （**带 usr_group 字段的**）启用 RLS" —— 精确到列存在。

**FORCE ROW LEVEL SECURITY**：让表 owner (ruisheng_dev) 也受 policy 约束（不绕）；
  只有显式 BYPASSRLS 角色 (ruisheng_gw) 可绕。gw 用于内网 TCP 监听入设备数据，
  api 用于 HTTP 服务（带 app.tenant_id）。详见 spec §3.7 L670-722。

**policy 语义**：USING + WITH CHECK 都设——查询与写入一致受限；
  usr_group 匹配 app.tenant_id，或 app.role='Administrators' 时豁免。

========== 函数所有权 ==========

downgrade 仅删 3 scene 触发器 + 12 policy + FORCE/ENABLE RLS；
**不**删 enforce_scene_tenant_consistency / fill_scene_views_snapshot 函数
——这两个函数由 D4 (20260416_09676586bfbd) 拥有，若本迁移 downgrade 把函数删了，
D4 的单独 upgrade/downgrade 链会错位（参见 D5 对 set_updated_at 的同样处理）。
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "da5852f072d5"
down_revision: str | Sequence[str] | None = "89a9dfebe138"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# 必须与 ORM Base.metadata 中 `has usr_group & name != wx_groups` 完全一致（12 张）
# Plan v1.2 反向 fix 后清单；wx_groups 自身是租户字典表，不启 RLS
RLS_TABLES: list[str] = [
    # 业务表
    "users",
    "user_wx_bindings",
    "devices",
    "alarm_records",
    "timing_plans",
    "maintain_plans",
    "scene_pages",
    "scene_views",
    "pay_orders",
    # 审计表（含 usr_group 列的）
    "user_control_actions",
    "maintain_actions",
    "user_login_records",
]


def upgrade() -> None:
    """Upgrade schema."""
    # --- drift detection: RLS_TABLES vs ORM usr_group 表 ---
    # lazy import：避免在 module import 阶段触发 ruisheng_shared 解析
    from ruisheng_shared.models import Base

    orm_tenant = {
        t.name
        for t in Base.metadata.tables.values()
        if "usr_group" in t.columns and t.name != "wx_groups"
    }
    plan_tenant = set(RLS_TABLES)
    diff = orm_tenant.symmetric_difference(plan_tenant)
    if diff:
        raise RuntimeError(
            f"RLS_TABLES drift from ORM: {sorted(diff)}\n"
            f"  ORM 含 usr_group: {sorted(orm_tenant)}\n"
            f"  plan RLS_TABLES: {sorted(plan_tenant)}\n"
            f"  修法：对齐列表或调整 ORM 对应表的 usr_group 列"
        )

    # --- 块 A：scene_* 3 个专用触发器（字母序 enforce → fill → updated） ---
    # scene_pages：只 enforce（scene_pages 无 scene_page_id / dev_number 列）
    op.execute("DROP TRIGGER IF EXISTS trg_scene_pages_enforce_tenant ON scene_pages;")
    op.execute(
        "CREATE TRIGGER trg_scene_pages_enforce_tenant "
        "BEFORE INSERT OR UPDATE OF owner_user_name, usr_group ON scene_pages "
        "FOR EACH ROW EXECUTE FUNCTION enforce_scene_tenant_consistency();"
    )

    # scene_views：enforce（INSERT + UPDATE OF 4 列） + fill_snapshot（仅 INSERT）
    op.execute("DROP TRIGGER IF EXISTS trg_scene_views_enforce_tenant ON scene_views;")
    op.execute(
        "CREATE TRIGGER trg_scene_views_enforce_tenant "
        "BEFORE INSERT OR UPDATE OF owner_user_name, usr_group, scene_page_id, dev_number "
        "ON scene_views "
        "FOR EACH ROW EXECUTE FUNCTION enforce_scene_tenant_consistency();"
    )
    op.execute("DROP TRIGGER IF EXISTS trg_scene_views_fill_snapshot ON scene_views;")
    op.execute(
        "CREATE TRIGGER trg_scene_views_fill_snapshot "
        "BEFORE INSERT ON scene_views "
        "FOR EACH ROW EXECUTE FUNCTION fill_scene_views_snapshot();"
    )

    # --- 块 B：12 张表 ENABLE + FORCE + tenant_isolation policy ---
    for t in RLS_TABLES:
        op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY;")
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {t};")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {t} "
            f"USING ("
            f"  usr_group = current_setting('app.tenant_id', true) "
            f"  OR current_setting('app.role', true) = 'Administrators'"
            f") "
            f"WITH CHECK ("
            f"  usr_group = current_setting('app.tenant_id', true) "
            f"  OR current_setting('app.role', true) = 'Administrators'"
            f");"
        )


def downgrade() -> None:
    """Downgrade schema."""
    # 反序：先 drop policy + 关 RLS（reversed，避免依赖序）
    for t in reversed(RLS_TABLES):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {t};")
        op.execute(f"ALTER TABLE {t} NO FORCE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE {t} DISABLE ROW LEVEL SECURITY;")
    # scene 触发器：按字母序反向 drop
    op.execute("DROP TRIGGER IF EXISTS trg_scene_views_fill_snapshot ON scene_views;")
    op.execute("DROP TRIGGER IF EXISTS trg_scene_views_enforce_tenant ON scene_views;")
    op.execute("DROP TRIGGER IF EXISTS trg_scene_pages_enforce_tenant ON scene_pages;")
    # 注：enforce_scene_tenant_consistency / fill_scene_views_snapshot 函数由 D4 管理，
    # 本迁移不 DROP FUNCTION（类似 D5 对 set_updated_at 的处理）
