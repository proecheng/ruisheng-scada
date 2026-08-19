---
title: 'Plan 5 B-01 离线生产配置等价'
type: 'bugfix'
created: '2026-08-19'
status: 'done'
baseline_commit: '511117502f269d361ff9b2967f77d57a3b4c825a'
context:
  - 'docs/superpowers/specs/spec-plan-5-customer-deployment-acceptance/SPEC.md'
  - 'docs/superpowers/specs/spec-plan-5-customer-deployment-acceptance/deployment-contract.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** 根生产 Compose 和环境模板已包含告警通知运行参数与 GW 告警刷新参数，但离线副本缺失；离线安装因此不能保持与构建配置相同的运行行为，现有字符串测试也无法阻止后续非预期漂移。

**Approach:** 补齐离线 Compose 与环境模板，并用结构化、归一化的契约测试锁定两套 Compose 和模板的等价性，只放行构建版 `build` 与离线版 `pull_policy` 这两个部署模式差异。

## Boundaries & Constraints

**Always:** 两套 Compose 在移除每个服务的 `build` 和 `pull_policy` 后必须结构完全相等；两份环境模板的键和值必须完全相等；Compose 引用的每个环境变量必须在对应模板中定义；通知 provider 默认关闭，凭据保持空占位；保留离线应用服务无 `build` 且 `pull_policy: never` 的现有约束。

**Ask First:** 实现中如发现除 `build`/`pull_policy` 外的差异、需要扩大差异 allowlist、需要改变现有默认值，或需要新增运行时变量，立即停止并请求批准。

**Never:** 不启用真实通知 provider，不写入真实凭据或联系人；不填写或伪造 `site-acceptance-profile.md` 的 `UNRESOLVED` 外部输入；不处理 B-02 至 B-07，不改变镜像固定、seed、网络、串口、WAL 告警恢复或备份策略。

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| 配置等价 | 两套 Compose 仅存在根 `build`、离线 `pull_policy` 差异 | 归一化结构相等，契约测试通过 | N/A |
| 非允许漂移 | 任一服务的环境、镜像、端口、卷、依赖、入口或其他字段不一致 | 契约测试失败并显示结构差异 | 不得静默加入 allowlist |
| 模板漂移 | 两份 env 模板缺键、多键或同键值不同 | 契约测试失败 | 修正模板，不接受例外 |
| 未声明引用 | Compose 出现模板未定义的 `${VAR}` 或 `${VAR:-default}` | 契约测试失败并指出变量 | 在对应模板补充安全默认值或占位符 |

</frozen-after-approval>

## Code Map

- `docker-compose.prod.yml` -- 构建型生产配置和功能契约基准。
- `deploy/docker-compose.prod.yml` -- 离线安装配置，当前缺少通知与 GW 告警参数。
- `.env.prod.example` -- 根生产环境变量基准，仅含占位符和安全默认值。
- `deploy/.env.prod.example` -- 离线环境模板，当前缺少告警通知段。
- `tests/tools/test_production_compose.py` -- 生产 Compose 渲染、环境模板与离线部署回归测试入口。

## Tasks & Acceptance

**Execution:**
- [x] `deploy/docker-compose.prod.yml` -- 同步 API 的 provider/worker 参数和 GW 的刷新/freshness 参数，使离线运行配置与根配置等价。
- [x] `deploy/.env.prod.example` -- 同步根模板的告警通知段，保持键、值、注释和安全默认值一致。
- [x] `tests/tools/test_production_compose.py` -- 通过 Docker Compose CLI 渲染并归一化比较两套配置，逐服务锁定部署字段，解析 env 键值并以 `config --variables` 校验精确变量闭包，同时保留已有离线、镜像、WAL、迁移和导出脚本断言。

