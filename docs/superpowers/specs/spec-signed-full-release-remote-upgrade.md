---
title: '签名全量远程升级'
type: 'feature'
created: '2026-09-01'
status: 'done'
baseline_commit: '67fcb6d9bf346457b81e49d0cf304b2bf876ca32'
context:
  - 'docs/superpowers/specs/spec-remote-maintenance-upgrades-subscriptions/SPEC.md'
  - 'docs/superpowers/specs/spec-plan-5-customer-deployment-acceptance/deployment-contract.md'
  - 'docs/REMOTE_DEBUG.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** 全量升级仍靠人工选目录、改环境和执行 Compose；维护脚本固定旧候选路径，API 匿名健康探针在认证启用后返回 403，无法可靠支持远程发布、恢复和审计。

**Approach:** 复用现有 OpenSSH Ed25519 候选签名和目标机包外 verifier，增加本机推送入口与目标端闭集状态机；首版只升级 Alembic head 不变的候选，以活动版本指针、双锁、备份、原子切换、内部健康 CLI 和回滚闭环。

## Boundaries & Constraints

**Always:** 使用 Tailscale key-only SSH、严格 host key、操作 ID、原因和当次批准。目标机先用受保护 `verify-publisher.ps1` 验证 v2/v3 签名包、完整文件集、平台和加载后镜像；其 `2/BLOCKED` 只证明真实性，仍须独立网络边界 PASS。按 shared-maintenance、legacy-hotfix 顺序获取并续租锁。活动指针精确绑定候选/逻辑身份/提交/候选根/站点根，禁止扫描猜测最新目录。仅原子替换 `TARGET_PLATFORM` 和五个 `*_IMAGE`，其他站点配置逐字保留；切换前保存带 SHA-256 的数据库逻辑备份和配置快照。候选 head 必须等于目标数据库 head；五服务、API/GW/Web 内部探针和网络边界通过后才提交指针。失败时仅在持锁且身份未漂移时恢复旧环境和服务；锁丢失或中断进入可重放恢复，不无锁回滚。双端审计不得含秘密。

**Ask First:** 上传或应用真实候选；允许 schema 变化/数据库恢复；变更发布或回执密钥、信任锚、站点网络/秘密；删除旧候选、备份或审计；放宽回退策略。

**Never:** 不实现定时检测、自动拉取、订阅或扣款；不接受任意远程命令；不在 argv、日志、临时文件或 SSH 环境暴露令牌；不使用 Compose `down`、删除卷、可变标签或候选内信任锚；不把镜像回滚称为数据库恢复。

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| 只读计划 | 签名候选、活动版本 | 返回身份、资源、schema、锁和步骤 | 不上传、不加载、不写目标状态 |
| 正常升级 | 同 head、预检通过 | 验签、备份、切换、健康验收并提交指针 | 输出 committed 记录 |
| 候选拒绝 | 篡改、错平台/key/head/版本 | 服务停止前拒绝 | 清理本次 incoming，旧服务不变 |
| 切换失败 | 启动、探针或边界失败 | 恢复旧六字段和旧服务 | 回滚验收失败记 recovery_failed |
| 重放/中断 | 相同操作 ID | 返回终态或按 journal 恢复 | 身份冲突拒绝；锁丢失记 uncertain |

</frozen-after-approval>

## Code Map

- `tools/remote_full_upgrade.ps1` -- 本机入口、传输、批准边界和审计。
- `tools/remote_full_upgrade/target-updater.ps1` -- 目标状态机、锁、备份、切换与恢复。
- `tools/remote_{maintenance,hotfix_deploy}.ps1` -- 活动版本解析和共享锁。
- `ruisheng-api/src/ruisheng_api/healthcheck.py` / `tools/remote_debug.ps1` -- 无明文令牌的内部就绪检查。
- `tests/tools/test_remote_full_upgrade.py` -- 状态机与故障注入。

## Tasks & Acceptance

**Execution:**
- [x] `ruisheng-api/src/ruisheng_api/healthcheck.py`、测试 -- 增加读取容器环境的 DB/Redis 健康 CLI；维护、热修、调试统一调用。
- [x] `tools/remote_full_upgrade.ps1` -- 实现本地候选/参数校验、dry-run、隔离传输和 SSH stdin 调度。
- [x] `tools/remote_full_upgrade/target-updater.ps1` -- 实现验签、边界/schema/资源门禁、双锁、备份、六字段切换、健康、指针、审计和恢复。
- [x] `tools/remote_maintenance.ps1`、`tools/remote_hotfix_deploy.ps1` -- 取消候选硬编码，从受保护指针解析并拒绝漂移。
- [x] `tests/tools/test_remote_full_upgrade.py`、`tests/tools/test_remote_operations.py` -- 覆盖矩阵、阶段故障、令牌泄漏、幂等、锁及 PowerShell 5.1/7。
- [x] `docs/REMOTE_DEBUG.md`、`deploy/setup-customer.md` -- 用受控入口替换正式人工升级并记录恢复方法。

