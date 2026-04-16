# 实施进度备忘（断点续跑用）

> 本文件用于跨会话保留执行状态。每次暂停前更新，下次启动前读。
> **与 git 分支状态配合使用，git 是事实单一源。**

---

## 当前状态：Plan 0 **Stage D 进行中（D0+D1+D2+D3 完成 4/10）**

**最后更新**：2026-04-16（D3 DB 角色 + GRANT 完成 + 2 轮 review APPROVED + fixup 覆盖 tech debt #2 format drift）
**工作分支**：`feature/plan-0-foundation`
**最近 commit**（worktree）：`d806a45 style(db): fixup D3 ruff-format + repo-wide tech-debt #2 cleanup`（前置 `2474275 feat(db): D3 ruisheng_gw/ruisheng_api roles + GRANT baseline`）
**最新 tag**：`plan-0-stage-c-complete`（Stage D 未打 tag）
**master 最新 commit**：`88b3576 fix(plan): D3 Step 2 — REVOKE 必须显式列 ...`（本次 D3 完成 PROGRESS 即将 commit）
**SHARED_SCHEMA_VERSION**：`20260415`
**测试状态**：321 passed + 8 skipped（D3 后仍 baseline，无回归）

**下一步**：**D4 — PL/pgSQL 通用函数（set_updated_at + enforce_scene_tenant_consistency + fill_scene_views_snapshot，搭 `SET search_path = pg_catalog, public` 硬绑）**。详见 plan v1.1 §Task D4。

---

### Stage D 进度表

| # | Task | Commit | Notes |
|---|---|---|---|
| D0 | Docker + TimescaleDB 环境校验 | （无 commit，纯验证） | ✅ 7 步全过：docker 29.4.0 / compose v5.1.1 / hello-world / PG+Redis healthy / timescaledb 2.16.1 扩展 / redis PONG / down -v；**关键经验**：timescale/timescaledb 镜像国内仅 `docker.1ms.run` 可拉（DaoCloud 500、USTC/163 DNS 失败、dockerproxy IP 被污染） |
| D1 | alembic init + async env.py | `edb23cf` + fixup `52904f9` | ✅ alembic 1.13 显式依赖 + env.py 加载 26 张表 metadata + DATABASE_URL 环境变量；post-review 修 `path_separator = space`（跨平台）+ 解释性注释（configparser Windows GBK 限制→注释用英文）；**关键经验**：CJK 路径下 uv UTF-8 `.pth` 被 Python mbcs 解码损坏，必须 `prepend_sys_path = . ruisheng-shared/src` |
| D2 | autogenerate 初始 schema 迁移 | `9f2f102` | ✅ `alembic/versions/20260416_e05529ef4abb_initial_schema_26_tables.py`（1034 行，26 张 CREATE TABLE）；upgrade → `\dt` 26 表 + alembic_version → downgrade base 只剩 alembic_version → 再 upgrade 幂等 → 321 passed + 8 skipped（无回归）；两轮 review APPROVED；**命名规范 PASS**（`op.f()` 包 naming_convention，无双叠，无 None）；**PG 特化类型保留**（INET×2 / JSONB×6+ / Double / LargeBinary 全对）；**server_default PASS**（created_at/updated_at/pay_state 等全有 DEFAULT 子句）；**范围合规**（无 hypertable/RLS/触发器/fillfactor DDL，延后 D3/D4/D5）；附带改动 1：`pyproject.toml` `[tool.ruff.lint.per-file-ignores]` 给 `alembic/versions/**/*.py` 加 `["PLR0915", "E501"]`（autogen 大函数固有形态，不加每次都要 noqa）；附带改动 2：pre-commit ruff-format 纯 cosmetic 改 migration（引号/typing PEP 604 语法）；**已知 artifact**（非 bug）：(1) `postgresql_where` 混风格 `sa.text(...)` vs 裸字符串（autogen 自己混的，两者等价）；(2) `point_data_realtime.info={"postgresql_with":{...}}` 是 metadata-only 不发 DDL，D4 做 ALTER TABLE 落实 |
| D3 | DB 角色 (ruisheng_gw/ruisheng_api) + GRANT 基线 | `2474275` + fixup `d806a45` | ✅ `alembic/versions/20260416_e74ffa548c2f_db_roles_ruisheng_gw_ruisheng_api_grants.py`；2 角色（gw BYPASSRLS / api 非）+ schema 级 GRANT ON ALL TABLES + SEQUENCES + ALTER DEFAULT PRIVILEGES + 3 张表细粒度 REVOKE+GRANT（pay_orders_seen api=r 只读；soft_logs gw=a 仅写；user_login_records gw 无权）；密码走 `_require_env()` raise + `DO $$ IF NOT EXISTS ALTER ROLE ELSE CREATE END $$` 幂等支持轮换；downgrade `DROP OWNED → DROP ROLE IF EXISTS`；验证全 PASS（含 M4 sequence USAGE）；**过程**：第 1 次派 implementer 发现 **plan bug**（`REVOKE ... FROM PUBLIC` 不影响具名角色 → 达不到缩权）→ BLOCKED 上报 → controller 反向 fix plan (`88b3576`) → 第 2 次 implementer 按修订实施 → fixup `d806a45` 修 ruff-format drift（+ 顺手清 13 个 Stage C 文件的 tech debt #2 drift：cosmetic + 1 处 ruff 删 Integer unused import）；**Plan bug #1 on Stage D** |

