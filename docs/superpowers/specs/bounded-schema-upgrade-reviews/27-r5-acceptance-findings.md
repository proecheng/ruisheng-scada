# R5 独立验收审查

审查输入：完整 `docs/superpowers/specs/spec-bounded-schema-upgrade.md` 及其 frontmatter 唯一 context 文档 `spec-bounded-schema-upgrade-context.md`；冻结 `implementation.diff` 的 SHA256 已核实为 `2e7f36c83bb86990a1cb2c64e815499a98f4fdbd02637550b224fd56407f5faa`。未读取既往审查报告。下列代码路径相对于 `C:/ProgramData/Ruisheng/publisher-build/bounded-schema-multidevice-20260908/`；升级器当前 SHA256 为 `e83a18f9e42c411459252551fd4e45ebcf31fc34945df539afd5614df2c5c5a4`，其冻结差异中全部 2765 行新侧内容/上下文与读取源码逐行一致。

## A1 — [P1] 未支持的 Python、WSL、Bash 执行宿主仍默认放行

位置：`tools/remote_full_upgrade/target-updater.ps1:1824`、`:1855`、`:2164`、`:2169`；消费结果的位置为 `:1655`。

R5 context 要求“脚本宿主仅在新增固定身份证明时允许，否则拒绝”，并保留普通无关原生任务正例。实现只拒绝 `wscript/cscript/mshta/rundll32`，其他 `.exe/.com` 即便是明确的解释器，也落到函数末尾的 `$false`（允许）；在 PowerShell 内递归判断这些程序同样如此。

将下列 Execute/Arguments 作为纯数据传入冻结源码提取的真实 `Test-UnprotectedStartupAction`，Windows PowerShell 5.1 与 PowerShell 7 均得到 `$false`：

| Execute | Arguments | 两环境结果 |
| --- | --- | --- |
| `python.exe` | `-c "import subprocess; subprocess.run(['docker.exe','start','ruisheng-api'])"` | 允许 |
| `wsl.exe` | `--distribution Ubuntu --exec sh -c "docker start ruisheng-api"` | 允许 |
| `bash.exe` | `-c "docker start ruisheng-api"` | 允许 |

同一探针中 `docker.exe` / `start ruisheng-api` 和 `cscript.exe` / `C:\Unknown\startup.vbs` 均被拒绝，`docker.exe` / `compose ps` 被允许。因此不是返回值理解错误，而是未支持宿主漏入原生程序默认分支。

触发条件：存在上述形式的任务，且对应解释器/WSL 分发及 Docker 访问能力存在。任务检查会通过，任务随后能在维护标记仍为 active 时启动业务容器，而其内容、参数执行语义和文件身份均未获证明。应对明确执行脚本/命令的宿主建立拒绝或有限证明规则，不能仅因扩展名为 `.exe` 将其归为无关原生程序。

证据边界：仅实际运行判定函数，所有样例始终只是字符串；没有启动 Python/WSL/Bash 样例、Docker、计划任务或业务服务，也未证明当前目标已经存在这些任务。

## A2 — [P1] Recover 外层环境/审计前置拒绝绕过跨进程停写清理

位置：`tools/remote_full_upgrade/target-updater.ps1:3533`、`:3630`、`:3631`、`:3651`；新增恢复分支位于 `:3729`、`:3741`，清理责任恢复位于 `:3274`，独立清理位于 `:3244`。

context 循环3要求新 Recover 的清理责任不能等待“权益、候选、环境或备份前置检查全部通过”，且日志失败不能遮蔽仍可独立执行的停写/停应用。实现中的 `Get-BoundedCleanupJournal` 和 `Record-BoundedFailure` 确实不依赖 `.env.prod` 或共享审计目录来证明操作、数据库及应用身份，但外层脚本在到达这些函数之前先检查 `.env.prod`，再检查共享审计目录和审计锁。任何一个检查抛错都会进入 `3651` 的 catch，释放锁、返回 `rejected` 并 `exit 0`；不会读取绑定 journal、恢复清理责任或尝试停写。

具体触发顺序：同操作已经进入 `application_start_intent`，两个原本可 LOGIN 的应用角色已恢复，业务容器已启动；旧进程在健康验收完成前死亡。维护标记、journal、环境备份、活动指针和真实容器/卷身份仍有效，两个维护锁可重新取得，但当前 `.env.prod` 缺失或其 ACL 检查失败，或共享审计目录/审计锁出现单独的 ACL/文件缺失故障。正确操作号与原因的新 Recover 在上述外层检查处立即退出，已解封角色和运行中应用保持原状。单独缺失环境文件/审计资产不妨碍通过已保存的保护材料及真实资产独立核验、重新建立停写保护。

应让有效 active 操作的失败清理能够覆盖这些外层拒绝：仍须先取得双锁并核实维护标记、journal 与真实资产，身份无法证明或失锁时照常拒绝；不得把必要的升级/解封前置检查变成清理前置条件。

证据边界：此项为完整入口的静态控制流证明，未通过修改任何文件/ACL或运行 Recover 故障测试制造该状态。现有跨进程集成测试在 `tests/integration/test_schema_upgrade_recovery.py:1149` 直接调用 `Invoke-BoundedRecovery` 并在 `:1150` 调用清理，因此不覆盖外层入口在这两个函数之前退出的情况。

## 验收与发布证据边界

以上两项是具体实现偏差。规格中仍未勾选的最终统一回归/独立审查、限定提交与签名构建、正式候选真实 API/GW/Web 启动、目标启动器安装、受控 Plan/Apply、现场采集验收，均属于明确保留的后续发布门禁；未将这些未完成步骤列为代码缺陷，也不据此宣称已经发布或部署通过。

本轮除双 PowerShell 的纯入口判断探针外，未执行测试、远程操作、Docker 操作、计划任务或被审查样例。静态检查及探针不替代正式候选/现场验收，也不构成全路径无缺陷证明。
