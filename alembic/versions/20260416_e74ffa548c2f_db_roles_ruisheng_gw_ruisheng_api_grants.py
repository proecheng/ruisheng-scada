"""db roles ruisheng_gw/ruisheng_api + grants

Revision ID: e74ffa548c2f
Revises: e05529ef4abb
Create Date: 2026-04-16 21:23:10.775719

Spec 依据：§3.7 L670-722 / §4.1.1 L982-987 / §4.2 L1379-1381, L1407-1409, L1451-1452
Plan：§Task D3

两个应用角色：
- ruisheng_gw：设备网关（BYPASSRLS，因为未来 RLS 按租户分区时 gw 需跨租户写原始点表）
- ruisheng_api：业务 API（无 BYPASSRLS；走 RLS 限制）

密码来自 env var（RUISHENG_GW_PASSWORD / RUISHENG_API_PASSWORD）；
未设置则 raise RuntimeError，禁止默认密码漏到生产。
"""

from __future__ import annotations

import os
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e74ffa548c2f"
down_revision: str | Sequence[str] | None = "e05529ef4abb"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _require_env(name: str) -> str:
    """env var 未设立刻报错；禁止 dev 密码漏到生产。"""
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(
            f"环境变量 {name} 未设置。\n"
            f"  dev 环境：.env 里填值后 `set -a; . ./.env; set +a` 再跑 alembic\n"
            f"  CI/生产：走 secret manager 注入；严禁默认密码\n"
            f"  参考：CONTRIBUTING.md §环境变量"
        )
    return value


def upgrade() -> None:
    """Upgrade schema."""
    gw_pw = _require_env("RUISHENG_GW_PASSWORD")
    api_pw = _require_env("RUISHENG_API_PASSWORD")

    # --- 幂等创建角色（已存在则重置密码，支持密码轮换） ---
    op.execute(f"""
        DO $$ BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='ruisheng_gw') THEN
            CREATE ROLE ruisheng_gw BYPASSRLS LOGIN PASSWORD '{gw_pw}';
          ELSE
            ALTER ROLE ruisheng_gw WITH LOGIN PASSWORD '{gw_pw}';
          END IF;
        END $$;
    """)
    op.execute(f"""
        DO $$ BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='ruisheng_api') THEN
            CREATE ROLE ruisheng_api LOGIN PASSWORD '{api_pw}';
          ELSE
            ALTER ROLE ruisheng_api WITH LOGIN PASSWORD '{api_pw}';
          END IF;
        END $$;
    """)

    # --- schema 级 GRANT（对现存 26 张表 + 未来新表） ---
    op.execute("GRANT USAGE ON SCHEMA public TO ruisheng_gw, ruisheng_api;")
    op.execute("GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public " "TO ruisheng_gw;")
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public " "TO ruisheng_api;"
    )
    # BIGSERIAL 列依赖 sequence USAGE（否则 INSERT 42501）
    op.execute(
        "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public " "TO ruisheng_gw, ruisheng_api;"
    )
    # 未来新表/新序列自动继承权限
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        "GRANT SELECT, INSERT, UPDATE ON TABLES TO ruisheng_gw;"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO ruisheng_api;"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        "GRANT USAGE, SELECT ON SEQUENCES TO ruisheng_gw, ruisheng_api;"
    )

    # --- 表级细粒度（spec §7.8 "各限权" + §4.2 L1379-1381 / L1407-1409 / L1451-1452） ---
    # 注意：schema 级 GRANT 已经把 gw=arw / api=arwd 给到所有 26 张表。
    # 要实现"缩权"必须 REVOKE FROM 两个具名角色（REVOKE FROM PUBLIC 是无效的，
    # PUBLIC 是独立 pseudo-role，不覆盖已授予具名角色的权限）。
    # 然后重新精确 GRANT 需要的最小权限。

    # pay_orders_seen：gw 写+清理（INSERT/SELECT/DELETE），api 只读（SELECT）
    op.execute("REVOKE ALL ON pay_orders_seen FROM PUBLIC, ruisheng_gw, ruisheng_api;")
    op.execute("GRANT INSERT, SELECT, DELETE ON pay_orders_seen TO ruisheng_gw;")
    op.execute("GRANT SELECT ON pay_orders_seen TO ruisheng_api;")

    # soft_logs：gw 只写（INSERT），api 写+读（INSERT/SELECT）
    op.execute("REVOKE ALL ON soft_logs FROM PUBLIC, ruisheng_gw, ruisheng_api;")
    op.execute("GRANT INSERT ON soft_logs TO ruisheng_gw;")
    op.execute("GRANT INSERT, SELECT ON soft_logs TO ruisheng_api;")

    # user_login_records：只 api 写+读（gw 不涉登录；spec §4.2 L1451-1452）
    op.execute("REVOKE ALL ON user_login_records FROM PUBLIC, ruisheng_gw, ruisheng_api;")
    op.execute("GRANT INSERT, SELECT ON user_login_records TO ruisheng_api;")
    # gw 不 GRANT（符合"gw 不涉登录"；若将来 gw 需要读登录审计，另起迁移补）


def downgrade() -> None:
    """Downgrade schema."""
    for role in ("ruisheng_api", "ruisheng_gw"):
        # 先 DROP OWNED（清所有 GRANT / DEFAULT PRIVILEGES 条目）
        # 再 DROP ROLE。IF EXISTS 兜底"已被手工删除"
        op.execute(f"""
            DO $$ BEGIN
              IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='{role}') THEN
                EXECUTE format('DROP OWNED BY %I', '{role}');
                EXECUTE format('DROP ROLE %I', '{role}');
              END IF;
            END $$;
        """)
