---
title: 'PR #10 Linux CI Compatibility Repair'
type: 'bugfix'
created: '2026-08-30'
status: 'done'
baseline_commit: '717928871f6b133b1e70e9d63146067b78f78386'
context:
  - 'docs/superpowers/specs/spec-plan-5-b08t-trust-root-freshness.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** PR #10 在 Windows 本地门禁全通过，但 Linux CI 因 Windows 专属类型、导入期强制访问现场 MDF、以及匿名调用已认证的 readiness 端点而有五项失败。修复不能放宽认证或源身份校验。

**Approach:** 将 Windows 原生调用收敛到仅在 Windows 执行的适配边界；允许 MDF 模块在固定源缺失时被测试收集，但实际提取仍因缺少加载期身份快照而 fail-closed；测试与 CI 探针按既有 Bearer 契约认证。

## Boundaries & Constraints

**Always:** Linux 与 Windows Mypy 均通过；Windows 原生安全语义保持不变；MDF 只接受固定路径及加载时锁定的父目录和文件身份；管理端点继续要求有效 Bearer token；CI 只用明确的非生产测试值。

**Ask First:** 修改生产 token、生产部署环境、端点可见范围，或改变 B-08-T trust-root/freshness 判定。

**Never:** 移除或绕过管理端点认证；用宽泛的全文件 Mypy ignore 掩盖平台错误；让缺少现场 MDF 的实际提取继续运行；连接目标机、发送 Modbus、修改生产数据或部署本轮修复。

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|---------------|----------------------------|----------------|
| Linux static analysis | Mypy 以 Linux 平台检查工具 | Windows 符号不产生平台误报，POSIX 路径仍受检查 | 真实类型错误继续失败 |
| Import without MDF | `DataBase/DataBase` 不存在 | 测试可导入和收集 | 实际读取前返回明确 `MdfEvidenceError` |
| Authenticated readiness | 正确测试 token 与匹配摘要 | API `/api/health/ready`、GW `/ready` 返回原有业务状态 | 缺失、错误或无摘要仍返回 403 |
| CI probe startup | API 暂不可用 | 认证请求重试，成功后进入 E2E | 超时输出日志且不泄漏 token |

</frozen-after-approval>

## Code Map

- `tools/release_artifacts.py`、`tools/validate_device_point_profile.py`、`tools/release_verification_receipt.py` -- Windows 原生调用的 Linux Mypy 错误来源。
- `tools/extract_legacy_mdf_points.py`、`tests/tools/test_extract_legacy_mdf_points.py` -- Windows 文件句柄、固定 MDF 加载期身份和 fail-closed 回归。
- `docs/superpowers/specs/evidence/b08-20260827/legacy-point-candidates.json` -- 绑定 extractor 与 GW runtime 字节身份的 canonical 账本。
- `ruisheng-api/tests/integration/test_health_ready.py` -- 真实 PostgreSQL/Redis readiness 集成测试。
- `ruisheng-gw/src/ruisheng_gw/main.py`、`ruisheng-gw/tests/replay/` -- 实时 GW 测试服务及 replay 探针。
- `.github/workflows/ci-web.yml` -- 真实后端 E2E 的 API 启动和 readiness 等待。

## Tasks & Acceptance

**Execution:**
- [x] 四个 `tools/*.py` -- 引入窄范围平台适配并移除无效 ignore，不改变 Windows 调用顺序和错误传播。
- [x] `tools/extract_legacy_mdf_points.py` 与对应测试 -- 把固定源身份捕获改为可缺失状态，运行时在打开源之前显式拒绝未捕获身份。
- [x] API/GW 集成与 replay 测试 -- 使用固定测试 token/摘要调用受保护 readiness，并保留匿名拒绝契约。
- [x] `.github/workflows/ci-web.yml` -- 为测试 API 配置匹配摘要，等待探针携带 Bearer token。
- [x] Python 工具测试 -- Linux 跳过 Windows Desktop PowerShell 解析，定向注入并发改写，并避免通过全局 `os.name` 污染 Pytest 路径类型。
- [x] Web 诊断页与真实后端 E2E -- 仅将普通登录 JWT 的 readiness `403` 建模为“健康状态受保护”，其他故障显示不可用；不向浏览器暴露管理 token。

