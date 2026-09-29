---
title: '首位管理员安全引导'
type: 'feature'
created: '2026-09-05'
status: 'done'
baseline_commit: 'd839330a91cdf7cae3f4c30aff9396fb99a47120'
context:
  - 'docs/superpowers/specs/spec-plan-5-b02-production-bootstrap.md'
  - 'docs/superpowers/specs/evidence/runtime-20260905/README.md'
---

<frozen-after-approval reason="human-owned intent">

## Intent

**Problem:** 目标站点无账号；注册只产生普通用户，生产迁移禁止演示账号。

**Approach:** 增加仅维护命令可用的一次性引导。提议由本机用户保管 `rs_admin`，租户为 `site-win-oaucm8uqugh`，角色 `Administrators`、`control_authority=0`。随机凭据通过本机 Windows 加密存储交接。

## Boundaries & Constraints

**Always:** 已认证 SSH、站点/活动版本核验、事务原子性、脱敏审计；保留数据、签名与订阅边界。控制位为零不等于剥夺管理员后续授权能力。

**Ask First:** 更换账号保管人、密码恢复、发布新候选或现场安装执行。

**Never:** 公开初始化接口、自动 seed、历史密码、覆盖旧账号、物理控制、轮询、通知；不重启 Windows/Docker、不改签名候选。改密与设备校准另行，不承诺首登改密。

## I/O & Edge-Case Matrix

| 情形 | 结果 |
|---|---|
| 空租户表、空用户表 | 同事务创建租户、管理员、安全审计 |
| 已有用户（含软删除）或租户 | 拒绝，不修改 |
| 并发、超时、审计失败 | 至多一次提交；失败回滚 |
| 提交后断线 | 标记结果未知、保留加密凭据；只读核对，不自动重置 |
| 不同站点、版本、身份或凭据冲突 | 拒绝，不将同名视为成功 |

</frozen-after-approval>

## Code Map

- `ruisheng-api/src/ruisheng_api/core/{security,tenant}.py`：bcrypt、事务租户上下文。
- `ruisheng-shared/src/ruisheng_shared/models/{users,tenants,logs}.py`：用户、租户、SoftLog。
- `alembic/versions/20260818_0012_alarm_notification_runtime.py`：GW 已无租户表权限；使用 API DB 角色，不提权。
- `tools/remote_debug.ps1`、`tools/remote_maintenance.ps1`：既有传输、身份与审计约束。

## Tasks & Acceptance

**Execution:**

- [x] `ruisheng-api/src/ruisheng_api/admin_bootstrap.py`：新增显式 CLI；仅限量 stdin JSON 传入凭据，拒绝多余字段；复用哈希与 API DB 配置，禁止路由/入口自动调用。
- [x] `ruisheng-api/src/ruisheng_api/admin_bootstrap.py`：事务设置管理员租户上下文，以固定顺序锁 `wx_groups/users`（SHARE ROW EXCLUSIVE，5 秒超时），全局检查空表，创建两行及 WARN/api SoftLog。回执仅含操作 ID、站点、账号、状态；只读核对须匹配审计操作、账号、租户、权限和凭据，不更新数据。
- [x] `tools/remote_admin_bootstrap.ps1`：实现 Plan/Create/Status/ShowCredential；默认 Plan。Create 持有共享维护锁，核验目标身份、活动签名版本与 remote-support，调用已安装镜像内 CLI；不得注入临时 Python 或自行发布。请求最多 4 KiB、响应 64 KiB、传输 60 秒；固定 SSH 公钥与主机密钥检查，日志不含密码、哈希、令牌或 SQL 参数。
- [x] `tools/remote_admin_bootstrap.ps1`：生成 32 字符随机 ASCII 密码，发出请求前以 DPAPI CurrentUser 加密、排他保存并验证可解密；目录 ACL 限当前 SID/SYSTEM，拒绝重解析路径。文件绑定站点、账号、操作；重复执行不重新生成。ShowCredential 仅在本机交互窗口显示，不写 stdout/剪贴板；非交互执行拒绝显示。
- [x] `ruisheng-api/tests/unit/test_admin_bootstrap.py`、`tests/tools/test_remote_admin_bootstrap.py`：覆盖矩阵、输入边界、泄密、DPAPI/ACL 失败、传输中断与错误目标。
- [x] `tests/integration/test_admin_bootstrap.py`：复用生产迁移测试模式，独占临时 PostgreSQL，使用真实 API 角色验证并发、RLS、回滚、重复执行及数据保留。
- [x] `deploy/setup-customer.md`：说明加密凭据查看和故障核对；保留未发布/未验收及其他生产门禁。

