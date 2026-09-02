---
title: '活动发布初始化现场纠正'
type: 'bugfix'
created: '2026-09-02'
status: 'complete'
baseline_commit: '63576b1378380af658c6607c9a3c52321018c877'
context:
  - 'docs/superpowers/specs/spec-signed-full-release-remote-upgrade.md'
  - 'docs/superpowers/specs/spec-windows-openssh-input-pipeline.md'
---

## Intent

首次在真实 Windows 目标机初始化活动发布指针时，依次暴露五个此前未覆盖的现场差异：本机已合规审计 ACL 被重复写入并要求 `SeSecurityPrivilege`；PowerShell 7 将审计 UTC JSON 字符串自动转换为本地 `DateTime`，导致合法哈希链重算失败；发布者校验器的管理员/SYSTEM 信任边界被普通审计 ACL 断言误判；初始化拒绝审计不接受空候选身份并覆盖原始错误；Docker Compose 对未发布端口输出 `ports: null`，被 `@($null)` 误判为一个非法端口。

纠正这些兼容性问题，但不放宽发布、审计或网络边界。活动指针仅在候选签名、正式环境六字段、Compose、运行容器、平台、数据库 head 和网络边界全部一致后写入。

## Tasks & Acceptance

- [x] 本机审计目录和文件 ACL 已精确合规时不重复调用 `Set-Acl`；任何所有者、继承、SID、权限或标志差异仍重建受限 ACL。
- [x] 审计哈希直接使用原始 JSONL 行中 `record_hash` 之前的规范字节，不受 PowerShell 日期自动转换影响，也不容忍等价时区文本改写；原始记录不删除、不改写。
- [x] 目标发布者校验器只允许 SYSTEM/Administrators FullControl，不要求 SSH 操作员写权限；候选外信任根保持管理员保护。
- [x] Compose `ports` 缺失或为 null 表示未发布；数组内 null、无 published、零端口或非 loopback 绑定仍拒绝。
- [x] 拒绝审计允许初始化前的空候选身份，并始终保留原始门禁错误，审计失败不得覆盖首因。
- [x] 真实目标机活动发布初始化成功，随后只读 `Status` 可重读同一指针且两个维护锁均 absent。

## Verification

- 目标：`lenovo@100.109.90.21`。
- 站点：`C:\Ruisheng\candidates\site-deploy-20260831.1`。
- 候选：`C:\Ruisheng\candidates\deploy-20260831.1`。
- 操作 ID：`32712215-01fb-4bd1-bbfd-299ac211ef88`。
- 初始化结果：`initialized`；逻辑身份 `sha256:719b6ab55c3b33ea7b5f054f6a55e4369568b9d7676eaf90bd9813f0d981a853`；源码提交 `a8d3c0ba5c8ff2523345b1969c25a8b5efea521d`。
- 初始化未上传候选、未修改正式环境六字段、未启动或重建服务。
- 后验 `Status` 为 `observed`，共享维护锁和旧热修锁均为 `absent`。
- `uv run pytest -q tests/tools/test_remote_full_upgrade.py`：60 项通过。
- `uv run pytest -q tests/tools/test_remote_operations.py`：63 项通过。
- PowerShell 5.1/7 AST 解析、Ruff 和变更文件 pre-commit 全部通过。
- 本机 4 条、目标机 2 条升级审计记录的前序哈希与原始 JSONL 字节哈希全部验证通过。
