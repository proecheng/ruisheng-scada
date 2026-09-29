# 有界升级循环8：迁移相关字段的字符比较规则

日期：2026-09-10。沿原限定提交、签名候选、目标安装升级与B11一次有界读取授权继续；不推送、不新增迁移边、不回灌/降级或删除生产数据，不改变信任或重启操作系统。

## 状态

R8审查未通过：Blind和Acceptance完成，Acceptance确认forfiles /C启动入口漏检；Edge初轮仅输出开场说明，重试服务过载后于15:58终止，无有效审查结论。主线程双PS分类器已复现并验证仅内存最小修正。进入R9局部修复，保留全部数据库证明与原失败记录，原件及覆盖见[39号分类](../superpowers/specs/bounded-schema-upgrade-reviews/39-r8-classification.md)。下段记录为自检完成时的历史状态，不代表三路通过。

R8自检完成，进入独立审查：179项工具通过；真实首轮5项通过、1项完整Apply失败、Core完整Apply未执行，随后同源码无诊断注入的双PowerShell完整Apply两项均通过。7个必需真实用例均有通过证据，但不是首轮单次全绿。原失败先触发docker_process_tree_incomplete，随后自动恢复未在原900秒内完成而超时；唯一原因仍未证明，不能归因为collation修补，也不声称补测已修复该偶发问题。独立审查、提交、构建及部署尚未完成。R7的179项工具和4项真实矩阵保留原版本证据，不作为R8通过。

完整审查原件、分类、实际覆盖及KEEP见[33号记录](../superpowers/specs/bounded-schema-upgrade-reviews/33-r7-classification.md)。

## 最小复现与反证

批准Timescale/PostgreSQL镜像为sha256:50a2abfa8bad354f4bc1567c6edf7426586fd99ee8cd8982bbaee157a460c6b1，实际PG15.7且有852个ICU collation。最小独有表在正常0013形状下双PS真实guard通过。单独ALTER read_profile为大小写不敏感规则后，CHECK文本变成逐元素cast，guard正确拒绝；再以原SQL重建枚举CHECK时，其完整文本与原一致，guard却错误放行并可插入POINT_GROUPS。

最终记录`tmp-test-logs/bounded-r7-collation-a42de008f85f42069d342483047dcf19/result.json` SHA256 34a41bf738df73fc9e17dee32208ccf669ef22dba6eb51ac8dea9539798d2424。此前正常拒绝的7f5723记录和初次夹具失败742f78记录均保留。三次新建容器全部停止、无网络/端口、数据卷保留；没有修改目标数据库。最小表证明结构判断缺口，不能冒充完整Recover故障验收。

## 目标源结构只读核验

2026-09-10 13:26:25目标WIN-OAUCM8UQUGH仍为deploy-20260907.1、0012。serial_port和transport_type的collation均为系统default，provider=d、deterministic=true且与类型默认相等；deleted_at和modbus_addr无collation。数据库provider=c、LC_COLLATE/LC_CTYPE为en_US.utf8，无ICU默认配置。

证据`tmp-test-logs/bounded-r8-target-collations-55a5568db4f0466c815df71f1fee73d9.json`。仅只读事务查询目录及版本，先核实主机/权益/活动候选；没有更改文件、配置、任务、ACL、服务或USB，物理TX为0。此处是目录观测，不替代部署前完整健康预检。

## 实施及验证范围

生产只增加迁移相关列的reviewed_collation证明：三个字符串字段必须使用pg_catalog.default且provider=d、确定性，其他字段没有collation；原CHECK、索引和类型约束保留。新增测试扩展既有源结构6个、目标结构9个实际SQL反例，保留原谓词及8个目标语义反例；新双PS真实Recover验证拒绝后两层停写与非空数据保留。没有新增仅搜索源码字符串的工具测试。

两树最终摘要一致，增量反向应用检查通过，其他实现保持：