---

## 仓库关键坐标

- **GitHub**：https://github.com/proecheng/ruisheng-scada （Private，账号 proecheng）
- **工作树**：`D:\江苏润盛\.claude\worktrees\plan-0-foundation`
- **主仓库**（master，只有设计/计划文档）：`D:\江苏润盛`
- **master 最新 commit**：`045c14f docs(progress): D3 complete — DB roles + GRANT baseline (4/10 in Stage D)`
- **worktree 实施分支最新 commit**：`d806a45 style(db): fixup D3 ruff-format + ruff-check`（前置 `2474275` feat D3）
- **alembic current**：`e74ffa548c2f (head)` — D3 migration
- **两个 worktree**：
  - `D:/江苏润盛` → master（只放 spec / plan / progress 文档）
  - `D:/江苏润盛/.claude/worktrees/plan-0-foundation` → feature/plan-0-foundation（实际代码）
- **已 push 的 tag**：`plan-0-stage-a-complete`、`plan-0-stage-b-complete`、`plan-0-stage-c-complete`
- **Docker stack**：运行中 (`docker compose -f docker-compose.dev.yml ps` 在 worktree)
  - `ruisheng-postgres-dev` (timescale/timescaledb:2.16.1-pg15) healthy，0.0.0.0:5432
  - `ruisheng-redis-dev` (redis:7-alpine) healthy，0.0.0.0:6379
  - 运行时长 ~1h+，内含 D3 应用的 26 张表 + 2 DB 角色 + GRANT

---

## 已完成

### Plan 0 Stage C（feature 分支，8/22 ✅）

| # | Task | Commit | Notes |
|---|---|---|---|
| C1 | Declarative Base + mixins | `dfb7157` + patch `1284a65` | 3 tests；patch 加 naming_convention |
| C2 | WxGroup (wx_groups) | `a27033c` | 3 tests |
| C3 | User + WxBinding + Phone + Email | `71eb752` | 12 tests；**2 次 revert**（详见下） |
| C4 | Device + Point + Static + Sim + Template | `8a770e7` retrofit + `c03db3c` 实现 | 16 tests；retrofit UQ template |
| C5 | DeviceWaringCfg + AlarmRecord + AlarmOutbox | `eef06ca` + fixup `ecdbefe` | 18 tests；spec review 抓出索引缺 DESC，已修；side-fix（devices.py mypy 紧化）被混入 feat commit（process 警告，已存 memory） |
| C6 | UserControlAction | `dfe10cb` + fixup `a7ae395` | 11 tests；单 commit（无 side-fix 混入）；fixup 补 CHANGELOG + 加 biconditional CK 注释 |
| C7 | TimingPlan + MaintainPlan + MaintainAction | `12bf516` + fixup `171b474` | 36 tests；2 轮审查 → spec v1.3.3（5 BLOCKER + 8 MAJOR inline 修）；fixup 补 SHARED_SCHEMA_VERSION bump + CHANGELOG 前缀 |
| C8 | ScenePage + SceneView | `4d8950e` + fixup `2e41292` | 38 tests + 2 Stage D 占位 skip；派发前 2 轮 review → spec v1.3.4（0 BLOCKER + 3 MAJOR inline 修，含 scene_* 租户一致性触发器 + 展示快照语义）；post-impl 2 轮 review → fixup（CHANGELOG + 3 语义警示 + skip-test 占位）；SHARED_SCHEMA_VERSION 保持 20260414（非破坏性扩展） |
| C9 | PayOrder + PayOrderSeen + ErrCode PAY_* | `232d413` + fixup `7517879` | 40 tests + 2 Stage D/E 占位 skip（+ErrCode 6 个）；派发前预检查发现 pay_orders DDL 未升级到 v1.3.3+ 通用规范 → 2 轮 review → spec v1.3.5（支付模块 DDL 补强 + gw_pool 回调路径 + 6 PAY_* ErrCode + §3.8.16 脱敏 + §5.10 两个 Job）；post-impl 2 轮 review → fixup（_HTTP_MAP 补 6 PAY_* + SHARED_SCHEMA_VERSION 20260414→20260415 breaking bump + CHANGELOG + http_status 测试 + skip-test 占位） |
| C10 | SoftLog + UserLoginRecord | `ba81585` | 36 tests + 2 Stage D 占位 skip；spec v1.3.6（soft_logs +source 字段 + level CHECK + 索引；user_login_records 全新 DDL）；spec review 抓出 5 MAJOR（全部时间列索引缺 DESC）；code review 抓出 1 IMPORTANT（ip_addr Mapped[Any]→Mapped[str] 与 devices.py 一致）+ 2 MINOR（对齐空格 + 模块 docstring 一致性）；全部 inline 修复后提交 |
| C11 | PointDataRealtime + PointDataHistory + WaveformHistory | `9ffa248` | 48 tests + 2 Stage D 占位 skip；关键决策：(1) SQLAlchemy 2.0.49 不支持 `postgresql_with` 作表级 kwarg — fillfactor 存入 `table.info` + Stage D Task D4 `ALTER TABLE` 落地；(2) hypertable 无显式 PK → ORM 用复合 PK (dev_number, point_id, recorded_at) 符合 TimescaleDB 要求；(3) `LargeBinary` for BYTEA（项目首次）；两阶段审查均 APPROVED（无需修复） |
| C21 | `__init__.py` 汇总 26 张表 | （增量维护完成，随每个 Cx 同步更新；无独立 commit）| 26 表全部导入 + `__all__` 排序 + scene_pages/scene_views NOTE（spec §3.7 gw 禁用）|
| C22 | Stage C 收尾 tag `plan-0-stage-c-complete` | tag pushed | 26 张 ORM 表 + mypy 0 issues (28 files) + 321 passed + 8 skipped + SHARED_SCHEMA_VERSION 20260415 + spec v1.3.6 |

