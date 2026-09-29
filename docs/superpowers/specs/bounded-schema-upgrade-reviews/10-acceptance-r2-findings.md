# 第二轮独立验收审查

日期：2026-09-08。审查员：`bounded_acceptance_r2`，无会话上下文，完整读取15173行冻结差异、规格及其唯一context。差异SHA256 `5f92c3bcfb455a3f42b72bb82c683d2c1de7ec34f1046a40368825d3d8f31219`。结论：暂不通过。

1. R2-A1/P1，`tools/remote_full_upgrade/target-updater.ps1:2719`：全新Recover进程在前置拒绝时跳过安全清理。旧进程在2443行解封并启动应用后，于2444行健康验收期间死亡；新的Recover在2735行权益或后续前置检查拒绝，而清理标志直到2787/2812行才建立。真实函数的纯内存诊断只有write_journal、write_audit，没有数据库身份核验、停写或停应用。应从受保护journal及维护标记恢复清理责任，逐项独立检查锁和真实身份。现有2536行测试预先设置两个标志为true，只覆盖同进程嵌套调用。
2. R2-A2/P2，`tools/remote_full_upgrade/target-updater.ps1:1679`：CMD仅检查首命令，PowerShell仅识别完整-File。真实函数对 `cmd /c "echo ready & docker compose up -d"`、`cmd /c "timeout /t 1 >nul && docker start ruisheng-api"`、`powershell -f C:\Other\start_ruisheng_local.ps1` 均返回false；直接Docker及完整-File对照返回true。应检查全部命令及合法参数缩写，并保留无关Windows任务不误拦的测试。

审查仅进行静态及PS7纯内存控制流诊断，没有编辑、Docker操作、目标访问或签名。未断言目标实际存在上述旁路任务。

签名候选、真实应用隔离健康、完整修订版回归、目标安装/Plan/Apply/数据保留及实物采集仍是未完成的发布验收，不是本轮新增测试失败；不能用替身证据代替。
