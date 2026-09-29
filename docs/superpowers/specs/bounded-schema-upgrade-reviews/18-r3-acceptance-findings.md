# R3 独立验收审查报告

## 结论

本报告撤回并替代此前“未发现已证实的实现验收违例”的错误初稿。收尾时遗漏了一条已经取得的纯内存解析证据，现据该证据报告 **1 项 P1 已证实验收违例**，不能据本轮审查放行发布。审查未访问目标机、Docker/数据库或其他审查结果，也未修改项目或冻结差异。

## A1 — P1：省略 `-Command` 的 Windows PowerShell 启动任务被静默放行

- **涉及 AC：** 第 10 条（恢复材料存在时，已安装入口/任务/收据漂移不得解封或完成维护）；context 第 71、83、84 行要求按实际包装语义核验业务启动入口，在 Recover 解封前拒绝新增旁路任务。也影响第 3 条所要求的重启/入口不能启动旧写入者。
- **文件位置：** 发布树 `tools/remote_full_upgrade/target-updater.ps1:1775` 进入 PowerShell 参数扫描，只有显式 `-File`/`-Command`/`-EncodedCommand`（及其缩写）触发递归检查；第 1906 行对未识别形式返回 `$false`。`Assert-InstalledMaintenanceGuards:1653` 据此接受任务，`Start-BoundedApplications:2639` 则依赖该结果在随后恢复角色 LOGIN 和启动应用前证明入口受保护。
- **触发条件：** 已安装任务的 Execute 为 `powershell.exe`，Arguments 为 `-NoLogo -NoProfile -NonInteractive docker.exe start ruisheng-api`（或 `docker.exe compose up -d`）。Windows PowerShell 5.1 支持省略 `-Command`，会将剩余参数作为命令执行。任务未使用受保护启动器，但扫描器逐个跳过这些参数并返回“不是未受保护启动”。无需任务内容混淆、变量拼接或特制引号。
- **后果：** Apply/Recover 可以在存在可启动业务容器的旁路任务时通过入口核验。该任务仍能在维护中直接启动服务；例如迁移已提交到 0013、`Start-BoundedApplications:2633` 尚未重建 API 的间隙，`docker.exe start ruisheng-api` 可启动仍保留的旧 API 容器，违反“不得在 0013 上启动旧程序”。数据库 NOLOGIN 在部分阶段仍可限制写入，但不能使“存在新增旁路启动任务仍允许解封”符合第 10 条 AC。
- **可复核证据：** 只从冻结发布树 AST 提取真实 `Test-UnprotectedStartupAction`，在内存注册函数，给它传入上述简单参数的 `ParsedArguments`；仅把进程 Job 类型初始化替换为空函数（本次不经过参数拆分，未替换判断逻辑）。PowerShell 7.6.4 对两个示例均返回 `[false,false]`；独立 Windows PowerShell 5.1.26100.9168 进程返回 `{"version":"5.1.26100.9168","rejected":false}`。另执行无害的 `C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe -NoLogo -NoProfile -NonInteractive Write-Output bounded_wrapper_probe`，实际输出 `bounded_wrapper_probe`，证明省略 `-Command` 的 CLI 形式会执行命令。未执行示例 Docker 命令、未创建/运行任务。
- **测试边界：** `tests/tools/test_remote_full_upgrade.py:2510`、`:2537` 的入口矩阵覆盖显式 `-File`、`-f`、`-Command`、`-Co`、编码命令、CMD 复合命令和 Start-Process；未覆盖这个合法的默认命令模式。既有 394/395 + 1/1 证据因此不能排除此违例。
- **修复方向：** 按 Windows PowerShell 的实际默认命令规则处理省略 `-Command` 的尾部，或在无法证明相关业务包装安全时拒绝。添加双 PowerShell 调用真实解析函数的正反例；不应执行真实 Docker 命令来做此门禁回归。
- **证据限制：** 本结论已证明合法 CLI 语义与门禁返回值之间的违例；未在真实目标计划任务或容器上复现其运行后果。未声明目标当前实际存在该任务。

## 覆盖与证据