| 文件 | SHA256 |
| --- | --- |
| tools/remote_full_upgrade/target-updater.ps1 | 1a79fe83850eb63e1e4c159c193d676d6a86e6d4a48629c7e7fbc725b0be1c71 |
| tests/tools/test_remote_full_upgrade.py（未修改） | c98c3e46af8f4bda84fe1ce10e83c39579be21faed6e2399fc57ec7cf052e47b |
| tests/integration/test_schema_upgrade_recovery.py | fd68feb7fbd28e97bf5cb263d585ae81cdf3219402498e6e868d4001bb9310eb |

根树最小实际绿色验证`tmp-test-logs/bounded-r8-collation-902247faddf6435c8d0c61eba78fd9f9/result.json` SHA256 d620915f92b5ed922688720e2f576c69b0d3c038192310f22b00f4091e131d6d：正常结构双PS允许，仅ALTER及原SQL重建CHECK后的异常均拒绝，fix_verified=true。前后源码摘要匹配；独有容器已停止、网络none、无端口、存储保留。该结果仅证明最小实际结构判断，不冒充完整Recover。

双PowerShell语法、Ruff0.15.11及固定0.11.13检查/格式检查通过。完整发布树179/179工具通过，0失败/错误/跳过；JUnit402.847秒、控制台403.31秒，退出码0、source_unchanged=true。取证目录`tmp-test-logs/bounded-r8-tools-839cf4b51f03462dbdd315cd37e8eead`，run.json SHA256 5b09ac59426d3c5ee58f1b9e0053afbe5a369425301bac37285342c10c30341d，results.xml为761e09bd571e037e1bd83c3ada8abf0a95b077bc29d6f27aee05b70a506ee85b。

随后固定同一源码运行源结构2项、目标语义1项（内部双PS）、新Recover2项、完整Apply2项，计划7个pytest项；最终5通过、1失败，因maxfail=1未执行Core完整Apply。新Recover已实际验证拒绝异常结构、重新NOLOGIN/停应用、维护active及非空设备/历史摘要保持。Windows PowerShell完整Apply先出现审计事件upgrade_apply_failed/error_code=docker_process_tree_incomplete，自动恢复最终被原900秒测试限时中断。实际Apply operation为f271439d-a497-42cb-9a00-fae970fcce08，与初始fixture operation不同；最后journal为candidate_staged/dependency_start_intent、restore_verified=true，不代表提交成功。

证据`tmp-test-logs/bounded-r8-integration-084ff1e132b142b7a763703b12064b59`，短夹具`D:/bounded-release-r2-acl-fixtures-20260908/r8-4e60f2a6`。JUnit2055.777秒、控制台2055.83秒，退出码1、source_hashes_verified_after=true。run.json SHA256为50a25b06cacb6803b4d34ebe6c9a48ce5fdb2e405d2640e573b254a4ee99a676，results.xml为7a3dc89689372ae6c40e1a3f433f5bc93186319a441857b1e3c8412407237b2c。原始失败保留；不能通过放宽250毫秒进程树保护或900秒测试限时获得通过。

14:22首次只读资产核验因docker ps查询60秒超时失败。后续对照确认取证辅助脚本的整个对象JSON格式会让Docker额外请求size=1、统计容器层大小；相同范围的名称/ID显式字段查询快速返回。仅修正辅助脚本取证输出字段，保留原范围、标签/镜像及无端口核验，未改生产升级器。14:29:12最终核实7个operation关联的35个容器全部停止、身份正确且无主机端口，存储保留；owned-assets.json SHA256 e48f4f333a948377018e4d438da7a2fa345525e92c141b613b75c7b5bd94f51a。该取证查询缺陷发生在原Apply失败之后，不能用作原失败原因。

保留原操作代理日志时间线failure-timeline.json：失败后恢复期间，若干镜像读取和容器创建请求各耗时约38至56秒。记录包含主机/VM不同观测点，同URL并发配对可能有歧义，不累计重复观测、不把代理返回当作守护进程提交或Windows空进程树证明。它支持恢复期间存在明显延迟，但未证明进程树报错的唯一原因。

同一R8源码的双PowerShell只读进程探针各30次通过、未复现空树报错，原失败仍保留。两个无Docker控制台实验仅作为诊断：单独增加内层CreateNoWindow会丢失原生程序标准输出，已排除直接采用该修改；生产文件未改变。首次辅助脚本因PS5读取无BOM中文路径失败，改为由PSScriptRoot求源路径后运行，属于探针夹具修正。