**Stage C 至今发现 7 个 plan bug（已全部反向 fix 到 master）：**

1. **C1 patch**：Plan 原未加 `Base.metadata.naming_convention`，code review 抓到。加后约束名对 Alembic 稳定。Master `f4e66db`。
2. **CK/UQ name 双叠**：Plan 原写 `name="ck_users_user_name_format"` → 与 naming_convention 模板叠加成 `ck_users_ck_users_user_name_format`。改为裸名 `name="user_name_format"`。Master `e32493e`。C3 第一次 revert 源于此。
3. **UQ 模板不支持多列**：原模板 `%(column_0_name)s` 对多列 UQ 只取第一列名，且 `unique=True` 也受影响。改为 `%(constraint_name)s` 并强制所有 UQ 显式 `name=`。Master `8463b94`。C4 第一次 revert 源于此。
4. **C6 control CK 误值 'paid'**：Plan 原写 `CheckConstraint("(result = 'paid') = (completed_at IS NOT NULL)")`，但 `result` 合法值是 `pending/success/failed/timeout/cancelled`，无 'paid'。改为 `(result = 'pending') = (completed_at IS NULL)`。Master `54e3e6d`。派发前 controller 预检查抓到（未触发 revert）。
5. **C7 spec §4.2 缺 maintain_* DDL + timing_plans 不完整**：spec §4.3 表清单提了 `maintain_plans / maintain_actions` 但 §4.2 只有 `timing_plans` 原始 12 年前老 DDL（无 usr_group/deleted_at/updated_at/RLS），两张保养表 DDL 完全缺失。Controller 派 2 轮 reviewer（schema + 端到端数据流/UX），抓出 5 BLOCKER（RLS session 变量名、CK 命名双叠、软删+FK 语义、gw BYPASSRLS、并发推进）+ 8 MAJOR（partial unique alembic 幽灵、触发器/policy op.execute、timing_plans breaking、dead update_flag、幂等 action_uuid、user_name 索引、60s 时间容差、FK 命名交 convention）+ 9 MINOR。全部 BLOCKER+MAJOR inline 改入 spec v1.3.3（commit `879629b`），MINOR 按"延后 Plan 2/3"或 spec TODO。SHARED_SCHEMA_VERSION 20260413→20260414（breaking）。
7. **C9 spec §4.2 pay_orders DDL 未升级到 v1.3.3+ 通用规范**：pay_orders 原始 DDL（v1.3.2 旧版）缺 usr_group / updated_at / deleted_at / refund_at / RLS / 索引；pay_state 仅 4 值无 cancelled/expired；pay_orders_seen 嵌在 §3.5 代码片段而非 §4.2 表定义区。2 轮 reviewer 产出 5 MAJOR（全修）：(A) pay_orders_seen RLS 简化为角色授权 + gw_pool 回调路径；(B) 迁移阻塞方案（拒兜底租户）；(C) SHARED_SCHEMA_VERSION breaking bump；(D) openid 无 CHECK 决策；(E) mark_paid 终态转移收紧至 {pending,failed} → paid。post-impl 两轮 review 另抓 1 BLOCKER（_HTTP_MAP 缺 PAY_*）。spec v1.3.5（commit `eebf554`，+131/-21 行）。SHARED_SCHEMA_VERSION 20260414→20260415（breaking）。
6. **C8 spec §4.2 缺 scene_pages / scene_views DDL**：spec §4.3 表清单提了"组态 (2): scene_pages, scene_views"，§4.5 L1342 只一行"直通"，但 §4.2 **完整 DDL 区空白**；旧表 `ZTPageInf` / `ZTViewInf` 无 UsrGroup/deleted_at/updated_at/RLS。Controller 派 2 轮 reviewer：第 1 轮产 DDL 初稿 + 抓 Q-B08 TODO；第 2 轮 Controller 追加 2 个 MAJOR（跨表租户一致性不变量 + Company/Department 快照语义），reviewer 回复最终方案（BEFORE INSERT/UPDATE 触发器 `enforce_scene_tenant_consistency` + BEFORE INSERT `fill_scene_views_snapshot`）+ 自抓 3 MAJOR（partial unique 字段从 4 元 `(page_id, dev, pos_x, pos_y)` 收敛到 2 元 `(page_id, dev)`、第 1 轮 §4.5"直通"误判必改非直通、pgloader AFTER LOAD 完整 SQL 块）。总计 0 BLOCKER + 3 MAJOR + 5 MINOR，全部 MAJOR inline 改入 spec v1.3.4（commit `0daa0d2`，+225/-2 行）。SHARED_SCHEMA_VERSION **保持 20260414**（仅 DB schema 扩展，未改 shared Pydantic/enum）。Q-B08（子页面层级 / 背景图分辨率 / SVG 支持）保留为 §4.2 DDL 顶部 TODO。

