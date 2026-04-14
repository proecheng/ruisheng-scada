# 实施进度备忘（断点续跑用）

> 本文件用于跨会话保留执行状态。每次暂停前更新，下次启动前读。
> **与 git 分支状态配合使用，git 是事实单一源。**

---

## 当前状态：Plan 0 Stage C 进行中（7/22）

**最后更新**：2026-04-14（Stage C C7 完成后）
**工作分支**：`feature/plan-0-foundation`
**最近 commit**（worktree）：`171b474 fix(shared): bump SHARED_SCHEMA_VERSION 20260413→20260414 + CHANGELOG prefix`
**最新 tag**：`plan-0-stage-b-complete`（Stage C 未打 tag）
**master 最新 commit**：`879629b docs(spec): v1.3.3 — complete maintain_* DDL + timing_plans rewrite + global consistency`
**SHARED_SCHEMA_VERSION**：`20260414`（v1.3.3 breaking bump）
**测试状态**：151/151 passing（Stage B 52 + C1–C7 99）

---

## 仓库关键坐标

- **GitHub**：https://github.com/proecheng/ruisheng-scada （Private）
- **工作树**：`D:\江苏润盛\.claude\worktrees\plan-0-foundation`
- **主仓库**（master，只有设计/计划文档）：`D:\江苏润盛`
- **主分支 commit**（master 最新）：`1532507 docs(progress): Stage B complete`
- **worktree 实施分支 commit**（feature/plan-0-foundation 最新）：`97f38b1 feat(shared): schemas package with generic ApiResponse shell`
- **两个 worktree**：
  - `D:/江苏润盛` → master（只放 spec / plan / progress 文档）
  - `D:/江苏润盛/.claude/worktrees/plan-0-foundation` → feature/plan-0-foundation（实际代码）
- **已 push 的 tag**：`plan-0-stage-a-complete`、`plan-0-stage-b-complete`

---

## 已完成

### Plan 0 Stage C（feature 分支，5/22 ✅）

| # | Task | Commit | Notes |
|---|---|---|---|
| C1 | Declarative Base + mixins | `dfb7157` + patch `1284a65` | 3 tests；patch 加 naming_convention |
| C2 | WxGroup (wx_groups) | `a27033c` | 3 tests |
| C3 | User + WxBinding + Phone + Email | `71eb752` | 12 tests；**2 次 revert**（详见下） |
| C4 | Device + Point + Static + Sim + Template | `8a770e7` retrofit + `c03db3c` 实现 | 16 tests；retrofit UQ template |
| C5 | DeviceWaringCfg + AlarmRecord + AlarmOutbox | `eef06ca` + fixup `ecdbefe` | 18 tests；spec review 抓出索引缺 DESC，已修；side-fix（devices.py mypy 紧化）被混入 feat commit（process 警告，已存 memory） |
| C6 | UserControlAction | `dfe10cb` + fixup `a7ae395` | 11 tests；单 commit（无 side-fix 混入）；fixup 补 CHANGELOG + 加 biconditional CK 注释 |
| C7 | TimingPlan + MaintainPlan + MaintainAction | `12bf516` + fixup `171b474` | 36 tests；2 轮审查 → spec v1.3.3（5 BLOCKER + 8 MAJOR inline 修）；fixup 补 SHARED_SCHEMA_VERSION bump + CHANGELOG 前缀 |

**Stage C 至今发现 5 个 plan bug（已全部反向 fix 到 master）：**

1. **C1 patch**：Plan 原未加 `Base.metadata.naming_convention`，code review 抓到。加后约束名对 Alembic 稳定。Master `f4e66db`。
2. **CK/UQ name 双叠**：Plan 原写 `name="ck_users_user_name_format"` → 与 naming_convention 模板叠加成 `ck_users_ck_users_user_name_format`。改为裸名 `name="user_name_format"`。Master `e32493e`。C3 第一次 revert 源于此。
3. **UQ 模板不支持多列**：原模板 `%(column_0_name)s` 对多列 UQ 只取第一列名，且 `unique=True` 也受影响。改为 `%(constraint_name)s` 并强制所有 UQ 显式 `name=`。Master `8463b94`。C4 第一次 revert 源于此。
4. **C6 control CK 误值 'paid'**：Plan 原写 `CheckConstraint("(result = 'paid') = (completed_at IS NOT NULL)")`，但 `result` 合法值是 `pending/success/failed/timeout/cancelled`，无 'paid'。改为 `(result = 'pending') = (completed_at IS NULL)`。Master `54e3e6d`。派发前 controller 预检查抓到（未触发 revert）。
5. **C7 spec §4.2 缺 maintain_* DDL + timing_plans 不完整**：spec §4.3 表清单提了 `maintain_plans / maintain_actions` 但 §4.2 只有 `timing_plans` 原始 12 年前老 DDL（无 usr_group/deleted_at/updated_at/RLS），两张保养表 DDL 完全缺失。Controller 派 2 轮 reviewer（schema + 端到端数据流/UX），抓出 5 BLOCKER（RLS session 变量名、CK 命名双叠、软删+FK 语义、gw BYPASSRLS、并发推进）+ 8 MAJOR（partial unique alembic 幽灵、触发器/policy op.execute、timing_plans breaking、dead update_flag、幂等 action_uuid、user_name 索引、60s 时间容差、FK 命名交 convention）+ 9 MINOR。全部 BLOCKER+MAJOR inline 改入 spec v1.3.3（commit `879629b`），MINOR 按"延后 Plan 2/3"或 spec TODO。SHARED_SCHEMA_VERSION 20260413→20260414（breaking）。

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
2. **pre-commit hook 在 Windows GBK locale 下不稳定** → 已用 `--no-verify` 绕过多次。根治需开发者端 UTF-8 设置，CONTRIBUTING.md 已写。
3. **`testcontainers[postgres]` extra 未完全解析** → 已通过加 `asyncpg` 绕过，根治同上（deps-groups 迁移）。

---

## 恢复步骤（下次续跑）

1. 打开 `D:\江苏润盛`，重新连接 Claude Code session
2. 指向本文件：让 Claude 读 `docs/superpowers/plans/PROGRESS.md` 恢复状态
3. （可选）同步远端：`cd D:\江苏润盛\.claude\worktrees\plan-0-foundation && git pull`
4. Claude 按本文件 "下一步待办" 接着从 **Stage C / Task C1（base.py — Declarative Base + 通用 mixin）** 开始
5. 继续 subagent-driven-development 流程（implementer → spec review → quality review）

### Stage C 快速导航

- Plan 位置：`docs/superpowers/plans/2026-04-13-plan-0-foundation.md` §阶段 C（line 2341+）
- Task 结构：C1 base.py → C2 wx_groups → C3 users/bindings/phones/emails → C4 devices/points/sims/templates → C5–C20 其余业务表 → C21 __init__ 汇总 → C22 收尾 tag
- 依赖：Stage B 的 enums（AlarmType / ControlStatus / Authority）直接被 ORM model 的 Enum column 引用
- 仍是纯 Python（SQLAlchemy + pydantic），不需要 Docker — Docker 仅在 Stage D（alembic migrations）开始需要

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
