# 有界升级循环6：启动、还原与恢复入口修复

日期：2026-09-09。用户已明确同意解除本次五轮审查上限，批准继续修复并在审查及验收通过后部署。原限定本地提交、签名构建、目标安装和升级授权持续有效；不开放新迁移边、生产回灌/降级、推送、删除数据、密钥变更或额外实物采集。

## R6审查收尾（2026-09-10）

三路同等级审查已恢复并交付最终原件；确认1项本次主启动器/回执祖先保护遗漏，主线程双PowerShell真实ACL夹具复现。已进入R7局部修订，原177+7项通过和历史失败保留版本边界。三项既有defer、审查服务403/中止retry、输入隔离与覆盖限制、96项冻结核对及原件摘要见[28号分类](../superpowers/specs/bounded-schema-upgrade-reviews/28-r6-classification.md)。尚未提交、构建、签名、上传、安装或部署。

最新目标只读观测为2026-09-10 11:34：deploy-20260907.1、提交2150b5ee904760ce0af4483c201009b8744669a2、数据库0012、五服务健康、四表0|0|0|0，无新guard或维护marker。证据为tmp-test-logs/bounded-target-health-242c2f540d4648f18a336687282a8c71.json；本轮未改目标服务、配置、ACL或USB。

## 历史状态（2026-09-09 23:36）

本轮实施自检完成，准备进入新的三路独立审查；尚未commit、构建、签名、上传或部署。最终工具177项与受影响真实7项全部通过；旧版104项的源码边界及历史失败继续保留，不冒称最终修正版重跑104项全绿。

23:19完整提交钩子完成：限定83个文件已暂存，实际pre-commit run退出0，trailing-whitespace、EOF、merge/case、mixed-line-ending、private-key、固定版Ruff检查/格式、Mypy及共享schema日期门全部通过；YAML/TOML无适用文件跳过。PRE_COMMIT=1明确检查已暂存模型，使用实际当天日期，检查后83文件摘要全部未变。首次命令误加不支持的布尔参数，CLI在执行钩子前退出2、文件未变；移除该参数后取得上述实际通过结果。尚未commit，后续报告/审查归档更新仍须纳入最终暂存和检查。

23:01:30追加目标只读核验：主机、活动候选deploy-20260907.1及2150b5e源码、数据库0012均与前次相同；五服务运行，PG/Redis healthy，API/GW内部全ready，Web200，四表计数0|0|0|0，无新guard回执或维护marker。证据`tmp-test-logs/bounded-target-health-2f581ff2edc14f4e99a1a799f9960939.json`；未修改目标。发布树83个拟提交文件的换行/尾空格只读预检无问题，实际预提交merge/case/private-key检查通过，YAML/TOML因无本次文件而跳过。尚未stage或commit。

部署后只读验收脚本`tmp-test-logs/verify-bounded-deployed-target.ps1`已准备，绑定干净提交与同签名候选应用验收，检查目标活动指针、journal/维护终态、备份v4及四个文件摘要、原环境非发布字段、guard/启动器摘要、五镜像、原角色LOGIN、数据库系统身份/0013、还原容器停止、内部健康及同操作提交审计。外层与内嵌远程脚本语法检查通过；尚未对部署后的目标执行，不记为目标验收证据。辅助脚本不属于发布源码。

最终修正版工具177/177通过，0失败、错误或跳过，JUnit399.919秒（控制台400.37秒）；证据为`tmp-test-logs/bounded-r6-lease-probe/final-d9e4a30907c9488b94c007186a310c20`，源码前后不变。主线程核对run.json SHA256 `1320dce4610456aeb8ab1469030f1c6511212e2de01fe03bfc1d14581bb42350`、results.xml SHA256 `d2aa2fa3871d9d28a1de9e934ddffd66b052c48e10a619faa9102f4c24a0c517`。下方f2a609版177项是格式整理前历史证据。