**Process 教训（2 次 implementer 静默改 spec）：**
- C3 attempt 1：implementer 发现双叠、静默改 spec 短名 → revert + prompt 加 STRONG guard
- C4 attempt 1：implementer 发现 UQ 模板 bug、静默改 base.py + users.py → revert + Path B 正式化

两次都是 implementer 经验正确、process 错误。每次 revert + 反向 fix plan 效果好，但实施成本高（多跑一轮）。

### 文档阶段（master 分支）

9 次 commit 从 spec v1.0 → v1.3.2（5 角色审查 + 一致性 + 边界/数据流三轮）+ Plan 0 完整实施计划 2678/4265 行。

### Plan 0 Stage B（feature 分支，12/12 ✅）

| # | Task | Commit | Notes |
|---|---|---|---|
| B1 | enums/__init__.py 骨架 | `b05279d` | 调整为 bare skeleton（B2–B6 增量追加 re-export） |
| B2 | FunCode 枚举 + normalize | `b8354b9` | 9 tests |
| B3 | AlarmType 枚举 | `fa1c068` | 7 tests |
| B4 | AlarmAction + PhoneAlarm 位编解码 | `6a4e1e2` | 3 tests；plan 错算修正见 master `ee8c309` |
| B5 | ControlStatus 枚举 | `bff1238` | 3 tests |
| B6 | Authority 4 级 RBAC | `ada7097` | 5 tests |
| B7 | ErrCode + BizError | `82b3dba` | 4 tests |
| B8 | constants/protocol.py | `97ad575` | 5 tests |
| B9 | constants/limits.py | `c82ae9d` | 5 tests |
| B10 | validators/rs485.py | `841dcb2` | 7 tests；plan 公式 bug 修正见 master `479dcdf` |
| B11 | schemas/ + ApiResponse | `97f38b1` | 2 tests |
| B12 | 覆盖率 + tag | tag `plan-0-stage-b-complete` | 52 tests pass，覆盖率 97.24% |

**Stage B 发现 2 个 plan bug（已改 master）：**
1. B4 测试用 `call_on_reset=False` 但期望 `0x2103`（数学上需 True）→ implementer 静默改了（process 违规）→ controller 后续加了"never silently modify"的 guard，并反向 fix plan
2. B10 `min_poll_interval_decisec` 原公式 `(ms+999)//100 + 10` 把 ms 按 centisec 除，× 10 offset 也不对 → 所有 5 个测试都 fail → controller 预检查后给 implementer 正确公式 `((ms+999)//1000) * 10`

---

### Plan 0 Stage A（feature 分支，15/15 ✅）

| # | Task | Commit |
|---|---|---|
| A1 | uv + pyproject | `7b45f95` + fixup `f68d88c` |
| A2 | .gitignore 扩展 | `56a783f` |
| A3 | ruisheng-shared 骨架 | `a225398` |
| A4 | pre-commit 配置 | `2f9f28c` |
| A5 | verify_schema_version.py stub | `3a77507` |
| A6 | docker-compose.dev.yml + .env.example | `d37b937` |
| A7 | Makefile + taskipy 12 tasks | `b9df8ec` |
| A8 | CI base workflow | `f0d8a03` |
| A9 | tests 骨架 + smoke (2 passed) | `e97ffc4` |
| A10 | README.md | `6733efb` |
| A11 | CONTRIBUTING.md（含 Windows 环境注意）| `e0a7df7` |
| A12 | bootstrap 验证 + pytest pythonpath fix | `de42b64` |
| A13 | .gitattributes | `5fec886` |
| A14 | task bootstrap 一键命令 | `a787cff` |
| A15 | Stage A 收尾 tag | tag `plan-0-stage-a-complete` |

---

## 下一步待办

### Stage C（23 task，ORM 模型）
### Stage D（8 task，Alembic 迁移，需 Docker）— 含新增 **D0 Docker + TimescaleDB 环境前置校验**
### Stage E（7 task，测试基建 + seeds，需 Docker）
### Stage F（6 task，PCAP 生成器）
### Stage G（7 task，CI 完备 + 文档 + release + 技术债清理）— 新增 **G6 deps 迁移**、**G7 release workflow**

---

## 2026-04-14 Spec v1.3.4 修订（C8 派发前 2 轮审查产出）

**变更范围**：master spec v1.3.3 → v1.3.4（commit `0daa0d2`，+225/-2 行）

**修订清单**：
- §3.7 追加"gw 禁止访问 scene_pages / scene_views"（CI lint 扫描违规 P0 阻塞）
- §3.8.18 新增 scene_views Company/Department 展示快照规则（INSERT 自动填充 / 允许覆盖 / UPDATE 不同步）
- §4.1.1 新增 (4)(5) 两个 PL/pgSQL 触发器函数：
  - `enforce_scene_tenant_consistency()`：scene_pages/scene_views 的 BEFORE INSERT/UPDATE 校验 usr_group 与 users / scene_pages / devices 一致（补 RLS 只防读不防跨表写的盲点），RAISE EXCEPTION 23514
  - `fill_scene_views_snapshot()`：BEFORE INSERT 从 users 反查填充 company/department
