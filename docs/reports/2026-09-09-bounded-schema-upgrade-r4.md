# 有界升级循环4：启动入口修复与验证

日期：2026-09-09。上一轮三路审查确认3项入口解析问题；首版R4的163项工具及5项真实数据库测试通过。目标只读预检又发现Windows任务误拒绝，补充修正已完成13项定向、165项完整工具及双PowerShell真实恢复复验。最终差异进入R4独立审查，尚未提交、签名构建、上传、切换目标服务或进行实物采集。

## 已完成

原始三路报告和去重分类见规格目录的16、17、18、19号记录。验收代理的“无发现”初稿已撤回，最终报告确认省略-Command的违例；不使用旧初稿放行。

修订只涉及：

- `tools/remote_full_upgrade/target-updater.ps1`：增加固定辅助脚本路径/摘要/参数/文件与祖先权限检查；拒绝未知或同名伪Status脚本。环境展开仅支持已核对的ComSpec/SystemRoot/windir；正确消费宿主参数，覆盖Windows PowerShell隐式命令、pwsh默认文件及CMD省略脚本扩展名。
- `tests/tools/test_remote_full_upgrade.py`：增加真实函数双PowerShell正反例、真实新建文件/ACL/链接证明及无害CLI对照。
- `tests/integration/test_schema_upgrade_recovery.py`：增加双PowerShell的 `test_real_recovery_wrapper_tasks_prevent_role_release`，使用真实入口判断决定拒绝，并检查0013、历史数据、角色停写、应用停止和维护状态。

发布工作树已精确同步上述三文件且摘要一致。43个已审定多设备文件与原快照完全一致；批准0013迁移摘要、状态机、数据库/快照/恢复主体、启动器和控制端均未改变。

| 文件 | SHA256 |
|---|---|
| target-updater.ps1 | 52c6f73f543d6a8fcee7ab9c807cce157d66499d71ca07a21c3033edc5a5b957 |
| test_remote_full_upgrade.py | 0b95abb4d839b40d3ced5eb54e4c569d63125c5c8c83f72a320b86eb53850913 |
| test_schema_upgrade_recovery.py | fe6e83ef897a7cd394bac43f7dc1fd1190740595682b9d22ba2230f046debfb4 |

## 工具验证

先在旧源码复现双PowerShell新增回归失败，修复后启动相关定向11项通过（24.49秒）。

完整工具组：163 passed、0 failures/errors/skips；控制台371.54秒，JUnit371.172秒。XML：`D:\江苏润盛\tmp-test-logs\bounded-r4-tools-20260909-1040.xml`。主线程重新读取XML确认计数；差异检查通过。

本地测试账号非管理员。辅助身份正例只在装载测试函数时将固定路径、摘要和允许SID映射到独有夹具，实际文件/祖先ACL判断原样执行；另验证未修改的生产策略拒绝当前账号所有者，并固定生产常量契约。这不证明目标真实安装ACL已通过，部署前仍需核验。12个独有 `C:\rs-r4-startup-*` 夹具保留，含6个中间失败和6个通过；没有删除或改变既有系统目录ACL。

## 实际数据库回归（首版R4完成）

运行器：`D:\江苏润盛\tmp-test-logs\run-bounded-r4-integration.py`。

证据目录：`D:\江苏润盛\tmp-test-logs\bounded-r4-integration-813babb648f34fbfbc9851aac219b41c`。

独有夹具根：`D:\bounded-release-r2-acl-fixtures-20260908\r4-122c0ca6`。

固定选择5项：双PowerShell完整Apply、原guard漂移、双PowerShell新增wrapper恢复拒绝。运行前后固定三文件SHA256和批准迁移摘要；只重定向ROOT/tmp-test-logs取证目录以避开已确认的PowerShell5.1长路径限制，其他源码、SQL、断言和迁移输入保持。未改数据库主体，因此不为重复计数重跑原3小时矩阵；新旧证据分别标注。

新增wrapper用例只替换已安装任务的环境边界，在其中调用真实分类器；不会执行危险示例或任务。原完整Apply仍使用测试安装回执边界和休眠应用/替代健康函数，只证明真实数据库升级/恢复链，不冒充正式候选的实际API/GW/Web验收。

首版固定R4源码5/5通过，控制台2963.58秒/JUnit2963.576秒，0失败/错误/跳过；运行器确认结束后三文件和迁移摘要未变。主线程重新读取results.xml及run.json。资产检查共57个容器、7个操作，全部停止、标签/镜像身份核验通过、无主机端口发布，容器/卷/备份/日志保留；证据同目录owned-assets.json。本次通过覆盖上表52c6f73f...源码，不直接声称覆盖随后Windows任务误拒绝补充修正。

