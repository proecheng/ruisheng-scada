---
title: '全项目测试与质量加固'
type: 'bugfix'
created: '2026-08-18'
status: 'done'
baseline_commit: '4bb03a288d4ffed10f6d294cf6cfab11111e9b25'
context:
  - '{project-root}/需求清单/功能全景清单.md'
  - '{project-root}/docs/ARCHITECTURE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** 项目虽已有较完整的单元与端到端测试，但仍存在时序数据跨租户读取、前端离线缓存和生命周期健壮性、破坏性测试误连开发库，以及离线部署镜像和网关 WAL 不闭环等高风险问题。

**Approach:** 在保留现有架构和接口契约的前提下修复可在仓库内验证的根因，补充针对性回归测试，并用隔离数据库执行前后端、迁移、回放、部署配置和端到端回归；依赖真实硬件、第三方凭据或产品决策的能力只记录验收缺口。

## Boundaries & Constraints

**Always:** 保持 `.claire/`、`.claude/` 和用户现有数据不变；破坏性迁移仅可连接显式测试数据库；所有租户时序查询必须经受 RLS 保护的设备归属约束；修复必须通过静态检查、单测、集成测试、构建和 E2E。

**Ask First:** 需要删除现有数据、访问真实客户环境、使用第三方生产凭据，或改变公开 API 契约时必须先征得用户同意。

**Never:** 不伪造短信、电话、微信支付、真实串口和性能验收结果；不把未实现的产品能力包装成缺陷修复；不修改或清理用户已有未跟踪目录。

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| 租户时序查询 | 普通租户查询报表或波形 | 只返回该租户设备的数据 | 外租户设备按不存在处理且不泄漏数据 |
| 会话恢复 | localStorage 含损坏或伪造用户 JSON | 应用正常启动并清除无效会话 | 不抛出未捕获异常 |
| WebSocket 关闭 | 创建连接后显式 close | 客户端和状态同步计时器均停止 | 后续 tick 不访问空 singleton |
| PWA 离线 | 已认证 API GET 成功后换账号/离线 | 不从共享缓存返回前一账号 API 数据 | 网络失败按正常请求失败处理 |
| 破坏性迁移测试 | DSN 指向非测试库 | 测试在 downgrade 前拒绝运行 | 给出明确测试库命名错误 |
| 离线部署 | 目标机仅有 deploy 包和导出镜像 | migrate 复用 API 镜像且无需源码构建 | Compose 配置可离线解析 |

</frozen-after-approval>

## Code Map

- `ruisheng-api/src/ruisheng_api/api/{reports,waveforms}.py` -- 无 RLS 时序表的租户查询入口
- `ruisheng-api/tests/` -- API 数据隔离回归测试
- `ruisheng-web/src/{stores/auth.ts,composables/useWsConnection.ts}` -- 会话恢复与连接生命周期
- `ruisheng-web/vite.config.ts` -- PWA 缓存策略
- `conftest.py`、`tests/integration/test_alembic_upgrade.py` -- 集成环境与破坏性迁移保护
- `docker-compose.prod.yml`、`deploy/docker-compose.prod.yml` -- 生产构建、离线镜像与 WAL 持久化

## Tasks & Acceptance

**Execution:**
- [x] 修复报表/波形查询的设备归属约束并补路由回归测试。
- [x] 修复会话损坏恢复、WebSocket 定时器泄漏及跨账号 API 缓存，并补单测。
- [x] 为迁移测试增加测试库硬保护，补齐集成标记和安全分层入口。
- [x] 让离线部署使用具名镜像、migrate 复用 API 镜像，并持久化 GW WAL。
- [x] 执行 Python/前端静态检查、单元/集成/回放、构建、Playwright 和 Compose 验证。

**Acceptance Criteria:**
- Given 两个租户存在同名或不同名设备时，when 普通租户请求日报或波形，then 响应不包含另一租户时序数据。
- Given 前端持久化会话损坏或连接已关闭，when 应用恢复或定时器触发，then 不产生未捕获异常和后台计时器泄漏。
- Given 迁移测试误指向开发库，when 测试准备执行 downgrade，then 在任何 schema 修改前失败。
- Given 仅复制 deploy 包与导出镜像，when 解析生产 Compose，then 所有应用服务均引用已导出的镜像且 GW WAL 位于持久卷。

## Spec Change Log

## Verification