- §4.2 新增 scene_pages / scene_views 完整 DDL（NUMERIC(10,2) 坐标 + sanity bounds CHECK 、partial unique `(scene_page_id, dev_number) WHERE deleted_at IS NULL`、RLS policy、zh-x-icu collation、Q-B08 TODO 注释块）
- §4.5 L1342 `ZTPageInf / ZTViewInf` 从"直通"展开为 2 行非直通迁移映射 + pgloader AFTER LOAD DO 完整 SQL 块（禁用触发器 → 反查填充 → NULL 断言 → 启用触发器 → 空 UPDATE 事后校验）

**审查发现**：0 BLOCKER + 3 MAJOR + 5 MINOR。全 MAJOR inline 修复；MINOR 按"延后 Stage D alembic"或 "implementer 备忘"处理。

**关键 MAJOR**：
- MAJOR A 跨表租户一致性：方案 1 PL/pgSQL 触发器（拒绝方案 2 GENERATED STORED 不支持跨表 / 方案 3 复合 FK 需改 users/devices UNIQUE）
- MAJOR B Company/Department 快照语义：INSERT-fill + 允许覆盖 + UPDATE 不同步
- MAJOR C partial unique 4 元改 2 元（语义弱：像素级唯一 → "同页同设备只配 1 个热点"）

---

## 2026-04-14 Spec v1.3.3 修订（C7 派发前 2 轮审查产出）

**变更范围**：master spec v1.3.2 → v1.3.3（commit `879629b`，+173/-13 行）

**修订清单**：
- §3.6 L656 保养 L1 权限 R → ▲自有（一线员工 CRUD 本人设备）
- §3.7 扩写软删+FK 语义 + gw `ruisheng_gw BYPASSRLS` 角色 + api SET LOCAL 规则
- §3.8.17 新增保养推进状态机（`max(now(), old) + interval` 非累加 + `FOR UPDATE` + action_uuid 幂等）
- §3.8.16 追加 maintain_actions 同款 3 年保留+脱敏
- §4.1 规约表 +7 行（TZ Asia/Shanghai 无 DST / VARCHAR 在 API 层 strip+NFC / FK-CHECK 命名交 naming_convention / Index 显式命名 / RLS policy 统一 tenant_isolation / Trigger trg_<table>_updated / 表单时间字段 60s 容差）
- §4.1.1 新增通用设施（`set_updated_at()` 触发器函数 + ruisheng_gw/ruisheng_api 两个 DB 角色定义）
- §4.2 timing_plans 原地重写（+usr_group/deleted_at/updated_at/FK/RLS/3 索引/触发器）+ 新增 maintain_plans + maintain_actions 完整 DDL
- §4.3 表计数 21 → 26（v1.0 起就数错了）
- §4.5 L1341 TimingPlan 迁移从"直通"改为"非直通"（usr_group 从 devices 反查填入）
- §5.1 PG SQLSTATE → ErrCode 中文映射表（TODO Plan 2 实现）
- SHARED_SCHEMA_VERSION 20260413 → 20260414（breaking）

**审查发现**：5 BLOCKER + 8 MAJOR + 9 MINOR。BLOCKER+MAJOR 全部 inline 修复；MINOR 按"延后 Plan 2/3"处理（具体清单见 spec §10 v1.3.3 changelog）。

---

## 2026-04-14 结构复核结论（进 Stage B 前）

Stage B-G 复核完成，已做 3 处 Plan 修订 — **全部已 commit 到 master**：

| 位置 | 修订 | commit |
|---|---|---|
| Stage D 开头 | 插入 Task **D0**：Docker / docker-compose / PG / TimescaleDB 扩展 / Redis 前置校验（7 steps 具体命令，无 commit，仅验证） | `ba6bfeb` |
| Stage G 末尾 | 新增 Task **G6**：迁移 `[tool.uv] dev-dependencies` → PEP 735 `[dependency-groups]`，顺便再测 `testcontainers[postgres]` 能否自带 `asyncpg`（处理遗留技术债 #1 + #3） | `ba6bfeb` |
| Stage G 末尾 | 新增 Task **G7**：ruisheng-shared release workflow（semver + SHARED_SCHEMA_VERSION + CHANGELOG + GitHub Release，**不发 PyPI**） | `ba6bfeb` |

**审查员误判已否决**（不改）：
- B11 已经是 schemas 骨架（含 ApiResponse 泛型壳 + 2 个测试）→ 无需再插 B12a
- B4 已经包含完整位编码测试（decode 0x0103 + encode 0x2103）→ 无需补

**风险确认**：
- ✅ docker-compose.dev.yml 用的是 `timescale/timescaledb:2.16.1-pg15`（不是普通 postgres:15），TimescaleDB 扩展风险 **不存在**
- ⚠️ Docker Desktop 仍未装 — **进 Stage D 前必须装**（Stage C 仍是纯 Python，不受影响）

---

## 环境前置条件（执行前请确认）

