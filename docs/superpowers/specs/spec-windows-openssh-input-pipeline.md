---
title: '修复 Windows OpenSSH EncodedCommand stdin 管道'
type: 'bugfix'
created: '2026-09-02'
status: 'complete'
baseline_commit: '72bfdaf8aef4510c0a01945e8cfab70219094264'
context:
  - 'docs/superpowers/specs/spec-remote-powershell-stdin-bootstrap.md'
---

## Intent

PR #15 合并后的真实目标机只读 `Status` 证明：Windows sshd 在 PowerShell `-EncodedCommand` 模式下不向 `[Console]::In` 传播可用 EOF，固定 bootstrap 的 `ReadToEnd()` 会永久等待；同一连接的 `$input | Select-Object -First 1` 能立即读取 SSH stdin。

以固定短 bootstrap 从 `$input` 读取唯一一条规范 Base64 记录，限制最大长度、UTF-8 解码后执行。完整 updater 与所有业务参数仍只存在于 stdin，不进入 argv、不落地目标文件；保持严格 host key、非交互、JSON 白名单和审批边界。

## Tasks & Acceptance

- [x] `tools/remote_full_upgrade.ps1` -- 将脚本编码为单条 UTF-8 Base64 stdin 记录，并以固定 bootstrap 解码执行。
- [x] `tests/tools/test_remote_full_upgrade.py` -- 更新原生替身，覆盖双 PowerShell、规范 Base64、截断拒绝、Unicode、参数绑定与精确回执。
- [x] 合并前本地测试和审查全部通过；目标机只读 `Status` 在有界时间内返回唯一合法 JSON。

## Verification

- 真实目标机小探针：`$input` Base64 bootstrap 返回 `{"ok":true,"probe":"base64-stdin","text":"传输正常"}`。
- `uv run pytest -q tests/tools/test_remote_full_upgrade.py`：51 项通过。
- `uv run pytest -q tests/tools/test_remote_operations.py`：63 项通过。
- PowerShell 5.1 与 PowerShell 7 AST 解析、Ruff 和变更文件 pre-commit 全部通过。
- 真实目标机只读 `Status` 在 16 秒内返回唯一合法 JSON；业务结果为 `rejected / active_release_pointer_missing`，证明传输闭环恢复且目标机尚未初始化活动发布指针。本次未执行 Initialize、Apply、Recover、上传或目标写入。
