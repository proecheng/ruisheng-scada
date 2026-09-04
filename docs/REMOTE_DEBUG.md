# 远程调试与单服务热修

本流程通过 Tailscale 上的 SSH 连接工作，目标机应用端口继续只绑定回环地址。它用于当前隔离调试部署，不改变正式生产验收状态，也不开放目标机应用端口。

## 调试隧道

在仓库根目录启动：

```powershell
.\tools\remote_debug.ps1 Start
```

本机调试入口：

- Web：`http://127.0.0.1:18080`
- API 调试探针：`http://127.0.0.1:18080/api/meta/version`
- GW 运维入口：`http://127.0.0.1:19090`（仍受站点 ACL 保护，隔离部署中返回 403 属于预期）
- GW 设备 TCP：`127.0.0.1:15020`

状态、日志和停止命令：

```powershell
.\tools\remote_debug.ps1 Status
.\tools\remote_debug.ps1 Health
.\tools\remote_debug.ps1 Logs
.\tools\remote_debug.ps1 Stop
```

隧道仅监听本机 `127.0.0.1`。脚本使用公钥认证、转发失败即退出和 SSH keepalive，并记录 PID 以避免误停其他进程。`Health` 从容器内部执行 API/GW 就绪检查，因此无需放宽站点 ACL。GW `/ready` 只有精确返回 200，或返回不超过 512 字节且 JSON 内容精确为 `{"detail":"health source is not approved"}` 的 403，才算可达；其他 403、其他状态、超大响应、连接失败和超时都使检查失败。

通过登录页提交无效测试凭据时，真实 API 返回 HTTP 401、业务码 `-101`。Web 应显示“用户名或密码错误”，且不得把这次登录失败广播成已有会话过期；其他受保护 API 的 HTTP 401 或 `-101` 仍会清理已有会话。

## 桌面一键启动

目标机桌面上的“润盛监控系统”快捷方式供现场用户启动本机应用。双击后，启动器会复用已运行的 Docker Desktop；Docker 尚未就绪时会启动当前登录用户的 Docker Desktop 并限时等待。随后它只从 `C:\Ruisheng\candidates` 下唯一受保护站点的 `active-release.json` 解析活动候选，不扫描或猜测最新版本。

启动器在任何 Compose 变更前核对站点、状态目录、活动指针和发布文件 ACL，并交叉检查 Manifest、Compose 闭集、`pull_policy: never`、已加载镜像、已有容器镜像及回环端口。需要恢复服务时，它按 `postgres/redis -> migrate -> gw/api/web -> health` 执行，并依次取得 `shared-maintenance` 和 `legacy-hotfix` 租约锁；配置、指针、锁或镜像身份发生漂移会立即停止。服务已经健康时只复核状态并打开 `http://127.0.0.1/`，不会重建或删除卷。

日常双击不需要管理员权限，也不会出现 UAC；安装目录仅 Administrators 和 SYSTEM 可写，`lenovo` 只有读取执行权限。启动日志使用独立的 `C:\Ruisheng\launcher-audit`，不会修改远程维护审计目录。安装器不保存 SSH 密钥、不自连 SSH，也不会修改开机任务、Docker 全局设置或串口配置。管理员进行无浏览器验收时可运行：

```powershell
& "C:\Program Files\Ruisheng\Launcher\start_ruisheng_local.ps1" -NoBrowser -NoUi
```

输出 `READY` 只表示本机五个服务已按活动候选就绪，不代表生产放行。失败时保留容器、卷、候选和审计现场，并只显示不含环境密钥的错误代码。

## 单服务热修

先执行只读预检：

```powershell
.\tools\remote_hotfix_deploy.ps1 -Service gw -DryRun
```

确认代码已提交且工作树干净后，可部署 `api`、`gw` 或 `web` 中的一个服务：

```powershell
.\tools\remote_hotfix_deploy.ps1 -Service gw
```

脚本依次执行服务测试、提交标签镜像构建、SHA-256 清单生成、SCP 传输、目标机校验、站点环境文件原子备份、单服务重建和健康检查。失败时自动恢复原环境文件并重建旧镜像。API 如包含 Alembic 变更会被拒绝，必须改用完整部署流程。