**Acceptance Criteria:**
- Given 当前 PR，when 运行 Linux 和 Windows Mypy，then 168 个源文件均无错误且没有新增宽泛忽略。
- Given 固定 MDF 路径不存在，when pytest 收集工具测试，then 导入成功；when 尝试读取，then 在源内容读取前 fail-closed。
- Given API/GW 管理端点受保护，when 集成、replay 与 Web CI 执行，then 认证探针通过，匿名或错误 token 仍为 403。
- Given 普通用户已登录 Web，when 诊断页请求管理 readiness，then 精确接受预期 `403` 并显示受保护状态；其他 API 4xx/5xx 继续使全功能 E2E 失败。
- Given 全量回归完成，when 查看改动，then B-08-T 安全边界、生产配置和现场状态均未改变。

## Spec Change Log

- 2026-08-30: 首次 Linux CI 运行暴露剩余平台测试隔离和 Web 诊断页授权预期问题；补充 Windows-only 跳过、定向并发改写、局部平台模拟及普通 JWT readiness `403` 回归，不放宽管理认证。
- 2026-08-30: 对抗审查发现 readiness catch 过宽及测试边界不足；保留管理认证、普通 JWT 精确 `403`、其他 API 错误继续失败和平台局部模拟，改为区分 `403`/其他故障、限制 E2E 请求身份、锁定测试源身份并为 PowerShell 解析增加超时。

## Design Notes

平台适配不模拟 Windows 行为。MDF 缺失是加载期事实；进程启动后即使文件出现也必须拒绝，避免绕过身份绑定。

## Verification

**Commands:**
- `uv run mypy --platform linux .` 与 `uv run mypy .` -- 168 个源文件均通过。
- `uv run pytest ruisheng-shared/tests tests/tools -q --cov=ruisheng-shared/src --cov-fail-under=90` -- 1203 passed，17 skipped，覆盖率 100%。
- `uv run pytest ruisheng-api/tests/integration -x`、`uv run pytest ruisheng-gw/tests/replay -v` -- 1 个 API 集成与 15 个 replay 均通过。
- API/GW 完整单元门禁 -- 222 与 174 个测试通过，覆盖率分别为 63.92% 与 86.44%。
- `uv run ruff check .`、`uv run ruff format --check .`、双平台 Mypy 及改动文件 pre-commit -- 全部通过；全文件 pre-commit 另发现并恢复了无关旧计划文件末尾空行清理。
- 本机实际 API 探针 -- 匿名返回 403，携带匹配 Bearer token 返回 200；临时服务和 Compose 测试容器已停止。
- PR #10 首轮 GitHub CI -- `lint`、`api-integration`、`gw-replay`、API/GW 单元与 benchmark、Web unit/build/mock E2E 均通过；仅 `unit` 和 `web-real-backend-e2e` 暴露后续问题。
- 后续 Python 定向回归 -- 3 passed；改动的两个 Python 测试文件 pre-commit 通过。
- Web 后续回归 -- typecheck、ESLint、19 个 Vitest 文件共 83 个测试通过；本机真实后端 Playwright 1 passed。
- 本机认证契约复核 -- 管理 token 请求 readiness 返回 200，普通登录 JWT 返回 403；临时 API、Compose 容器和 `8000` 监听均已清理。
- 对抗审查修复后回归 -- 三路审查结论已处理；Python 3 passed、Web typecheck/ESLint/83 tests 和真实后端 Playwright 1 passed；API 正常退出且 Compose 无残留容器。

## Full PR Review Reference

**后续 CI 平台与授权回归**

