"""Stage D 完整集成测试：roles / functions / triggers / RLS / hypertables 全覆盖。"""

from __future__ import annotations

import subprocess

import pytest
from sqlalchemy import text


@pytest.mark.integration
async def test_upgrade_down_and_up_again():
    """alembic base → head 对称性。

    这里用 subprocess.check_call 是按 Plan §D6 Step 1 原样；
    alembic 的 CLI 入口是同步的，走子进程最省事也最贴合 plan；
    异步化要么 asyncio.create_subprocess_exec 要么 to_thread，都是
    不必要的复杂化，故在此抑制 ASYNC221。
    """
    subprocess.check_call(["uv", "run", "alembic", "downgrade", "base"])  # noqa: ASYNC221
    subprocess.check_call(["uv", "run", "alembic", "upgrade", "head"])  # noqa: ASYNC221


@pytest.mark.integration
async def test_roles_exist(dev_engine):
    """ruisheng_gw (BYPASSRLS) + ruisheng_api (非 BYPASSRLS)"""
    async with dev_engine.connect() as conn:
        rows = await conn.execute(
            text("""
            SELECT rolname, rolbypassrls FROM pg_roles
            WHERE rolname IN ('ruisheng_gw', 'ruisheng_api') ORDER BY rolname;
        """)
        )
        roles = {r.rolname: r.rolbypassrls for r in rows}
    assert roles == {"ruisheng_api": False, "ruisheng_gw": True}


@pytest.mark.integration
async def test_functions_are_invoker(dev_engine):
    """3 个 PL/pgSQL 函数都是 SECURITY INVOKER 且 search_path 硬绑定"""
    async with dev_engine.connect() as conn:
        rows = await conn.execute(
            text("""
            SELECT proname, prosecdef, proconfig
              FROM pg_proc
              WHERE proname IN ('set_updated_at',
                                'enforce_scene_tenant_consistency',
                                'fill_scene_views_snapshot');
        """)
        )
        funcs = {r.proname: (r.prosecdef, r.proconfig) for r in rows}
    assert len(funcs) == 3
    for name, (secdef, cfg) in funcs.items():
        assert secdef is False, f"{name} must be SECURITY INVOKER"
        assert cfg and any("search_path" in c for c in cfg), f"{name} missing SET search_path"


@pytest.mark.integration
async def test_updated_at_triggers_count(dev_engine):
    """13 张表各有 trg_<table>_updated (post Plan bug #3)"""
    async with dev_engine.connect() as conn:
        n = await conn.scalar(
            text("""
            SELECT count(*) FROM pg_trigger
              WHERE tgname LIKE 'trg_%_updated' AND NOT tgisinternal;
        """)
        )
    assert n == 13


@pytest.mark.integration
async def test_scene_triggers_exist(dev_engine):
    """scene_pages enforce + scene_views enforce + fill_snapshot"""
    async with dev_engine.connect() as conn:
        rows = await conn.execute(
            text("""
            SELECT tgrelid::regclass::text AS tbl, tgname
              FROM pg_trigger
              WHERE NOT tgisinternal
                AND tgname IN (
                  'trg_scene_pages_enforce_tenant',
                  'trg_scene_views_enforce_tenant',
                  'trg_scene_views_fill_snapshot'
                );
        """)
        )
        pairs = {(r.tbl, r.tgname) for r in rows}
    assert pairs == {
        ("scene_pages", "trg_scene_pages_enforce_tenant"),
        ("scene_views", "trg_scene_views_enforce_tenant"),
        ("scene_views", "trg_scene_views_fill_snapshot"),
    }


@pytest.mark.integration
async def test_rls_forced_on_12_tables(dev_engine):
    """12 张 RLS 表必须同时 ENABLE + FORCE (post Plan bug #4)"""
    async with dev_engine.connect() as conn:
        rows = await conn.execute(
            text("""
            SELECT relname FROM pg_class
              WHERE relnamespace='public'::regnamespace
                AND relrowsecurity AND relforcerowsecurity
              ORDER BY relname;
        """)
        )
        rls_tables = {r.relname for r in rows}
    assert len(rls_tables) == 12


@pytest.mark.integration
async def test_policies_exist(dev_engine):
    """12 张表各 1 条 tenant_isolation policy"""
    async with dev_engine.connect() as conn:
        rows = await conn.execute(
            text("""
            SELECT polrelid::regclass::text AS tbl, polname
              FROM pg_policy WHERE polname='tenant_isolation';
        """)
        )
        tables = {r.tbl for r in rows}
    assert len(tables) == 12


@pytest.mark.integration
async def test_hypertables_exist(dev_engine):
    """5 张 hypertable (post Plan bug #5 Q3-B — 无 user_control_actions)"""
    async with dev_engine.connect() as conn:
        rows = await conn.execute(
            text("""
            SELECT hypertable_name FROM timescaledb_information.hypertables;
        """)
        )
        names = {r.hypertable_name for r in rows}
    assert names == {
        "point_data_history",
        "waveform_history",
        "soft_logs",
        "user_login_records",
        "alarm_records",
    }


@pytest.mark.integration
async def test_d8_pk_composite_and_fk_dropped(dev_engine):
    """D8 schema prep（Plan bug #5）：3 张表 PK 复合 + alarm_outbox FK 已拆"""
    async with dev_engine.connect() as conn:
        rows = await conn.execute(
            text("""
            SELECT conname, pg_get_constraintdef(oid) AS constraint_def
              FROM pg_constraint
              WHERE contype='p'
                AND conrelid::regclass::text
                    IN ('alarm_records','soft_logs','user_login_records')
              ORDER BY conname;
        """)
        )
        pk_defs = {r.conname: r.constraint_def for r in rows}
        assert "triggered_at" in pk_defs["pk_alarm_records"]
        assert "recorded_at" in pk_defs["pk_soft_logs"]
        assert "logged_at" in pk_defs["pk_user_login_records"]
        n = await conn.scalar(
            text("""
            SELECT count(*) FROM pg_constraint
              WHERE contype='f'
                AND conname='fk_alarm_outbox_alarm_id_alarm_records';
        """)
        )
        assert n == 0


