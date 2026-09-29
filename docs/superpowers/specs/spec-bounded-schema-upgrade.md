---
title: '多设备版本的有界数据库升级与恢复'
type: 'feature'
created: '2026-09-08'
status: 'done'
release_status: 'pending-local-commit-and-deployment'
spec_loop_iteration: 9
last_completed_review_iteration: 9
review_gate: 'r9-passed'
review_loop_limit_override: 'user-approved-2026-09-09'
baseline_commit: 'd839330a91cdf7cae3f4c30aff9396fb99a47120'
approved: '2026-09-08'
context:
  - 'docs/superpowers/specs/spec-bounded-schema-upgrade-context.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** 多设备功能要求数据库从 `0012_alarm_notification_runtime` 升至 `0013_serial_polling_profile`；现有升级器只能恢复程序，不能安全处理数据库已升级的失败。

**Approach:** 保留同版本路径，仅开放上述迁移边。先停写并验证备份可还原，再迁移；失败依据真实数据库状态恢复。

## Boundaries & Constraints

**Always:** 保留验签、权益、身份、双锁及审计；持久维护标记与应用角色停写保护跨重启有效。绑定迁移文件及镜像身份；恢复角色原状态。使用独立发布工作树，先隔离测试再部署，保留现有数据、配置和备份。

**Ask First:** 其他迁移边、生产数据库回灌或降级、密钥变更、推送、删除数据及未经批准的实物采集。

**Never:** 不删除版本门禁；不以旧备份覆盖新写入；不在0013上启动旧程序；不把模拟测试当作实物验收。

## I/O & Edge-Case Matrix

| 状态 | 行为 | 失败处理 |
|---|---|---|
| 其他迁移边或身份不符 | 停服前拒绝 | 保持原服务 |
| 停写或备份验证失败 | 禁止迁移 | 能证明安全才恢复旧版 |
| 迁移已终止，库仍0012 | 恢复旧程序 | 失败保留维护状态 |
| 库已0013 | 保留数据，恢复已验签新程序 | 可重试Recover |
| 超时、失锁、未知库状态 | 不猜测提交结果 | 保留阶段和停写保护 |

</frozen-after-approval>

## Code Map

- `tools/remote_full_upgrade/target-updater.ps1` — 门禁、备份、状态与恢复。
- `tools/remote_full_upgrade.ps1` — 计划、传输和安全结果。
- `tools/start_ruisheng_local.ps1` — 桌面启动保护。

## Tasks & Acceptance

**Execution:**

- [x] R9 `tools/remote_full_upgrade/target-updater.ps1`、`tests/tools/test_remote_full_upgrade.py`及必要集成测试 — 已拒绝forfiles各入口，双PS实际分类器/主guard通过；同11 Windows入口经现场核验仅更新9项固定摘要，工具测试仅修正计时起点。最终181工具和4项真实Recover/完整Apply通过、源码前后不变，50容器停止并保留。保留R8 collation、原900秒上限及迁移摘要；证据和夹具边界见R9报告，独立审查及发布仍待完成。

- [x] R8 `tools/remote_full_upgrade/target-updater.ps1`及必要工具/集成测试 — 证明迁移相关列使用审定默认字符比较规则，拒绝列/索引共同漂移以及隐式ICU比较规则改变而CHECK文本相同的0013。双PowerShell真实结构、非空Recover及完整Apply均有对应通过证据；工具179项通过，真实首轮5通过/1失败与原始完整Apply补测2通过分别保留，原偶发进程树报错唯一原因仍未证明。未改进程树保护、原900秒上限、迁移摘要、R7祖先保护及原43文件代码；进入独立审查，不提前放行发布。