原Windows PowerShell完整Apply单项诊断通过：`tmp-test-logs/bounded-r8-integration-c0551ef5a2c5413b9b123846cb3cb003`，夹具r8-4f90f373。在实际受控命令前后追加不含明文参数的逐命令摘要，并在原250毫秒空树判断失败后、原收容清理前观测成员PID/名称；原命令、900秒外层预算、每命令期限、空树门槛及停止逻辑均不变。总536.66秒，setup181.484、call348.761、teardown5.945秒；退出码0且源码前后相同。run.json SHA256 e997780bcc3ebea0f53c7092b72480887d04b50e32cd79c119db5c4779758cd4，results.xml为3b7ffb68a4f382a536d094e176a298c7abc18e1c70a2a354b5931ae8419da526。

诊断捕获282次受控调用，278次直接完成；还原容器就绪轮询中的4次exec返回docker_command_failed，后续轮询成功并继续完成。未观察到tree_incomplete，不能据此声称原偶发问题已修复。完整Apply断言committed/completed、0013、非空历史、原LOGIN布尔值及依赖引用/重启策略均通过。contained-process-trace.jsonl SHA256 328d1de217a32a8790f6158dc10be1aa9e6ab44a28cf5869bd416ef55c6551e1；14:45:28核实13容器停止/身份/无端口并保留，owned-assets.json为bad560da583da8121b7a28268ef60725de9be26976e70303dcdf725b0e2ac20d。此为增加观察的诊断运行，不冒称原始完整回归。

随后无诊断注入的双PowerShell原始完整Apply两项均通过：`tmp-test-logs/bounded-r8-integration-b52167392d394d5abc047cb3d8ced350`，夹具r8-26bb08bc，使用同三份源码摘要及原900秒上限。总1215.80秒、退出码0、source_hashes_verified_after=true；Windows PowerShell call385.738秒，PowerShell7 call514.521秒。最终检查升级提交、0013、非空历史及角色/依赖引用/重启策略均通过。run.json SHA256 23c6c7829b9d2c2ee81173d4a913bf99ba6b97207b390f3addf9eddf995047c2，results.xml为5a2c28bec591c84e150d9a08fcb573bfa08b583ee3843f1a23ee604c92f960bc。15:06:30核验24容器全部停止、身份正确、无主机端口且保留存储；owned-assets.json为6d5f9f7304392bf93a981a552d6c18c3998a75c12ab91e70e2ef0ff4b9196714。

同版本7个所需真实用例的通过证据由首轮5项与原始完整Apply补测2项组成；额外诊断1项另列，不重复计数。35/13/24三个批次资产分别完成停止与身份核验。没有修改进程树代码、扩大超时或替换迁移来取得通过。原偶发失败及恢复期延迟属于仍需审查的可靠性风险；本轮事实支持有界结构修正和完整路径可通过，不证明升级永不失败。独立审查通过后才继续限定提交和发布。

14:40:57目标只读健康检查通过：deploy-20260907.1/0012、五服务运行、API/GW ready、Web200、四表0|0|0|0，未安装新guard或维护marker，changes=false。证据`tmp-test-logs/bounded-target-health-59bc0bb6e77b4aedab40c46af0733894.json`。本轮未向目标机写入或向USB发送。

仅修订升级器的迁移相关列结构证明和必要测试，保持0012→0013批准迁移及摘要、R7祖先保护、R6快照/数据库属性/备份/续租和43文件代码。双PS真实结构正反例与非空Recover拒绝应覆盖比较规则漂移而CHECK文本保持、索引与漂移列互相一致，以及拒绝后的NOLOGIN、停应用、维护active和数据保留。

实施完成后统一发布树源码摘要，再执行完整工具及受影响真实结构/恢复/完整Apply，不重复未改逻辑的长矩阵凑数。复审通过后按原順序进行限定提交、签名候选、真实API/GW/Web隔离验收、目标入口安装与Plan/Apply、部署验收及B11一次读取。未完成阶段不提前勾选。
