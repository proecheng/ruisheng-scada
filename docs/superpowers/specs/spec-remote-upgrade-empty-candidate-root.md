---
title: '修复远程升级只读操作空候选根绑定'
type: 'bugfix'
created: '2026-09-02'
status: 'done'
route: 'one-shot'
---

# 修复远程升级只读操作空候选根绑定

## Intent

**Problem:** `Status` 和 `Plan` 按设计不传远端候选目录，但控制器内部必填字符串参数拒绝空值，导致命令在连接目标机前失败。

**Approach:** 允许内部调度器接收空候选根，并用 PowerShell 5.1/7 行为测试验证 `Status`、`Plan` 的单次 stdin 传输、空值编码和完整响应契约。

## Suggested Review Order

**参数契约**

- 只放宽内部传输参数，保持顶层操作校验不变。
  [`remote_full_upgrade.ps1:164`](../../../tools/remote_full_upgrade.ps1#L164)

**行为回归**

- 双 PowerShell 版本执行两种只读操作并核对完整传输结果。
  [`test_remote_full_upgrade.py:68`](../../../tests/tools/test_remote_full_upgrade.py#L68)
