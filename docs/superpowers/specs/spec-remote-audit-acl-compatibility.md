---
title: '共享审计权限兼容与升级前置检查'
type: 'bugfix'
created: '2026-09-06'
status: 'done'
baseline_commit: '0a43b2a54f95e7253fc66962d335a821b63319b1'
context:
  - 'docs/superpowers/specs/evidence/admin-bootstrap-release-20260905/README.md'
  - 'docs/superpowers/specs/spec-signed-full-release-remote-upgrade.md'
---

<frozen-after-approval reason="human-owned intent">

## Intent

**Problem:** USB 维护将共享审计根限定为 SYSTEM/Administrators，升级工具却强制增加当前用户，导致完整上传后拒绝；Plan 漏检该门禁。

**Approach:** 运维工具兼容合法的严格权限，上传前自动预检，再复用已上传 `.2` 完成受控升级。

## Boundaries & Constraints

**Always:** 保留 SSH 公钥、主机密钥、签名、订阅、双锁、备份和审计链；仅固定远程审计路径增加兼容分支。工作在干净发布基线上，保留原目录改动。

**Ask First:** 更换候选、操作身份、信任锚、现场 ACL、数据库结构或恢复策略。

**Never:** 通用 ACL 放宽、递归改写共享审计、修改签名包、删锁/数据卷/旧版本/备份/审计；不重启 Windows/Docker，不创建账号、修改订阅或操作设备，不推送 GitHub。

## I/O & Edge-Case Matrix

| 情形 | 预期 |
|---|---|
| 固定审计根的合法双主体 ACL，调用者有有效管理员令牌 | 接受，不改权限 |
| 相同 ACL 出现在站点/状态/信任/本机审计等其他路径 | 保持原规则，不借兼容放行 |
| 错 owner、多余/拒绝/重复 ACE、错误继承、链接或非管理员 | 上传前拒绝，无目标写入 |
| 无 journal、预检通过 | 允许上传或续传，Apply 仍重新检查 |
| 同身份终态/已提交指针 | 不重复上传，由原 updater 核对并完成必要审计 |
| 中间态、损坏或身份冲突 | 不上传、不改 journal，提示只读核对/受控恢复 |

</frozen-after-approval>

## Code Map

- `tools/remote_full_upgrade/target-updater.ps1`：ACL、Plan、锁和 journal 状态机。
- `tools/remote_full_upgrade.ps1`：上传、内部调用 action 和回执绑定。
- `tools/remote_maintenance.ps1`：远程审计检查；本地检查独立。
- `tools/remote_maintenance_prepare.ps1`：共享审计权限准备。

## Tasks & Acceptance

**Execution:**

- [x] `tools/remote_full_upgrade/target-updater.ps1`：仅规范化后精确为 `C:\Ruisheng\audit` 的目录兼容双主体；要求 SYSTEM/Admin owner、protected DACL、恰好两条显式 Allow FullControl、CI/OI、Propagation=None、非链接、有效管理员令牌。固定日志接受继承双主体，固定互斥文件兼容既有三主体受保护 ACL；其他路径保持原规则。
- [x] `tools/remote_full_upgrade/target-updater.ps1`：Plan/Apply 共用只读门禁，覆盖 SSH、状态/审计/互斥文件权限、锁、schema/platform/空间及 journal 身份；未知状态拒绝，保留锁内复查和原恢复语义。
- [x] `tools/remote_full_upgrade.ps1`：内部 action 显式传参，Apply 在 incoming 准备前自动预检；严格绑定 action、操作、候选及就绪字段。终态转原重放路径，中间态不写；不通过修改全局 Action 模拟 Plan。
- [x] `tools/remote_maintenance.ps1`：远程审计根复用同一限定规则，本地权限不变。文件兼容仅限固定日志/互斥文件，验证受保护父目录、owner 和继承来源；不通配放行审计子树。
- [x] `tools/remote_maintenance_prepare.ps1`：合法严格审计根及既有文件原样保留，不递归重写其他工具审计；本轮未在现场运行权限准备。
- [x] `tests/tools/test_remote_full_upgrade.py`、`tests/tools/test_remote_operations.py`：141 项回归测试通过，PowerShell 5.1/7 解析、Ruff、`git diff --check` 均通过。
- [ ] `docs/REMOTE_DEBUG.md`、发布证据 README：记录只读预检限制、工具补丁身份和现场结果；静态检查、独立审查通过后按原操作继续 Apply，再做服务/浏览器/备份验收及管理员 Plan，不执行 Create。

**Acceptance Criteria:**

- Given 上传前门禁失败，when Apply，then 未运行 prepare/scp/sftp，目标 ACL、文件、锁、journal 不变，返回失败。
- Given 合法双主体现场，when 新 Plan，then 通过且目标权限与数据不变；Plan 不代替目标验签或 Apply。
- Given 同操作重放，when 预检，then 不重传、不跳过补审计，未知结果不冒充成功。
- Given 已批准 `.2`，when Apply 完成，then 指针/镜像一致、API/GW/Web 健康、备份摘要有效、锁释放，非版本配置/订阅/业务数量不变。

## Spec Change Log

## Design Notes

2026-09-06 用户以 `a` 确认规格；冻结意图不变。实现工作区为
`C:\ProgramData\Ruisheng\publisher-build\audit-acl-upgrade-20260906`，基线如 frontmatter。
无 BMAD 项目配置或 sprint story，沿用中文文档约定，不更新不存在的 sprint 状态。

2026-09-06 用户批准修复并续部署。本稿绑定原操作与原因，详见发布证据；候选源码与本机控制工具补丁分别追溯，不改候选 MANIFEST。

当天只读复核：旧活动 `.20260903.2`、两锁为空、journal 不存在；29 文件共 543806904 bytes，payload 摘要及索引匹配原批准。审计根为严格双主体，调用者有效管理员令牌为 true。不能直接运行旧 prepare 来回改权限。

## Verification

实施验证记录：

- `pytest -q tests/tools/test_remote_full_upgrade.py tests/tools/test_remote_operations.py`：141 passed in 379.70s；实现代理独立复核为 141 passed in 414.41s。
- Ruff、PowerShell 5.1/7 解析、`git diff --check`：通过。
- 目标机 Status：通过；活动版本仍为 `deploy-20260903.2`，两把锁为空，journal 不存在。
- 目标机 Plan：通过；候选 `deploy-20260905.2`，schema/platform 兼容，候选与备份空间充足，Plan 未写入目标状态。
- 下一阶段继续使用原操作 ID执行同候选 `ResumeUpload`/Apply，并在提交后完成服务、备份、配置、订阅、业务数量、锁和浏览器验收；不执行管理员 Create，不重启 Windows/Docker，不操作设备。
