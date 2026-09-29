# 首位管理员功能发布记录（2026-09-05）

## 范围

用户“按照建议执行”后，完成本机干净源码提交、签名候选构建和目标机只读预检。
随后用户“批准”授权最终 `.2` 候选的上传和受控 Apply；2026-09-05 16:40（北京时间）
启动下述操作，使用既有 `-ResumeUpload -Approved` 路径。29 个文件已完整上传；
Apply 在目标权限预检时返回 `rejected / restricted_acl_required_identity_missing`，未切换。
未创建现场管理员、未生成实际账号密码，未重启目标 Windows/Docker。
账号创建仍为部署验收后的独立批准事项。

## 源码与签名

- 独立 worktree：`C:\ProgramData\Ruisheng\publisher-build\admin-bootstrap-release-20260905`。
- 分支：`codex/admin-bootstrap-release-20260905`，仅本地提交，未推送 GitHub/创建 PR。
- 功能提交：`38146fd9a35744d3d31c64577a21b756901e6888`。
- 兼容修复提交：`0a43b2a54f95e7253fc66962d335a821b63319b1`。
- 原工作目录及其他未提交改动保留，未混入候选。
- 沿用 `ruisheng-release` / `ruisheng-candidate-v1`，公钥指纹
  `SHA256:Go/TiuSZ89zJvTCzVick7GT6gP6yrOK+CRdYMRp07Fk`；不轮换信任锚。
- 构建环境使用示例配置，仅构建不启动服务，无需读取现场密码。
- PostgreSQL 固定现用摘要 `sha256:50a2abfa8bad354f4bc1567c6edf7426586fd99ee8cd8982bbaee157a460c6b1`。
- Redis 固定现用摘要 `sha256:ff02b58f971e7d7d156a1267e283fcbbeee91773b6aa36c49dac28ecfe28eadf`。

## 发布前修复

1. 目标 OpenSSH 匿名 stdin 验签挂起：复用系统 cmd 文件重定向，保留签名身份、受保护句柄和严格路径白名单。
2. Docker `Devices=null` 被误判成一个映射：接受真实空值，实际映射仍拒绝。
3. SSH 中 PowerShell 控制台原始流读取超时：改用已验证的首行 JSON 输入，检查大小、禁进度输出并明确结束。
4. 升级文档顺序测试误把管理员 Plan 纳入比较：按完整升级命令匹配，不降低 Initialize 在 Plan 前的要求。

三路复核无阻断项。远端首行内存读取不是逐字节硬上限；受信发送端发送前限制总字节数，
接收端在 JSON 解析前再次检查，不宣称抵御已控制 SSH 发送端的无限长输入。

## 测试

- 上轮功能验收：API 单元 261 passed，覆盖率 64.28%；隔离真实数据库/生产镜像 5 passed；生产部署契约 51 passed。
- 本轮最终管理员工具：39 passed（111.39 秒），PowerShell 5.1/7 均实际执行。
- 本轮完整升级工具：60 passed（88.93 秒）。
- 发布/签名/升级联合首轮：381 passed、2 POSIX-only skipped、1 文档顺序失败；修正后完整升级测试通过，未把定向复验冒充完整联合重跑。
- Ruff、定向 pre-commit、`git diff --check` 通过。
- `.1` 和最终 `.2` 镜像内实际隔离解释器均成功导入管理员模块，源码 SHA-256 为
  `604c16cc2d9cf1b69eebb25af76ab9065a62f0b753eea8eca842e566da987c06`。
- 最终控制工具 SHA-256：`370af46574fb8d0c8f5ae2e0143f28f6c3c03f1934bedae5e0d86ce9d243125f`。
- 临时 DPAPI/签名测试目录按独占名称清理，无实际账号凭据；隔离镜像导入容器使用 `--rm`。

## 升级前目标机观察

- 目标：`lenovo@100.109.90.21` / `WIN-OAUCM8UQUGH`。
- 站点：`C:\Ruisheng\candidates\site-deploy-20260831.1`。
- 活动版本仍为 `deploy-20260903.2`，提交 `1093e21a5172c7cb2be3bdb37fc157a70792b6aa`。
- 状态查询成功，共享维护锁和 legacy 锁均不存在。
- 最终管理员工具两次实际 Plan 返回 `planned`，覆盖签名镜像、站点、数据库地址、remote-support 和健康检查。
- 未执行数据库写入 CLI，未触发 Modbus/持续采集/设备控制/通知/订阅变更。

