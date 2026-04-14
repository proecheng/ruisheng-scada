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
- breaking: SHARED_SCHEMA_VERSION 20260413 → 20260414 (spec v1.3.3 §4.2: new maintain_plans/maintain_actions + timing_plans rewrite with usr_group/deleted_at/updated_at/RLS)
- feature: add TimingPlan + MaintainPlan + MaintainAction models (spec §4.2 v1.3.3); action_uuid ULID idempotency key; maintain_plans next_due_at 60s clock-drift tolerance
- feature: add ScenePage + SceneView models (spec §4.2 v1.3.4); partial UNIQUE `(usr_group, owner_user_name, page_name)` / `(scene_page_id, dev_number)` WHERE `deleted_at IS NULL`; zh-x-icu collation on `page_name` / `sonpage_name`; `pos_x`/`pos_y` Numeric(10,2) + sanity bounds CHECK; `radius` Numeric(8,2) CHECK (0.01–100000); PL/pgSQL triggers `enforce_scene_tenant_consistency` / `fill_scene_views_snapshot` deferred to Stage D alembic (spec §4.1.1 (4)(5)); SHARED_SCHEMA_VERSION unchanged (non-breaking DB schema extension; no shared Pydantic/enum impact)
- breaking: SHARED_SCHEMA_VERSION 20260414 → 20260415
  (spec v1.3.5 §4.2: new pay_orders/pay_orders_seen ORM + §5.1 PAY_* ErrCode 6 items)
- feature: add PayOrder (pay_orders) — 6-值 pay_state + biconditional CK + partial indexes +
  RLS tenant_isolation (see spec §4.2 v1.3.5); PK 例外 out_trade_no 直接作 PK (§4.6)
- feature: add PayOrderSeen (pay_orders_seen) — 幂等守门表; BRIN index; 无 RLS; 30天TTL (§5.10.3)
- feature: add PAY_SIGN_FAIL(-400) / PAY_DUPLICATE(-401) / PAY_STATE_CONFLICT(-402) /
           PAY_AMOUNT_MISMATCH(-403) / PAY_EXPIRED(-404) / PAY_REFUND_FAIL(-405) ErrCode

## 2026-04-13 v0.1.0

- chore: 初始版本，SHARED_SCHEMA_VERSION=20260413