旧ad0bac/f9a7/096713版真实数据库补测最终24/24通过，JUnit7708.708秒；与首次80项通过合并后，同版104个唯一用例均有通过证据，但不是单次104项全绿，也不称为最终续租修正版全量通过。原Core Apply进程树错误和900秒超时保留，唯一原因仍未证明；同源码同上限重测call501.697695秒通过。补测证据为`tmp-test-logs/bounded-r6-integration-4668b88153c849feb922942be11565fa`，run.json SHA256 `35e5d785f81f793a5bcaa273b363f8869206336bc976562bdb4d4e94ae33c189`、XML SHA256 `8c9f4710209f502cf7d3b0f534c573e17ca9ec7721e9d4bb99a9a281079d5300`，前后源码核验通过。22:39:04独立只读核验30操作132容器全部停止、身份正确、无主机端口，存储保留，同目录owned-assets.json为证。

最终三个文件已用apply_patch同步根树和独立发布树，前后备份为`tmp-test-logs/bounded-r6-final-sync-ce9af32d90fa4e41820304fcf064863b`：

| 文件 | 最终SHA256 |
|---|---|
| tools/remote_full_upgrade/target-updater.ps1 | `6e2708820fbed26b0e7746a415acdc4ceb353c350adf91872af20a9eda9f9acc` |
| tests/tools/test_remote_full_upgrade.py | `948bdbd8f0976c175bb32d86e0dffb2390e15da9413a62c4cc8269fd189c8d99` |
| tests/integration/test_schema_upgrade_recovery.py | `cc2aecaaa4785de82071c91593d74e3b643bc9bb6f4f4de79b0f26c8a84e95c1` |

这三个文件在最终真实7项运行期间冻结；证据`tmp-test-logs/bounded-r6-integration-3d54d640692f400a9bace790c9aa5567`、短夹具`D:\bounded-release-r2-acl-fixtures-20260908\r6-30a93516`。最终7/7通过，0失败/错误/跳过，JUnit3128.958秒（控制台3128.96秒），源码前后核验通过。主线程核对run.json SHA256 `24629173e48f3bd92aac849fc83938e5f1e1ab24256080f70523c80acfdee5ae`、XML SHA256 `b9a3623bd458d75674e507b336bd6d72781022fbdbf9f30e6aa62616ad3cdc83`。

| 最终真实用例 | Windows PowerShell5.1 call秒 | PowerShell7 call秒 |
|---|---:|---:|
| 完整Apply：实际备份还原、0013迁移、提交 | 375.583 | 428.599 |
| 完整outer Recover：进程中断及14类故障/不误停 | 641.438 | 793.187 |
| 快照父进程死亡后的恢复停止 | 21.108 | 23.500 |

另1项失锁保护、材料保留及重新获锁后的安全回退通过，call194.498秒。23:35:50独立只读资产核验：本轮9个操作51个容器全部停止，标签/镜像身份正确，无主机端口，容器/卷/备份保留；同目录owned-assets.json为证。测试中真实数据库、进程/锁与外层Recover边界按下方夹具说明执行，不能代替正式候选真实API/GW/Web或目标管理员ACL环境验收。

发布树Ruff0.15.11全仓check和format --check通过（345文件），Mypy1.20.1全仓177个源码文件通过，git diff --check通过；钩子固定Ruff0.11.13全仓检查/格式也通过。钩子实际排除规则下的18个本次Python实现文件，Mypy1.11.1严格检查通过。曾将旧mypy扩大到不属钩子范围的pcap_gen，缺scapy/typer出现4错误；该诊断不冒称通过，不修改无关代码。最终两测试文件格式整理前后Python AST完全相同，noqa仅定位到对应定义行，证据为`tmp-test-logs/bounded-r6-format-4978d9a56d0f421cb44abe565fdf36de`及`tmp-test-logs/bounded-r6-format-5d57115381a04c72ba6e264da7ebeea3`。完整提交钩子仍将在限定提交前执行。

