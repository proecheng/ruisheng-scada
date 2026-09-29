# 有界升级循环9：forfiles启动入口拒绝

日期：2026-09-10。沿既有本地限定提交、签名构建、隔离应用验证、目标受控升级和B11一次读取授权继续；无推送、新迁移、生产回灌/降级、信任变更、OS重启或连续实物读取。

## 当前状态

18:57完整提交检查通过：126个限定baseline范围文件，全部适用hook通过，YAML/TOML因无匹配文件跳过；source_unchanged=true。相对发布HEAD实际暂存113文件，未纳入根工作区无关改动。证据tmp-test-logs/bounded-review-r9-iONeJS/precommit-initial；run.json SHA256 b30fc8dab22974f2159ef0fb4c025db3c9efcf70f7e569e34d38cfb732d69f5e，stdout.log SHA256 94a4528a898ce0b286e8db1ccb152932fbb9bb8c93747541886e78c659638a9d。实际Git提交仍执行原钩子；以下发布状态均为本地提交制作时记录，不预先标记候选或目标完成。

18:50三路独立审查通过，额外的同等级Acceptance重试也完成。六条Blind/Edge发现去重为四项defer：三项既有记录及一项原多设备模板创建的部分成功问题；本次升级器无确认的新违例，43文件不变。完整分类、覆盖及原件身份见[45号记录](../superpowers/specs/bounded-schema-upgrade-reviews/45-r9-classification.md)。尚未完成本地提交、签名构建或部署。

17:36:20最新只读健康检查仍为旧deploy-20260907.1/0012，五服务运行、API/GW ready、Web200、四表0|0|0|0，无新guard/marker，changes=false；证据bounded-target-health-f80430fe11df4743849331d93e2a9a88.json。部署前仍须实时核验。

17:29最终自检完成：固定源码181项工具和4项真实Recover/完整Apply全部通过；三源码测试前后摘要相同，50个本轮容器全部停止、身份及无宿主端口核验通过并保留。进入R9三路独立审查，尚未本地提交、构建候选、目标安装/升级或发送实物命令。

16:30实施自检补充：正式源码已拒绝forfiles，并依据现场逐项核验更新同11 Windows入口的9个摘要。实际新源码16:24再次枚举255目标任务，仅剩既有Docker辅助文件权限拒绝（安装阶段限定修正），无新增Windows误拒绝；证据bounded-r6-target-entrypoints-02238f253f7f4519ab6d8d0fb0c913a5.json。没有目标写入、任务执行或USB发送。

计时诊断共12真实函数探针、8原测试内存场景均符合预期：原测试提前计时加1.5秒调用前延迟会误报；测试时钟从首核锁回调开始后通过。调用内1.2秒仍减少查询预算、第二次失锁拒绝、15.1秒才返回0仍被生产拒绝。只修正测试时基，不改生产函数或15秒/严格断言；原失败唯一原因仍不能证明。诊断报告tmp-test-logs/bounded-r9-writer-clock-990fd159731a42e6adbad963fa3bae02/diagnosis.md SHA256 2679a6d8ec4f04830e28e9057b0736f1a89bac6c68e7fa49a2b232c712aa86f8。最终完整工具181项通过；真实Recover/Apply进度见下文，下列历史统计不改写。

R8审查确认forfiles /C入口漏检，R9已完成局部修订。双PS旧代码真实分类红阶段及仅内存修正绿阶段已记录于[39号分类](../superpowers/specs/bounded-schema-upgrade-reviews/39-r8-classification.md)。正式修正版的完整工具已通过；受影响真实Recover/Apply进度见下文。最终三路审查、提交与部署尚未完成。

## 修正版源码与验证

两树已逐字节同步；批准迁移、controller/launcher和原43文件代码保持。当前固定源码：

| 文件 | SHA256 |
| --- | --- |
| tools/remote_full_upgrade/target-updater.ps1 | 03b6faa02dba2cb8a677c9d470714876bbcf32b3ac07ec976d24781923cbe9e2 |
| tests/tools/test_remote_full_upgrade.py | 26cf9d71455d3c4a5758dcb6699e339e7c75905465b7ff17ea8b3f82dea856b2 |
| tests/integration/test_schema_upgrade_recovery.py | 91044d781f31d42fb184e6b538449dd9756ca2f7759976c8d7cfdb4b4af43e26 |

初版forfiles红/绿/相邻12项取证见tmp-test-logs/bounded-r9-forfiles-7e8063d38ecc403389b4b1bfb00d126d/implementation-evidence.md；工具初版180/181失败记录见下文。Windows现场固定身份更新与测试时基修正后的完整181工具全部通过，440.40秒，0失败/错误/跳过、exit0、source_unchanged=true。证据tmp-test-logs/bounded-r9-tools-4d915cd688824d46b1b7bfdf581e4134，run.json SHA256 5425a6664d0a9f31bff751a8bf9fd2eee6308e221839c22da10587bb60201596，results.xml 1c0026ef0a6119e23af6447254fe7639d2406d607fdb6d0ad603dc51053f0c59。固定Ruff0.11.13 check及format检查两测试文件通过；原缓存缺失后已恢复提交检查环境，最终完整检查结果见当前状态。

