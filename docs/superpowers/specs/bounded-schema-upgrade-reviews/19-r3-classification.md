# 第三轮复审分类与循环4

日期：2026-09-09。服务恢复后第三轮三路审查完成，未通过。冻结差异 SHA256 为 `e565e348793ad14d3c4028d4ce890d140f28ea96f24c754b1194a3c93d9348c0`。原始报告见16、17、18号文件；验收审查员最初的“无发现”结论已撤回，以18号修正版为准。

| 去重问题 | 来源 | 分类 | 处理 |
|---|---|---|---|
| 自定义脚本包装默认放行 | R3-B1 | bad_spec | 补明脚本内容/身份的证明边界，不以 basename 或 Status 参数证明陌生脚本安全；未知、缺失、漂移或无法证明的相关脚本拒绝。 |
| 环境变量形式的命令入口漏判 | R3-E1 | bad_spec | 按目标 Windows/CMD 实际环境展开规则处理已确定变量；未解析、递归或歧义展开不能默认为无关任务。 |
| Windows PowerShell 隐式命令漏判 | R3-E2、R3-A1 | bad_spec | 正确消费宿主参数，再按宿主默认命令/文件规则解析剩余输入，不能只找显式 -Command/-File。 |
| 同总线容量、串口退出监督、物理静默、共享审计首次 ACL、LX 竞态 | R3-E3..E8 | defer（既有） | 与11号分类及 deferred-work.md 已有项一致，不重复扩展本次数据库升级范围。 |

不存在需要重谈冻结意图的 intent_gap。循环从3升至4，只细化非冻结实施约束，迁移摘要及 baseline 不变。此次是 step-04 要求的 bad_spec 局部重推导，已有用户授权覆盖修复，不需重问部署权限。

## 主线程核验

从冻结升级器 AST 装载真实初始化和任务判断函数，在 PowerShell5.1/7 内存执行分类：自定义 ps1、cmd、bat 三种输入都返回 false；直接 docker compose up 返回 true，普通 cleanmgr 返回 false。未执行被检查的脚本或 Docker 命令。边界和验收代理另完成环境变量与省略 -Command 的纯函数证据，以及无害 Write-Output 的真实 CLI 对照。

本轮审查期间曾在发布升级器临时增加一条扩展名整体拒绝语句，立即撤回；未以该状态运行测试或构建。另两次未匹配上下文的补丁被 apply_patch 原子拒绝，未改变文件。主线程随后重新确认升级器 SHA256 仍为 `9e449fd98ae0f8bb51ebac8f9fe9813b12a1fb2dc71bf8a420672b3c7e6753e7`，工具测试仍为 `3070c28e6275f3b1599ec89f22917462ae973144c5ebedf22afc5a83305fa52d`。冻结差异未修改，三路报告均以其为输入。全面拒绝所有 exe/脚本的临时思路未纳入实现，会误伤正常 Windows 任务。

## 目标预检事实

2026-09-09 10:01:54 +08:00：目标 `WIN-OAUCM8UQUGH` 在线，活动候选仍 `deploy-20260907.1`、源提交 `2150b5ee904760ce0af4483c201009b8744669a2`，数据库0012；API/GW各内部健康项 ready，devices/device_points/point_data_realtime/point_data_history计数均0。新 guard 回执和维护标记均不存在。权益核验沿用现有 last-seen 协议，没有改变 grant、业务配置或服务。

只读核实两条现有任务：`C:\Ruisheng\tools\start-docker-ruisheng.ps1` 仅启动 Docker 服务/Desktop 后查询容器状态，SHA256 `63806068e41fc1c3521a540c130a0eab747d99d9e0c8a3ada06a9e6a0569c388`；`C:\Ruisheng\tools\serial_hardware_attach.ps1` 负责固定 USB 绑定和 WSL 别名，SHA256 `429cb0902fa6a24aa604b30ac9e0d53dc37575afb576159d9cd3176017be38ac`，与仓库文件一致。两者没有直接启动业务容器的命令。核实内容不等于已经建立受保护的身份放行规则；实现仍须检查固定路径、文件/祖先目录可替换权限、参数范围及摘要，不能按名称广泛放行。这次没有执行这两个任务、USB操作或任何实物读取。

## KEEP 与验证范围

任务实参证据：两个任务宿主均为 `C:\Program Files\PowerShell\7\pwsh.exe`，使用 `-NoProfile -NonInteractive -ExecutionPolicy Bypass -File`。Docker辅助脚本不带脚本实参；串口辅助脚本仅带 `-ConfigPath "C:\Ruisheng\site\serial-hardware.json"`，没有WorkingDirectory。闭集例外只允许这两个实际脚本参数组合，不顺带允许RunOnce、AuditPath或StatePath覆盖。

- 保留43个审定多设备文件、批准0013迁移摘要、原baseline、已部署兼容修复及其他根工作区修改。
- 保留当前升级状态机、停写先行、跨进程清理责任、双锁、权益/验签、后台未决意图、Job Object进程树收容、原角色LOGIN/restart策略、精确引擎/卷/环境身份。
- 保留同快照非空备份/真实还原、函数/权限/触发器指纹、辅助资产停止及不删除策略，不重写与发现无关的恢复代码。
- 保留已通过的CMD复合命令/引号/重定向、PowerShell别名/Start-Process参数解析、受保护启动器检查与Recover收据重验。
- 只重推导入口解析及必要的受限辅助身份验证块，增加有意义的双PowerShell回归。源码改变后重跑升级器工具完整组、受影响真实 guard/Apply 验收，按新证据准确标注覆盖；未经本次修改的原395项主组合及补测作为历史证据保留，不将其冒称R4全量回归。
- 不整包撤销已验证工作、不改迁移或诊断摘要、不推送、签名、上传或执行生产迁移，直到修复测试与独立复审完成。