- Windows Desktop PowerShell 解析测试只在具备对应运行时的 Windows 执行。
  [`test_publisher_authenticity.py:79`](../../../tests/tools/test_publisher_authenticity.py#L79)

- 并发改写只注入目标文件描述符，Windows CLI 通过模块局部平台对象模拟。
  [`test_release_artifacts.py:2078`](../../../tests/tools/test_release_artifacts.py#L2078)

- 普通用户无法读取管理 readiness 时显示稳定的受保护状态。
  [`DiagView.vue:11`](../../../ruisheng-web/src/views/DiagView.vue#L11)

- 精简真实后端巡检锁定 readiness `403` 与页面状态。
  [`real-backend.spec.ts:30`](../../../ruisheng-web/e2e/real-backend.spec.ts#L30)

- 全功能巡检仅豁免该端点的精确预期 `403`，其他运行时错误仍失败。
  [`real-backend-full.spec.ts:26`](../../../ruisheng-web/e2e/real-backend-full.spec.ts#L26)

**MDF fail-closed 边界**

- 将固定源缺失建模为加载期事实，并在任何路径访问前拒绝。
  [`extract_legacy_mdf_points.py:995`](../../../tools/extract_legacy_mdf_points.py#L995)

- 回归锁定缺失源可导入、实际读取仍提前失败。
  [`test_extract_legacy_mdf_points.py:487`](../../../tests/tools/test_extract_legacy_mdf_points.py#L487)

**跨平台原生适配**

- 收敛 Windows DLL、错误码和 POSIX UID 的平台访问。
  [`release_artifacts.py:222`](../../../tools/release_artifacts.py#L222)

- 保持 validator 的 Windows handle 与 ACL 调用顺序不变。
  [`validate_device_point_profile.py:3790`](../../../tools/validate_device_point_profile.py#L3790)

- 隔离 receipt 发布路径中的 Windows-only ctypes 符号。
  [`release_verification_receipt.py:1665`](../../../tools/release_verification_receipt.py#L1665)

- 统一 MDF 工具的 Windows last-error 与异常传播。
  [`extract_legacy_mdf_points.py:332`](../../../tools/extract_legacy_mdf_points.py#L332)

**认证 readiness 探针**

- API CI 使用匹配摘要、掩码 token 和有界重试。
  [`ci-web.yml:140`](../../../.github/workflows/ci-web.yml#L140)

- API 集成测试按既有 Bearer 契约请求受保护端点。
  [`test_health_ready.py:10`](../../../ruisheng-api/tests/integration/test_health_ready.py#L10)

- GW 测试 harness 强制显式提供健康端点摘要。
  [`main.py:429`](../../../ruisheng-gw/src/ruisheng_gw/main.py#L429)

- Replay fixture 持有匹配的测试 token 与摘要。
  [`conftest.py:18`](../../../ruisheng-gw/tests/replay/conftest.py#L18)

- 实时 replay 在发送业务帧前验证认证 readiness。
  [`test_replay_corpus.py:34`](../../../ruisheng-gw/tests/replay/test_replay_corpus.py#L34)

**内容寻址证据**

- Canonical JSON 绑定更新后的 extractor 与 GW runtime 字节。
  [`legacy-point-candidates.json:38`](evidence/b08-20260827/legacy-point-candidates.json#L38)

- 校准规格同步工具摘要并保持 B-08 阻断结论。
  [`spec-plan-5-b08-device-identity-point-calibration.md:294`](spec-plan-5-b08-device-identity-point-calibration.md#L294)

- 证据摘要同步 canonical JSON 与 validator 身份。
  [`spec-plan-5-b08-device-identity-point-evidence.md:9`](spec-plan-5-b08-device-identity-point-evidence.md#L9)

## Suggested Review Order

**Readiness 状态边界**

- 仅精确 `403` 显示受保护，其余失败明确显示不可用。
  [`DiagView.vue:26`](../../../ruisheng-web/src/views/DiagView.vue#L26)

**真实后端授权回归**

- 入口测试锁定同源、GET、无查询参数的 readiness 请求。
  [`real-backend.spec.ts:3`](../../../ruisheng-web/e2e/real-backend.spec.ts#L3)

- 全功能错误收集仅豁免同一精确请求的预期 `403`。
  [`real-backend-full.spec.ts:44`](../../../ruisheng-web/e2e/real-backend-full.spec.ts#L44)

**跨平台测试稳定性**

- Windows 专属解析门禁有平台边界和有界执行时间。
  [`test_publisher_authenticity.py:78`](../../../tests/tools/test_publisher_authenticity.py#L78)

- 并发改写用预捕获身份定向注入，消除路径复查竞态。
  [`test_release_artifacts.py:2040`](../../../tests/tools/test_release_artifacts.py#L2040)

- 局部 Windows 模拟保留 `os` 其余接口且不污染 Pytest。
  [`test_release_artifacts.py:2146`](../../../tests/tools/test_release_artifacts.py#L2146)