- [x] R7 `tools/remote_full_upgrade/target-updater.ps1`及工具/集成测试 — 启动器和安装回执在消费前证明从叶文件到盘符根的完整路径保护；保留叶文件原有SYSTEM/Administrators要求、已核实祖先TrustedInstaller兼容与固定摘要。双PowerShell实际主guard正反例、179项完整工具和4项真实Recover/完整Apply全部通过；44容器停止并保留，源码前后摘要一致。不修改迁移/备份/续租/其他43文件代码，独立复审和部署尚未完成。

- [x] R6自检 `tools/remote_full_upgrade/target-updater.ps1`及工具测试 — 操作级续租已修复，最终177项工具和7项受影响真实Apply/Recover/失锁/快照回归全部通过；依据真实锁记录验证后续租，原Core Apply进程树错误与独立续租缺陷分开记录，不放宽进程树或测试超时门槛。
- [x] R6 `tools/remote_full_upgrade/target-updater.ps1`、`tests/tools/test_remote_full_upgrade.py` — 补齐宿主profile/AutoRun、嵌套stdin及未支持解释器边界，双PowerShell真实函数及171项完整工具通过；目标255项任务只读预检无新增Windows误拒绝，既有Docker辅助文件权限修正在已授权安装阶段执行。
- [x] R6 `tools/remote_full_upgrade/target-updater.ps1`、`tests/integration/test_schema_upgrade_recovery.py` — 同快照保存、回放并比较数据库级权限/连接限制/设置；双PowerShell非默认真实还原与漂移拒绝通过，第三轮矩阵前62项全通过。两处测试夹具失败及修订边界保留于R6报告，完整恢复矩阵继续执行。
- [x] R6 `tools/remote_full_upgrade/target-updater.ps1`及上述测试 — 完整Recover外层入口覆盖环境/审计单独故障的跨进程清理；最终统一源码双PowerShell各14类以及失锁、快照死亡回归通过，验证完成态、未知/未授权操作或身份漂移不误停；51容器停止/身份/无端口核验通过并保留。最终工具177项和真实7项边界详见R6报告。

- [x] R5 `tools/remote_full_upgrade/target-updater.ps1`、`tests/tools/test_remote_full_upgrade.py`、集成恢复测试 — 动态/实例成员、未知或省略扩展名脚本、未支持宿主均有界拒绝；固定系统rundll32及11入口精确身份证明消除Windows误拒绝。最终17项定向、169项完整工具及双PowerShell真实Recover2项（每项4类）通过；26容器全部停止/身份/无端口核对通过并保留。进入独立复审，不代表发布部署通过。

