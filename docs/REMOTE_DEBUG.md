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

隧道仅监听本机 `127.0.0.1`。脚本使用公钥认证、转发失败即退出和 SSH keepalive，并记录 PID 以避免误停其他进程。`Health` 从容器内部执行 API/GW 就绪检查，因此无需放宽站点 ACL。

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

## 当前门禁

此工具不提供发布签名、站点审批、管理员交接、串口真机参数或备份恢复验收。因此当前部署的正式生产结论仍是 `BLOCKED`；不得把调试热修当作生产放行证据。
