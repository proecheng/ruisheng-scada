---
title: '修复远程 PowerShell 大型 stdin 脚本传输'
type: 'bugfix'
created: '2026-09-02'
status: 'done'
baseline_commit: '9bf25f24cabb8918f250d4f551af52bc5ac8a73d'
context:
  - 'docs/superpowers/specs/spec-signed-full-release-remote-upgrade.md'
  - 'docs/superpowers/specs/spec-remote-upgrade-empty-candidate-root.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** `remote_full_upgrade.ps1` 通过 `powershell.exe -Command -` 从 SSH stdin 执行完整 updater；Windows PowerShell 5.1 会按交互式逐行语义处理大型嵌套脚本，SSH 虽返回 0，却没有 JSON，导致所有远程升级动作在传输层失效。

**Approach:** 用 UTF-16LE `-EncodedCommand` 传递不含业务参数的固定短引导程序，由引导程序一次性读取 stdin、创建脚本块并执行；完整 updater 与参数仍只经 stdin 传输，避免 Windows 命令行长度限制。

## Boundaries & Constraints

**Always:** 保留 `BatchMode=yes`、严格 host key、非交互 SSH、操作 ID、结果字段白名单和现有批准边界；远端 PowerShell 使用文本输出并禁用 progress，完整 stdin 只发送一次；Windows PowerShell 5.1 与 PowerShell 7 必须执行同一传输契约；传输或输出异常继续 fail closed。

**Ask First:** 任何真实 `Initialize`、`Apply`、`Recover`、候选上传或目标机写入；更改 SSH 身份、信任锚、host key 策略、批准规则或目标升级状态机。

**Never:** 不使用 `Invoke-Expression`；不把完整 updater、候选路径、原因或秘密放入 argv/EncodedCommand；不在目标机落地临时脚本；不放宽主机密钥检查、key-only 登录或 JSON 校验；不借机重构升级状态机。

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|---------------|----------------------------|----------------|
| 完整大型 updater | PowerShell 5.1/7，嵌套脚本与参数经 stdin | stdin 单次读取并执行，只返回一个 JSON 文档，无提示符或 CLIXML | 非 JSON、额外输出或非零退出码由控制器拒绝 |
| 只读操作 | `Status`/`Plan` 的候选根为空 | 空字符串保持原义，响应动作、操作 ID 与状态契约不变 | 绑定或响应漂移时 fail closed |
| 变更操作 | `Initialize`/`Apply` 使用非空候选根 | 路径和元数据只存在于 stdin 负载，原审批校验不变 | 不绕过上传、签名、身份或审批门禁 |
| 引导执行失败 | stdin 截断、脚本语法错误或 PowerShell 启动失败 | 不产生可接受的成功响应 | 控制器报告传输失败或非法响应 |

</frozen-after-approval>

## Code Map

- `tools/remote_full_upgrade.ps1` -- 构造固定引导程序、编码远端命令并通过 SSH 单次发送 updater。
- `tools/remote_full_upgrade/target-updater.ps1` -- 被传输的完整闭集状态机；本修复不改变其业务行为。
- `tests/tools/test_remote_full_upgrade.py` -- 在 PowerShell 5.1/7 中执行真实引导链路并验证安全参数、stdin 和纯 JSON 输出。

## Tasks & Acceptance

**Execution:**
- [x] `tools/remote_full_upgrade.ps1` -- 将 `-Command -` 替换为固定 `-EncodedCommand` bootstrap，显式使用 `-OutputFormat Text`，并保持 updater 只经 stdin 传输。
- [x] `tests/tools/test_remote_full_upgrade.py` -- 更新静态安全契约；用两个 PowerShell 版本执行完整 updater 的本地 SSH 替身，核对单次 stdin、固定 bootstrap、无 CLIXML/提示符和完整响应。
- [x] `tests/tools/test_remote_full_upgrade.py` -- 覆盖截断/非法输出或执行失败路径，证明控制器不会把传输异常当成成功。