- [x] R4 `tools/remote_full_upgrade/target-updater.ps1`、`tests/tools/test_remote_full_upgrade.py`、必要的`tests/integration/test_schema_upgrade_recovery.py` — 补齐未知脚本身份/内容证明、环境变量包装和PowerShell隐式命令；实际目标255任务预检发现的6项Windows误拒绝已修正。首版163工具+5真实、最终165工具+2真实wrapper通过，原版与补充修正版证据分别记录。进入独立复审，不代表发布或目标入口安装通过。
- [x] `tools/remote_full_upgrade/target-updater.ps1` — 实现迁移白名单、停写、隔离还原验证及可重放恢复。
- [x] `tools/remote_full_upgrade.ps1` — 对齐Plan/Apply判断，展示恢复阶段而不泄密。
- [x] `tools/start_ruisheng_local.ps1`、`tools/remote_maintenance.ps1`、`tools/remote_hotfix_deploy.ps1`、`tools/remote_admin_bootstrap.ps1` — 实现维护中旁路拦截及已安装入口核验逻辑。本地入口及ACL回归已通过；不代表目标安装核验已完成。
- [x] `tests/tools/test_remote_full_upgrade.py`、`tests/tools/test_desktop_launcher.py`、`tests/tools/test_remote_operations.py` — 实现矩阵及断电状态、锁过期、重复恢复测试；最终升级器108项及独立发布组合维护入口165项通过。
- [x] `tests/integration/test_schema_upgrade_recovery.py` — 已实现真实Timescale备份、迁移和故障后数据保留测试，最终统一版本38项通过；集成替代边界详见实施记录。
- [x] `docs/REMOTE_DEBUG.md`、`docs/reports/2026-09-08-bounded-schema-upgrade.md` — 已记录恢复方法、当前本地证据及未完成的发布/现场验收；部署证据在下列阶段追加。
- [x] R1 `tools/remote_full_upgrade/target-updater.ps1`、`tests/tools/test_remote_full_upgrade.py` — 按context审查修订补齐受控Docker进程树、任务入口及精确结构检查；修复停写顺序、嵌套恢复状态和独立失败清理，修订工具136项及真实结构定向通过。发布组合统一回归和新一轮独立审查仍未完成。
- [x] R2 `tools/remote_full_upgrade/target-updater.ps1`、`tests/integration/test_schema_upgrade_recovery.py` — 快照生命周期、Recover辅助资产收敛、中断/语义漂移/连续失败已修订；最终生产与测试源码完整395项组合已执行，394通过，另1项PS5长测试路径失败经同源码短目录补测通过，原失败保留。
- [x] R3 `tools/remote_full_upgrade/target-updater.ps1`、`tests/tools/test_remote_full_upgrade.py`、`tests/integration/test_schema_upgrade_recovery.py` — 跨进程清理责任、包装命令解析、Recover入口复核及函数/触发器还原证明已修订并取得同版本通过证据；主组合与单项补测分别记录，不冒称单次395/395全绿。三路复审另行进行。
- [x] R3自检 `tools/remote_full_upgrade/target-updater.ps1`及上述工具/集成测试 — 有界等待未知连接自然退出、持续连接或失锁拒绝、独立保留Apply初始审计及审计失败仍恢复、PowerShell5.1角色JSON数组兼容均已实现；156项工具及10项真实定向通过，包含双PowerShell完整Apply及原LOGIN布尔值保留。395项统一组合另在运行，不据定向通过放行发布；原偶发回退唯一根因仍未证明。

**Release & Deployment Verification (not completed by the local implementation checkboxes):**

- [x] 统一版本最终回归及独立代码审查通过，限定发布工作树内容核对通过；R9完整提交检查覆盖126个baseline范围文件，未改动内容，113个文件形成相对发布HEAD的限定暂存差异。
- [ ] 创建限定本地提交，重新构建并验证签名候选；不推送。
- [ ] 用本次正式候选进行隔离的真实API/GW/Web启动及健康验收；不以休眠进程或替换健康检查代替。
- [ ] 目标状态预检、受保护启动器安装及实际启动入口核验。
- [ ] 受控Plan/Apply、备份实际还原、0013及五服务身份/健康/审计/数据保留验收。
- [ ] 记录现场点位验收范围；仅已证实1号设备，实物有界读取不绕过现有诊断门禁。

**Acceptance Criteria:**

- Given 原版健康，when Plan或门禁拒绝，then 不改变目标运行状态。
- Given 新程序已写入，when 启动验收失败后Recover，then 新配置、地址复用记录和历史数据均保留。
- Given 进程中断且锁已过期，when 重启或点击桌面图标，then 不启动旧写入者，不自动解除保护。
- Given 获准候选通过测试，when 目标升级完成，then 数据库0013、五服务健康、身份和审计一致；现场采集另行验收。
- Given 首个容器尚在停止，when 进程中断，then 数据库角色停写已持久建立，保留恢复材料。
- Given 应用已启动且健康失败，when 二次恢复前置拒绝或journal/审计落盘失败，then 仍持锁时独立尝试经身份核验的停写清理，不因日志失败或状态清零跳过。
- Given Docker命令超时或父进程退出，when 决定恢复，then 所属进程树已经收容且停止；后台容器操作结果不明时保留维护状态。
- Given 结构仍含原关键词但语义已变，when 校验0012/0013，then 拒绝；同操作还原辅助资产在恢复完成前核验并停止，保留数据。
- Given 应用已解封但未验收且旧进程死亡，when 新Recover在权益、候选或备份前置拒绝，then 从受保护操作状态恢复清理责任，独立核验锁和真实资产后尝试停写/停应用；不误停已完成操作或身份不符资产。
- Given 恢复材料存在，when 已安装入口/任务/收据漂移或实际还原的函数、权限、触发器语义不符，then 不解封、不完成维护，保留恢复材料。

