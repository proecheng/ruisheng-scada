# 有界升级循环7：启动器完整路径保护

日期：2026-09-10。沿用已批准的限定提交、签名候选、目标安装升级与有界采集授权；不新增迁移边、推送、生产回灌/降级、密钥变更、OS重启或连续实物采集。

## 状态

R7局部修订及统一发布树回归已完成：179项工具测试、双PowerShell共4项真实数据库恢复/完整Apply全部通过，测试前后源码摘要一致。第7轮三路独立审查已完成，确认1项字符比较规则结构缺口，按原授权进入R8修复；尚未提交、构建或部署。R6最终177工具+7真实回归仍只对应R6源码。

原冻结输入、最终审查原件、去重分类、主线程双PS复现、KEEP及已知覆盖限制详见[28号审查分类](../superpowers/specs/bounded-schema-upgrade-reviews/28-r6-classification.md)。本轮只允许修改升级器主guard及必要工具/恢复测试；迁移和其他生产路径保持。

## 目标只读预检

2026-09-10 11:52:38，对现有主启动器及完整祖先执行R6已有真实身份函数：`C:\Program Files\Ruisheng\Launcher\start_ruisheng_local.ps1` SHA256 `966e92b5c0b3bf82237b9109dcc143528277becc84de495e9311c8e10918b74a`，完整路径保护通过。新`schema-upgrade-guard.json`不存在，符合尚未安装本次guard的状态；不能把现有旧launcher通过保护检查等同于新guard已经部署。

证据：`tmp-test-logs/bounded-r7-target-guard-paths-dc6004273df4497389af9acbef445e05.json`。探针仅装载同摘要函数到远端内存进行只读核验，主机/权益确认通过，没有修改文件、服务、任务、ACL或USB。最新完整健康观测为12:54:40的旧候选deploy-20260907.1、0012、五服务运行、API/GW就绪及Web200，四表仍为0|0|0|0，新guard和维护标记不存在；证据`tmp-test-logs/bounded-target-health-32dfd604d2894db197b7c1e7d173dc42.json`。本轮尚无目标变更。

## 实施与定向验证

生产相对R6仅在`Assert-InstalledMaintenanceGuards`增加6行：原叶文件owner/ACL校验后、读取回执前，复用完整祖先身份函数；失败沿用`installed_maintenance_guards_acl_invalid`。固定路径、回执结构/版本、launcher摘要及首次guard收据绑定不变。

最终三文件已同步根树和发布树，SHA256一致；反向应用`tmp-test-logs/bounded-review-r6-2sNMq9/r7-implementation-delta.diff`检查通过，证明其余原实现保持。

| 文件 | SHA256 |
| --- | --- |
| tools/remote_full_upgrade/target-updater.ps1 | d7ecf14c1134708c0948a02aec00f9e482183b88c40a37765c8105d170cfb626 |
| tests/tools/test_remote_full_upgrade.py | c98c3e46af8f4bda84fe1ce10e83c39579be21faed6e2399fc57ec7cf052e47b |
| tests/integration/test_schema_upgrade_recovery.py | 83cdc5a44485e89e99c4bde272ae29f6cbfee7313eb96cef1aeca56192ac4a8a |

旧R6确切红阶段`tmp-test-logs/r7-guard-red3-20260910.xml`：双PS均完成真实夹具后，在36类危险祖先权限/四级链接以及坏回执读取顺序上因错误放行而失败；XML SHA256 `fd9f62173f4dbe63e3ce14c104065e37eda165d77f7aeafd3f0e97f9d3e30074`。red/red2分别为generic ACL枚举和SDDL构造夹具错误，不作为产品红测。

最终定向`tmp-test-logs/r7-guard-green3-20260910.xml`：4通过、0失败/错误/跳过，JUnit39.863秒（控制台40.48秒）；SHA256 `7fc03a44ff0ab9373d674a81be1702bb5003cc7b8f29471a2f34d206b68804b7`。双PS主guard各67个实际观察全部符合预期；另外两项在新进程重开真实保护夹具、验证首次回执绑定与漂移拒绝，不调用Docker。两处早期green/green2夹具初始化失败完整保留，不抹去或计作最终通过。

固定Ruff0.11.13与本机0.15.11对两个测试文件的检查、格式检查通过，最终定向测试后未改源码。随后完整发布树179项工具和4项真实Recover/完整Apply均已通过，最终证据见下节。

后续诊断绑定辅助`tmp-test-logs/install-bounded-serial-binding.ps1`已准备，外层与远端脚本语法通过。它要求匹配的部署验收/活动指针/空点位库，备份原7个工具/模板/回执及ACL，通过已登记publisher的InstallSerialTools更新并核验GW绑定；不会读取物理端口。尚未执行，不代表目标已更新。