**Acceptance Criteria:**
- Given 当前版本和同 head 候选，when dry-run，then 容器、配置、锁、审计和目标文件快照不变。
- Given 已批准升级，when 门禁通过，then 五服务切换且健康，备份有摘要，活动指针和双端审计关联同一操作 ID。
- Given 任一验签、schema、备份或切换故障，when 状态机处理，then 及早拒绝或恢复旧版本，不删卷、不泄密、不误报成功。
- Given 目标机当前 `deploy-20260831.1`，when 读取活动状态，then 不访问旧 `.21.1`，API 探针不再因缺 Bearer 返回 403。

## Spec Change Log

## Design Notes

目标 updater 是按需闭集状态机，不是常驻 agent。首版阻断 schema 变化，因为现有备份与镜像回滚尚不能证明数据库完整恢复。

## Verification

**Commands:**
- `uv run pytest -q tests/tools/test_remote_full_upgrade.py tests/tools/test_remote_operations.py ruisheng-api/tests/unit ruisheng-gw/tests/unit` -- 相关回归通过，包含 API/GW 运行态健康检查与 CLI/UDS 验证。
- `uv run ruff check ruisheng-api/src ruisheng-api/tests ruisheng-gw/src ruisheng-gw/tests tests/tools/test_remote_full_upgrade.py` -- 新增健康检查源代码和测试静态检查通过。
- `uv run ruff check tools tests/tools ruisheng-api/src ruisheng-api/tests` -- 静态检查通过。
- PowerShell 5.1/7 解析脚本；隔离 Docker 执行成功升级、篡改/head 拒绝和健康失败回滚 -- 全部满足矩阵。

## Suggested Review Order

**受控入口与批准边界**

- 从严格批准、目标和路径校验理解全量升级的闭集入口。
  [`remote_full_upgrade.ps1:327`](../../../tools/remote_full_upgrade.ps1#L327)

- 计划、初始化、上传和应用均经同一受控调度器执行。
  [`remote_full_upgrade.ps1:373`](../../../tools/remote_full_upgrade.ps1#L373)

**目标事务与恢复闭环**

- 双租约锁固定顺序获取，并在锁内重新确认活动版本。
  [`target-updater.ps1:1205`](../../../tools/remote_full_upgrade/target-updater.ps1#L1205)

- 备份回执先于六字段原子切换，避免无恢复依据的变更。
  [`target-updater.ps1:1438`](../../../tools/remote_full_upgrade/target-updater.ps1#L1438)

- 指针、审计与 journal 形成可重放的提交边界。
  [`target-updater.ps1:1470`](../../../tools/remote_full_upgrade/target-updater.ps1#L1470)

- 失败仅在锁和旧身份仍有效时恢复原环境。
  [`target-updater.ps1:952`](../../../tools/remote_full_upgrade/target-updater.ps1#L952)

**无密钥健康验收**

- API CLI 同时验证数据库、Redis 与实际运行服务。
  [`healthcheck.py:35`](../../../ruisheng-api/src/ruisheng_api/healthcheck.py#L35)

- GW CLI 经受限 Unix socket读取运行态完整健康状态。
  [`healthcheck.py:26`](../../../ruisheng-gw/src/ruisheng_gw/healthcheck.py#L26)

- GW 生命周期负责创建、限权并清理内部健康 socket。
  [`main.py:118`](../../../ruisheng-gw/src/ruisheng_gw/main.py#L118)

**既有运维兼容**

- 维护流程从受保护指针解析活动版本并拒绝漂移。
  [`remote_maintenance.ps1:863`](../../../tools/remote_maintenance.ps1#L863)

- 热修部署复用活动指针、共享锁和语义健康探针。
  [`remote_hotfix_deploy.ps1:684`](../../../tools/remote_hotfix_deploy.ps1#L684)

**回归证据与操作文档**

- 升级契约测试覆盖供应链、切换、恢复、重放和边界门禁。
  [`test_remote_full_upgrade.py:41`](../../../tests/tools/test_remote_full_upgrade.py#L41)

- 故障注入验证环境切换失败后的明确终态。
  [`test_remote_full_upgrade.py:488`](../../../tests/tools/test_remote_full_upgrade.py#L488)

- 运维手册以正式闭集命令替代人工改目录和 Compose。
  [`REMOTE_DEBUG.md:98`](../../REMOTE_DEBUG.md#L98)
