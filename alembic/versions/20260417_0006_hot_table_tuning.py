"""fillfactor + autovacuum tuning (3 UPDATE-heavy tables)

Revision ID: 378761167d8c
Revises: da5852f072d5
Create Date: 2026-04-17 16:58:27.962961

Spec 依据：
- §4.2 L1126-1129  — devices WITH clause:
                     fillfactor=80 + autovacuum_vacuum_scale_factor=0.05
                     （last_call_at / loss_count 中等频率 UPDATE）
- §4.2 L1184-1190  — point_data_realtime WITH clause (5 项):
                     fillfactor=70
                     autovacuum_vacuum_scale_factor=0.05
                     autovacuum_analyze_scale_factor=0.02
                     autovacuum_vacuum_cost_limit=1000
                     autovacuum_vacuum_insert_scale_factor=0.1
                     （HOT UPDATE 最密集，激进 autovacuum 防膨胀）
- §5.10 L1930      — device_waring_cfgs: fillfactor=80
                     （waring_flag / enable 变更频繁，无 autovacuum 调优）

Plan：§Task D7（Plan 0 Stage D，Plan v1.2）

========== 运维安全性说明 ==========

ALTER TABLE ... SET / RESET (fillfactor, autovacuum_*) 对表数据**无影响**：
- 仅修改 pg_class.reloptions（HEAP 预留空间参数 + autovacuum 阈值）
- **不触发锁升级**（仅需 ShareUpdateExclusiveLock，VACUUM 级别，读写不阻塞）
- **不执行表扫描 / 行重写**（fillfactor 只影响**后续**新写入块的预留空间，已有行
  不会被重新打包；需要 fillfactor 立刻生效需配合 VACUUM FULL / pg_repack，本迁移
  不包含该操作，由后续运维 Runbook 按需触发）
- 因此 upgrade / downgrade 均为秒级完成，可安全在生产窗口随时执行

========== Stage C C11 tech debt 兑现 ==========

PointDataRealtime ORM 在 C11（Stage C）时将 5 项 WITH 参数以
``__table_args__["info"]["postgresql_with"]`` dict 形式 carry 为 metadata-only
（SQLAlchemy 2.0 不支持 WITH 作为 Table kwarg，无法由 ORM DDL 直接发出）。
D7 本迁移落实为真实的 ``ALTER TABLE ... SET (...)``，至此 C11 遗留 metadata
映射为 DDL 状态，ORM info 字典继续作为**事实源回环断言**（见 upgrade() 顶部）。

devices / device_waring_cfgs 的 ORM（C4 / C5）未存储 postgresql_with info —
spec 为单一事实源，本迁移不扩展这两张表的 ORM metadata（避免 side-fix）。
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "378761167d8c"
down_revision: str | Sequence[str] | None = "da5852f072d5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Plan / spec 权威值（point_data_realtime 5 项）
# 用于 upgrade() 顶部 ORM drift 回环断言
_EXPECTED_REALTIME_WITH: dict[str, str] = {
    "fillfactor": "70",
    "autovacuum_vacuum_scale_factor": "0.05",
    "autovacuum_analyze_scale_factor": "0.02",
    "autovacuum_vacuum_cost_limit": "1000",
    "autovacuum_vacuum_insert_scale_factor": "0.1",
}


def upgrade() -> None:
    """Upgrade schema: ALTER TABLE ... SET fillfactor + autovacuum (3 tables)."""
    # --- drift detection: plan / spec values vs ORM __table_args__["info"]["postgresql_with"] ---
    # C11 已把 point_data_realtime 的 5 项存入 ORM metadata（SQLAlchemy 2.0 不支持 WITH
    # 作为 Table kwarg，因此 C11 以 info dict 形式 carry，D7 才落实为 ALTER TABLE SET）。
    # 若未来 spec 调整数值，ORM 会先改，此断言会 raise 让 implementer 同步改迁移；
    # 若迁移先改，也会让 ORM 不同步时被 import/启动时抓住。
    from ruisheng_shared.models.timeseries import PointDataRealtime

    orm_realtime = PointDataRealtime.__table__.info.get("postgresql_with", {})
    if orm_realtime != _EXPECTED_REALTIME_WITH:
        raise RuntimeError(
            "D7 point_data_realtime drift from ORM info:\n"
            f"  expected (plan/spec): {_EXPECTED_REALTIME_WITH}\n"
            f"  ORM info:             {orm_realtime}"
        )
    # 注：devices / device_waring_cfgs 无 ORM info 元数据（C4/C5 未存；spec 是单一事实源），
    # 不对这两张表做 drift 回环。

    # point_data_realtime：HOT update 最密集，激进 autovacuum（spec §4.2 L1184-1190）
    op.execute(
        """
        ALTER TABLE point_data_realtime SET (
          fillfactor = 70,
          autovacuum_vacuum_scale_factor = 0.05,
          autovacuum_analyze_scale_factor = 0.02,
          autovacuum_vacuum_cost_limit = 1000,
          autovacuum_vacuum_insert_scale_factor = 0.1
        );
        """
    )
    # devices：中等 UPDATE 频率 last_call_at / loss_count（spec §4.2 L1126-1129）
    op.execute(
        """
        ALTER TABLE devices SET (
          fillfactor = 80,
          autovacuum_vacuum_scale_factor = 0.05
        );
        """
    )
    # device_waring_cfgs：waring_flag / enable 变更频繁（spec §5.10 L1930）
    op.execute("ALTER TABLE device_waring_cfgs SET (fillfactor = 80);")


def downgrade() -> None:
    """Downgrade schema: ALTER TABLE ... RESET (逆序，与 upgrade 对称)."""
    # RESET 无 FK 依赖风险（reloptions 是 pg_class 字段，不跨表引用），
    # 但保持与 upgrade 对称顺序便于审计阅读。
    op.execute("ALTER TABLE device_waring_cfgs RESET (fillfactor);")
    op.execute(
        """
        ALTER TABLE devices RESET (
          fillfactor,
          autovacuum_vacuum_scale_factor
        );
        """
    )
    op.execute(
        """
        ALTER TABLE point_data_realtime RESET (
          fillfactor,
          autovacuum_vacuum_scale_factor,
          autovacuum_analyze_scale_factor,
          autovacuum_vacuum_cost_limit,
          autovacuum_vacuum_insert_scale_factor
        );
        """
    )
