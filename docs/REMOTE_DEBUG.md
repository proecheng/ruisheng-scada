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

## 当前门禁

此工具不提供发布签名、站点审批、管理员交接、串口真机参数或备份恢复验收。因此当前部署的正式生产结论仍是 `BLOCKED`；不得把调试热修当作生产放行证据。
