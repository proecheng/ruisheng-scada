# 有界升级独立审查记录

日期：2026-09-08。状态：三路独立审查完成，已分类，进入非冻结实施细节修订。此记录不是发布放行。

审查输入：`tmp-test-logs/bounded-review-20260908-1140/implementation.diff`。主线程已使用 `git apply --reverse --check` 确认差异与独立发布工作树匹配；未实际应用或撤销补丁。

## 盲审发现

| 编号 | 等级 | 位置 | 审查意见 | 初步核实 |
|---|---|---|---|---|
| B1 | P1 | `ruisheng-gw/src/ruisheng_gw/domain/registry.py:97` | 第129台设备可提交，但会使全部注册表刷新失败，影响其他总线配置生效 | 待结合既有容量约束归类 |
| B2 | P1 | `ruisheng-gw/src/ruisheng_gw/scheduler/serial_poller.py:116` | 同地址、同长度迟到帧可能归属新请求或复用地址的新设备 | 已批准多设备规格明确RTU无事务号，不能识别任意延迟同形帧；仍需核对实际可执行边界 |
| B3 | P1 | `tools/remote_full_upgrade/target-updater.ps1:1480` | 任务扫描只匹配docker后接空白，遗漏docker.exe及完整路径 | 已复现，需修复 |
| B4 | P2 | `ruisheng-gw/src/ruisheng_gw/scheduler/serial_poller.py:161` | 静默间隔未随迟到接收字节重新计时 | 待结合原规格事务后静默含义归类 |
| B5 | P2 | `tools/remote_full_upgrade/target-updater.ps1:1511` | 关键词匹配不能证明数据库索引及约束完整 | 已复现，需修复 |
| B6 | P2 | `tools/remote_full_upgrade/target-updater.ps1:2389` | 进程终止绕过finally后，Recover未收敛该操作的隔离还原容器 | 代码确认恢复路径缺少辅助资产收敛；须补故障测试 |
| B7 | P2 | `tools/remote_full_upgrade/target-updater.ps1:1763` | 快照850秒寿命短于顺序备份操作的总超时预算 | 代码确认时间预算不一致；须明确全流程期限与终止确认 |
| B8 | P2 | `tools/remote_maintenance_prepare.ps1:62` | 合规审计目录中新建互斥文件继承ACL，与后续闭集校验不一致 | 该文件属于发布基线兼容修复，不是本次新增差异；待归类 |
| B9 | P3 | `tools/remote_maintenance_prepare.ps1:44` | 新建审计目录立即进入已有目录校验，首次初始化失败 | 同上；待归类 |
| B10 | P2测试缺口 | `tests/integration/test_schema_upgrade_recovery.py:472` | 完整Apply测试替换应用和健康检查，不能证明正式镜像启动 | 报告已明确证据限制；正式候选真实健康验收尚未执行，不得用模拟结果代替 |

## 主线程最小复现

仅在本机内存加载最终升级器的 `Assert-BoundedSchema` 函数，替换SQL返回值；未连接数据库或目标机。

- 唯一索引保留原关键词、额外加入 `AND modbus_addr <> 7`：当前检查接受。
- 读取方案CHECK保留原两值、额外允许 `unreviewed`：当前检查接受。
- 对任务原始正则执行三条字符串检查：`docker compose up -d`被识别，`docker.exe compose up -d`和带完整可执行文件路径的形式均未被识别。

这些复现证明当前判断分支有缺口，不宣称目标机已经存在错误索引或旁路任务。独立审查尚未汇总分类，生产源码暂未修改，也未提交、签名或部署。

## 边界审查发现

| 编号 | 位置 | 触发条件及后果 | 初步核实 |
|---|---|---|---|
| E1 | `tools/remote_full_upgrade/target-updater.ps1:2022` | Compose超时只终止docker.exe，插件子进程可能在恢复检查后继续重建或启动容器 | 已复现清理函数只终止父进程；本次恢复流程暴露的进程生命周期缺口 |
| E2 | `ruisheng-gw/src/ruisheng_gw/domain/registry.py:95` | 第129个未删除设备阻断所有刷新及重启 | 与B1合并 |
| E3 | `tools/remote_maintenance_prepare.ps1:242` | 新建审计互斥文件继承ACL导致后续拒绝 | 与B8合并；相同准备函数还被嵌入远端脚本 |
| E4 | `ruisheng-gw/src/ruisheng_gw/scheduler/serial_poller.py:210` | 超时旧回复落入同形新读取窗口，可能按新点位处理 | 与B2合并；须尊重已明确的RTU物理边界 |
| E5 | `ruisheng-gw/src/ruisheng_gw/transport/serial_bus.py:101` | 发送超时令总线worker退出，服务仍报告就绪且没有重启该总线 | 待核对与旧监督机制的关系及本次规格范围 |

E1最小复现使用升级器真实 `Stop-StartedProcess` 函数及本机自建PowerShell父/子进程：父进程退出后，子进程仍运行。主线程已在finally中停止并确认该测试子进程退出；没有使用Docker或目标机进行此故障试验。现有发布验证器的Windows Job Object与启动前门控机制可作为修复参考，不能仅在进程已启动后补一次父进程终止。

## 验收审查与分类

- A1/P1：`target-updater.ps1:2318`清空同调用链的停写清理状态，首次恢复解封并启动后健康失败，二次入口权益/验签拒绝会跳过停应用和NOLOGIN。
- A2/P1：`target-updater.ps1:2306`先写journal，持续落盘失败令整个共享catch提前退出，停应用和NOLOGIN均不执行；停止动作失败同样不能遮蔽其他可执行的清理。
- A3/P2：`target-updater.ps1:1601`逐个停止容器之后才封锁角色，首个GW停止期间中断仍留下可写API及其原自动重启策略；该窗口尚未迁移，不宣称旧程序已经在0013运行。

验收审查员已分别用真实函数的纯内存故障注入验证A1/A2/A3，不读取生产数据、不执行Docker或文件写入。A1输出仅锁检查、journal/审计且两清理标志false；A2仅锁检查及journal失败；A3在首次GW stop处角色仍未封锁且API/Web仍运行并保留原重启策略。

去重后B3/B5/B6/B7/E1/A1/A2/A3共8项归为bad_spec，细化已有保护要求后重推导对应实现，不修改冻结意图。B1/E2、E5、B8/B9/E3及B4是本次有界升级之前的功能/范围问题，纳入deferred-work，不以本次修改触碰其他迁移或现场ACL。B10是文档已明确的待验收边界，补为正式候选构建后、生产部署前的真实应用隔离验证门禁，不能用现有休眠应用替身消除。

修订实施与复核尚未完成，无提交、签名、上传或目标变更。审查前代码差异、摘要、测试资产与报告保留，修订后的测试结论另行记录。