## Spec Change Log

- 2026-09-10 18:50，R9独立审查通过：Blind两项、Edge四项去重为四项defer（三项重复、一项原多设备模板创建问题新增到清单）；两个独立Acceptance运行均无确认的本次验收违例。全部116冻结文件摘要保持，原记录与覆盖归档见[45号分类](bounded-schema-upgrade-reviews/45-r9-classification.md)。无需循环10或修改43文件；提交钩子、签名构建、真实应用和目标部署仍待执行。

- 2026-09-10 17:29，R9自检完成：最终181工具及双PS真实Recover/完整Apply4项全部通过，6个操作关联50容器停止、身份及无端口核验通过并保留。两树43文件保持批准范围；原失败与计时诊断仍分别记录。冻结完整差异进入三路独立审查，不据隔离夹具通过标记真实应用、签名发布或目标验收完成。

- 2026-09-10，R9部署自检：目标Windows已更新，原固定宿主与11 DLL中9摘要变化导致全部11系统入口拒绝。逐项只读核实有效微软签名/保护路径/无侧载及原任务参数，再用9个明确摘要仅内存提案确认255任务无新误拒绝。非冻结context允许在本次审查前重定同11入口精确身份，仍不自动刷新或增加入口、不改发布信任锚。R9工具初版180通过/1计时预算断言失败，原记录保留，诊断中；尚未完成最终回归/审查/部署。

- 2026-09-10，循环9局部修订：R8 Acceptance确认forfiles /C启动入口漏检，主线程双PS真实分类器红阶段和仅内存修正绿阶段复现。非冻结context补充该命令宿主拒绝与正常入口控制；KEEP R8比较规则证明及所有已审定数据库/进程/恢复逻辑、冻结意图、baseline、迁移摘要和43文件代码。R8 Blind仅重复既有defer，Edge无有效结论且重试服务过载，未将不完整审查标为通过；见[39号记录](bounded-schema-upgrade-reviews/39-r8-classification.md)。

- 2026-09-10，循环8局部修订：R7 Acceptance提出隐式collation缺口；主线程精确镜像实测表明仅ALTER会被现有deparser比较拒绝，但按原SQL重建CHECK后双PS错误放行且POINT_GROUPS可写入。非冻结context新增迁移相关列默认collation证明与真实非空恢复测试要求；冻结意图、原baseline、迁移摘要及43文件代码保持。KEEP全部R7祖先保护及R6备份/属性/续租与恢复逻辑，仅重推导结构违例块；完整分类与正反证见[33号记录](bounded-schema-upgrade-reviews/33-r7-classification.md)。

- 2026-09-10 13:02，R7自检完成：固定发布树179/179工具及双PowerShell真实矩阵4/4通过，44个本轮容器停止、身份及无端口核验通过并保留。冻结差异后进入第7轮三路独立审查，不据测试通过提前标记提交、候选或部署完成；细节及源码/证据摘要见R7报告。

- 2026-09-10，循环7局部修订：R6 blind报告的启动器祖先保护遗漏经主线程双PowerShell真实ACL/目录链接夹具复现。旧主入口允许4类危险祖先权限与junction，而已有辅助身份函数全部拒绝；正常保护及仅创建无关子项均允许。非冻结context明确主启动器/回执也必须做整条路径证明，复用既有保护逻辑。KEEP：冻结意图、原baseline、迁移摘要、叶文件SYSTEM/Administrators要求、Windows兼容、停写/恢复/续租/备份及已批准多设备代码；仅重推导此违例块，不撤销整树。R6最终177+7项保留原版本边界，R7须补实际函数及受影响恢复验收。原发布树继续冻结，待R6三路原件收齐后同步。