**Commands:**
- `uv run ruff check . && uv run ruff format --check . && uv run mypy .` -- 287 个 Python 文件格式与规则通过，156 个源文件类型检查通过。
- `uv run pytest -q` -- 默认安全套件 639 passed、8 skipped；隔离 PostgreSQL 两租户回归连续两次各 2 passed。
- `pnpm typecheck && pnpm lint && pnpm test:coverage && pnpm build` -- 79 项前端单测通过，覆盖率 11.37%，生产构建通过。
- `pnpm exec playwright test ... --project chromium` -- mock E2E 20 passed；真实后端只读 E2E 前序 1 passed。
- `uv run pytest ...integration/replay...` -- 迁移/RLS 15、API readiness 1、API 租户 2、GW 集成 10、PCAP 回放 15 均通过。
- `uv run pytest ...test_p95_flush.py --benchmark-only` -- 50×500 行，均值 256.6ms、最大 398.4ms，满足小于 500ms 门禁。
- `docker compose ... config` 与容器内 `sh -n` -- 两套 Compose、离线镜像/WAL 契约和脚本语法通过。

## Deferred Work

- 时序历史表不保存租户归属快照；设备若发生跨租户转让，历史可见性需要产品规则和 schema 迁移共同定义。
- PWA 品牌安装图标仍缺少正式设计资产；当前 manifest 不再引用不存在的文件，避免安装时 404。
- 前端总体覆盖率 11.37%，尚未建立覆盖率阈值；ECharts 生产 chunk 约 1.04 MB，仍需专项优化。
- 通知适配器尚未接入告警运行链路，微信支付下单仍为 MVP stub；真实串口、第三方凭据、客户机部署、规模性能、遗留双跑和抓包对账需外部条件验收。

## Suggested Review Order

**租户与历史数据隔离**

- 从日报入口理解 RLS 设备归属联接与完整日期边界。
  [`reports.py:24`](../../../ruisheng-api/src/ruisheng_api/api/reports.py#L24)

- 波形查询与分析复用相同租户约束。
  [`waveforms.py:54`](../../../ruisheng-api/src/ruisheng_api/api/waveforms.py#L54)

- 真实 PostgreSQL 用例覆盖跨租户、日末和重复执行。
  [`test_api_timeseries_tenant_scope.py:107`](../../../tests/integration/test_api_timeseries_tenant_scope.py#L107)

**前端会话与离线安全**

- 启动前退役旧 Worker，再挂载并注册安全版本。
  [`main.ts:12`](../../../ruisheng-web/src/main.ts#L12)

- 旧 API 缓存和 Worker 迁移具有单次重载保护。
  [`cacheCleanup.ts:13`](../../../ruisheng-web/src/pwa/cacheCleanup.ts#L13)

- 会话恢复校验用户形状及 JWT 声明一致性。
  [`auth.ts:68`](../../../ruisheng-web/src/stores/auth.ts#L68)

- WebSocket 同步失败、关闭和卸载均释放资源。
  [`useWsConnection.ts:27`](../../../ruisheng-web/src/composables/useWsConnection.ts#L27)

**测试与生产保护**

- 破坏性集成测试默认指向显式测试库。
  [`conftest.py:40`](../../../conftest.py#L40)

- 生产启动拒绝占位、弱口令和 URL 非安全密钥。
  [`entrypoint-migrate.sh:4`](../../../scripts/entrypoint-migrate.sh#L4)

- 角色密码不再嵌入 dollar-quoted PL/pgSQL 块。
  [`20260416_e74ffa548c2f_db_roles_ruisheng_gw_ruisheng_api_grants.py:60`](../../../alembic/versions/20260416_e74ffa548c2f_db_roles_ruisheng_gw_ruisheng_api_grants.py#L60)

**离线部署闭环**

- 根 Compose 支持构建具名镜像并复用 API 迁移镜像。
  [`docker-compose.prod.yml:33`](../../../docker-compose.prod.yml#L33)

- 离线 Compose 禁止构建和拉取，WAL 使用持久卷。
  [`docker-compose.prod.yml:35`](../../../deploy/docker-compose.prod.yml#L35)

- 导出脚本按解析后的镜像集合保存并拒绝归档名碰撞。
  [`export-images.sh:26`](../../../deploy/export-images.sh#L26)

**回归契约**

- 数据库保护测试锁定安全默认值与拒绝条件。
  [`test_database_guard.py:15`](../../../tests/tools/test_database_guard.py#L15)

- Compose 契约覆盖镜像、WAL、密钥和迁移实现。
  [`test_production_compose.py:20`](../../../tests/tools/test_production_compose.py#L20)