发布文档准备：实际schema-version钩子要求共享模型提交当天的CHANGELOG条目。22:55仅追加2026-09-09发布准备记录，准确标明发布/目标验收尚待完成，SHARED_SCHEMA_VERSION仍20260415。原43文件中另外42个文件在两树逐字节匹配批准快照；CHANGELOG去掉这一段后也与原件完全相同。原SHA256 `ba20049be982db2fef56acbad44699d9fb979afca4c4111b1b948148cb318f25`，追加后两树均为`c4d5e70d985ebd8c2a554ac419fa594483f29918f9b4742d25740105ffc00780`。这是已授权发布的必要文档更新，不改冻结意图、迁移或模型语义；若跨日提交，按实际日期再核对。

正式候选真实应用验收工具`tmp-test-logs/verify-bounded-candidate-apps.py`补充实际GET /login页面引用的本地JS/CSS，核对状态、URL、MIME、内容大小和摘要。语法检查及现有dist解析（2 JS、1 CSS）通过，尚未运行正式候选验收，不能记为应用已通过。此辅助工具不属于发布源码。

## 本轮输入与范围

R5三路审查确认5项bad_spec，分类及具体修复方案见[24号记录](../superpowers/specs/bounded-schema-upgrade-reviews/24-r5-classification.md)。本轮按顺序修复真实PowerShell/CMD启动配置、stdin及未支持解释器，数据库级权限/连接限制/设置还原证明，以及完整Recover外层环境/审计故障后的安全清理。

保持原baseline d839330a91cdf7cae3f4c30aff9396fb99a47120，R6开始发布HEAD为5d83607bc6d5bf90234ac713aa5dab6b96eb3461。43个多设备文件的原有内容、批准迁移摘要和24号KEEP不变；仅CHANGELOG追加上述当天发布准备条目，根工作树其他变更不纳入。

## 历史实施与验证状态（进度以上方最新记录为准）

- 最新自检：操作级续租修正版仅修改三个生产函数，根树177项工具全部通过，JUnit475.943秒（控制台476.56秒），0失败/错误/跳过，源码前后摘要一致。发布树仍冻结为ad0bac...，等待原版24项数据库补测结束后才同步并运行受影响真实Apply/Recover；以下原171项和104项矩阵不冒称续租修正版证据。
- 22:01补测进度为17/24通过、0失败，运行仍未结束。新增完成持续日志失败后的停写及成功重试、两PS各六类跨进程恢复故障/数据保留/完成态不误停、三类guard漂移，以及5.1四类包装入口拒绝。PowerShell7包装入口和最后六项中断/辅助资产用例仍在运行队列；不能记为24项全部通过。
- 原Core完整Apply在相同ad0bac/f9a7/096713源码、同900秒上限的新运行中通过，call501.697695秒；原docker_process_tree_incomplete和超时记录仍保留，不能据再次通过声称原因已确定。
- 20:52:29目标只读检查：deploy-20260907.1、0012、五服务运行、API/GW内部ready、Web200，四表计数0|0|0|0，无维护标记/新guard回执。证据`tmp-test-logs/bounded-target-health-b3a6b6458604426885325bc0e052d3ab.json`；没有变更目标。0配置不代表已完成实物验收。
- 规格已记录用户解除循环上限的明确授权，新增R6三项执行任务和context实施约束；冻结意图未改。
- 实施代理已完成三组修订，仅修改升级器及对应工具/集成测试；主线程核对后同步到独立发布树。双PowerShell工具组已通过，完整真实数据库矩阵正在运行；新一轮独立审查尚未开始。
- R6隔离集成运行器为`tmp-test-logs/run-bounded-r6-integration.py`，显式选取同一集成文件中的测试并固定三个源码摘要和迁移摘要。仅重定向既有输出根到短夹具目录，并在原ProofRun调用前记录操作身份；不替换实际升级/恢复/SQL/断言。
- `tmp-test-logs/inspect-bounded-r6-assets.cjs`将在测试结束后只读核对本轮独有容器停止、镜像/标签和无主机端口，保留数据。工具CLI/语法检查通过，不代表资产检查已完成。
- 构建/真实应用/目标安装脚本已再次静态核对，仍等待完成修复及审查。构建输出使用既有受保护C盘发布目录；预检门槛C≥3GiB、D≥10GiB，构建前再次核对。临时目录位于D盘独有路径。
- 历史R5的169项工具及2项双PowerShell恢复通过继续保留原范围，不能代替此次修改后的证据。最终正式候选的真实API/GW/Web验收仍须单独执行。

