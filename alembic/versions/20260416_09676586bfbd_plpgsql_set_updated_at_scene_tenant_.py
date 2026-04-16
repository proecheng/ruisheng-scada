"""plpgsql: set_updated_at + scene tenant helpers

Revision ID: 09676586bfbd
Revises: e74ffa548c2f
Create Date: 2026-04-16 22:06:47.132874

Spec 依据：
- §4.1.1 (1) L978-979: set_updated_at() 通用 updated_at 维护
- §4.1.1 (4) L996-1038: enforce_scene_tenant_consistency() scene 跨表租户一致性触发器
- §4.1.1 (5) L1043-1054: fill_scene_views_snapshot() scene_views company/department 快照反查

Plan：§Task D4

三个 PL/pgSQL 函数（全部 SECURITY INVOKER + SET search_path = pg_catalog, public）：
- set_updated_at(): NEW.updated_at = now() 触发器函数
- enforce_scene_tenant_consistency(): scene_pages / scene_views 行级跨表 usr_group 一致性校验；
  错误消息严格英文（'scene_tenant_violation: ...'），ERRCODE='23514' (check_violation)
- fill_scene_views_snapshot(): scene_views.company / department 从 owner_user_name 反查 users 表填充

M3 hardening：
- SET search_path = pg_catalog, public 函数级硬绑定（防会话级 search_path 劫持）
- SECURITY INVOKER（默认；绝不用 DEFINER —— 会绕过 RLS）
- 所有 WHERE 子句保留 AND deleted_at IS NULL（已软删行不参与一致性校验）
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "09676586bfbd"
down_revision: str | Sequence[str] | None = "e74ffa548c2f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 函数 1：通用 updated_at 维护（spec §4.1.1 (1)）
    op.execute(r"""
        CREATE OR REPLACE FUNCTION set_updated_at() RETURNS TRIGGER AS $$
        BEGIN
          NEW.updated_at = now();
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        SET search_path = pg_catalog, public;
    """)

    # 函数 2：scene 租户一致性校验（spec §4.1.1 (4)）
    # 注意：严格按 spec L996-1038 复刻，错误消息为英文 'scene_tenant_violation: ...'
    op.execute(r"""
        CREATE OR REPLACE FUNCTION enforce_scene_tenant_consistency()
        RETURNS TRIGGER AS $$
        DECLARE
          v_user_ug  varchar(40);
          v_page_ug  varchar(40);
          v_dev_ug   varchar(40);
        BEGIN
          -- 1) owner_user_name 存在且 usr_group 与传入一致
          SELECT usr_group INTO v_user_ug FROM users
            WHERE user_name = NEW.owner_user_name AND deleted_at IS NULL;
          IF v_user_ug IS NULL THEN
            RAISE EXCEPTION 'scene_tenant_violation: owner not found or deleted'
              USING ERRCODE='23514';
          END IF;
          IF v_user_ug <> NEW.usr_group THEN
            RAISE EXCEPTION 'scene_tenant_violation: user/scene usr_group mismatch'
              USING ERRCODE='23514';
          END IF;

          -- 2) 若是 scene_views：scene_page_id 的 usr_group 一致
          IF TG_TABLE_NAME = 'scene_views' THEN
            SELECT usr_group INTO v_page_ug FROM scene_pages
              WHERE id = NEW.scene_page_id AND deleted_at IS NULL;
            IF v_page_ug IS NULL THEN
              RAISE EXCEPTION 'scene_tenant_violation: page not found or deleted'
                USING ERRCODE='23514';
            END IF;
            IF v_page_ug <> NEW.usr_group THEN
              RAISE EXCEPTION 'scene_tenant_violation: page/view usr_group mismatch'
                USING ERRCODE='23514';
            END IF;

            -- 3) dev_number 的 usr_group 一致
            SELECT usr_group INTO v_dev_ug FROM devices
              WHERE dev_number = NEW.dev_number AND deleted_at IS NULL;
            IF v_dev_ug IS NULL THEN
              RAISE EXCEPTION 'scene_tenant_violation: device not found or deleted'
                USING ERRCODE='23514';
            END IF;
            IF v_dev_ug <> NEW.usr_group THEN
              RAISE EXCEPTION 'scene_tenant_violation: device/view usr_group mismatch'
                USING ERRCODE='23514';
            END IF;
          END IF;

          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        SET search_path = pg_catalog, public;
    """)

    # 函数 3：scene_views company/department 快照反查（spec §4.1.1 (5)）
    op.execute(r"""
        CREATE OR REPLACE FUNCTION fill_scene_views_snapshot()
        RETURNS TRIGGER AS $$
        BEGIN
          IF NEW.company IS NULL OR NEW.department IS NULL THEN
            SELECT
              COALESCE(NEW.company, u.company),
              COALESCE(NEW.department, u.department)
            INTO NEW.company, NEW.department
            FROM users u
            WHERE u.user_name = NEW.owner_user_name AND u.deleted_at IS NULL;
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        SET search_path = pg_catalog, public;
    """)


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS fill_scene_views_snapshot();")
    op.execute("DROP FUNCTION IF EXISTS enforce_scene_tenant_consistency();")
    op.execute("DROP FUNCTION IF EXISTS set_updated_at();")
