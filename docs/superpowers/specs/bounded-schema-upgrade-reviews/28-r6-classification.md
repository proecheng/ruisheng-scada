# R6 审查分类与循环7局部修订

日期：2026-09-10。三路审查原件已收齐；主线程确认1项本次bad_spec，3项既有问题defer（其中1项重复），没有intent_gap。R6不能放行构建，按用户已同意的继续授权进入R7，不再因五轮限制请求审批。

## 冻结输入与审查边界

冻结包为 `tmp-test-logs/bounded-review-r6-2sNMq9`；完整diff SHA256 `8a33f1d4475dbf0633d7fc22718da773d2bf002a754779f476b462d7425553a7`，96文件、1,138,454字节。2026-09-10 11:47:43发布树96项全部匹配，HEAD仍为`5d83607bc6d5bf90234ac713aa5dab6b96eb3461`。此后的归档、规格及真实日期CHANGELOG为新增文档，不冒称仍与原96项manifest全部相同。

原三名同等级、无对话上下文审查者曾因服务403终止，恢复原会话后分别交付最终报告；原错误不代表审查通过。期间两名低等级retry误创建后立即中止，其输出未采用，也未作为合格审查替代。

| 原件与归档 | 原件SHA256 |
| --- | --- |
| blind-findings.md → 29-r6-blind-findings.md | 31a55d2f52aa9277ad37fe8e70ef4a1a92c5d7c7dc7ea7f227486effa694f2cc |
| edge-findings-final.json → 30-r6-edge-findings.json | 0c82beb96e067477a3b984e8c9b4d952dd4acc23fe4e91cf8c189b2ff84ab289 |
| acceptance-findings.md → 31-r6-acceptance-findings.md | bf894488365790d122e0526d0ea5a340519035a05d6075fef8fcdbc2010bcec4 |
| edge-review-coverage-final.md → 32-r6-edge-coverage.md | f3dc8159d0fa7ac24e456273d1445059fcab58772812f01fb50f74e0105fde30 |

Blind确认1项，Edge3项，Acceptance未发现可证实验收违例。Blind/Edge覆盖生产实现差异，但未逐行审查所有测试/文档；完整diff包含历史报告，Blind已接触其内容，不能称严格历史结论盲隔离。原件准确记录这些限制，不声称全包逐行零缺陷。Edge中间`edge-findings.json`保留，不替代final；其中发送drain失败退出是已有fail-closed断言，串口监督恢复已在旧defer中记录。

## bad_spec：主启动器与回执的祖先保护遗漏

来源Blind P1，`tools/remote_full_upgrade/target-updater.ps1:1627`。主guard仅验证叶文件，父目录可删除子项、被删除、改权限、取得所有权，或存在junction时仍放行。该入口是本次新增的维护保护，直接偏离完整保护与恢复重验要求，归为bad_spec，不能以已跑回归替代修复。

主线程执行两版PowerShell真实ACL/目录夹具，见冻结目录`probe-guard-ancestry.ps1`、`guard-ancestry-ps51.json`、`guard-ancestry-ps7.json`。两版各7种情况：安全正例和仅创建无关子项均允许；4类危险权限及junction，旧guard仍允许而既有辅助身份检查全拒绝。固定路径、可信SID映射到本次非管理员独有夹具，任务枚举为空；真实文件ACL、路径和摘要判断未mock，没有执行夹具脚本或修改目标。未声称目标实际已存在这些危险权限。

非冻结context已细化：对launcher及receipt复用既有`Assert-StartupScriptIdentity`证明完整祖先，保留主叶文件仅SYSTEM/Administrators可信、固定路径/版本/摘要及Recover绑定；父路径使用已核实TrustedInstaller兼容规则。补双PS主guard和真实Recover拒绝后停写测试，并跑完整工具与受影响Apply/Recover。原baseline、冻结意图、迁移摘要、43文件代码及停写/备份/续租KEEP不变，仅局部重推导违例块。

## defer：既有问题

1. Edge1：首次创建共享audit mutex后未建立受保护三主体ACL，与2026-09-08既有记录重复。`tools/remote_maintenance_prepare.ps1`相对发布HEAD无差异，不重复追加或改变本轮范围。
2. Edge2：共享审计日志分支仅检查继承ACE而未检查owner，旧维护工具本机及远端分支均存在；相对HEAD此次变化未涉及这些分支。新增独立defer，未来同时覆盖日志owner、幂等安装与生产ACL验证。本次不修改共享审计权限或信任。
3. Edge3：旧帧进入告警仓储等待后，设备/配置变更可使旧样本按新配置触发；仓储未接收期望版本或在事务内核对设备启用/软删。此路径和已批准多设备快照既存，本次升级器未改变该代码；新增独立defer，不能把调用前内存检查当作事务原子性。与旧LX计数清理竞态分别记录。

## 验证与后续

R6最终177工具+7真实回归全部通过，保留原源码摘要与旧104项的80+24及失败边界。它们不是R7结果。9月10日11:34只读目标观测仍是deploy-20260907.1、0012、五服务健康、四表0|0|0|0；证据`tmp-test-logs/bounded-target-health-242c2f540d4648f18a336687282a8c71.json`，没有更改目标。

R7完成后再冻结审查、限定本地提交、签名候选、真实API/GW/Web隔离验收、目标guard安装与Plan/Apply、部署后身份/数据/审计验收及原B11范围有界读取。授权范围不扩展：不推送，不开放新迁移边、生产回灌/降级、密钥变更、OS重启、任意扫描或连续实物采集。