**Acceptance Criteria:**

- Given 空隔离库，when 两次并发引导，then 仅一个管理员、一个租户、一条成功审计，设备/点表仍为空。
- Given 引导成功，when 经真实登录刷新并访问用户列表，then 账号可用；控制入口拒绝且无命令入队。首次登录不要求不存在的改密流程。
- Given 重跑、异常或断线，when 核对结果，then 密码不变、无部分创建；不能确认时保持未知而非宣称成功。
- Given 全部输出与测试证据，when 检查，then 无凭据、令牌、密码哈希；浏览器测试关闭 trace/视频，不保存含凭据截图。

## Spec Change Log

- 2026-09-05 发布前兼容修复：目标 Windows OpenSSH 的匿名管道验签挂起，复用既有
  发布校验器的固定系统 `cmd.exe /d /q /v:off` 与受保护文件 stdin，路径拒绝空白及
  shell 元字符；只对系统工具允许 TrustedInstaller owner，按 SID 读取 ACL，不放宽
  凭据或信任锚 ACL。目标 PowerShell 直接读取标准输入会挂起，改用既有升级入口的
  首行 JSON 传输、大小核对与明确退出。生产 Docker `Devices=null` 视作无映射，
  非空映射仍拒绝。文档顺序回归仅比较 `remote_full_upgrade.ps1` 的操作，不混入管理员 Plan。
  KEEP：所有冻结意图、DPAPI 交接、签名身份、维护锁、数据库核对及单独现场审批不变。

## Design Notes

2026-09-05：用户批准规格，进入本机实施与隔离验收；现场发布和创建仍保留独立审批。

2026-09-05 只读复验：隧道可用，目标 `users=0/active_admins=0`；本轮未写现场数据。凭据保管方案待批准，不读取聊天中的密码。SoftLog 保留一年，记录缺失时核对不得推断成功或重置账号。现场验收须先有单独批准且验证通过的新候选，按相同 User-Agent/IP 登录并登出，仅读业务页面；控制拒绝测试仅在隔离库运行。

设备点表继续以 B-08 与 `evidence/b08-20260905/` 为准：46 个候选均不可导入。下一阶段需要绑定实物的固件/点表定义或独立物理参照；不新增探测或由零值猜语义。

2026-09-05 后续批准：用户“按照建议执行”授权构建和发布准备。在独立发布 worktree
提交本功能，原工作目录及无关改动保留；不推送 GitHub，不创建 PR。首个不可变候选
`deploy-20260905.1`（提交 `38146fd9a35744d3d31c64577a21b756901e6888`）已生成并通过
只读升级预检，但不含前述控制工具修复，保留原样、不用于此次目标机切换。
修复后使用新候选 ID；真实上传和切换仍绑定具体候选、操作 ID、原因及当次审批。

## Verification

2026-09-05 本机隔离验收完成；`done` 仅指本地实现完成，不是目标机全量运行或发布验收。

| 检查 | 结果 |
|---|---|
| `uv run pytest ruisheng-api/tests/unit -q --cov=ruisheng_api --cov-report=term --cov-fail-under=60` | 261 passed；API 覆盖率 64.28%，达到现有 CI 60% 门槛 |
| `uv run pytest tests/tools/test_remote_admin_bootstrap.py -q` | 35 passed，144.89 秒；Windows PowerShell 5.1/7 均实际执行 |
| `uv run pytest tests/integration/test_admin_bootstrap.py -m integration -q` | 5 passed，134.62 秒；真实受限角色、生产迁移和实际 API 镜像，无 skip |
| `uv run pytest tests/tools/test_production_compose.py -q` | 51 passed；补充文档后重新通过 |
| Ruff、format、mypy、定向 pre-commit、`git diff --check` | 通过；无适用文件的 hook 跳过不计作功能测试 |

真实镜像按远程命令 `/app/.venv/bin/python -I -m ruisheng_api.admin_bootstrap` 验证创建、
只读核对及拒绝回执/退出码；同时确认 `getent` 可执行且生产入口仍不创建账号。
隔离库验证了并发至多一次、审计失败回滚、RLS 下已有软删除用户拒绝、锁超时、身份冲突、
真实登录/刷新/登出、管理员列表访问及设备控制拒绝/无命令入队。Redis 使用隔离替身，
未宣称此次测试等同于完整生产服务组合或浏览器现场验收。