- 冻结差异 `implementation.diff` 的 SHA256 与提示给出的 `e565e348793ad14d3c4028d4ce890d140f28ea96f24c754b1194a3c93d9348c0` 一致；关键升级器、工具测试、集成测试、规格和 context 五文件均重新核对摘要，与 manifest 一致。`manifest.json` 的 `reverseCheckPassed` 是输入记录，未将其当作本轮重新执行的逆向校验。
- 停写顺序和持久阶段：`tools/remote_full_upgrade/target-updater.ps1:3195-3228` 先写 journal/维护标记、建立角色 NOLOGIN、停止应用，再执行备份/迁移；`Set-ApplicationRoleFence` 在 `:2036-2048` 验证连接归零和两个角色均为 NOLOGIN。
- 跨进程清理责任：`Record-BoundedFailure`/`Invoke-BoundedRecovery` 在 `:2981-3014` 依据受保护 journal 和维护标记恢复责任，独立尝试停写、停应用、停止还原资产和快照；数据库身份漂移时不会执行 stale SQL。对应真实/工具测试位于 `tests/tools/test_remote_full_upgrade.py:2608-2862`、`tests/integration/test_schema_upgrade_recovery.py:1066-1182`。
- Docker 进程树与后台意图：`Invoke-DockerText` 在 `:709-783` 使用受控进程树并在超时/残留时停止确认；`docker_intent` 在 `:786-835` 持久化并在完成未知时保留。测试覆盖父子进程、后台意图和快照期限（`tests/tools/test_remote_full_upgrade.py:2435-2494, 2801-2838`）。
- 入口解析：`Test-UnprotectedStartupAction` 在 `:1663-1906` 解析 CMD 复合命令、PowerShell 缩写/AST、Start-Process 具名及位置参数；`tests/tools/test_remote_full_upgrade.py:2510-2604` 覆盖 docker.exe、重定向、引号、别名、`-f` 与 Start-Process 边界。省略 `-Command` 的缺口详见 A1。
- 迁移边和结构证明：`Assert-BoundedMigrationImage` 在 `:1981-2000` 固定迁移脚本摘要、revision/down_revision 和单 head；`Assert-BoundedSchema` 在 `:1916-1978` 比较列类型/默认值/非空、完整 CHECK、唯一索引键/谓词；函数、权限、触发器定义在 `Get-DatabaseFingerprintSql`（`:2192-2218`）中纳入还原指纹。真实反向测试见 `tests/integration/test_schema_upgrade_recovery.py:333-414, 1034-1066`。
- 快照与还原辅助资产：`New-BoundedDatabaseBackup`/`Stop-BoundedSnapshot` 在 `:2267-2388` 绑定同操作期限、身份和终止证明；`Test-BoundedBackupRestore`/`Stop-BoundedRestoreAssets` 在 `:2391-2569` 绑定镜像、网络、卷、token 和容器生命周期。定向生命周期测试位于 `tests/integration/test_schema_upgrade_recovery.py:1225-1266`。
- Recover 启动前入口复核：`Assert-BoundedMaintenanceGuards` 在 `:2954-2960` 比较首次收据摘要；`Start-BoundedApplications` 在 `:2622-2645` 于角色恢复/启动前调用该核验。入口漂移/任务漂移测试位于 `tests/integration/test_schema_upgrade_recovery.py:1182-1204`。

## 证据限制与未完成发布项

本轮完整读取规格及 context，分段审查冻结差异中的升级控制部分，并只读核实发布树的相关实现、测试和迁移文件；没有独立重跑 395 项组合，也没有重新进行所有 43 个已批准多设备业务文件的功能审查。上述覆盖条目说明检查范围，并不意味着对每个任意故障情形作出无缺陷保证。

规格明确的发布工作仍未完成：最终统一回归记录为主组合 394/395，PowerShell 5.1 长路径同源码短目录补测 1/1；不能表述为单次 395/395 全绿。签名构建、真实 API/GW/Web 启动健康、目标预检/安装入口核验、目标受控 Plan/Apply、目标备份还原和五服务身份/审计/数据保留验收均未完成，这些是后续发布验收，未另记为实现缺陷。只有 1 号设备有物理证据，2～5 号模拟不证明实物存在；本轮不建议绕过 B11 的 80 字节门禁。