- 2026-09-09 23:36，R6自检完成：最终冻结源码工具177/177、受影响真实矩阵7/7通过，51个独有测试容器停止并保留；旧版数据库104项由80通过及24补测构成，保持原失败与源码边界。83个限定发布文件实际提交钩子通过、内容摘要未变。进入新一轮独立审查，发布/部署步骤尚未完成。

- 2026-09-09，R6发布文档准备：实际提交门禁要求共享模型在提交当天登记CHANGELOG。仅在原43文件中的CHANGELOG追加当天发布准备条目，保留原条目全文及模型语义，SHARED_SCHEMA_VERSION仍20260415；另外42文件两树均与批准快照逐字节相同。此必要文档追加不改变冻结意图或24号KEEP的代码行为。最终工具177项与旧版数据库104项证据分别记录，最终7项受影响真实矩阵仍在运行；不提前标记发布审查、构建或目标验收完成。

- 2026-09-09，循环6恢复实施：用户明确回复“同意”，解除本次五轮审查上限并批准24号三组修复方案。非冻结context细化真实宿主与内部AST、数据库级属性及完整Recover入口验证，新增R6执行项；冻结意图、原baseline、批准迁移摘要及43个多设备文件保持。KEEP沿用24号记录及既有停写/快照/身份/Windows兼容证明，只局部重推导5项bad_spec；不把既有测试当成R6通过，不再因本次循环计数上限重复审批。

- 2026-09-09，循环5三路复审完成：确认宿主profile/AutoRun、嵌套stdin、未支持解释器、数据库级还原属性、Recover外层前置清理5项bad_spec；2项旧问题维持defer。双PowerShell真实判断探针确认9类输入漏检，6类控制符合预期；备份/Recover问题为主线程静态复核，未制造现场故障。规则要求下一循环计数由5到6后暂停，因此本次只记录分类、KEEP和三组具体修复方案，尚未重新实施。冻结意图、baseline、迁移摘要和发布源码保持不变。详见[24号审查与修复方案](bounded-schema-upgrade-reviews/24-r5-classification.md)；等待用户对解除本次循环上限的决定，原部署授权仍有效。

- 2026-09-09，循环5自检：目标255任务中新增11项Windows rundll32维护任务误拒绝，context补充固定系统宿主/DLL摘要、精确导出参数、任务工作目录与二进制保护规则。KEEP脚本256KiB限制和未知/动态脚本拒绝，仅闭集二进制使用固定2MiB边界；裸DLL不推广为任意搜索放行，嵌套调用拒绝。目标只读证据核实全部系统文件签名/祖先保护及无重定向；当前未更改目标。原15项定向证据仅覆盖初版fc1e6eef源码，自检修订后重跑，不冒称已通过。

- 2026-09-09，循环4独立验收：确认动态 `ScriptBlock.Create(...).Invoke()`、省略扩展名相对 PowerShell 脚本及 `cscript`/`wscript`/`mshta` 宿主可落入默认安全分支，违反入口必须可证明且维护期间不得旁路启动的既有要求。非冻结部分新增 R5 闭集入口约束与双 PowerShell 实际函数回归要求：无法静态证明执行语义的成员调用、动态脚本和未受支持宿主一律拒绝；仅保留已核验固定辅助脚本、普通无关原生任务、只读命令正例。KEEP：迁移摘要、停写/恢复/审计、受保护启动器、43个多设备文件及 R4 对普通 Windows 任务的兼容修正；不执行审查样例、不扩大信任到任意脚本或文件名。已知坏状态是未受保护任务可在维护标记下启动业务容器。