随后按同三源码摘要运行真实wrapper Recover及完整Apply各双PowerShell，共4项全部通过，2973.64秒，0失败/错误/跳过、exit0、source_hashes_verified_after=true。取证tmp-test-logs/bounded-r9-integration-a7dc6d0fe3c549cf8e2a19e6d8bc9bb8，短夹具D:/bounded-release-r2-acl-fixtures-20260908/r9-55ea3b7d；保留原900秒完整Apply限制与所有真实恢复断言。

| 用例 | Windows PowerShell 5.1 call秒 | PowerShell 7 call秒 | 结果 |
| --- | --- | --- | --- |
| wrapper Recover，五类不安全入口 | 862.767 | 840.703 | 两项通过；拒绝解封、NOLOGIN、停应用、维护active、0013与历史内容摘要保持 |
| 完整Apply，真实备份/还原/迁移/提交 | 346.753 | 397.388 | 两项通过；committed/completed、0013、原LOGIN布尔值及非空历史保留 |

以上是隔离真实Timescale与实际升级/恢复函数证据；夹具仍替代发布验签、权益、健康和安装guard边界，应用使用惰性测试镜像。实际安装主guard的真实NTFS/任务分类证据来自工具组，不能把完整Apply夹具说成真实API/GW/Web或目标部署。正式签名候选和真实应用隔离验收仍待发布阶段。

本輪6个操作关联50个容器已只读核验：全部停止、标签/镜像身份吻合、无宿主端口，所有容器/卷/备份保留。证据摘要：

| 文件 | SHA256 |
| --- | --- |
| run.json | 4f680476264bfd0c241974b32feb030bf4018d84a3c1af3fb1a50e3c50b70079 |
| results.xml | 17715b608a7e8e37f6946de0ddd7036c5290bbd8ef5cce61f43a4b98347ddd4e |
| owned-assets.json | e5191268e7c1692357a449f4dd4305ab51598129362d1882b8952ec9680f4075 |

17:23两树原43文件再次核验：42文件逐字节匹配批准快照；CHANGELOG仅09/09、09/10两节追加，移除追加后原文完全相同。固定Ruff和baseline完整差异mypy已通过；审查后完整提交检查已执行通过，见当前状态。

## 保留与范围

拒绝forfiles执行宿主并补必要真实测试，不解释或运行其/C载荷；同时依据现场证据更新同11系统入口的9个固定摘要，仅修正工具测试的计时起点。保留R8列collation证明、R7完整路径保护、R6数据库属性/备份/恢复/续租，以及所有进程树/NOLOGIN/维护保护和原900秒测试限时。0013迁移SHA256仍df20fff11e1b8ea04ef4ce69126c29021e221685ad27a138bbfafe10143ac5d1；原43文件代码不变，日期CHANGELOG追加例外保持。

R8工具179通过，真实必需用例通过证据为首轮5及原始完整Apply补测2；首轮PS5的docker_process_tree_incomplete及恢复超时、额外观测诊断1项分别保留，不当作已知根因或单次全绿。此历史证据不改标为R9执行结果。

## 目标只读观察

16:42:57确认登记适配器USB\\VID_0403&PID_6001\\AI06JYFW存在于usbipd状态，bus1-9，已连接WSL客户端；同一身份的serial-hardware-state时间16:42:56、ready、/dev/ttyUSB0、稳定路径/dev/ruisheng-rs485。证据bounded-r9-usbipd-presence-0eed501f59664bfdb8321f8b009cfc75.json，changes=false、ports_opened=0、modbus_tx=0。此前按原VID/PID查询Win32_PnPEntity未找到；usbipd中的StubInstanceId为VID_80EE/PID_CAFE，不能把原PnP查询空结果误称实物断开。该观察只证明系统映射，不是Modbus响应或点位验收。

### R9自检：Windows更新后的同一固定入口

16:12最终forfiles源码对目标255任务只读检查，11个既有Windows rundll32任务因固定文件摘要不再匹配而拒绝；此为预期的fail-closed行为，不能关闭任务或自动信任系统目录。16:14逐项核实原12文件（宿主+11 DLL），9个摘要变化、3个保持，全部微软Windows有效签名、完整路径/祖先保护通过、64位宿主、无.local/.manifest重定向，11任务路径/参数/空工作目录保持。16:17目标记录Windows 25H2 build26200.9445、09/09安装KB5124008/KB5126052并于17:33重启；WindowsUpdate事件查询无返回，不声称逐文件补丁归因已证明。

只对原固定11入口更新已核实的9项精确摘要，不增加入口、导出或参数，不放行旧/新混合任意宿主，不引入候选自报/运行时自动刷新、发布验签密钥或系统信任锚变更。其余3项原摘要、所有路径/ACL/侧载/大小限制、顶层限定及forfiles拒绝保持。16:21仅内存用这9个明确摘要的提案对255任务实际分类后，只剩既有Docker辅助文件ACL拒绝（已授权安装时限定修正）；没有目标写入或任务执行。该提案只是现场兼容证据，最终代码仍须完整工具、同11入口正反例、真实Recover/Apply及三路独立审查。

