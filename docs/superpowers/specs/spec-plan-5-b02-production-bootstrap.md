---
title: 'Plan 5 B-02 生产迁移与 Demo Seed 分离'
type: 'bugfix'
created: '2026-08-19'
status: 'done'
baseline_commit: '32cef116126c003f6ac9e7128fdcc47fe36ffffb'
context:
  - 'docs/superpowers/specs/spec-plan-5-customer-deployment-acceptance/SPEC.md'
  - 'docs/superpowers/specs/spec-plan-5-customer-deployment-acceptance/deployment-contract.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** 生产迁移在 Alembic 后无条件创建公开固定凭据及 Demo 业务数据，相关 seed 还进入生产镜像和文档，空白部署首次监听前即不安全。

**Approach:** 生产 bootstrap 只校验密钥并迁移 schema；镜像和生产文档移除 Demo seed/凭据，仓库保留显式开发 seed。用临时 TimescaleDB 执行真实入口，验证空库、幂等和数据保留。

## Boundaries & Constraints

**Always:** 两套生产 Compose 共用无 seed 入口；API 镜像不含 `seeds/` 或 `tools/run_seeds.py` 且可运行 API/Alembic；空白迁移可重复且四类业务表为空；开发 seed 仅显式、幂等；生产文档不承诺 Demo、默认账号或尚不存在的管理员引导。

**Ask First:** 如需设计管理员引导/凭据交接、清理已有数据库中的 Demo 数据、改变 seed 内容或幂等语义、增加任何生产 seed 开关，立即停止并请求批准。

**Never:** 不清理既有卷、不把凭据迁入 Alembic、不增加生产 seed 开关；不宣布 G0-05、CAP-2 或 Plan 5 完成，不处理其他阻断项/Profile。

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| 空白/重复迁移 | 有效密钥，入口运行两次 | head；四表始终为空 | 失败则阻止 API/GW |
| 既有升级 | 已有业务数据 | 仅迁移，身份/数量不变 | 不自动清理 |
| 开发演示 | 显式 seed 两次 | 精确 Demo 集合、计数稳定 | 生产镜像无此命令 |
| 无效密钥 | 缺失/占位/格式错误 | Alembic 前非零退出 | 不输出密钥值 |

</frozen-after-approval>

## Code Map

- `scripts/entrypoint-migrate.sh` / `ruisheng-api/Dockerfile` -- 生产入口和镜像边界。
- `pyproject.toml` / `tools/pcap_gen/` -- `uv` workspace 构建约束。
- `tools/run_seeds.py` / `seeds/*.sql` -- 开发 seed。
- `tests/tools/test_production_compose.py` / `tests/integration/test_production_bootstrap.py` -- 静态与临时库验证。

## Tasks & Acceptance

**Execution:**
- [x] `scripts/entrypoint-migrate.sh` -- 删除 seed 调用与日志，只保留密钥校验、Alembic 和成功退出。
- [x] `ruisheng-api/Dockerfile` -- 不复制 seed 资产；只保留 `uv sync --package ruisheng-api`、API/Alembic 必需的 workspace 与运行文件，以镜像构建判定依赖。
- [x] `tools/run_seeds.py` / `README.md` / `deploy/setup-customer.md` -- 标明显式开发 seed；删除生产 Demo/默认凭据，注明管理员引导/交接仍阻断交付。
- [x] `tests/tools/test_production_compose.py` -- 锁定无 seed 入口、开发 seed 路径、两 Compose 入口及成功依赖；从 seed 提取文档禁用值，不重复硬编码凭据。
- [x] `tests/integration/test_production_bootstrap.py` -- 在自动销毁的独占 TimescaleDB 容器中运行真实入口两次，验 head/空表；seed 两次核对 `1/2/1/2`，再次迁移验记录不变；不得操作外部库。

