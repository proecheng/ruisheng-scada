"""timescale hypertables + retention + compression (5 tables, composite PK prep)

Revision ID: 959079e6cae9
Revises: 378761167d8c
Create Date: 2026-04-17 17:35:50.151264

Spec 依据：
- §4.2 L1184-1190  — point_data_history / waveform_history：hypertable + 1y retention + 7d compression
- §4.2 v1.3.6      — soft_logs：hypertable + 1y retention + 7d compression
                     user_login_records：hypertable + 3y retention + 7d compression segmentby=usr_group
- §3.8 / §4        — alarm_records：hypertable + 2y retention + 30d compression

Plan：§Task D8（Plan 0 Stage D，Plan v1.4 — 新增 Plan bug #5 + #6 反修）

========== Plan bug #6 反修与用户决策（v1.4 新增）==========

Plan bug #6 (D8): TimescaleDB 2.16.1 refuses "ALTER TABLE ... SET (timescaledb.compress)"
on tables with RLS enabled (upstream timescaledb#6827). Affected: alarm_records +
user_login_records (both got D6 FORCE RLS). user decision Option A: keep retention,
skip compression on these 2. Restore in v1.3.7 when upstream ships fix.

净结果：5 张 hypertable + 5 张 retention，但 **只 3 张 compression**（非 5）：
  - point_data_history / waveform_history / soft_logs → retention + compression
  - alarm_records / user_login_records → retention only（RLS 兼容；v1.3.7 补回 compression）

========== Plan bug #5 反修与用户决策 ==========

Controller pre-dispatch probe 抓到 TimescaleDB 2.16.1 两条硬约束，v1.3 反修
（master `5901243`），决策权威落地：

1. **FK → hypertable forbidden**（TS 2.16.1）：
   `fk_alarm_outbox_alarm_id_alarm_records` 阻止 alarm_records 升级为 hypertable。
   实测报错：`ERROR: cannot have FOREIGN KEY constraints to hypertable
   "alarm_records"`。
   → **Q1-A 决策**：删 FK。outbox 为 transient 事件表；完整性由 app 层保证
     （publish job 读 alarm_records 时按 alarm_id 外连，缺失行跳过即可）。

2. **PK/UNIQUE 必须包含分区列**（TS 2.16.1）：
   alarm_records / soft_logs / user_login_records 当前 PK 仅 `id`，TS
   `create_hypertable` 会拒绝。
   → **Q2-A 决策**：改复合 PK `(id, <time_col>)`。BIGSERIAL id 自身仍保持唯一，
     复合 PK 只为满足 TS 约束，不改变语义。

3. **user_control_actions**：
   该表有 `UNIQUE (cmd_id)` 幂等键；若升级为 hypertable，TS 要求 UNIQUE 包含
   分区列，会破坏幂等语义。
   → **Q3-B 决策**：本张**不转 hypertable**，保留为常规表。spec §5.10
     L1958-1960 作为 v1.3.7 TODO（D10 picks up）。

净结果：D8 = **5 张 hypertable**（不是 6）+ 3 张 composite PK + 1 个 FK 删除
        + **3 张 compression**（Plan bug #6 令 RLS 两张跳过 compression）。

========== 迁移结构 ==========

Step A — drop FK alarm_outbox → alarm_records
Step B — 3 张表 id-only PK → 复合 PK `(id, <time_col>)`
         命名保持 `pk_<tablename>`（与 naming_convention 一致），用
         raw ALTER `ADD CONSTRAINT <old_name>` 显式保名。
Step C — CREATE EXTENSION IF NOT EXISTS timescaledb
         for each of 5 tables:
             create_hypertable(if_not_exists => TRUE)
             remove_retention_policy + add_retention_policy（幂等）
             if compress_after is not None (Plan bug #6 RLS-skip):
                 compress + remove_compression_policy + add_compression_policy（幂等）

3 张配置 compression（point_data_history / waveform_history / soft_logs），
2 张仅 retention（alarm_records / user_login_records，Plan bug #6 RLS 兼容）。

========== 下行 (downgrade) 策略 ==========

**Forward-only partial downgrade**：

- TimescaleDB 不支持 un-hypertable（无法把 hypertable 退回普通表）
- FK 重建需要 alarm_records 变回普通表（不可能）
- 复合 PK 拆回 id-only 在 hypertable 上也非法（TS 仍要求 PK 含分区列）

故 downgrade 仅撤销 policy（retention / compression），schema 结构保留。
开发期如需干净回滚，使用 `docker compose down -v` 重置整个 volume。
生产期 D8 之后结构变更不可逆，这是 hypertable 迁移的业界常态。

========== 运维安全性说明 ==========

`create_hypertable` 对空表是秒级操作（TimescaleDB 将底层表改写为分区根 +
将现有行迁到第一个 chunk；空表无行则无数据移动成本）。本环境 D2 以来 5 张表
均为空（无业务写入），故本迁移在 dev / prod 首次部署均可秒级完成。

若将来对有数据的非空表执行 `create_hypertable`，TS 会按 chunk_time_interval
切分历史行，该过程可能持续较久并持写锁 —— 当前场景不涉及。
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "959079e6cae9"
down_revision: str | Sequence[str] | None = "378761167d8c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# (table, time_col, chunk, retention, compress_after, segmentby)
# 5 张 hypertable。compress_after=None → 跳过 compression（Plan bug #6 RLS-compat）。
# segmentby=None 表示压缩不设 segmentby 维度。
HYPERTABLES: list[tuple[str, str, str, str, str | None, str | None]] = [
    ("point_data_history", "recorded_at", "1 month", "1 year", "7 days", "dev_number, point_id"),
    ("waveform_history", "recorded_at", "1 month", "1 year", "7 days", "dev_number"),
    ("soft_logs", "recorded_at", "1 month", "1 year", "7 days", None),
    # Plan bug #6: D6 FORCE RLS 与 TS 2.16.1 compression 冲突 (timescaledb#6827)。
    # user decision Option A: 只保留 retention，compression 等上游修复后 v1.3.7 补回。
    ("user_login_records", "logged_at", "1 month", "3 years", None, None),
    ("alarm_records", "triggered_at", "1 month", "2 years", None, None),
    # user_control_actions 不转 hypertable（保 UNIQUE(cmd_id) 幂等键；spec v1.3.7 摘除）
]


# 3 张 id-only PK → 复合 PK。保名 pk_<tablename>（naming_convention pk_<tablename> 一致）。
PK_COMPOSITES: list[tuple[str, str, str]] = [
    ("alarm_records", "pk_alarm_records", "id, triggered_at"),
    ("soft_logs", "pk_soft_logs", "id, recorded_at"),
    ("user_login_records", "pk_user_login_records", "id, logged_at"),
]


def upgrade() -> None:
    """Upgrade schema: schema prep + 5 hypertables + policies.

    Idempotency：downgrade() 为 forward-only（仅撤销 policy），因此 upgrade()
    必须能在 "schema 已 D8 化、policy 已撤销" 的半状态上原样再跑一次。
    - Step A: DROP CONSTRAINT IF EXISTS —— 天然幂等。
    - Step B: 用 pg_constraint 查询守门，仅在 PK 仍是 id-only 时做 DROP+ADD；
             已是复合 PK 则跳过（避免在压缩 hypertable 上 DROP PK 触发
             "operation not supported on hypertables that have compression
             enabled"）。
    - Step C: create_hypertable if_not_exists + remove+add retention/
             compression policy —— TS 原生幂等。
    """
    # Step A: drop FK alarm_outbox → alarm_records (TS 2.16.1 禁止 FK → hypertable)
    op.execute(
        "ALTER TABLE alarm_outbox DROP CONSTRAINT IF EXISTS fk_alarm_outbox_alarm_id_alarm_records;"
    )

    # Step B: composite PK (id, time_col) on 3 tables —— 仅当 PK 列数==1 时改。
    # 已是复合 PK（downgrade 后重跑场景）则跳过，避免 compressed hypertable 上
    # DROP PK 的 "operation not supported" 异常。
    for table, pk_name, new_cols in PK_COMPOSITES:
        op.execute(
            f"""
            DO $$
            DECLARE
                col_count int;
            BEGIN
                SELECT array_length(conkey, 1) INTO col_count
                  FROM pg_constraint
                 WHERE conname = '{pk_name}'
                   AND conrelid = '{table}'::regclass;
                IF col_count = 1 THEN
                    ALTER TABLE {table} DROP CONSTRAINT {pk_name};
                    ALTER TABLE {table} ADD CONSTRAINT {pk_name}
                        PRIMARY KEY ({new_cols});
                END IF;
            END$$;
            """
        )

    # Step C: hypertable + retention + compression（幂等：if_not_exists + remove+add）
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb;")
    for table, tcol, chunk, retain, compress, segby in HYPERTABLES:
        op.execute(
            f"SELECT create_hypertable('{table}', '{tcol}', "
            f"chunk_time_interval => INTERVAL '{chunk}', "
            f"if_not_exists => TRUE);"
        )
        op.execute(f"SELECT remove_retention_policy('{table}', if_exists => TRUE);")
        op.execute(f"SELECT add_retention_policy('{table}', INTERVAL '{retain}');")

        if compress:
            segby_clause = f", timescaledb.compress_segmentby = '{segby}'" if segby else ""
            op.execute(f"ALTER TABLE {table} SET (timescaledb.compress{segby_clause});")
            op.execute(f"SELECT remove_compression_policy('{table}', if_exists => TRUE);")
            op.execute(f"SELECT add_compression_policy('{table}', INTERVAL '{compress}');")


def downgrade() -> None:
    """Forward-only partial downgrade: 仅撤销 policy，schema 结构不可逆。

    TimescaleDB 不支持 un-hypertable；dev 需要彻底回滚请使用
    `docker compose down -v` 重置 volume。详见模块 docstring。
    """
    for table, *_ in reversed(HYPERTABLES):
        op.execute(f"SELECT remove_retention_policy('{table}', if_exists => TRUE);")
        op.execute(f"SELECT remove_compression_policy('{table}', if_exists => TRUE);")
