# 有界升级循环5：闭集启动入口证明

日期：2026-09-09。R5修订与自检已完成，三路独立复审确认5项仍须修复的问题，当前暂停在第6轮实施之前。尚未提交、签名构建、上传、安装入口或执行目标数据库迁移。完整发现、复现、KEEP及三组修复方案见[24号审查记录](../superpowers/specs/bounded-schema-upgrade-reviews/24-r5-classification.md)。

## 输入与修订

R4冻结差异SHA256 `be3aaa55f1d7bd8b371922bc789b3d1d7de480ca532c408ec2398c3282acaf14`，三路原始报告及分类位于 `docs/superpowers/specs/bounded-schema-upgrade-reviews/20-r4-classification.md` 与21、22、23号文件。无须修改冻结意图；迭代计数从4到5。

修订限于升级器启动入口及对应工具/集成测试。PowerShell不执行被检查的代码，只用AST和固定身份规则证明；动态成员、定义/转换/赋值等无法证明的执行拒绝，未知扩展名或省略扩展名的嵌套启动拒绝。保留静态可证明Process.Start/Start-Process、只读输出/Docker查询和三条固定辅助脚本。按真实语义处理模块限定名、别名声明执行作用域及绑定失败，避免只按basename信任代码。

## 初版定向证据

先在旧R4源码复现新增2项预期失败，XML `tmp-test-logs/bounded-r5-proof-red.xml`。初版R5最终启动相关15项通过，0失败/错误/跳过，40.639秒，XML `tmp-test-logs/bounded-r5-startup-final.xml`；仅判断输入，危险样例不执行。

初版摘要：升级器 `fc1e6eef1ca0e2fd92408d1e651fff6fb92f2815ca29116437ce867424de78d7`，工具测试 `83f47d816098ef13e77caf981bd92b303e5874ca595f1203bdeeadf6fc378666`，集成测试 `53b122138aa6263076b6489352c9f31ba29347b0841bc20a271eb84efe0e7edd`。真实Recover矩阵已改为每版PowerShell四种代表（旧未知ps1、动态ScriptBlock、省略扩展名、cscript），本段记录时尚未运行。

## 目标只读自检与Windows兼容

13:36:16在目标 `WIN-OAUCM8UQUGH` 内存装载初版真实函数，检查255个动作，除既有Docker辅助文件可写权限外，又误拒绝11条Windows rundll32维护任务。取证文件沿用旧探针r4文件名前缀：`tmp-test-logs/bounded-r4-target-entrypoints-e9b0787657664a88ac1a6abbe42d5af1.json`；测试实际源码为上表R5初版，不误称R4测试。

13:41及13:43分别核实固定系统宿主与11 DLL，最后证据 `tmp-test-logs/bounded-r5-native-tasks-23d1e384472b4b09b50d8e4ff3dc32f8.json`。所有文件都有有效Microsoft Windows签名，文件和全部祖先保护均通过。首次部分DLL失败原因是脚本检查256KiB上限；后次仅探针将该上限改为2MiB，其他路径/ACL/摘要算法保持原样。未改变任何系统文件或ACL，不能把第一次大小拒绝称为ACL不安全。

11项任务工作目录均空，精确宿主System32的.local和外部manifest不存在。非冻结context补充固定路径/宿主与DLL摘要/精确参数/空工作目录/64位环境的顶层任务闭集例外；脚本256KiB限制不变，内部闭集二进制上限2MiB，陌生及嵌套宿主继续拒绝，OS文件摘要漂移不自动接受。兼容修正仍属R5 step-03自检，后续证据不能沿用初版15项作为最终通过。

## 待完成与证据限制

14:12:52目标健康只读复核：活动候选deploy-20260907.1/提交2150b5ee904760ce0af4483c201009b8744669a2/数据库0012；五服务运行，Postgres和Redis healthy，API/GW全部内部字段ready，Web HTTP200，devices/device_points/realtime/history为0/0/0/0。guard回执及维护标记仍不存在，未改变目标。证据 `tmp-test-logs/bounded-target-health-ce59cf002a2e482aa7ed6aa8f09a1e19.json`。同期Status操作69fbe142-30f0-475d-a5c0-9c124fcb377a返回observed且双锁缺失；只核验状态，没有Apply。

