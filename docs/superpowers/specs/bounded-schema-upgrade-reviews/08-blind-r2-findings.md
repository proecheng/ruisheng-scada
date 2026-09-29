# 第二轮独立盲审结果

日期：2026-09-08。审查员：`bounded_blind_r2`，未传入会话上下文，仅提供冻结差异。

输入：`tmp-test-logs/bounded-review-r2-XxS5An/implementation.diff`，SHA256 `5f92c3bcfb455a3f42b72bb82c683d2c1de7ec34f1046a40368825d3d8f31219`。主线程复核77个文件摘要全部一致。此记录不构成发布放行；边界审查与验收审查尚未完成。

| 编号 | 等级 | 冻结源码位置 | 审查发现 |
|---|---|---|---|
| R2-B1 | P1 | `tools/remote_full_upgrade/target-updater.ps1:2719` | 旧进程在应用解封启动后中断，新进程Recover先遇授权或候选校验拒绝时，内存清理标志未建立，跳过停写清理。须从受保护的持久操作状态独立建立身份核验及清理责任。 |
| R2-B2 | P1 | `tools/remote_full_upgrade/target-updater.ps1:1682` | CMD复合命令只检查第一个程序，`cmd.exe /c "echo starting & docker.exe compose up -d"` 的后半段漏检。 |
| R2-B3 | P1 | `tools/remote_full_upgrade/target-updater.ps1:1715` | `Start-Process docker.exe 'compose up -d'` 的第二个位置参数未作为ArgumentList解析，漏检启动动作。 |
| R2-B4 | P2 | `tools/remote_full_upgrade/target-updater.ps1:2734` | Recover未重新校验已安装维护保护，也未使用持久journal的guard_receipt_sha256；中断期间入口退化后仍可能完成解封。 |
| R2-B5 | P2 | `tools/remote_full_upgrade/target-updater.ps1:1995` | 还原指纹缺少函数定义/权限和触发器定义/启用状态，行为和权限变化可能不影响现有指纹。并非断言实际pg_restore已经丢失这些对象。 |
| R2-B6 | P2 | `tools/remote_maintenance_prepare.ps1:230` | 首次创建审计目录后立即要求已有受保护ACL，首次初始化拒绝；既有延期问题。 |
| R2-B7 | P2 | `tools/remote_maintenance_prepare.ps1:245` | 新建审计锁未设置专属ACL，后续门禁拒绝；既有延期问题。 |
| R2-B8 | P2 | `ruisheng-gw/src/ruisheng_gw/domain/registry.py:97` | API允许同总线第129台设备，整个注册表刷新随之失败；既有延期问题。 |
| R2-B9 | P2 | `ruisheng-gw/src/ruisheng_gw/scheduler/serial_poller.py:161` | 事务后固定等待没有随最后接收字节重新计时；既有延期问题。 |
| R2-B10 | P2 | `ruisheng-gw/src/ruisheng_gw/main.py:249` | 发布新配置后异步清零LX计数，可能覆盖新配置的首批计数；多设备规格及报告已列延期，发布树延期清单应核对补全记录。 |

## 主线程复现

`tmp-test-logs/probe-bounded-r2-fresh-recover.ps1` 仅以AST加载冻结源码中的真实函数，外部操作使用内存替身，不执行数据库、Docker或远端命令。

Windows PowerShell5.1及PowerShell7均得到相同结果：

- R2-B1：新进程清理标志为false时，仅调用journal和audit；同进程保留true标志时，另行调用fence、stop、restore_stop及snapshot_stop。
- R2-B2/R2-B3：上述两种包装命令均返回未拒绝；直接docker启动及Start-Process具名参数对照均被拒绝。
- R2-B4：完整Recover源码未调用安装保护核验；最终分类等待验收审查。
- R2-B5：项目0012及此前迁移确实含租户一致性、审计保护、更新时间等函数和触发器。主线程随后用 `tmp-test-logs/probe-bounded-r2-fingerprint.ps1` 加载真实指纹SQL，对本轮独有源测试容器分别在事务内改变set_updated_at函数体、函数EXECUTE权限、devices更新时间触发器启用状态，三种变化均未导致指纹改变。每次均ROLLBACK，无业务内容输出；测试容器随后停止且restart=no。这是实际PostgreSQL漏报复现，不是生产数据库试验。

## 同版本测试进度

- 启动器及远程维护126项通过，389.34秒；`tmp-test-logs/bounded-release-r2-guards-acl-20260908.xml`。
- 管理员初始化39项通过，111.16秒；`tmp-test-logs/bounded-release-r2-admin-20260908.xml`。
- 最初定向运行33通过、1失败，确认为自建测试目录缺少WRITE_OWNER；在新建专用目录只为当前用户增加可继承FullControl后重测通过。没有修改生产ACL函数、冻结测试代码或工作区权限。
- 14:30，在确认R2代码存在发布阻断问题后结束完整组合回归；未生成最终XML，不能作为完整通过证据。原运行的最终失败数未取得，不能把全部失败推定为权限问题。修订后须重跑48项真实数据库测试及最终工具组合。较早6项辅助/中断定向通过的证据为 `D:/bounded-r2-auxiliary-v2.xml`，不替代完整组合结论。
- 停止测试进程后，核对三个仍运行的本轮容器的完整ID、名称、精确镜像、测试标签和隔离网络，逐个设为restart=no并停止。随后全体该测试标签下运行容器为0，匹配发布树路径的子进程为0。数据卷、容器及证据目录全部保留，没有删除资产。当前操作 `f472d9a7-8198-48cc-b99d-10f89ee83199` 的中间还原材料保留于发布树 `tmp-test-logs`。

本轮尚未提交、签名构建、上传、访问目标或改变现场采集配置。