**Acceptance Criteria:**
- Given 根与离线生产 Compose 及各自 env 模板，when 使用 `docker compose config --format json` 渲染并删除根服务的 `build`、离线服务的 `pull_policy`，then JSON 数据结构完全相等且不存在其他 allowlist 项。
- Given 两份生产 env 模板，when 按 dotenv 的非注释 `KEY=VALUE` 行解析，then 键符合环境变量标识符语法、无重复项，且两份模板的键集合和原始值逐项一致。
- Given 任一生产 Compose，when 使用 `docker compose config --variables` 读取模型变量，then 变量键集合与对应环境模板的键集合完全相等。
- Given 两套生产 Compose，when 结构化检查应用服务，then 根 `migrate`、`api`、`gw`、`web` 各自有 `build` 且无 `pull_policy`，离线同名服务各自无 `build` 且为 `pull_policy: never`。
- Given 环境模板默认值，when 检查 provider 配置，then 微信、邮件、短信、语音均为关闭状态且凭据为空。

## Spec Change Log

- 2026-08-19 / iteration 1 -- 审查发现测试只按总数检查离线 `pull_policy`，且原始 YAML 比较未满足上层契约的渲染要求。规格改为逐应用服务断言部署字段、通过 Docker Compose CLI 渲染 JSON，并以 `config --variables` 校验模板与引用的双向精确闭包，避免策略错配和 Compose schema/插值错误被结构比较漏过。KEEP：完整同步 19 个 API 通知变量与 2 个 GW 参数；两份 env 模板原始值一致且拒绝重复键；provider 安全默认关闭；差异 allowlist 仅限根 `build` 与离线 `pull_policy`；保留既有生产部署回归断言。

## Design Notes

契约测试通过子进程调用仓库要求的 Docker Compose CLI，并把 `config --format json` 输出交给标准库 `json` 解析；Docker/Compose 缺失或渲染失败必须给出明确失败信息，不得跳过。归一化只从根渲染结果移除 `build`、从离线渲染结果移除 `pull_policy`，逐服务断言则在归一化前锁定字段方向和值。变量集合来自 Compose 自身的 `config --variables`，避免手写正则漏掉合法插值形式、误读注释或转义。env 解析器忽略空行和注释，验证键为 `[A-Za-z_][A-Za-z0-9_]*`，在第一个 `=` 处分割并保留右侧原始值，以覆盖空凭据及包含 `=` 的合法值。

## Verification

**Commands:**
- `uv run pytest tests/tools/test_production_compose.py -q` -- expected: 所有生产配置契约测试通过。
- `uv run ruff check tests/tools/test_production_compose.py` -- expected: 无 lint 错误。
- `docker compose --env-file .env.prod.example -f docker-compose.prod.yml config --format json` -- expected: 根生产配置成功渲染。
- `docker compose --env-file deploy/.env.prod.example -f deploy/docker-compose.prod.yml config --format json` -- expected: 离线生产配置成功渲染且无未解析变量。

## Suggested Review Order

**配置契约**

- 从渲染与未插值双重比较理解差异 allowlist。
  [`test_production_compose.py:135`](../../../tests/tools/test_production_compose.py#L135)

- 逐服务锁定构建版与离线版的部署字段方向。
  [`test_production_compose.py:146`](../../../tests/tools/test_production_compose.py#L146)

- 精确匹配模板变量，并锁定必需变量的所属服务。
  [`test_production_compose.py:186`](../../../tests/tools/test_production_compose.py#L186)

- 验证模板与渲染后的 provider 安全默认值。
  [`test_production_compose.py:203`](../../../tests/tools/test_production_compose.py#L203)

- 清理调用进程的同名变量，防止污染渲染结果。
  [`test_production_compose.py:229`](../../../tests/tools/test_production_compose.py#L229)

**离线配置**

- 同步 API 通知 provider 与 worker 运行参数。
  [`docker-compose.prod.yml:65`](../../../deploy/docker-compose.prod.yml#L65)

- 同步 GW 告警刷新与关联值 freshness 参数。
  [`docker-compose.prod.yml:101`](../../../deploy/docker-compose.prod.yml#L101)

- 提供默认关闭、空凭据的离线环境模板。
  [`.env.prod.example:25`](../../../deploy/.env.prod.example#L25)