## 目标入口自检补充（11:28）

11:15在目标内存装载上表固定源码的真实分类/保护函数，255个任务动作中有7个拒绝。六项是本轮实现误拒绝：OneDrive的localappdata路径3项、cleanmgr普通参数systemdrive、Windows Media Player的ProgramFiles路径和Windows热补丁脚本。另1项Docker启动辅助文件归lenovo且该用户有FullControl，属于正确拒绝。串口辅助脚本和祖先保护已通过。保留完整只读证据 `tmp-test-logs/bounded-r4-target-entrypoints-491c784d90b44ea4a7d0c9ed4d564a9d.json`；未执行任务、修改ACL、服务或USB。

仍在step-03自检，循环计数不变。context补明原生程序路径槽/参数与解释器命令的边界；不使用其他任务账号的私人环境值，不按任务名跳过。固定Windows热补丁脚本完整内容只管理hpatchmon/VBS，其固定路径、SHA256 `5347ad556fbc6bb1faf408b466b6bd10180b9299923325794e9e5afb0118408f`、零实参及文件/全部祖先权限必须共同满足。未放宽既有两辅助脚本策略。

根工作区补充修复先复现4项预期失败，最终13项定向通过，控制台36.99秒/JUnit36.487秒，0失败/错误/跳过，XML `tmp-test-logs/bounded-r4-windows-native-final-20260909.xml`。只改变升级器与工具测试，最终摘要分别为 `854eab2f069904a6642c7975146b8b2fa3a5a34a97f5925c00fa61c564778db8`、`c452ff613e8205725b0cc601b159b1a9ba9500d5cda1f330248cba8395e5f6c3`；集成文件未变。新增6个独有ACL夹具保留。发布树暂未同步，避免改变仍在运行的上表5项测试源码。

11:27:56目标用补充修复的真实函数再次分类255个动作，六项误拒绝全部消除，仅剩正确拒绝的Docker辅助文件权限。证据 `tmp-test-logs/bounded-r4-target-entrypoints-f76fe8cfee8c4b358c60a72b8847af8e.json`；不等于guard安装验收通过。已准备限定提交/实际应用验收后使用既有安装器保护入口的本地包装，语法通过但未执行，届时备份原辅助文件、摘要和SDDL后仅收紧该文件权限，其他共享审计/任务/USB不改。完整工具回归及受影响真实恢复仍待补充修正版验证。

11:39固定首版回归结束且资产检查通过后，发布树按精确前后摘要同步两处补充修正，集成文件保持原摘要；43个多设备文件再次全部匹配快照。最终165项工具全部通过，0失败/错误/跳过，控制台392.97秒/JUnit392.499秒，主线程已读取XML `tmp-test-logs/bounded-r4-tools-final-20260909-1139.xml`。

11:45对补充变动所影响的双PowerShell真实wrapper恢复用例复验已启动。证据 `tmp-test-logs/bounded-r4-integration-f8eaff546c3e4aff95e8782d337c2863`，独有夹具 `D:\bounded-release-r2-acl-fixtures-20260908\r4-3c876e02`。运行器增加明确的wrappers选择，只缩减收集范围，生产/测试源码、断言、SQL未改；仍固定前后三文件/迁移摘要，仅ROOT/tmp-test-logs路径重定向。上面完整Apply及guard漂移结果保留其首版R4边界，不重复未受影响的数据库主体矩阵，不冒称这两项是另一次完整5项组合。

最终双PowerShell真实wrapper恢复复验2/2通过，0失败/错误/跳过，1307.21秒；run.json确认三文件/迁移摘要运行后仍一致。主线程重读XML确认结果；本次24个容器、2个操作全部停止，标签/镜像身份及无主机端口检查通过，存储、备份、日志保留，证据同目录owned-assets.json。首版57容器和最终24容器各自单列，不修改历史R3资产计数。局部自检结束，随后冻结完整发布差异进入R4三路独立审查。

## 目标与发布边界

今天10:01:54 +08:00目标观察仍为旧候选deploy-20260907.1和数据库0012；API/GW内部健康ready，四张设备/采集表计数0。两条辅助任务内容已只读核对；没有执行它们或改变现场配置。细节见19号分类记录。

下一步：冻结完整发布差异、三路独立R4复审，再依既有授权进行本地限定提交、签名构建、真实应用隔离验收、目标安装入口验证和Plan/Apply。现场仅1号有物理证据，不将2～5号模拟结论写成实物验收，不绕过B11的80字节上限。

R3原主组合394/395及同源码短目录补测1/1保留历史范围，不能改写为R4全量通过。
