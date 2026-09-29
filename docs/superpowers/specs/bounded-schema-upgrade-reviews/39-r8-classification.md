# R8审查分类与循环9局部修订

日期：2026-09-10。R8未通过审查：Blind和Acceptance完成；Edge首次退出0但仅给开场说明，重试服务过载5次后仍无有效结果。15:58只停止精确核验PID/父链/创建时间/命令、无后代的本次native审查进程31104，保留全部原件；重试exit4294967295、output_present=false。不能把两路完成或CLI退出0当作三路通过。当前已确认1项bad_spec，先修复，再重新冻结完整三路审查。

## 冻结与原件

冻结包tmp-test-logs/bounded-review-r8-8tVlr7，共109文件、21069行，diff SHA256 169da4cd1506b4e7e53bde50c7e32f997baa7ba296c5b47376230a323caff3fb；109项摘要及reverse apply check已核验。原baseline d839330a91cdf7cae3f4c30aff9396fb99a47120，发布HEAD 5d83607bc6d5bf90234ac713aa5dab6b96eb3461，均未改。新R9文档/代码不在该冻结包内。

40号为blind-findings.md原件，41号为acceptance-cli-final.md正文归档，42号为blind-coverage.md；43、44号保留Edge初轮与重试运行回执。41号仅按仓库规范补末尾一个LF：原件3970字节、SHA256 f7eb913d5d5ea03db6cbe4e77828d1782350cecd4b478d4ee1ac25c6aad97b3c，归档3971字节、SHA256 ec5e8320d84f41cd877a51c88293fdfd8ec1d1d0e140d17f58bcc94dfbd548a3；正文不变，其余四份逐字节一致。全部日志和无效开场输出留在原目录，不伪造Edge结论。Blind仅读取全部37生产文件变更及部分测试，24个假设；Acceptance读取规格/context、37生产文件变更和引用、64非文档postimage，并独立核实六个获准证据目录。详细覆盖限制见原件，不声称所有历史文件逐行验收。

## bad_spec：forfiles /C宿主入口遗漏

Test-UnprotectedStartupAction的未支持宿主集合漏掉forfiles。任务Execute为C:\Windows\System32\forfiles.exe、Arguments为/P C:\Windows\System32 /M cmd.exe /C "cmd /d /c docker start ruisheng-api"时，实际分类器落入默认false，被任务guard接受。它能显式启动被停止的业务容器、旁路维护启动器；此结论没有证明突破独立数据库NOLOGIN停写。

主线程仅对真实函数传入分类数据，未运行载荷、Docker命令或任务。R8真实红阶段双PS各7项：3个forfiles输入错误允许，4个控制符合预期。加入仅内存拒绝提案后双PS各10项：6个forfiles输入（绝对/裸名/profile/CMD/PowerShell/Start-Process）与4控制全符合；profile入口以startup_task_arguments_invalid拒绝。源码未改、docker_calls=0、target_changes=false。

| 证据目录（tmp-test-logs下） | result.json SHA256 |
| --- | --- |
| bounded-r8-command-host-fd2dd80d5ac24f09a58bfd10113c67b6 | 17e37a9ff375f1f475fa6e5370df0e15c7b9c955d6b54528be4a11667d22764d |
| bounded-r8-command-host-c0ec20c491a84d4fade440ea9047b48f | 542f831c5b6a89dd404f6b92e8e68fe0d8f0f675ad464f880d6c82bb7c924eb8 |
| bounded-r8-command-host-3211049675ae439491a111bf14c56dda | 59bae69bbff7037b0c90aeb3229d4ae15d68ea3be127ffe3a085c5386654a051 |
| bounded-r8-command-host-e8edabe6b958442c9a910b77d72e9104 | e537a03a2cfd830508c94c284201458072189f9339eec2993a1098289a369fd4 |

修正：拒绝该命令执行宿主，不解析/C载荷或建立任意信任例外。新增双PS真实分类、实际任务guard及受影响Recover验证；重新完整工具和完整Apply。KEEP冻结意图、baseline、迁移摘要、43文件代码、R8 collation、R7祖先保护和R6数据库属性/备份/续租及所有停写/进程保护。无intent_gap，不扩大已获部署授权。

旧目标入口JSON只记录拒绝项，不能以未搜到forfiles声称目标全部任务没有它；部署预检须用最终源码重新实际枚举。

## 既有defer与测试边界

Blind仅再次发现共享审计mutex首次创建ACL与消费约定不一致；09/08已登记defer，remote_maintenance_prepare.ps1相对发布HEAD没有修改，维持原待办，不重写目标共享ACL。

Acceptance独立确认R8工具179通过；真实首轮5通过/1失败（Core完整Apply未执行），观测注入诊断1通过，原始双PS完整Apply补测2通过。7个必要真实用例的通过证据是首轮5+补测2；原docker_process_tree_incomplete/900秒恢复超时的唯一根因未证明，不能据补测通过称已修复。35/13/24容器停止/身份/无端口及保留为已记录观察，不是本时刻重新检查。完整Apply夹具有验签/权益/guard/健康替代，不能替代签名候选、真实API/GW/Web、目标和USB验收。

下一步按R9非冻结规范实施，自检通过后重新完整三路审查，再继续既有提交/构建/部署与单次B11流程。尚无新本地提交、候选、目标变更或USB发送。