| 需求 | 谁用 | 状态 |
|---|---|---|
| Docker Desktop | Stage D/E/A12 runtime 验证 | ❌ 未装（阻塞 D6 集成测试）|
| Git for Windows `usr/bin` 在 PATH | pre-commit 能正常工作 | ⚠️ 需手工加 |
| `chcp 65001` 或 `$env:PYTHONUTF8=1` | 避免 GBK 编码 bug | ⚠️ 需手工设置 |
| `gh` CLI 已登录 | push / PR | ✅ proecheng 账号 |
| uv 0.11.x 已装 | 所有 task | ✅ |

---

## 遗留技术债

1. **`[tool.uv] dev-dependencies` 已被 uv 弃用**（warning 每次 sync 出现）→ 将来迁移到 `[dependency-groups]`。Plan 里标为 Minor，Stage G 前处理。
2. ~~**pre-commit hook 在 Windows GBK locale 下不稳定** → 已用 `--no-verify` 绕过多次。根治需开发者端 UTF-8 设置，CONTRIBUTING.md 已写。~~ **D3 fixup `d806a45` 已全仓清扫 13 个 drift 文件**（纯 cosmetic + 1 处 unused import）。后续 implementer 务必 `uv run task fmt` 再 commit。
3. **`testcontainers[postgres]` extra 未完全解析** → 已通过加 `asyncpg` 绕过，根治同上（deps-groups 迁移）。
4. **Migration 里 SQL 字符串用 f-string 插 env var**（D3 code quality reviewer IMPORTANT 2）：当前可接受（密码来自开发者自控 env，含单引号会自炸不是外部注入），但后续新 migration 规范里应统一 `quote_literal()` 或 `EXECUTE format('... %L', var)` 模式。若有含单引号的合法密码会当场炸 ProgrammingError，可在 `_require_env` 里加 `assert "'" not in value`。

---

## 恢复步骤（下次续跑）

1. 打开 `D:\江苏润盛`，重新连接 Claude Code session
2. 指向本文件：让 Claude 读 `docs/superpowers/plans/PROGRESS.md` 恢复状态
3. （可选）同步远端：`cd D:\江苏润盛\.claude\worktrees\plan-0-foundation && git pull`
4. Claude 按本文件 "下一步待办" 接着从 **Stage D / Task D2（autogenerate 初始 schema）** 开始
5. 继续 subagent-driven-development 流程（implementer → spec review → quality review）

---

## 🔖 会话交接点（2026-04-16，D3 完成后 — D4 准备）

**当前位置**：Stage D，D0/D1/D2/D3 完成 **4/10**，下一步 **D4（PL/pgSQL 函数 ×3）**

**环境状态**（续跑直接用，无需重建）：
- Docker stack 运行中（postgres + redis，~1h+ uptime，均 healthy）
- alembic 当前：`e74ffa548c2f (head)` = D3 roles+grants
- PG 库里已有：26 张表 + alembic_version + 2 角色（ruisheng_gw BYPASSRLS / ruisheng_api）+ schema/table/sequence GRANT
- 环境变量（续跑时重新 export，shell 会话不保留）：
  ```bash
  export RUISHENG_GW_PASSWORD='dev-gw-change-me'
  export RUISHENG_API_PASSWORD='dev-api-change-me'
  ```
  值以 `.env.example` 为准；dev 直接用占位；生产走 secret manager

**本次会话（2026-04-16）主要成果**：
1. ✅ D2 完成 — autogenerate 26 张表 + 幂等 + 双 review APPROVED（commit `9f2f102`）
2. ✅ Plan v1.1 重编 D3-D10 — 2 轮 plan gap review 抓 5 MAJOR + 落 controller 3 顾虑（master `79119e0`）
3. ✅ Plan bug #1 反向 fix — D3 `REVOKE FROM PUBLIC` 无法缩权 → 改 `REVOKE FROM PUBLIC, gw, api`（master `88b3576`）
4. ✅ D3 完成 — DB 角色 + 三级 GRANT + 3 张表细粒度 + 密码 raise + 幂等 ALTER ROLE（commits `2474275` + fixup `d806a45`）
5. ✅ Tech debt #2 全仓清扫 — D3 fixup 顺手过 `ruff format .` 清 13 个 Stage C 遗留 drift 文件

**下一步：D4 — PL/pgSQL 通用函数（3 个）**

**Spec 依据**：§4.1.1 (1)(4)(5) L978-1054，共 3 个函数：
1. `set_updated_at()` — 简单 `NEW.updated_at = now()`（L978-979）
2. `enforce_scene_tenant_consistency()` — scene_pages / scene_views BEFORE INSERT/UPDATE 校验（L996-1038）含 5 处 `RAISE EXCEPTION USING ERRCODE='23514'` 英文错误消息（**不是 CJK**）
3. `fill_scene_views_snapshot()` — scene_views BEFORE INSERT 反查 users 填 company/department（L1043-1054）

**D4 关键要点**（M3 已融合入 plan）：
- 每个函数尾部硬绑 `SET search_path = pg_catalog, public`（函数级属性，防会话层 search_path 劫持）
- 三函数全部 **SECURITY INVOKER**（默认，绝不用 DEFINER 否则绕 RLS）
- 函数体所有 `WHERE ... AND deleted_at IS NULL` 必须保留（spec L1003/L1015/L1026/L1051）
- `CREATE OR REPLACE FUNCTION` 本身幂等，downgrade 用 `DROP FUNCTION IF EXISTS` 反向
- Dollar-quoting `$$ ... $$` 避免 Python f-string 单引号冲突；建议 `r"""..."""` 原始字符串