## 目标与现场边界

最新目标健康只读观测为2026-09-09 17:49:52：deploy-20260907.1、数据库0012、五服务运行；Postgres/Redis healthy，API/GW内部各项ready，Web200，四张配置/采集表记录仍为0，无维护marker或新入口收据。证据 `tmp-test-logs/bounded-target-health-ff1c94c750b64e949bb8c7c67ce1bf44.json`；不能把0条配置当作设备无响应结论。16:10:24以R6同摘要分类器在目标内存只读检查255项任务动作，没有新增Windows误拒绝，只有既有Docker辅助文件普通用户可写而被正确拒绝；证据为 `tmp-test-logs/bounded-r6-target-entrypoints-67ee9f40c1bc4a5da524a277afa33c65.json`。任务、服务、ACL和USB均未修改。正式安装时仍按已批准方案备份并限定收紧该文件。现场仅证实1号下位机，2～5号为模拟范围；不绕过原B11诊断80字节上限。

## R6首版冻结源码与测试证据

| 文件 | SHA256 |
|---|---|
| 升级器 | `ad0bac53839be39efb1e0a9d53a05e40c77aee25e932d15a458f433cfc7dd418` |
| 工具测试 | `f9a7b1887cab7985f4c9536a2fc458b232512aff8ec435da0dca4b071fc4a622` |
| 集成测试 | `9b6e1291b1a2009526a1f3ad98477ca1e0352f4baec22896c30b418dc9615958` |

完整工具组171/171通过，0失败/错误/跳过，控制台457.91秒、JUnit457.427秒；XML为 `tmp-test-logs/bounded-r6-tools-final-20260909.xml`，主线程已读取核对，三文件摘要保持。43个多设备文件在根目录和发布树与批准快照全部相同，`git diff --check`通过。

首轮完整真实集成矩阵收集100项，执行至第17项停止：16通过、1失败、0错误/跳过，214.527秒。证据目录 `tmp-test-logs/bounded-r6-integration-7b2b4a56487e4bdc88bc771e2f6c7ca7`，短夹具根 `D:\bounded-release-r2-acl-fixtures-20260908\r6-5e225cf2`；run.json确认源码前后摘要一致。失败是新增数据库设置测试直接比较setconfig数组顺序：源库先search_path后TimeZone，还原库顺序相反、设置名称和值相同。真实还原指纹本已排序比较并通过，不因本次断言修改生产实现或忽略配置差异。后续将修订测试为稳定排序后重新执行，原失败与XML保留。

首轮3个独有容器已于16:22:53只读核对全部停止、镜像/标签匹配且无主机端口，容器/卷/备份保留；见同目录owned-assets.json。源镜像实际为PostgreSQL150007、Timescale2.16.1；使用固定镜像、新建非空数据库、非默认owner/有效ACL授予链/连接限制/数据库及角色在库设置。备份回执升为schema_version=4并绑定database-properties.sql，真实还原后比较数据库属性指纹。

完整Recover外层测试使用独有 `C:\Ruisheng\candidates\bounded-test-<operation UUID>` 站点，保留生产站点格式验证。当前本机不是管理员，测试副本仅将共享审计的管理员token/SID边界映射到当前测试用户；文件与目录ACL、双锁、资产身份、外层调度和清理函数仍实际运行，不代表生产管理员ACL环境验收。既有发布验签、权益、外网、应用健康等夹具替代范围沿用历史集成报告；正式候选的真实API/GW/Web仍须另行验收。