## 统一发布树回归

完整工具179/179通过，0失败/错误/跳过；JUnit550.929秒，控制台551.41秒。证据tmp-test-logs/bounded-r7-tools-1c0ee0554e32451fb274cc3824ccf3d6，run.json SHA256为33945b3d8da1f463d4e674f6283b6e1f2072cedada1065713ddc4eab0e11ff1a，results.xml为a0154d7f8773d1dbf7b03b857e24dc937daab7af8a0d5630ca4d3543471774af；主线程核对测试前后源码及迁移摘要均未变。

根树和发布树的原43文件再次核对：42文件逐字节保持，CHANGELOG只追加2026-09-09与2026-09-10条目，删除这两个新增段后与原批准全文完全一致。

双PS新增真实祖先漂移Recover及完整Apply共4/4通过，0失败/错误/跳过；JUnit2798.321秒，控制台2798.38秒。证据`tmp-test-logs/bounded-r7-integration-f0569158b64240e5842abeeb21ee0b94`，短夹具`D:/bounded-release-r2-acl-fixtures-20260908/r7-0b129bdd`；run.json SHA256为ae905580822c028225bc6dad4d8847609be472fd5bd04b6bc642d710a6da25b8，results.xml为b58d8e1c71290b3dd95e05c01164a65fa912b42b27c5d128ca92dbbf789c2726。退出码0，source_hashes_verified_after=true，主线程核对两树三源码均与上表匹配。没有改动原900秒子调用上限或重复已经通过的用例。

13:02:38只读核验本轮6个操作的44个容器，全部已停止、精确标签/镜像身份匹配、没有宿主端口发布；存储及备份保留。独有`owned-assets.json` SHA256为d50dc41f9e65da0f0b21236d0c55655b5e2a5d56384c489652f965535744cd8a。该矩阵验证祖先权限/链接漂移拒绝后保持NOLOGIN、维护状态、停止应用和非空数据，以及正常备份、还原、迁移和提交；原R6测试不冒充R7通过。

目标入口辅助probe-bounded-r6-target-entrypoints.ps1新增显式VerifyInstalledGuards选项：安装后实际调用本次主guard、核对回执与发布树launcher摘要，避免仅靠任务分类推断入口已经安装。现场单次调度辅助invoke-bounded-site-probe.ps1已准备DryRun/Execute/Collect；Execute前本地CreateNew持久记录一次派发，丢失回执只收集证据，不自动重复发送。二者尚未执行安装后/现场阶段，不属于发布源码。

## 验证计划

审查收尾：冻结102文件于13:21:02全匹配。Blind/Edge各2项均为重复既有defer；Acceptance1项经主线程精确PG15.7、真实双PS最小结构复现确认。仅ALTER反例被现有guard正确拒绝，但原SQL重建CHECK后文本相同却错误放行并接受POINT_GROUPS。三次测试容器停止、存储保留、目标未动；包括初次夹具错误和完整反证，详见[33号分类](../superpowers/specs/bounded-schema-upgrade-reviews/33-r7-classification.md)。后续R8仅局部修改结构证明，不能把本报告测试视为R8通过。

审查期间另修正根树临时现场调度辅助的JSON键顺序误判；真实探针本地DryRun加9项正反比较验证通过，物理TX为0。仅辅助文件`tmp-test-logs/invoke-bounded-site-probe.ps1`变化（SHA256 a4a54c889d047f7b91f9a54c15d052861d551e7351e36ccdcbf5bbdc5ec88404），不在发布源码或R7冻结差异内。

- 双PowerShell实际主guard函数测试，保留叶owner、固定路径/结构/hash约束；验证所有既存祖先的危险有效权限和junction拒绝，允许安全目录及仅创建无关子项。
- 真实Recover用例在父权限/链接漂移时验证NOLOGIN、应用停止、维护active和非空数据保留；统一完整工具组及双PS完整Apply。
- 每批记录前后源码摘要，保留原红阶段/夹具失败；测试资产停止并核对精确身份后保留。
- 审查通过后创建本地限定干净提交，正式候选真实API/GW/Web隔离验收通过，才安装目标guard、执行Plan/Apply和部署验收。

本次未改变数据库属性/迁移/进程树/续租主体，因此不重复全部104项长矩阵；未执行的测试不报告为通过。原1号设备证据、2～5模拟与点位含义未校准的边界保持，B11仍最多80字节、固定既有请求，禁止改为38寄存器绕过原上限。