**D4 执行路径**：
1. `cd D:/江苏润盛/.claude/worktrees/plan-0-foundation`
2. `uv run alembic revision -m "plpgsql: set_updated_at + scene tenant helpers"`
3. 按 plan §Task D4 Step 2 的完整代码片段贴入生成的 migration 文件（3 段 `op.execute(r"""...""")`）
4. `uv run alembic upgrade head` → `\df public.*` 见 3 个函数，`prosecdef=false`，`proconfig={search_path=pg_catalog, public}`
5. downgrade `-1` → `\df` 无；再 upgrade 幂等
6. 回归 pytest 321+8
7. commit + push（conventional commits `feat(db): D4 plpgsql functions ...`）

**D4 后的 task 链（D5-D10）**：见 plan §Stage D 完整 markdown；每个 task 都带详细 code snippet + 验证步骤 + 关键风险。

**执行纪律**：
- subagent-driven：每 task 1 implementer + 2 review（spec → quality）
- 发现 plan/spec bug **必须 BLOCKED**，绝不静默改（已见 Plan bug #1 良好先例）
- commit 前必须 `uv run task fmt`（D3 fixup 已验证 pre-commit format gate）
- commit 不混 side-fix（除非与 feat 强关联，如 D3 的 `.env.example` / `CONTRIBUTING.md`）
- 每 task 完成后立即更新 PROGRESS + push master

**Plan v1.1 重编原因**：D2 完成后做 plan gap review，发现 v1.0 对 spec v1.3.3/1.3.4/1.3.6 覆盖严重不足：
- 3 个 PL/pgSQL 函数（set_updated_at / enforce_scene_tenant_consistency / fill_scene_views_snapshot）0 task 覆盖
- 2 个 DB 角色（ruisheng_gw BYPASSRLS / ruisheng_api）0 task 覆盖
- 16 个触发器（13 updated_at + 3 scene）0 task 覆盖
- 6 张 RLS 漏表（maintain_plans / maintain_actions / pay_orders / user_login_records / user_wx_bindings / alarm_outbox）
- user_login_records hypertable（v1.3.6 新增）漏
- device_waring_cfgs fillfactor（§5.10 L1930）漏
- soft_logs 压缩（§4.2 L1399-1400）漏
- 原 D3 downgrade `DROP TABLE CASCADE` 是 bug（误删 D2 建的表）

**2 轮 plan review 抓的 5 MAJOR（已合入 Plan v1.1）**：
1. **M1** RLS 必须 `FORCE`（owner 绕过）：D6 每表 `FORCE ROW LEVEL SECURITY`
2. **M2** hypertable 需幂等：D8 用 `if_not_exists => TRUE` + `remove_*_policy → add_*_policy`
3. **M3** 函数 `SET search_path = pg_catalog, public` 硬绑定：D4 三函数末尾补
4. **M4** GRANT 漏 SEQUENCES：D3 补 `GRANT USAGE,SELECT ON ALL SEQUENCES` + `ALTER DEFAULT PRIVILEGES`
5. **M5** PROGRESS 与 spec RLS 变量名冲突：已修正（**`app.tenant_id`** + **`app.role`**，非 `app.current_usr_group`）

**新 D3-D10 结构**（detail 见 plan §Stage D）：

| # | Title | Migration 文件 |
|---|---|---|
| D3 | DB 角色 + GRANT 基线 | `0002_db_roles_and_grants.py` |
| D4 | PL/pgSQL 函数（3 个，search_path 硬绑） | `0003_plpgsql_functions.py` |
| D5 | 17 张表 `trg_<table>_updated`（ORM drift 断言） | `0004_updated_at_triggers.py` |
| D6 | scene_* 3 触发器 + 18 张表 FORCE RLS + tenant_isolation policy | `0005_scene_triggers_and_rls.py` |
| D7 | fillfactor/autovacuum（含 device_waring_cfgs） | `0006_hot_table_tuning.py` |
| D8 | TimescaleDB hypertable×6（含 user_login_records） | `0007_timescale_hypertables.py` |
| D9 | 集成测试（13 case） | `tests/integration/test_alembic_upgrade.py` |
| D10 | 收尾 tag + spec v1.3.7 follow-up 清单 | tag `plan-0-stage-d-complete` |

**新 D3 要做的事**（detail 见 plan §Task D3）：
1. docker stack 保持运行（D2 没关）；如停了 `docker compose -f docker-compose.dev.yml up -d`
2. 生成 revision：`uv run alembic revision -m "db roles ruisheng_gw/ruisheng_api + grants"`
3. 填充 upgrade()：`_require_env()` helper + 幂等 `DO $$ IF NOT EXISTS ... $$` + schema/table/sequence 级 GRANT + ALTER DEFAULT PRIVILEGES + 3 张表 REVOKE+GRANT
4. 填充 downgrade()：`DO $$ DROP OWNED BY ... DROP ROLE IF EXISTS END $$`
5. 同步改 `.env.example` + `CONTRIBUTING.md`（追加环境变量小节）
6. export `RUISHENG_GW_PASSWORD` + `RUISHENG_API_PASSWORD` → upgrade → `\du` → downgrade → upgrade 幂等
7. commit + push