## 候选

`deploy-20260905.1` 已签发并保留原样，但缺少本轮控制工具兼容修复的源码追溯，
不用于此次目标机切换。其只读升级预检通过，操作 `0e56033b-da78-4a5f-a043-0af3d90307c9`：
schema/platform 均兼容，目标空闲约 130 GB，候选空间约需 1.09 GB，备份空间约需 5.37 GB。

最终候选 `deploy-20260905.2` 已完成原子发布、发布者签名回验、完整文件集、归档身份和
Compose 校验；发布源码 worktree 保持干净。签名代理已关闭。

| 字段 | 结果 |
|---|---|
| 本机目录 | `C:\ProgramData\Ruisheng\publisher-output\deploy-20260905.2` |
| 源码 | `0a43b2a54f95e7253fc66962d335a821b63319b1` |
| 逻辑身份 | `sha256:ed627b41c0eb27ff3df099fcdb9874d838fd9b13a599f493294a7cd8c3f4b35a` |
| SHA256SUMS 文件摘要 | `970ed7fe331029e25e01b6fc07a750c60674dc13635b60577172505ec2d25e16` |
| 包大小 | 543806904 bytes |
| 数据库 head | `0012_alarm_notification_runtime`，与现场相同 |
| 平台 | `linux/amd64`，与现场相同 |
| 只读 Plan | `planned`，schema/platform/resource 全通过；两把锁为空 |
| 操作 ID | `e43167a7-f84a-4712-a7c8-e80ff5dde771` |
| 原因 | `approved first administrator bootstrap release` |

最终 API 镜像 ID：`sha256:8faaebca9410101de109234929b88a77d0db5a6043f2cd321387ba5842d8b759`。
最终 GW 镜像 ID：`sha256:d846ae0f1243d2b16d0cbe7781a465ca3a7381aea4379ce4750c5564dcb6db95`。
最终 Web 镜像 ID：`sha256:2c3da35750cac33d18cba0b7944d2bf12e96c3d9831568e9e572710e3cc08e22`。

最终预检目标盘可用 130168844288 bytes，候选工作空间要求 1087613808 bytes，备份空间
要求 5368709120 bytes。以上为预检时刻结果，Apply 前必须重新确认。

## 本次部署批准

用户已批准将上述 `.2` 上传到 `WIN-OAUCM8UQUGH`，使用上述操作 ID 和原因执行受控 Apply。
顺序为目标验签与网络边界检查、锁内数据库/角色/配置备份、应用容器切换、健康及镜像
身份验收、活动指针提交。会重建应用容器并有短暂停服，不重启 Windows/Docker Desktop，
不执行 Compose down 或删除数据卷。失败按既有恢复约束处理，不无锁绕过门禁。
此审批不包括管理员账号创建、密码恢复、持续采集、设备控制、点表导入或订阅变更。

本机发布校验和只读 Plan 不等于目标机包外验签成功；Apply 仍需现场重新验签。
该记录不是候选 payload，不向不可变候选目录添加证据文件。

## 部署过程

- Apply 前重新确认发布工具 worktree 干净、`SHA256SUMS` 摘要与批准一致。
- 新鲜 Status：旧活动版本 `.20260903.2`；本操作 journal 不存在，两把维护锁为空。
- 新鲜 Plan：`planned`；schema/platform 兼容，目标空闲 130116636672 bytes，空间充足。
- 16:47 升级前只读快照：API/GW 全部 ready，Web/版本 HTTP 200；users、active_admins、
  tenants、devices、points 均为 0；GW 没有串口设备映射或串口环境变量。
- 订阅文件 SHA-256：`1e6b3212a31bed5d7de0faf2ac7d97accb8a42905572af9fb170d2dbddeca233`。
- 快照保存于 `D:\江苏润盛\tmp-test-logs\admin-release-20260905-before.json`，只记录白名单字段，
  不含密码、令牌、环境变量值或业务行明细。