不要使用 `-SkipTests` 做正常部署。热修产物保存在本机 `dist/hotfix/` 和目标机 `C:\Ruisheng\hotfix\`，均按提交和服务隔离。

## 远程应用维护

首次真实维护前，必须先关闭目标机 SSH 密码与键盘交互认证，并获得单独的 ACL 修改批准。随后只执行一次安全目录准备；脚本会再次检查有效 SSH 姿态，在密码认证仍开启时不会修改本机或目标机：

```powershell
.\tools\remote_maintenance_prepare.ps1 -Approved
```

该命令只创建维护状态目录、目标机/本机审计目录和审计互斥文件；它限制状态和审计目录的递归访问，并仅限制站点根目录本身来保护旧版兼容锁，不递归改写 `.env.prod` 等已有站点文件 ACL。允许身份为当前运维账号、SYSTEM 与本机 Administrators。它不调用 Docker，也不启停应用。现阶段目标机仍报告 `passwordauthentication=yes`，因此不要把准备失败当作应用故障；需先另行批准 SSH 加固。

先执行只读状态检查。输出仅包含目标机身份、SSH 有效认证姿态、维护锁、服务镜像与健康状态，不输出 Compose 环境内容：

```powershell
.\tools\remote_maintenance.ps1 Status
```

未指定 `-SiteRoot` 时，脚本只在目标机固定的 `C:\Ruisheng\candidates` 目录下查找唯一的 `site`，或名称匹配 `site-[a-z0-9][a-z0-9._-]{0,57}` 的目录。名称匹配的重解析目录会被拒绝；选中的站点目录、`.remote-maintenance-state` 目录和 `active-release.json` 指针都必须受保护。没有匹配、出现多个匹配、ACL 不可信或指针内容漂移都会 fail-closed，并返回具体拒绝码；非标准目录可显式传入 `-SiteRoot` 覆盖自动发现，但不会绕过目录和指针保护校验。

任何启停操作都先 dry-run。原因必须为 8–200 个字符且不能包含控制字符：

```powershell
.\tools\remote_maintenance.ps1 StopApp -Reason "approved maintenance window" -DryRun
.\tools\remote_maintenance.ps1 StartApp -Reason "recover after approved maintenance" -DryRun
.\tools\remote_maintenance.ps1 RestartApp -Reason "apply approved runtime recovery" -DryRun
```

dry-run 不创建远端锁、状态或审计记录；返回的 `snapshot_before`、`snapshot_after` 和 `snapshot_equal` 用于确认执行前后状态一致。目标机尚未禁用 SSH 密码认证时，计划会显示 `security_blocked`，真实操作始终被拒绝。

真实生命周期操作需要当次人工批准，并且远端 `passwordauthentication=no`、`kbdinteractiveauthentication=no`、`pubkeyauthentication=yes`、`authenticationmethods=publickey`、`gssapiauthentication=no`、`hostbasedauthentication=no` 均已生效。获得单独批准后才可增加 `-Approved`：

```powershell
.\tools\remote_maintenance.ps1 StopApp -Reason "approved maintenance window" -Approved
.\tools\remote_maintenance.ps1 StartApp -Reason "recover after approved maintenance" -Approved
.\tools\remote_maintenance.ps1 RestartApp -Reason "approved application restart" -Approved
```

脚本按 `web -> api -> gw -> redis -> postgres` 停止应用，且只使用 Compose `stop`，不会执行 `down` 或删除卷。启动时先恢复 PostgreSQL 和 Redis，再运行迁移，最后启动 GW、API 和 Web，并从容器内部验证五个常驻服务。

维护和热修使用同一共享锁，并在过渡期同时获取旧热修锁。热修从预检开始预留双锁并续租，进入目标机部署时把双锁无缝移交给部署进程；部署期间继续校验和续租。若部署报告锁丢失，它不会在无锁状态下继续回滚，必须先检查目标机镜像、容器和 `.env.prod` 现场再决定恢复。锁冲突或无法确认所有者时应先运行 `Status` 检查，不要手工删除锁。若停止过程返回 `partial`，按结果中的 `stopped`、`remaining` 和 `recovery_hint` 核对现场，再用新的操作 ID 执行 `StartApp` dry-run；实际恢复仍需单独批准。

每次真实请求会在连接前显示操作 UUID。只有在确认该 ID 来自同一次真实请求后，才可用完全相同的动作和原因重放；同一 ID 已有终态时只返回结果，不会再次调用 Docker。重放真实请求仍需 `-Approved`，不要用 `-DryRun`，因为 dry-run 只生成新计划：

```powershell
.\tools\remote_maintenance.ps1 StartApp -Reason "recover after approved maintenance" `
  -OperationId "00000000-0000-4000-8000-000000000001" -Approved
```

目标机 JSONL 审计与本机 `%LOCALAPPDATA%\Ruisheng\audit\remote-maintenance.jsonl` 镜像通过操作 ID 和审计 ID 关联，并使用前序哈希检测意外编辑。它们不提供对恶意管理员的防篡改保证。

## 签名全量远程升级

全量升级使用闭集入口 `tools\remote_full_upgrade.ps1`，不再远程执行人工 Compose 命令。先设置目标与站点根：

```powershell
$Target = "operator@100.x.y.z"
$SiteRoot = "C:\Ruisheng\candidates\site-deploy-20260831.1"
```