16:24仅修订新增设置比较断言：按角色和设置名称/值排序并使用JSON保留完整字段；生产指纹、还原过程和其他断言不改。集成测试新SHA256为 `0986741f46517ca30ee9ba17f77eae42cd02526bbc63b86bad51894a2c98d724`，升级器与工具测试仍为上表摘要，171项工具证据继续有效。根与发布树精确同步、diff检查通过。第二轮收集完整100项，优先新增属性及外层恢复用例，证据 `tmp-test-logs/bounded-r6-integration-7a66f5f3e9e4447d973469ef7c08216c`；当前运行中，不称最终通过。

第二轮执行至第11项停止：10通过、1失败，178.829秒；原XML及源码前后核对记录保留。新增属性还原及9类漂移检测通过；不支持属性用例在尝试从当前连接执行 `ALTER DATABASE ... ALLOW_CONNECTIONS false` 时被PostgreSQL先行拒绝，尚未进入升级器检查。16:28:33只读核验本轮3容器全部停止、身份和无端口检查通过，存储保留。

16:29仅修订该故障夹具：在独有还原副本的事务内将真实pg_database.datallowconn置false，再执行原生产属性检查，错误退出后验证该属性及模板状态均已回滚。不声称当前连接实际执行了上述DDL；模板属性仍使用真实ALTER DATABASE。生产代码不变，拒绝错误码/head断言保留。集成文件新SHA256为 `4adf88667f98a369bbf8dc2acd79ff6cd272d4439af00a6f7020d87f65780191`。第三轮完整100项开始，证据 `tmp-test-logs/bounded-r6-integration-6cebac8ca4704f7e8c4498cdae000c45`，短夹具根 `D:\bounded-release-r2-acl-fixtures-20260908\r6-019115c3`；运行完成前不记为通过。

第三轮执行至第63项停止：62通过、1失败，JUnit923.067秒，源码前后摘要保持。双PowerShell各31项备份、属性还原/漂移、数据/权限/函数/触发器/表达式验证均通过；两处前次夹具失败在两环境都已修正验证。

完整外层Recover的Windows PowerShell5.1旧进程已实际到达application_start_intent，应用运行、角色解封并写入保留数据后被终止。下一步夹具成功核实旧PID死亡并仅使同操作锁过期，但辅助ProofRun仍以shared/legacy简称取锁，和完整入口留下的shared-maintenance/legacy-hotfix不符，正式锁校验返回upgrade_lock_unrecognized。失败发生在首次fault准备阶段，不能称14类外层故障已通过。仅修订该外层夹具的锁名称配置，使所有辅助调用一致；生产锁检查和旧记录保持。16:46:28只读核对第三轮3操作13容器全部停止、身份及无主机端口检查通过，存储保留。

16:50夹具锁修订已同步：ProofRun.lock_names统一控制真实Acquire和旧进程死亡后的过期校验，默认shared/legacy保留；完整外层夹具从初始化即指定正式shared-maintenance/legacy-hotfix，旧锁文件/记录及生产规则不改。新增两PS×两名称组的真实进程死亡、过期复用、释放及原收据保留测试；中间版本0d3c15d5...的4项通过、39.73秒，无Docker。随后仅将该新测试取证目录改到ROOT/tmp-test-logs独有直接子目录，保持R6运行器短路径映射和资产归属规则，不将中间4项冒称调整后已重跑。

最新集成文件SHA256为 `50c07b09c7461fcedf65c34b9857c7ef5b1c28f30f1dcdc10f7b1cb9fbe62055`，根和发布树一致；生产升级器及工具测试摘要不变。第四轮完整104项开始，先执行两版完整恢复入口，再执行其余回归；证据 `tmp-test-logs/bounded-r6-integration-a61586502e08477990c5d915a0880633`，短夹具根 `D:\bounded-release-r2-acl-fixtures-20260908\r6-b96e8b20`。未结束前不称全量通过。

第四轮在首项Windows PowerShell5.1完整外层故障测试中失败：0完整用例通过、1失败，JUnit601.942秒，源码前后摘要保持。旧进程实际解封/启动/写入后死亡，第一次env_missing完整入口的拒绝、NOLOGIN、应用停止、维护active及非空数据保留断言全部执行通过；后续env_acl夹具用Get-Acl/Set-Acl注入权限时触发本机缺少SeSecurityPrivilege，未进入该分支的生产判断。不能将首分支通过称为整个14类用例通过。