**RLS 变量名（权威）**：`app.tenant_id`（租户）+ `app.role`（'Administrators' 跨租户权限）。严禁用 `app.current_usr_group`。

**参考**：plan §Stage D 完整代码片段、spec §3.7 / §4.1 / §4.1.1 / §4.2 / §5.10

---

## 📚 Stage D 累计关键经验（后续 task 可参考）

### 镜像加速（国内必须）
- **`timescale/timescaledb:2.16.1-pg15` 只在 `docker.1ms.run/` 可拉**
- DaoCloud 对该镜像返 500（其他 image 正常），USTC/163 DNS 解析失败，dockerproxy IP 被污染
- 已配置 5 个镜像源到 Docker Engine（`docker.m.daocloud.io` 等），特定 image 失败时用 `docker pull docker.1ms.run/<name> && docker tag <1ms.run/name> <name>` 变通

### CJK 路径 + uv editable install
- `D:\江苏润盛\...` 路径下，uv 写入 UTF-8 `.pth` 被 Python `mbcs` 解码器损坏 → editable install 的 `ruisheng_shared` 包 import 失败
- 修法：alembic.ini `prepend_sys_path = . ruisheng-shared/src` + `path_separator = space`（跨平台）
- **Stage B-C 的 pytest 配置已经 hack 过**：`pyproject.toml` `pythonpath = ["ruisheng-shared/src"]` 原因同此

### Windows configparser GBK 限制
- `alembic.ini` 的注释不能含中文（GBK 解码器会炸 `UnicodeDecodeError: 'gbk' codec can't decode`）
- 所有 `.ini` 注释必须 ASCII/英文

### Docker Desktop 配置层次
- `C:\Users\<user>\.docker\daemon.json` — Docker CLI 配置（**Docker Desktop 不读**）
- `%APPDATA%\Docker\settings-store.json` — Docker Desktop 应用配置（`UseContainerdSnapshotter`、`AutoStart` 等）
- **Docker Engine 的 daemon 配置**（含 `registry-mirrors`）通过 **Docker Desktop UI → Settings → Docker Engine** 标签页编辑，存在别处，不是上面两个文件
- 启用 containerd snapshotter 时 `registry-mirrors` 生效路径不同；本项目用默认（不启 containerd）

---

## 当前技术栈状态（2026-04-16 D3 完成后）

- 26 张 ORM 模型全部实现，mypy 0 issues（28 files），pytest 321 passed + 8 skipped
- `SHARED_SCHEMA_VERSION=20260415`，spec `v1.3.6`（Plan v1.1 重编对应 spec v1.3.7 follow-up，见 D10）
- Alembic 1.13 已加为显式 dev-dep，`alembic/env.py` async 版本就绪
- **`alembic/versions/` 已有 2 个迁移**：
  - D2：`20260416_e05529ef4abb_initial_schema_26_tables.py`（26 张 CREATE TABLE）
  - D3：`20260416_e74ffa548c2f_db_roles_ruisheng_gw_ruisheng_api_grants.py`（2 角色 + GRANT）
- Docker stack 运行中（postgres + redis healthy）；PG 里有完整 26 张表 + alembic_version + 2 角色
- Git 全部 push 完毕；tag `plan-0-stage-c-complete` 已存在（Stage D 未打 tag，D10 才打）
- worktree HEAD: `d806a45`，master HEAD: `045c14f`

### Stage D 快速导航

- Plan 位置：`docs/superpowers/plans/2026-04-13-plan-0-foundation.md` §Stage D（v1.1 重编）
- Task 顺序（v1.1）：~~D0 环境校验~~ ✅ → ~~D1 baseline~~ ✅ → ~~D2 初始 schema~~ ✅ → **D3 DB 角色 + GRANT** ← 下一步 → D4 PL/pgSQL 函数 → D5 updated_at 触发器 → D6 scene 触发器+FORCE RLS → D7 fillfactor → D8 hypertable → D9 集成测试 → D10 tag + spec follow-up
- 与 Stage C 的区别：**需要真实 PG + Redis 容器**（不再是纯 Python 单测）
- 关键产出：7 个 `alembic/versions/*.py` 迁移脚本 + `tests/integration/test_alembic_upgrade.py`（13 case）+ tag `plan-0-stage-d-complete`

---

## 关键决策回放（Plan 0 启动时）

- 架构：方案 B 前后分离 + Redis（非单体，非 MQTT Hub）
- 技术栈：Python FastAPI + Vue 3 + PostgreSQL + TimescaleDB + Redis
- 部署：Windows Server + B/S
- 外部集成：微信公众号 + 微信支付 + 短信 + IVR + SMTP 全上
- MVP 范围：P0 全部 + 部分 P1（12 周）
- Plan 拆分：Plan 0 基础 / Plan 1 gw / Plan 2 api / Plan 3 web / Plan 4 部署
- 执行方式：subagent-driven（每 task 一个 implementer + 两阶段 review）
- 自动 push：每 task commit 后自动 push

---

**END OF PROGRESS**