最终Windows兼容补充定向17/17通过，0失败/错误/跳过，JUnit58.967秒；XML `tmp-test-logs/bounded-r5-startup-native-final.xml`。升级器SHA256 `e83a18f9e42c411459252551fd4e45ebcf31fc34945df539afd5614df2c5c5a4`，工具测试 `60d41151c761acaa1e9c4a097bd23633dc45ae572a6ccb228c36101f892c1f45`，集成测试 `53b122138aa6263076b6489352c9f31ba29347b0841bc20a271eb84efe0e7edd`；根目录和发布树逐文件一致，diff检查通过。三条脚本正例及固定系统宿主/DLL路径、摘要、真实ACL、上限、缺失、重定向和链接反例均通过；本地非管理员夹具仅替换固定身份常量，不代表目标已安装。

13:51:43使用该最终源码在目标内存再次检查255动作，Windows误拒绝全部消除；仅剩Docker辅助文件lenovo可写而正确拒绝。证据 `tmp-test-logs/bounded-r4-target-entrypoints-0d722ac3f37b49c58f957ccdbb050ed1.json`，沿用r4探针文件名前缀，实际R5源码摘要如上。增加固定二进制父目录禁止非受信主体创建子文件/目录，.local/.manifest缺失证明不依赖可写父目录；目标真实检查通过。没有修改目标任务、ACL、服务或USB。

最终完整工具组169/169通过，0失败/错误/跳过，控制台374.14秒/JUnit373.000秒，XML `tmp-test-logs/bounded-r5-tools-final-20260909.xml`。主线程重读XML和三文件SHA256确认源码未变。43个多设备文件在根目录和发布树与原快照全部一致。

双PowerShell真实Recover正在运行，证据 `tmp-test-logs/bounded-r5-integration-29e029d55c064966847b2a48edcefd42`，独有夹具 `D:\bounded-release-r2-acl-fixtures-20260908\r5-fd137181`。运行器 `tmp-test-logs/run-bounded-r5-integration.py` 固定上述三文件及迁移摘要，选择2个参数化用例、每项4类代表，仅将ROOT/tmp-test-logs重定向至短取证目录；真实恢复/SQL/断言保持原实现。任务安装环境边界使用替代函数，但真实分类器决定拒绝；不执行危险脚本。完成前不称为通过，不取代正式候选真实API/GW/Web验收。

双PowerShell真实Recover最终2/2通过，0失败/错误/跳过，1664.930秒；主线程重读results.xml及run.json，source_hashes_verified_after=true。每项4类代表，正式函数/SQL/断言执行边界如上，不能作真实API/GW/Web通过证据。14:27:52只读检查本轮2操作26个容器全部停止、标签及镜像身份匹配、无主机端口；容器/卷/备份/日志保留，证据同目录owned-assets.json。再次核对43个多设备文件全匹配，git diff --check通过。

三路独立复审现已完成，未放行发布：PowerShell/CMD启动配置、嵌套stdin和未支持解释器3项入口缺口，以及数据库级还原属性遗漏、Recover外层失败清理缺口，共5项bad_spec。主线程双PowerShell探针确认每版9个缺口样例错误允许、6个控制符合预期；危险样例均未执行。另用仅输出固定标记的无害CLI验证stdin语义。数据库及Recover完整入口以静态控制流核验，本次未运行新的数据库故障试验或目标操作。

14:40:43重新核对冻结91文件与43个多设备快照全匹配后，才追加审查报告和流程状态；冻结diff/manifest及源码不变。按bmad-quick-dev的step-04-review.md中“If it exceeds 5, HALT and escalate to the human.”，计数从5到6并暂停在重新实施之前，等待用户决定是否解除本次循环上限。不是自动审批拒绝；原限定提交、签名候选、隔离真实API/GW/Web、目标入口安装及Plan/Apply授权保持有效，必须先修复并通过复审。

三组方案为统一补齐启动语义检查、补数据库级备份/还原证明、补完整Recover入口清理，详见24号记录；没有开始R6代码修改。旧R3/R4/R5测试保持各自源码边界，不能以169工具及2项恢复通过冒称这些新增缺陷已覆盖。现场仅证实1号设备；2～5号模拟结果不能作实物结论，不绕过B11的80字节诊断上限。