Windows 工具测试使用实际 DPAPI、ACL、进程管道和临时 Ed25519 签名；只在测试中替代
目标管理员令牌及 Docker 返回值，不连接真实 SSH。测试创建的临时容器、镜像、凭据目录
及签名测试密钥均按独占名称清理；未创建现场凭据。交互式密码显示窗口尚未人工验收，
目前已验证非交互显示拒绝；不得用含凭据截图补充证据。

发布前兼容修复后的源码 SHA-256（不是候选发布签名）：

- `ruisheng-api/src/ruisheng_api/admin_bootstrap.py`：`604C16CC2D9CF1B69EEBB25AF76AB9065A62F0B753EEA8ECA842E566DA987C06`
- `tools/remote_admin_bootstrap.ps1`：`370AF46574FB8D0C8F5AE2E0143F28F6C3C03F1934BEDAE5E0D86CE9D243125F`

发布前补充验收：目标机两次执行最终工具的 `Plan -ExpectedCandidateId deploy-20260903.2`
均返回 `planned`，包含活动签名镜像、受限 DB 目标、实际解析地址、订阅与服务就绪检查；
不生成本地账号凭据，不写现场业务数据，不改变活动指针或启停服务。
升级工具完整回归 60 passed；发布/签名/升级联合首轮 381 passed、2 POSIX-only skipped，
唯一失败为管理员 Plan 混入升级文档顺序断言，限定命令后完整升级回归通过。
最终管理员 Windows 双版本回归 39 passed（111.39 秒），包含分片首行回执、真实签名、
shell 元字符拒绝、空设备映射及非空拒绝；定向 pre-commit 通过。兼容补丁三路复核无阻断项。
SSH 大小上限由受信本机发送前检查及远端首行解析前复验共同保证；PowerShell 首行读取
本身不是逐字节内存限额，不宣称能够承受已控制 SSH 发送端的无限长输入。

## Review Results

三路独立审查完成，合并重复问题后均按现有意图修复，无需修改冻结意图：

1. 生产 API 无 Docker HEALTHCHECK：改用既有内部健康检查并严格校验四字段就绪回执。
2. 合法 `unknown` 回执曾释放维护锁：现保留未知操作的锁，仅允许相同操作只读核对。
3. 数据库名称未绑定实际容器：拒绝额外 hosts，核对容器内解析地址与已验镜像的 PostgreSQL 地址。
4. 失败结果曾以进程成功结束：回执映射 `0/2/3`；前置拒绝也以真实进程验证退出码 `2`。
5. 镜像测试调用不同于现场：已使用固定解释器及 `-I`，并验证依赖的解析工具存在。

独立验收审查员对前述核心修复只读复核后未发现剩余实质缺陷；真实测试由主流程执行。
快速开发流程的提交步骤在本轮权限边界处停止：不自动提交、推送、发布或创建现场账号。
未修改已有无关工作。后续新候选发布和现场初始化仍按冻结边界分别审批。

## Suggested Review Order

**维护入口与凭据**

- 从批准、身份绑定及先保存凭据后派发的入口开始。
  [remote_admin_bootstrap.ps1:553](../../../tools/remote_admin_bootstrap.ps1#L553)
- 检查 DPAPI、排他写入与解密回验。
  [remote_admin_bootstrap.ps1:166](../../../tools/remote_admin_bootstrap.ps1#L166)

**远程边界与事务**

- 核对签名镜像、站点、订阅、健康与维护锁。
  [remote_admin_bootstrap.ps1:315](../../../tools/remote_admin_bootstrap.ps1#L315)
- 检查空库限定、固定锁序、审计及只读核对。
  [admin_bootstrap.py:120](../../../ruisheng-api/src/ruisheng_api/admin_bootstrap.py#L120)
- 核对输入边界和异常输出脱敏。
  [admin_bootstrap.py:215](../../../ruisheng-api/src/ruisheng_api/admin_bootstrap.py#L215)

**测试与交接**

- 查看真实进程退出码和签名保护回归。
  [test_remote_admin_bootstrap.py:326](../../../tests/tools/test_remote_admin_bootstrap.py#L326)
- 查看隔离真实库登录及控制拒绝验收。
  [test_admin_bootstrap.py:153](../../../tests/integration/test_admin_bootstrap.py#L153)
- 检查现场审批、加密凭据交接与未知结果处理。
  [setup-customer.md:159](../../../deploy/setup-customer.md#L159)