现场证据（tmp-test-logs下）：bounded-r6-target-entrypoints-fd5c6a4e639740d6a70af77c60405c09.json、bounded-r9-native-tasks-c49e9d22a5584833b48f5fe60c919e61.json、bounded-r9-servicing-49a10c0102734de0a4be21818a387be7.json、bounded-r9-native-proposal-dcc6e72a785d402696d261c8b0de4d2a.json。源码/系统文件摘要及原失败在R9报告留档；不冒充新增审查循环或用户修改信任锚。

| 固定文件 | 原SHA256 | 当前经核实SHA256 |
| --- | --- | --- |
| rundll32.exe | c4c8160c42407741baf0946a1fc0e8162e14c6cc543e754efd78cc52bb316af5 | f3e73f8a59b991fa8cdb91d4b37e11110c33a9eabdb25af8ac8ab5ccf9e3bbf5 |
| PcaSvc.dll | fead0b1c882c950c35586d5b217dc534e5780cdb808349614fa9d7fd20637126 | b4bab2558ff39033131fca4fede16bc6958e04f150802132a4e895f6f8d50680 |
| Startupscan.dll | 2280a8aaa510c6f05d7bcfd8bc02fdae737415665c98c26c9305ff65e109dbba | 2280a8aaa510c6f05d7bcfd8bc02fdae737415665c98c26c9305ff65e109dbba |
| Windows.Storage.ApplicationData.dll | 5378477ded9fa0a8eea217e93b2499183eb79a8b3bd967a92c571c9df318acdd | 05f3ba69ee74f7d4346b3a1e59d5b68d3298cfd79d102b61d31961bf8627c33b |
| AppxDeploymentClient.dll | a69790cb77aac0891ef880080e854f7de4e6e2be1be2b50e5030bcef1d988fb5 | 2e01fea8ea24980884ff8309ef60318a3dfc183dc0fc58f666cd0df6e8b1fd77 |
| acproxy.dll | eaba99f4fc722276267f3e36b7ce6f906340ce2ec70ecd061a3e3b795ad8ca77 | eaba99f4fc722276267f3e36b7ce6f906340ce2ec70ecd061a3e3b795ad8ca77 |
| CapabilityAccessManager.dll | 53ecf43f987d74cd54e5b36c79f3a3c02dbc66da6cd6a7cdd91da20de3a2c328 | dbaf1077e2ff38e51d56c48b52011c90d2058e8f2f53697b4db2efde4582a5c4 |
| dfdts.dll | 389d2736a967d51fe50ec033f23d7f14ff62b73d8a7c1b4fffd8f7db2f488a29 | 389d2736a967d51fe50ec033f23d7f14ff62b73d8a7c1b4fffd8f7db2f488a29 |
| pcrpf.dll | 1f15e4f8cde602614b3cd09389ad68777bcb59e637d994f80ab6252e17dcfc79 | e38886c9984453403ba137b5e1dc2ec756d99c2f74b162c837ee979400c50ec8 |
| Windows.StateRepositoryClient.dll | d4151f812dd8c666cc84b6a67f944c24849f2583d6cb9507ede169492d59a3e1 | c4277c4088f124b241707fee55ebbca31a4382de55688e77318d4fd2e63607c1 |
| sysmain.dll | ab53066e8adb1bc55d8dd028f15150487a294e5fe223e747e13f87654d9246e9 | a1c8ac760fb68bab5b24136632b7cf317bfabb86631d25fe48f42c6b7b433a51 |
| bfe.dll | 93ed3969c46cdf7801be5c1567fccc63efe8f66620596543d285c03c6c1deb7f | a27a54e3a6b3c9961d0ef1fa6bc082352debf33fb69d9bd8eaa87e2d887b1128 |

初版R9工具180通过/1失败，507.49秒，源码前后保持；失败为PowerShell7 test_unknown_writers_waits_for_actual_zero_with_remaining_query_budget的query_budget_exceeded。该测试的外层时钟在函数定义/实际调用之前启动，真实操作内部另起时钟；时基差异的诊断及最终修正见前文，原失败未被覆盖或唯一归因，15秒门槛保持。取证目录bounded-r9-tools-385b85c90c4e45b9a7d5c51f6f69d6ec，run.json SHA256 701de5b77641ec535e517808927591b8e8f6064669e75c22a92665098640a1cf，results.xml 235492e929da51e47d2859e116f64c56a53201611caa6ae2995f06ecdda021cd。

16:03:48核验目标WIN-OAUCM8UQUGH仍运行deploy-20260907.1，源提交2150b5ee904760ce0af4483c201009b8744669a2，数据库0012；五服务运行，API/GW内部ready，Web200，四表0|0|0|0，尚无新guard或维护marker。证据tmp-test-logs/bounded-target-health-2520dee3fdf1488cb7984358aad71e2a.json，changes=false。该观察不代替最终实时部署预检；没有发送USB采集命令。