@pytest.mark.integration
async def test_rls_actually_blocks_cross_tenant_read(api_engine, dev_engine, seed_tenants):
    """ruisheng_api + SET LOCAL app.tenant_id='ug_A' → 只看见 A 行。
    预先 dev (superuser) 幂等清理 dev_number='901'，保证 test 可重复运行
    （gw 角色没有 DELETE 权限）。"""
    async with dev_engine.connect() as conn, conn.begin():
        await conn.execute(text("DELETE FROM devices WHERE dev_number='901'"))
    async with api_engine.connect() as conn:
        async with conn.begin():
            await conn.execute(text("SET LOCAL app.tenant_id = 'ug_A'"))
            await conn.execute(
                text("""
                INSERT INTO devices
                    (usr_group, dev_number, dev_ser_number, dev_name,
                     modbus_addr, update_interval_decisec, loss_count,
                     is_online, update_flag)
                VALUES
                    ('ug_A', '901', 'SER-901', 'A-dev',
                     1, 100, 0, false, 0)
            """)
            )
        async with conn.begin():
            await conn.execute(text("SET LOCAL app.tenant_id = 'ug_A'"))
            rows = await conn.execute(text("SELECT count(*) FROM devices WHERE dev_number='901'"))
            assert rows.scalar() == 1
        async with conn.begin():
            await conn.execute(text("SET LOCAL app.tenant_id = 'ug_B'"))
            rows = await conn.execute(text("SELECT count(*) FROM devices WHERE dev_number='901'"))
            assert rows.scalar() == 0


@pytest.mark.integration
async def test_rls_blocks_cross_tenant_insert(api_engine, seed_tenants):
    """ruisheng_api + SET tenant=A → 插 usr_group=B 被 WITH CHECK 拒"""
    async with api_engine.connect() as conn, conn.begin():
        await conn.execute(text("SET LOCAL app.tenant_id = 'ug_A'"))
        with pytest.raises(Exception) as ei:
            await conn.execute(
                text("""
                    INSERT INTO devices
                        (usr_group, dev_number, dev_ser_number, dev_name,
                         modbus_addr, update_interval_decisec, loss_count,
                         is_online, update_flag)
                    VALUES
                        ('ug_B', '902', 'SER-902', 'B-dev',
                         1, 100, 0, false, 0)
                """)
            )
        assert "row-level security" in str(ei.value).lower()


@pytest.mark.integration
async def test_non_bypassrls_user_sees_zero_under_bogus_tenant(api_engine):
    """M1 核心：非 BYPASSRLS 角色 (ruisheng_api) + 不存在租户 → 0 行。
    注：dev 容器的 POSTGRES_USER=ruisheng_dev 按 Docker 默认是 SUPERUSER,
    superuser 天然绕过 RLS（FORCE RLS 也不管），故 M1 隔离性的真正载体是
    ruisheng_api（NOSUPERUSER + NOBYPASSRLS）。"""
    async with api_engine.connect() as conn, conn.begin():
        await conn.execute(text("SET LOCAL app.tenant_id = 'ug_X_not_exist'"))
        rows = await conn.execute(text("SELECT count(*) FROM users"))
        assert rows.scalar() == 0


@pytest.mark.integration
async def test_gw_bypasses_rls(gw_engine):
    """ruisheng_gw BYPASSRLS：不设 tenant_id 也能读"""
    async with gw_engine.connect() as conn:
        rows = await conn.execute(text("SELECT count(*) FROM users"))
        assert rows.scalar() is not None


@pytest.mark.integration
async def test_scene_trigger_raises_23514(api_engine, seed_tenants):
    """跨租户 INSERT scene_pages 被 enforce 触发器抛 23514"""
    async with api_engine.connect() as conn, conn.begin():
        await conn.execute(text("SET LOCAL app.tenant_id = 'ug_A'"))
        with pytest.raises(Exception) as ei:
            await conn.execute(
                text("""
                    INSERT INTO scene_pages (usr_group, owner_user_name, page_name)
                    VALUES ('ug_A', 'user_of_ugB', 'p1')
                """)
            )
        assert "23514" in str(ei.value) or "scene_tenant_violation" in str(ei.value)


@pytest.mark.integration
async def test_api_insert_uses_sequence(api_engine, dev_engine, seed_tenants):
    """ruisheng_api INSERT 必须有 sequence USAGE (BIGSERIAL; devices.id)。
    预先 dev (superuser) 幂等清理 dev_number='997'，保证 test 可重复运行。"""
    async with dev_engine.connect() as conn, conn.begin():
        await conn.execute(text("DELETE FROM devices WHERE dev_number='997'"))
    async with api_engine.connect() as conn, conn.begin():
        await conn.execute(text("SET LOCAL app.tenant_id = 'ug_A'"))
        new_id = await conn.scalar(
            text("""
                INSERT INTO devices
                    (usr_group, dev_number, dev_ser_number, dev_name,
                     modbus_addr, update_interval_decisec, loss_count,
                     is_online, update_flag)
                VALUES
                    ('ug_A', '997', 'SER-997', 'seq-probe',
                     1, 100, 0, false, 0)
                RETURNING id
            """)
        )
        assert new_id is not None