- 2026-09-09，循环4自检补充：目标真实函数预检发现6项普通Windows任务误拒绝，按context细化路径槽与命令文本边界，并加入已核实受保护Windows热补丁脚本的固定身份例外。4项红阶段复现后13项定向、最终165项工具和2项真实wrapper通过；首版5项真实结果保留其源码边界。KEEP：未知脚本/动态命令拒绝、文件及祖先保护、迁移/状态/备份主体、43个多设备文件不变。目标Docker辅助文件可写权限正确拒绝，部署前以原件/摘要/ACL备份及既有安装规则修正，不降低验证门槛。仍属step-03自检，循环计数保持4。

- 2026-09-09，审查循环4：三路R3复审确认未知脚本默认放行、环境变量命令入口、Windows PowerShell隐式命令3项bad_spec（后者两路重复）。在context细化脚本证明和宿主解析边界；禁止按文件名/Status参数信任陌生代码，不以全部拒绝可执行程序误伤正常任务。KEEP：43个多设备文件、冻结意图、baseline、迁移摘要、原状态机/停写/跨进程清理、快照还原/身份和受保护启动器内容不变，仅重推导违例入口块及必要受限辅助身份验证。审查服务故障已恢复；验收初稿已撤回，使用修正版。分类及主线程复现见[19号记录](bounded-schema-upgrade-reviews/19-r3-classification.md)，原R3测试证据不改写为R4通过。

- 2026-09-08，循环3自检兼容补充：新增双PowerShell审计测试发现5.1把角色JSON数组包装为单项，正式控制端使用powershell.exe，构成停服前的部署阻断。主线程两环境复现并确认仅替换为既有JSON helper不足；增加显式角色数组展开及LOGIN布尔值保留验证，完整真实Apply覆盖5.1/7。不修改冻结意图、公共JSON helper、其他任务或迁移摘要。

- 2026-09-08，循环3自检补充：统一回归371/374通过，完整Apply一次安全回退且初始异常丢失；单项诊断通过但未稳定复现。真实pg_isready竞争实验37/100快照捕获短连接，补充非冻结实施细节的15秒内部等待预算、每轮及放行前核锁、最终0连接，以及独立初始失败审计。KEEP：所有冻结意图、迁移摘要、43个多设备文件及既有停写/恢复/身份保护不变；不忽略健康探测名、不杀未知连接、不以再次通过冒称原根因已确定。仍在step-03自检，审查循环计数保持3。

- 2026-09-08，审查循环3：第二轮三路复审及主线程复现确认新进程清理、CMD/PowerShell参数、Start-Process绑定、Recover入口重验和函数/触发器证明5项bad_spec，细化context，新增R3及故障AC；冻结意图、迁移摘要和baseline不变。KEEP详见本轮分类记录：保留43个多设备文件、已部署兼容、进程树收容、后台意图、精确引擎/卷/环境、同快照真实还原及原角色/重启策略，只重推导违例实现块。避免再次把进程内标志当作跨进程证明、只检查包装命令首段或将不完整指纹称为行为/权限保留。R2完整组合已因确认的阻断问题结束，未生成最终XML，不记录为通过。

- 2026-09-08，循环2验证补充：主线程纯内存复现后台exec及LOGIN命令超时后不保留意图；在context明确后台启动、解封与restart恢复均受完成证明约束，并明确未观察到快照启动不能凭0连接认定停止。冻结意图、迁移边、baseline及既有KEEP保持不变；132项工具中间通过不冒充此补充修正版通过。

- 2026-09-08，审查循环2：三路独立审查确认B3/B5/B6/B7/E1/A1/A2/A3，归为bad_spec（含未被实施细节防止的直接验收偏离）。补充context的进程生命周期、停写清理、辅助资产、快照期限和结构/入口证明，新增R1/R2及故障AC；冻结意图、批准迁移边和原baseline不变。KEEP：保留已通过的非空还原、原环境字节、双锁/失锁保护、精确引擎/数据卷/system_identifier、候选tag及image_id对齐、原角色和重启策略恢复；保留已部署兼容修复及其他工作区改动。审查前源码与证据已冻结，仅重推导发生违例的实现块，不整包撤销既有成果。避免再次出现“测试通过即等于发布通过”、日志失败跳过停写或父进程退出即认定后台操作终止。

