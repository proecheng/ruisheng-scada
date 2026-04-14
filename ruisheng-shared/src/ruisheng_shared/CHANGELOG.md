# ruisheng-shared 变更日志

每次改动本包必须在此文件登记一条，用下列前缀之一：

- `breaking:` — 需同步升级 SHARED_SCHEMA_VERSION 与 api/gw 的 REQUIRED
- `deprecation:` — 不立即破坏，两个小版本内清理
- `feature:` — 新增字段/类型
- `fix:` — 错误修正
- `chore:` — 重构、重命名、注释（无语义变化）

## 2026-04-14

- feature: SQLAlchemy Base + TimestampMixin + SoftDeleteMixin
- internal: add naming_convention to Base.metadata for Alembic constraint name stability
- feature: add WxGroup (wx_groups) tenant model
- feature: add User + UserWxBinding + UserPhoneNumber + UserEmail models
- chore: refactor UQ naming_convention uses constraint_name; User.user_name UQ declared explicitly
- feature: add Device + DevicePoint + DeviceStaticData + SimCard + DeviceTemplate models
- feature: add DeviceWaringCfg + AlarmRecord + AlarmOutbox models (spec §3.8)
- feature: add UserControlAction (user_control_actions) model — control command audit log, spec §3.5, hypertable-ready (no FK on dev_number/user_name)
- fix: tighten Mapped[dict] to Mapped[dict[str, Any]] on Device.last_state / DeviceTemplate.payload (mypy --strict)
- C7: plans.py (TimingPlan / MaintainPlan / MaintainAction) — spec v1.3.3 §4.2 完整 DDL；action_uuid 幂等键；保养 next_due_at 60s 容差。

## 2026-04-13 v0.1.0

- chore: 初始版本，SHARED_SCHEMA_VERSION=20260413
