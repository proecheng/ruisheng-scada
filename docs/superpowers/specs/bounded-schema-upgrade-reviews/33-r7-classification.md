# R7审查分类与循环8局部修订

日期：2026-09-10。三名同等级、无对话上下文审查者已完成；去重后两项既有defer、1项本次bad_spec，无intent_gap。现有部署及解除五轮上限授权持续有效，不再次索要批准。R7测试通过不能放行已证实语义缺口的候选。

## 冻结与覆盖

冻结包 `tmp-test-logs/bounded-review-r7-9j86Vs`，原baseline仍为d839330a91cdf7cae3f4c30aff9396fb99a47120，HEAD仍5d83607bc6d5bf90234ac713aa5dab6b96eb3461。完整diff SHA256为28dc646351acee3c9e2eca9abe2320370d2f819d75e383bf92c505e70ee385bd，共102文件、20621行；13:21:02全部102项仍与冻结摘要匹配。此后新增审查归档及R8规格不能冒称已包括在原manifest中。

Blind和Edge覆盖生产差异；Blind抽读测试，Edge仅做一个实际registry纯内存复现。两者没有读取嵌入历史审查结论；Acceptance完整读取规格/context及有界升级主要调用链，明确没有访问根树XML或认证未执行部署。覆盖边界分别见37、38及36号原件，不声称整个20,621行或全部传递代码逐行零缺陷。

| 原件 → 归档 | 原件SHA256 |
| --- | --- |
| blind-findings.md → 34-r7-blind-findings.md | 95d5dd5977e2994d5a6f52ef85b8a5ec4d22628918adf1640d320b9c8486165c |
| edge-findings.json → 35-r7-edge-findings.json | 921473e1c09b34a9e4f7220b33b92a934e9ac9f2e0a591753b5c13bcb64e60ae |
| acceptance-findings.md → 36-r7-acceptance-findings.md | 9312ae010ccaf2845e10794150eda56d4b23aabad84e7f101f9b61856689a350 |
| blind-coverage.md → 37-r7-blind-coverage.md | e8f5dd2fd57a6178d6f5bb9896c41990e94ff8d45005e7b984c68dc10d4c6f14 |
| edge-coverage.md → 38-r7-edge-coverage.md | 527fe33fcfacecaa0b73ae1b686dda1b05c142f56cfb5feb826144a3d0989b3e |

两树5份归档均与原件SHA256完全一致。

## bad_spec：迁移相关字段的隐式字符比较规则

Acceptance A1指出`Assert-BoundedSchema`不读取列的attcollation，完整CHECK文本未必能证明实际比较行为。主线程使用批准PG15.7精确镜像、无网络/端口的新建独有数据库及真实双PowerShell函数进一步验证：

1. 初次夹具742f78...存在DB传输适配遗漏和initdb临时服务竞态，未完成；已停止并保留，不作为产品缺陷。
2. 完整试验7f5723...：正常结构双PS通过；仅ALTER列collation后，PostgreSQL重写枚举约束为逐元素cast，guard双PS正确拒绝。原审查的单条ALTER示例本身没有复现放行，不能隐去该反证。
3. 完整试验a42de0...：仍按上述ALTER，但随后以原SQL重建同名枚举CHECK，全部被查询结构/约束文本保持，两个真实guard均错误接受，实际插入POINT_GROUPS成功。正常默认比较下大小写不相等。改动的是测试数据库；只适配真实函数的DB传输到独有容器，不替换结构判断。

最终证据`tmp-test-logs/bounded-r7-collation-a42de008f85f42069d342483047dcf19/result.json` SHA256为34a41bf738df73fc9e17dee32208ccf669ef22dba6eb51ac8dea9539798d2424。三次各自独有容器均已停止、网络none、无宿主端口、数据卷保留，发布源码未改。该最小表复现不冒充完整恢复路径验收；调用链由Acceptance静态核实，后续须补真实非空Recover。

此缺口属于本次新增有界结构证明，违反既有“语义已变则拒绝/不得额外允许读取方案”的AC，归为bad_spec。非冻结context补迁移相关字符串列的审定默认collation身份证明，包括影响CHECK和索引的read_profile、transport_type、serial_port；仅证明索引与已经漂移的列互相一致不足。

KEEP：冻结意图、baseline、原0012→0013迁移及摘要、43个已批准多设备文件代码、R7完整启动路径保护、R6数据库属性/备份/续租、原恢复与停写/身份/审计全部保留。只局部重推导结构证明和必要测试，不整树回退、不新增迁移、不扩大信任或改生产库。

## defer去重

- Blind1/Edge1：共享审计mutex首次创建的继承ACL与消费约定不一致，已在09/08待办登记。`remote_maintenance_prepare.ps1`相对发布HEAD无差异，不重开本轮范围或修改目标共享审计ACL。
- Blind2/Edge2：第129个未删除串口设备写入成功后，Registry全量刷新失败并保留旧配置；已在09/08登记，原43文件快照内既有问题。现有目标仅证实1个设备，暂不引入新的跨租户容量事务或修改批准迁移。原条目已涵盖禁用/跨总线配置刷新受阻，无需重复追加。

## 后续

R7最终179工具及4真实回归保持其版本边界。按R8局部规范修复、做红绿证据与受影响完整工具/真实结构/Recover/Apply回归，重新冻结三路审查后继续限定提交、签名构建、真实API/GW/Web、目标guard/Plan/Apply及B11单次读取。目标仍为旧版本，当前无目标变更或物理发送。