此次仅修正测试DACL故障注入方式，沿用生产已有Set-FileAccessControl/Set-DirectoryAccessControl与仅Access节的获取方式，不要求本机提权，不改生产保护要求。先在新建本地文件/目录用两PS实际验证注入与真实拒绝，再同步重跑。17:02:48只读核验本轮11容器全部停止、身份及无主机端口检查通过，存储保留。

权限夹具最终修订：新增仅供测试准备/故障使用的Get/Set/Reset-TestAccessAcl辅助函数，只读取/修改DACL并保留owner；Core使用原生FileSystemAclExtensions，5.1沿用原兼容setter。完整outer中的生产函数定义、Write-JsonAtomic、Set-RestrictedFileAcl及全部真实权限判定未被替换。非管理员Core兼容setter要求SeSecurityPrivilege仅属于本机测试构造限制，不据此冒称目标管理员环境故障；不修改目标权限或信任。

最终独立快速验证根为 `tmp-test-logs/bounded-r6-acl-final-66c6ca09ebff470ca564f66244f51c16`。主线程读取并核对两结果：Desktop5.1.26100.9168和Core7.6.4均在原样测试准备/注入片段中验证env文件、audit mutex、audit root，基线接受，增加Everyone Read后真实restricted_acl_invalid，owner不变；重复准备后三者及继承日志重新接受。未调用Docker，不代表完整Recover通过。Desktop结果SHA256 `5bd170104a1101cf8db0033f8327c1d102cb2c653e99fe7e6a827148066f4304`，Core结果 `0eece7889266d528d0dc9c08042701de15a621ce4e25277c7b60eadd1fefc6af`。探索目录e036的Desktop结果曾被覆盖，现存内容保留，不用作c467中间版本原件或最终唯一证据。

最新集成源码SHA256 `adb49d1f276f81b2f054ff94240b8be7c02961451fb8baa90f2688f257b3216a`，根和发布树一致；升级器与工具测试摘要仍不变。第五轮完整104项已启动，证据 `tmp-test-logs/bounded-r6-integration-7b28ba516a84465d9cb0f18096a294fd`，短夹具根 `D:\bounded-release-r2-acl-fixtures-20260908\r6-497bf688`；仍先执行完整外层恢复，不以快速权限验证放行。

17:34第五轮的完整外层Recover在Windows PowerShell5.1通过，call阶段734.363秒（不含fixture准备）。旧进程实际死亡后，env文件缺失/ACL、审计根缺失/ACL、互斥文件缺失/ACL、未授权、未知操作、无绑定、失锁、活动指针/数据库/应用身份漂移及完成态共14类全部断言通过；已接收非空数据保持，正式入口和真实清理执行边界如上。PowerShell7同组及其余102项仍待完成，不记104项全绿。

第五轮最终1通过、1失败，JUnit1116.004秒，源码前后摘要保持。PowerShell5.1完整14类通过记录保持；PowerShell7在初次prepare_acls中调用RemoveAccessRuleSpecific(null)而报错，尚未启动该版完整outer进程。后续干净Core宿主探针确认更具体原因：Microsoft.PowerShell.Security尚未加载时，DirectorySecurity的PowerShell扩展属性Access为null，实际GetAccessRules仍返回12条规则；仅过滤null会跳过现存规则。限定修订两处测试枚举为原生GetAccessRules并滤空，覆盖真实继承-only文件/目录、空DACL及混合显式规则；生产实现不变。下一轮优先PowerShell7完整入口，仍覆盖所有104项。

17:46:15只读核验第五轮2操作19个容器全部停止，标签/镜像身份正确且无主机端口，存储保留；证据为同目录owned-assets.json。Docker全量容器列表超时后，仅将资产取证工具的查询限定到已验证的本轮UUID和源容器名称，原精确筛选与身份检查保持；没有重启Docker或操作其他容器。