**Acceptance Criteria:**
- Given 完整 updater 远大于安全命令行长度，when 经 Windows PowerShell 5.1 或 PowerShell 7 的新链路执行，then 两者均消费一次 stdin 并产生唯一、可解析且符合白名单的 JSON。
- Given 远端输出包含 CLIXML、提示符、额外文本或无数据，when 控制器解析响应，then 操作被明确拒绝且不误报成功。
- Given SSH 调度参数被检查，when 执行任一操作，then key-only、严格 host key、非交互和文本输出设置均存在，EncodedCommand 只包含固定 bootstrap。

## Spec Change Log

## Design Notes

PowerShell 的 `-EncodedCommand` 按 UTF-16LE 编码。固定 bootstrap 设置停止型错误、静默 progress 和 UTF-8 输入输出，从标准输入 `ReadToEnd()`，再用 `[ScriptBlock]::Create()` 执行；因此业务负载不受 argv 长度和逐行解析语义影响。`ScriptBlock::Create()` 仍是动态代码执行边界，其安全性依赖控制器只把本地受信 updater 与 Base64 化参数写入 key-only SSH stdin；禁止把其他来源的文本送入该入口。

## Verification

**Commands:**
- `uv run pytest -q tests/tools/test_remote_full_upgrade.py` -- 50 项传输、状态机契约和 PowerShell 5.1/7 回归全部通过。
- `uv run pytest -q tests/tools/test_remote_operations.py` -- 63 项既有远程维护兼容回归全部通过。
- `uv run ruff check tests/tools/test_remote_full_upgrade.py` -- Python 测试静态检查通过。
- `powershell.exe` 与 `pwsh.exe` 解析两个升级脚本 -- 无语法错误。
- `uv run pre-commit run --files tools/remote_full_upgrade.ps1 tests/tools/test_remote_full_upgrade.py docs/superpowers/specs/spec-remote-powershell-stdin-bootstrap.md docs/superpowers/specs/deferred-work.md` -- 本次文件全部门禁通过。
- `SKIP=end-of-file-fixer uv run pre-commit run --all-files` -- 全仓其余门禁通过；旧计划文档的既有 EOF 格式差异不纳入本次提交。
- 合并后使用既有操作 ID执行目标机只读 `Status` -- 返回唯一合法 JSON，不写目标状态。

## Suggested Review Order

**固定传输边界**

- 固定短 bootstrap 明确 UTF-8 输入输出与单次 stdin 执行。
  [`remote_full_upgrade.ps1:22`](../../../tools/remote_full_upgrade.ps1#L22)

- SSH 调度保留严格参数并在调用后恢复本机编码。
  [`remote_full_upgrade.ps1:143`](../../../tools/remote_full_upgrade.ps1#L143)

**参数与准备回执**

- 参数哈希表消除多行 here-string 续行丢失风险。
  [`remote_full_upgrade.ps1:192`](../../../tools/remote_full_upgrade.ps1#L192)

- Apply 准备阶段只接受精确 `prepared` 回执。
  [`remote_full_upgrade.ps1:457`](../../../tools/remote_full_upgrade.ps1#L457)

**可执行证据**

- 双 PowerShell 原生子进程执行完整大型 updater。
  [`test_remote_full_upgrade.py:516`](../../../tests/tools/test_remote_full_upgrade.py#L516)

- Unicode 输出与本机编码恢复形成端到端证据。
  [`test_remote_full_upgrade.py:589`](../../../tests/tools/test_remote_full_upgrade.py#L589)

- 非空路径、元数据和审批参数均经真实 splatting 绑定。
  [`test_remote_full_upgrade.py:624`](../../../tests/tools/test_remote_full_upgrade.py#L624)

- 审查识别的架构风险留待独立加固。
  [`deferred-work.md:16`](deferred-work.md#L16)