- Tailscale 当前经 DERP(hkg) 中继；上传进度用 SFTP 进程字节计数观察，不把未关闭的大文件
  目录大小当成停滞，也不把发送字节数当成目标完整校验结果。

## 部署拒绝与复核

- Apply 进程已退出，退出码 1；受控 JSON 回执 `ok=false/status=rejected`，错误码
  `restricted_acl_required_identity_missing`。随后同操作 Status 仍为旧版本；无 journal、
  无备份、无共享/legacy 维护锁，没有执行候选验签加载、环境切换或服务重建。
- 只读 ACL 定位：站点根、`.env.prod`、维护状态目录和审计互斥文件符合升级器规则；
  `C:\Ruisheng\audit` 为受保护 SYSTEM/Administrators 双主体 ACL，owner 为 Administrators，
  缺少升级器额外要求的当前用户 SID（lenovo）。现有 `full-upgrade.jsonl` 继承同一双主体 ACL。
- 代码层存在策略冲突：`serial_hardware_attach.ps1::Initialize-ProtectedAuditPath` 将共享
  审计根设为 SYSTEM/Administrators；`run_modbus_probe.ps1::Assert-ProtectedPath` 拒绝普通
  用户 SID 的显式写权限。升级器 `Assert-RestrictedDirectory` 却强制三主体齐备。
  因此不能直接运行会递归改写审计权限的 `remote_maintenance_prepare.ps1` 作为临时绕过。
- `Plan` 未执行这项写操作前置 ACL 检查，导致上传完成才发现不兼容；后续修复应同时让
  上传前只读预检暴露该错误。不能把本次 `planned` 当作所有 Apply 前置条件通过。
- 17:22 失败后复验：五容器 ID/镜像/启动时间/重启次数/端口、活动指针、系统启动时间、
  订阅摘要、健康 ACL、业务表数量和 GW 串口配置与升级前逐字段一致；API/GW 全 ready，
  Web/版本 HTTP 200。未改变现场权限、密码、信任锚、订阅或设备设置。
- 上传目录保留：
  `C:\Ruisheng\incoming\e43167a7-f84a-4712-a7c8-e80ff5dde771\deploy-20260905.2`。
  只读核验 29 文件、543806904 bytes、完整文件集合与全部 payload SHA-256 通过；
  `SHA256SUMS` 与批准摘要一致，签名文件摘要为
  `19bc2540dcb31edccdc28f2b7f6111c67159d101b603335889935a5e551bfc62`，与本机一致。
  这是传输完整性复核，不等同于目标包外发布者验签、网络验收或部署成功。
- 失败后快照：`D:\江苏润盛\tmp-test-logs\admin-release-20260905-rejected.json`。
  本机只读验收脚本：`tmp-test-logs/verify-admin-release-20260905.ps1`。
  已准备浏览器验收脚本并通过 Node 语法检查，但未对未部署的新版本运行，不能计为现场验收。
- 本机审计 `%LOCALAPPDATA%\Ruisheng\audit\remote-full-upgrade.jsonl` 记录本操作拒绝结果。
  发布 worktree 仍干净；签名候选及旧版本、备份、审计均未删除。

## 后续修复批准

修复升级/维护工具与设备审计的权限兼容，并补齐上传前预检和 Windows 双版本回归；
保留设备侧严格权限、维护双锁和审计链，不直接放宽共享目录权限。审查通过后复核并
复用已上传的 `.2`，按同一操作 ID/原因和既有恢复规则继续受控部署；不得盲目重放或删锁。
上述修复及续部署已获用户 2026-09-06“批准”；管理员创建仍另行批准。

2026-09-06 13:48（北京时间）只读复核：活动仍为 `.20260903.2`，同操作 journal 不存在，
两把维护锁为空；上传包仍为 29 文件、543806904 bytes，全部 payload SHA-256 和索引摘要
与批准一致。审计根仍为受保护 SYSTEM/Administrators 双主体、Administrators owner，
远程调用者的有效管理员令牌为 true。

具体修复稿见 `../../spec-remote-audit-acl-compatibility.md`。本轮按 `bmad-quick-dev` 的
规格确认检查点暂停：完成调查和规格草案，尚未修改运行代码、运行修复回归或重新 Apply。
原发布 worktree 保持干净；未修改目标机 ACL 或业务状态。