如果目标机是首次接入受控升级且还没有 `active-release.json`，必须在 Plan 前对当前正在运行的签名候选执行一次显式初始化，因为 Plan 需要读取活动指针。初始化不扫描目录猜测版本、不上传文件，也不启停或重建服务；它在双锁内验签并交叉核对正式环境六字段、Compose、运行容器、平台、数据库 head 和网络边界，只在全部一致后写入活动指针和审计。相同操作和候选可补写中断的审计；不同操作或候选会拒绝，不能用它改写版本：

```powershell
$CurrentCandidateRoot = "C:\Ruisheng\candidates\deploy-20260831.1"
$InitializationOperation = [Guid]::NewGuid().ToString("D")

.\tools\remote_full_upgrade.ps1 -Action Initialize -Target $Target `
  -SiteRoot $SiteRoot -CurrentCandidateRoot $CurrentCandidateRoot `
  -OperationId $InitializationOperation `
  -Reason "approved active release initialization" -Approved
```

活动指针存在后，生成升级操作 ID 并执行只读 Plan；Plan 不上传、不加载镜像、不获取锁、不写目标状态：

```powershell
$Candidate = "C:\ProtectedRelease\deploy-YYYYMMDD.N"
$Operation = [Guid]::NewGuid().ToString("D")
$Reason = "approved signed full release upgrade"

.\tools\remote_full_upgrade.ps1 -Action Plan -Target $Target `
  -CandidatePath $Candidate -SiteRoot $SiteRoot -OperationId $Operation -DryRun
```

首版只允许候选 Alembic head 与目标数据库 head 完全一致。Apply 前必须核对活动版本指针、候选逻辑身份、平台、资源与锁，并针对同一操作 ID、候选和原因取得当次批准：

```powershell
.\tools\remote_full_upgrade.ps1 -Action Apply -Target $Target `
  -CandidatePath $Candidate -SiteRoot $SiteRoot -OperationId $Operation `
  -Reason $Reason -Approved
.\tools\remote_full_upgrade.ps1 -Action Status -Target $Target `
  -SiteRoot $SiteRoot -OperationId $Operation
```

如果 Apply 在候选上传阶段因网络或 Tailscale 中继切换失败，且 Status 证明活动版本未变、双锁为空、目标端尚未进入升级事务，可使用完全相同的操作 ID、候选和原因断点续传。该模式会先验证既有 incoming 目录的所有者和 ACL，拒绝链接、越界路径、超长文件及额外文件；每次 SFTP 中断后从已有长度继续，并在完整文件集合和长度通过后才调用目标 updater。`UploadAttempts` 只控制本次自动重连次数，不放宽候选签名、SHA-256、镜像身份、schema、网络边界或健康门禁：

```powershell
.\tools\remote_full_upgrade.ps1 -Action Apply -ResumeUpload -UploadAttempts 8 `
  -Target $Target -CandidatePath $Candidate -SiteRoot $SiteRoot `
  -OperationId $Operation -Reason $Reason -Approved
```

不要对不同候选或原因复用 incoming 操作目录。若 Status 显示 journal 已进入 `uncertain`、`rolled_back` 或 `recovery_failed`，不要继续上传，应按下方 Recover 流程处理。

目标机固定 verifier 返回退出码 `2`、publisher `VERIFIED` 和 `B-04 remains BLOCKED` 是预期结果：它只证明签名与完整包通过，网络边界仍由 updater 独立检查，B-04 现场验收没有因此解除。切换前会生成数据库逻辑备份、角色备份和 SHA-256 回执；只修改六个发布字段，其他站点配置逐字保留。成功时最后提交受保护的 `active-release.json`，其候选 ID、逻辑身份、源码提交、候选根、站点根和操作 ID 是后续维护与热修的唯一版本来源。

失败时状态机持锁恢复旧环境和旧服务；它不执行 `down`、不删卷，也不把镜像恢复称为数据库恢复。中断或锁丢失返回 `uncertain`，此时保留现场并使用相同操作 ID、完全相同的原因和新的当次批准执行 Recover：

```powershell
.\tools\remote_full_upgrade.ps1 -Action Recover -Target $Target `
  -SiteRoot $SiteRoot -OperationId $Operation -Reason $Reason -Approved
```

`recovery_failed` 时不得手工改指针或删除 journal、候选、备份、锁和审计。该工具不会定时检测更新或自动拉取，也不替代 B-04、B-07、B-08 的现场验收。

## 当前门禁

此工具不提供发布签名、站点审批、管理员交接、串口真机参数或备份恢复验收。因此当前部署的正式生产结论仍是 `BLOCKED`；不得把调试热修当作生产放行证据。

串口主机附加工具在把脚本写入 WSL `sh -s` 前会去除 UTF-8 BOM，并把 CRLF/CR 统一为 LF；Windows PowerShell 5.1 会把规范化文本显式编码成无 BOM 的 UTF-8 字节并直接写入子进程标准输入基础流。该修复只恢复身份匹配的稳定设备别名，不增加 GW 设备映射、不设置 `GW_SERIAL_PORTS`，也不发送任何 Modbus 报文。