17:48最终两行测试枚举修订已同步，集成源码SHA256为 `09671308d89516ccc51987a1b722eb2b8d53615e24b7f7f2378a3b1a15f1ec45`。独有验证目录 `tmp-test-logs/bounded-r6-acl-mixed-cbfd653b7ef6429c9c3a670678b0ac27` 的result-v4-Core.json及result-v4-Desktop.json全部通过，主线程核对摘要分别为 `769447f6c356f95840db65abb9947d4fc76d0e4bc2898e02f4c34b025ed950b3`、`c99c7c19c6b679b474fc01f340d931f821acd8e35ac9fe10d4b326915c4ac84c`。干净Core宿主四个继承/混合文件目录复现旧异常；新helper在安全模块未加载时完成原样prepare遍历，后续真实权限校验、8个多层目标、磁盘空DACL文件/目录、日志空/继承规则和三种Everyone故障→拒绝→重复准备均通过。该快速验证无Docker，不代替恢复矩阵。

第六轮完整104项已启动，先PowerShell7完整outer，再5.1及其余矩阵。证据 `tmp-test-logs/bounded-r6-integration-44a4057055ae4fdebac072cf2f93ee7c`，短夹具 `D:\bounded-release-r2-acl-fixtures-20260908\r6-33c89a8c`。升级器/工具摘要未变；17:50再次核对两树43个多设备文件全匹配。发布输出空间C3.22GiB、D102.95GiB，拟候选deploy-20260909.1尚不存在；构建前重查。

18:07第六轮PowerShell7完整外层Recover已通过全部14类故障/无误停断言；18:17 PowerShell5.1对应组同样全部通过，进程死亡后的写入及历史数据保留。其余102项正在执行，尚非完整104项通过。该记录对应最新096713...集成源码，不合并前轮不同测试摘要。

19:14第六轮累计75/104通过、0失败，仍在运行。双PS各31项备份/属性/数据权限/函数触发器/表达式检查及4项进程死亡锁复用均通过；恢复用例已验证还原不一致不迁移源库、停库/缺应用及重复恢复、迁移已提交但journal滞后时保留新配置/地址复用/历史写入、原卷重建、两类维护marker缺口、迁移事务失败后安全恢复。当前进入迁移超时后观测迟到提交用例，不将剩余29项标为已通过。18:07和18:17两版outer call耗时分别740.611秒和607.927秒。19:03本机可用物理内存约2.09GiB，测试耗时增加，但未中断测试或关闭无关进程。

第六轮最终80通过、1失败，23项未执行，JUnit8788.491秒（控制台8788.53秒）；源码前后摘要保持。失败为PowerShell7完整Apply的外层900秒超时，call901.898秒，备份实际还原及0013迁移校验已完成，持久phase=migration_verified、application_start_attempted=false、snapshot stopped=true。真实Apply操作856799f0-f152-4af1-9232-333d7d11b4ba的审计于20:11:16记录 `docker_process_tree_incomplete`，随后尝试恢复直至外层超时；不能把失败单纯归为测试预算不足。Windows PowerShell5.1完整Apply已通过，call407.906秒。20:22:42只读核对23操作133个容器全部停止、身份匹配且无主机端口，数据与备份保留；同目录owned-assets.json为证。

追加只读调查发现独立的续租缺陷风险：每条受控命令都将renewAt重置到now+Lease/3，连续短命令只Assert而不Renew，累计超过租约时可能失锁；失败操作的锁创建19:59:25、到期20:19:25，未续租。该到期时间晚于20:11:16进程树错误，不能声称续租缺陷是本次错误根因。Job退出门实际为250ms，其后StopAndConfirm最多5秒；子进程未完全退出时拒绝成功符合既有契约，250ms是否受宿主调度影响尚未证明。主线程曾在进度更新错误关联两者，已明确更正。不得据此放宽进程树判定或仅延长测试预算。

使用同一ad0bac/f9a7/096713源码、同一900秒上限，失败项及其余23项在新目录 `tmp-test-logs/bounded-r6-integration-4668b88153c849feb922942be11565fa` 续测，短夹具 `D:\bounded-release-r2-acl-fixtures-20260908\r6-f91ac07a`。根树进行最小操作级续租修复，发布树保持冻结直到本轮结束。续租修复须以多个短调用跨续租阈值和锁失效拒绝验证；原80项不改写为新修正版通过。