**Acceptance Criteria:**
- Given 空白临时 TimescaleDB，when 以有效密钥执行真实生产入口两次，then `alembic_version` 为 head 且 `wx_groups/users/devices/device_points` 均为零。
- Given 生产 Dockerfile，when 构建并检查镜像，then API 可导入、入口和迁移文件存在，`seeds/` 与 `tools/run_seeds.py` 不存在。
- Given 根和离线生产 Compose，when 渲染服务模型，then 两者仍指向同一无 seed 迁移入口，API/GW 只在迁移成功后启动。
- Given 已迁移的临时库，when 显式运行开发 seed 两次并再次运行生产入口，then 精确 Demo 身份和 `1/2/1/2` 计数稳定，入口不清理既有记录。
- Given 生产文档，when 阅读首次启动说明，then 无自动 Demo、默认凭据或启动后改密，且声明管理员引导未交付、B-02 不解除 G0-05/CAP-2。

## Spec Change Log

- 2026-08-19：对抗审查补齐真实入口、隔离销毁、workspace/镜像、精确 seed、数据保留、Compose 依赖和管理员阻断验收。

## Design Notes

集成测试仅使用 Testcontainers 独占库，不执行 `DROP DATABASE`。B-02 只移除不安全默认数据，不提供首位管理员。

## Verification

**Commands:**
- `uv run pytest tests/tools/test_production_compose.py -q` -- expected: 静态契约通过。
- `uv run pytest tests/integration/test_production_bootstrap.py -m integration -q` -- expected: 迁移/seed/保留验收通过。
- `uv run ruff check tests/tools/test_production_compose.py tests/integration/test_production_bootstrap.py tools/run_seeds.py` -- expected: lint 通过。
- `docker build -f ruisheng-api/Dockerfile -t ruisheng-b02-api:test .` -- expected: 构建成功。
- `docker run --rm --entrypoint sh ruisheng-b02-api:test -c "test -e /app/scripts/entrypoint-migrate.sh && test -d /app/alembic && test ! -e /app/seeds && test ! -e /app/tools/run_seeds.py && python -c 'import ruisheng_api'"` -- expected: 必要运行资产存在且 Demo seed 资产不存在。
- `docker compose --env-file .env.prod.example -f docker-compose.prod.yml config --format json` -- expected: 成功。
- `docker compose --env-file deploy/.env.prod.example -f deploy/docker-compose.prod.yml config --format json` -- expected: 成功。

## Suggested Review Order

**生产 Bootstrap 边界**

- 生产入口只校验密钥并执行 Alembic，不再加载 Demo。
  [`entrypoint-migrate.sh:37`](../../../scripts/entrypoint-migrate.sh#L37)

- 镜像保留 workspace 与迁移资产，同时排除开发 seed。
  [`Dockerfile:13`](../../../ruisheng-api/Dockerfile#L13)

**部署与开发分流**

- 首次部署仅启动依赖和迁移，明确管理员交付阻断。
  [`README.md:23`](../../../README.md#L23)

- 客户手册禁止在管理员交付前开放完整服务。
  [`setup-customer.md:80`](../../../deploy/setup-customer.md#L80)

- Seed runner 明确限定为显式开发与测试工具。
  [`run_seeds.py:1`](../../../tools/run_seeds.py#L1)

**自动化保护**

- 静态契约锁定入口、镜像、Compose、文档和开发路径。
  [`test_production_compose.py:328`](../../../tests/tools/test_production_compose.py#L328)

- 构建镜像并验证必要资产存在、Demo 资产缺失。
  [`test_production_bootstrap.py:149`](../../../tests/integration/test_production_bootstrap.py#L149)

- 真实入口覆盖无效密钥、空库迁移、幂等和数据保留。
  [`test_production_bootstrap.py:189`](../../../tests/integration/test_production_bootstrap.py#L189)

- Seed runner 变化会触发真实后端 Web CI。
  [`ci-web.yml:12`](../../../.github/workflows/ci-web.yml#L12)