## Design Notes

本规格仅补充旧签名升级规格的跨库版本例外，不改其冻结内容。备份实际还原到隔离环境，不自动还原生产库；细节及授权记录见context。

## Verification

2026-09-10 18:57：完整pre-commit覆盖126个限定文件通过（YAML/TOML无匹配文件而跳过），Ruff、格式、mypy、schema-version及其余适用检查均通过，源内容前后不变。证据tmp-test-logs/bounded-review-r9-iONeJS/precommit-initial，run.json SHA256 b30fc8dab22974f2159ef0fb4c025db3c9efcf70f7e569e34d38cfb732d69f5e。创建本地提交时仍执行原Git钩子；本条不预先标记提交、构建或部署完成。

2026-09-10 18:50 R9三路独立审查完成并通过，服务错误后的独立Acceptance重试亦完成；未确认新的本次阻断问题。详见[45号分类](bounded-schema-upgrade-reviews/45-r9-classification.md)。下文测试数字、原失败及替代边界保持；发布步骤不能由审查通过自动勾选。

2026-09-10 17:29 R9：最终工具181/181、受影响真实矩阵4/4通过，源码前后摘要相同；50容器停止并保留。当前仅进入独立审查，提交、签名候选、真实API/GW/Web隔离验收、目标部署和B11均未执行。详细源码、run/XML/资产摘要、原失败和替代边界见[循环9记录](../../reports/2026-09-10-bounded-schema-upgrade-r9.md)。

2026-09-10 15:06 R8自检完成：179项工具通过；真实首轮5通过、1项Windows PowerShell完整Apply先触发docker_process_tree_incomplete后恢复超时，Core完整Apply未执行。35容器停止/身份/无端口核验通过。原源码增加观察的完整Apply诊断1项通过、13容器保留；随后无诊断注入的双PowerShell原始完整Apply补测2项通过，24容器停止并保留。7个所需真实用例均有同源码通过证据，原首轮失败及唯一原因未明的风险仍保留，不声称单次全绿或已修复偶发问题。没有放宽进程树或原900秒上限；进入独立审查，提交/构建/部署均未完成。各版证据见[循环8记录](../../reports/2026-09-10-bounded-schema-upgrade-r8.md)。

2026-09-10 R7三路审查已完成：两项重复既有defer、1项本次bad_spec，经主线程真实隔离双PS复现确认，进入R8。179/179及4/4保留为R7结果，不能作为R8源码放行依据；尚未提交、构建或部署。详见[33号记录](bounded-schema-upgrade-reviews/33-r7-classification.md)。

2026-09-10 R7：生产升级器d7ecf14c...、工具测试c98c3e46...、集成83cdc5a4...；定向4项、完整工具179项、双PowerShell真实Recover/完整Apply4项通过，测试前后摘要相同。44个容器停止并保留，三路独立复审正在进行；本地提交、签名候选、真实应用隔离验收和目标部署尚未完成。完整证据见[循环7记录](../../reports/2026-09-10-bounded-schema-upgrade-r7.md)。下文均保留历史版本和阶段边界。

2026-09-09 R5：最终升级器e83a18f9...、工具测试60d41151...、集成53b12213...，169工具及2项双PowerShell实际恢复通过，源码前后匹配；26容器全部停止并保留。目标255任务只读检查中11项Windows误拒绝已消除，仅Docker辅助文件权限待安装时收紧；14:12旧候选/0012及五服务健康。三路独立审查已完成但未通过，发现5项bad_spec并触发循环上限；代码保持R5冻结版本，第6轮尚未实施。详细证据、替代边界和未完成发布步骤见[循环5记录](../../reports/2026-09-09-bounded-schema-upgrade-r5.md)及[24号分类](bounded-schema-upgrade-reviews/24-r5-classification.md)。

