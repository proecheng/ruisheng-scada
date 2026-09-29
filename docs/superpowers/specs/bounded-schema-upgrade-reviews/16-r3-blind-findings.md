# R3 独立盲审发现

- **[P1] `Test-UnprotectedStartupAction` 静默放行未知脚本包装，绕过计划任务保护。** 位置：`tools/remote_full_upgrade/target-updater.ps1:1778-1779` 将 PowerShell `-File` 参数递归当作可执行文件检查，最终在 `tools/remote_full_upgrade/target-updater.ps1:1906` 默认返回安全；任务检查调用处为该文件 1653–1656 行。示例：计划任务 `Execute=powershell.exe`、`Arguments=-File D:\Ruisheng\start-prod.ps1`，其中脚本执行 `docker compose up -d`；或 `Execute=cmd.exe`、`Arguments=/c D:\Ruisheng\start-prod.cmd` 包装同一启动命令。函数只识别 `start_ruisheng_local.ps1`、特定 `remote_*`、`entrypoint-migrate` 等固定文件名，未知 `.ps1/.bat/.cmd` 最终直接 `return $false`，既不核验包装文件也不拒绝未知结果。因此即使全部已安装 launcher/receipt 检查通过，这种遗留业务启动任务仍能在迁移维护期间重新启动或重建业务容器，破坏停服和容器身份稳定性；若包装包含迁移服务，还可能启动额外的高权限迁移。此处不声称目标现场已存在示例任务，也不将普通 API/GW 的 NOLOGIN 保护视为已被绕过；缺陷是本应拒绝的业务启动入口获得通过。差异内 `docs/superpowers/specs/spec-bounded-schema-upgrade-context.md:83` 明确要求“对于无法证明安全的相关业务启动包装不得静默放行”。应对相关未知脚本核验受控内容/摘要，无法证明时拒绝，并增加自定义脚本名及多层包装回归，同时保留明确非业务任务的正常行为。

## 阅读范围

- 完整阅读冻结输入 `implementation.diff`，覆盖逻辑行 1–16424（按 LF 分割为 16425 个元素，含末尾空行）。
- 文件大小 894550 字节；SHA-256 `e565e348793ad14d3c4028d4ce890d140f28ea96f24c754b1194a3c93d9348c0`。
- 差异包含 81 个文件；本轮补读先前未覆盖的 13200–16424 行（串口轮询单测、架构恢复集成测试、串口设备生命周期集成测试），并复核启动任务解析实现及其回归测试。
- 本报告依据差异内静态执行路径；没有运行被审查命令、项目代码、Docker、远程操作，也未读取其它项目文件。仅写入本报告。差异内已明确记录延期的容量、静默期、串口恢复、LX 计数和 ACL 问题不重复作为本轮新增发现。

## Diff 摘要

- 新增串口设备 API 合约、串口轮询器及其单元测试。
- 新增 schema 升级/恢复和串口设备生命周期集成测试。
- `target-updater.ps1` 增加计划任务启动包装解析、受控恢复与数据库结构验证逻辑；上述发现涉及其未知脚本分支。
- 完整差异还包含 0013 迁移、管理员引导、维护入口保护、前端串口配置及审查/规格文档；总计新增 13441 行、删除 414 行。