后续顺序为完成三组修复及对应真实验证、冻结差异和三路审查、限定本地提交、签名候选、真实应用隔离验收、目标预检及受保护入口安装、Plan/Apply和健康/数据保留验收，再进行已授权现场有界采集。

## 操作级续租修复证据

补充静态自检：Ruff发现完整outer用例的14分支/82语句超过PLR0912/PLR0915限额。按同文件既有真实恢复夹具的做法，仅对该函数标注这两项复杂度例外，并说明所有故障须绑定同一份保留的中断journal和真实资产；没有改变断言或测试执行语句。主线程比较整份Python AST（不含位置属性）确认根与冻结发布树完全等价，两个测试文件Ruff检查均通过。根集成文件SHA256现为`85802ca3427cc074505f50afcc3feb57f1af2a770a25547967a8c6cee2fab0aa`；运行中的发布树仍为`09671308d89516ccc51987a1b722eb2b8d53615e24b7f7f2378a3b1a15f1ec45`，等本批完成才同步，后续真实回归使用最终摘要。

20:58本机发布盘空间准备：仅对本次发布树node_modules中445个普通文件做NTFS无损压缩，跳过所有重解析点；逐文件SHA256前后相同，没有删除文件。证据`tmp-test-logs/bounded-dependency-compression-b38127a75a0949d1884ad4aaacdf0815.json`，完成时C盘可用3.05GiB；构建前仍重新检查3GiB门槛。只读取证工具read-bounded-r6-progress.ps1补充当前用例和同目录精确UUID的额外Apply journal显示，避免把夹具prepared状态误当实际Apply进度；不改变测试执行或发布源码。

生产仅修改Invoke-ContainedUpgradeProcess、Invoke-DockerText和Assert-LocksOwned：读取持久锁的实际expires_at，剩余租期不超过LeaseSeconds的2/3时续租；先检查两把锁的操作/PID/进程开始时间及未过期，再调用原Renew-Locks。受控命令入口、等待和完成边界共用此判断；完成后再次失锁时不清除未决daemon意图。未扩大租期、进程树250毫秒判定、五秒收容清理或测试900秒外层限制。

红阶段`tmp-test-logs/bounded-r6-lease-probe/baseline-be796406e0a9474898fd451aaa12fe79`：旧ad0bac...生产代码在两PS均因upgrade_lock_lost失败。使用真实文件和真实时间，测试专用六秒租期、14次100毫秒外部替代调用及400毫秒间隔；无Docker，不代表真实数据库操作。XML SHA256为`aae2d1095cec7f900a7c601c7b4a30a67da21a0bd1f902af9b07da99b406e70d`，run.json为`c57ec6a0a58179441814f1da2a299b601fadab03db3b2f78b2b6f9cd11fc5de4`。

最终177项`tmp-test-logs/bounded-r6-lease-probe/final-2ab13936f44d429984f74490f1d8a87d`全部通过，主线程核对XML/run和摘要。根树生产SHA256为`6e2708820fbed26b0e7746a415acdc4ceb353c350adf91872af20a9eda9f9acc`，工具测试为`f2a6093bb660d2bee58580640908e01f36cf85ba975e68deeab02c7346561a67`，集成仍为`09671308d89516ccc51987a1b722eb2b8d53615e24b7f7f2378a3b1a15f1ec45`。XML SHA256为`d660d77e7d77179f0c88f16bd492224c520b8508b1f7efa8ddf97840db123b2a`，run.json为`5f6066cfa3cd9382f556671047f55f2ba1bf70681010c0de82a2d5dd982db080`。其中新增六项覆盖短调用跨原到期、第二把锁过期/外来操作/PID/进程开始时间漂移时两锁均不写且不发命令、调用后失锁仍保留daemon意图；既有真实父子进程收容、超时及快照期限回归亦通过。