2026-09-09：R4修订与自检完成。最终生产升级器854eab2f...、工具测试c452ff61...、集成fe6e83ef...；165项完整工具及2项实际wrapper恢复通过。首版52c6f73f...的5项真实回归通过但不冒称最终源码5项重跑。首版57及最终24个容器全部停止/身份/无端口检查通过并保留；43个多设备文件全匹配。目标255动作只读核验已消除6项Windows误拒绝，Docker辅助文件权限待受控安装时修正。完整证据与限制见[循环4记录](../../reports/2026-09-09-bounded-schema-upgrade-r4.md)。R4独立审查开始；发布阶段均未完成。R3“无发现”验收初稿已撤回，以下保留历史范围。

前两轮三路独立审查已完成，循环3修订和自检现已结束，进入第三轮复审。最终395项主组合394通过、1项PS5长测试路径失败；同一生产与测试源码仅缩短取证目录后该项通过，全部395项均有对应通过证据，但不是单次全绿。主组合204个及补测13个容器均停止，备份和日志保留。三个固定文件摘要、详细限制与历史记录见[实施记录](../../reports/2026-09-08-bounded-schema-upgrade.md)。以下原始结果不覆盖R1/R2/R3修订；签名、真实应用及目标部署仍未验收：

- `uv run pytest -q tests/tools/test_remote_full_upgrade.py tests/tools/test_desktop_launcher.py tests/tools/test_remote_operations.py` — 契约及双PowerShell回归通过。
- `uv run pytest -q tests/integration/test_schema_upgrade_recovery.py -m integration` — 自建隔离容器38项通过，1637.59秒；不证明发布验签、真实应用健康或现场采集通过。

## Suggested Review Order

本地实施与代码审查已完成；发布、真实应用和目标验收仍按上方清单逐项完成。

**升级入口与迁移边界**

- 先看完整升级入口，理解停写到提交的顺序。
  [target-updater.ps1:3509](../../../tools/remote_full_upgrade/target-updater.ps1#L3509)

- 只读计划先核验兼容性与资源。
  [remote_full_upgrade.ps1:672](../../../tools/remote_full_upgrade.ps1#L672)

- 唯一批准迁移保持既有读取行为。
  [20260907_0013_serial_polling_profile.py:20](../../../alembic/versions/20260907_0013_serial_polling_profile.py#L20)

**备份与恢复**

- 真实还原验证先于生产迁移。
  [target-updater.ps1:1161](../../../tools/remote_full_upgrade/target-updater.ps1#L1161)

- 按实际数据库状态恢复并保留新数据。
  [target-updater.ps1:3377](../../../tools/remote_full_upgrade/target-updater.ps1#L3377)

- 精确证明结构和字符比较规则。
  [target-updater.ps1:2204](../../../tools/remote_full_upgrade/target-updater.ps1#L2204)

**启动保护**

- 消费回执前证明文件和完整祖先保护。
  [target-updater.ps1:1624](../../../tools/remote_full_upgrade/target-updater.ps1#L1624)

- 分析实际任务入口并拒绝未支持命令宿主。
  [target-updater.ps1:1801](../../../tools/remote_full_upgrade/target-updater.ps1#L1801)

- 桌面启动先检查持久维护标记。
  [start_ruisheng_local.ps1:207](../../../tools/start_ruisheng_local.ps1#L207)

**验证证据**

- 双PowerShell调用真实分类器和主guard。
  [test_remote_full_upgrade.py:2891](../../../tests/tools/test_remote_full_upgrade.py#L2891)

- 真实数据库验证备份、迁移、提交及原角色恢复。
  [test_schema_upgrade_recovery.py:1490](../../../tests/integration/test_schema_upgrade_recovery.py#L1490)
