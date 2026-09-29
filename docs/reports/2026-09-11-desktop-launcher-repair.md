# 2026-09-11 桌面入口修复

状态：修复、受控安装与目标机入口验收均已完成。

目标 `WIN-OAUCM8UQUGH` 的活动版本为 `deploy-20260911.1`，数据库为 `0013_serial_polling_profile`。用户以管理员打开桌面应用，入口在取得维护租约后的项目容器检查中报 `unexpected_project_container`；五项后台服务仍正常。

## 原因与修复

升级操作 `4d786d05-fb02-4e68-927a-3eec4edaa823` 保留四个已成功退出的迁移校验容器。它们继承 `ruisheng-prod` 项目及 `api` 服务标签，此外保留新旧两个 migrate。旧启动器只按服务名唯一性检查，把升级证据误认为重复服务。截图对应审计操作 `1a4594d0-fbea-49db-b1b5-e6ff5cd032a6`，时间 `2026-09-11T14:45:40.3352419+08:00`。

新启动器将辅助容器与同站点受保护的 `committed/completed` 升级记录交叉核验，检查完整 ID、名称、操作、镜像、退出状态与隔离配置；普通重复服务和未知辅助容器继续被拒绝。已经健康的入口保持 `already_ready`。

有合格留存证据但服务停止时，先核验五个既有服务的固定 ID 和实际配置，然后按 PostgreSQL、Redis、GW、API、Web 顺序启动停止服务。应用启动前等待基础设施健康，只读核对数据库版本。实际配置由活动 Compose 与固定镜像默认值构造，核验环境、命令、入口、用户、目录、数据卷、只读绑定、网络及设备权限。此路径避免 Compose 收敛再次处理旧迁移器或校验容器。

## PowerShell 入口

目标已经安装 PowerShell 7.6.5。桌面快捷方式曾明确指向 Windows PowerShell 5.1；安装 PowerShell 7 不会自动改变该路径。2026-09-11 20:33 已将目标快捷方式切换为 `C:\Program Files\PowerShell\7\pwsh.exe`，并在普通 `lenovo` 会话下验证启动器进程实际使用 7.6.5。

## 验证记录

- 现场形状回归先在两个引擎下复现原错误，再通过修复；拒绝异常身份、状态、权限、网络和挂载。
- 初版完整工具测试 `desktop-launcher-retained-initial-tools-20260911.xml` 为 40 通过、1 个旧静态正则失败；修正测试定位后 `desktop-launcher-retained-final-tools-20260911.xml` 为 41 通过，137.846 秒。
- 初版实际 Docker 两项通过，360.50 秒，结果 `desktop-launcher-retained-docker-20260911.xml`。此结果早于运行配置补强，不能代替最终验收。
- 独立审查发现运行配置核验缺口，先证明同镜像、同数据库版本的错误数据卷会被旧路径放行，再增加配置门禁。审查同时发现集成夹具使用生产项目标签；已改为唯一测试项目。本轮首批 16 个临时测试容器核验所有者及完整 ID 后收尾，测试卷和原结果保留，记录 `desktop-initial-fixture-cleanup.json`；目标机和既有升级证据不受影响。
- 配置补强后的实际 Docker 两项通过，375.72 秒，结果 `desktop-launcher-retained-config-final-docker-20260911.xml`。随后验收复审发现同卷 `VolumeOptions.Subpath` 的数据子树边界，已明确拒绝；原复现脚本在两个引擎均改为拒绝，见 `desktop-launcher-reviews-20260911/acceptance-closed-review.md`。
- 最终脚本 SHA256：`5f3197e9ca5e474f2cebb843b806bafacf5cf36a78ae8ad2897c5c63251bbe73`。独立本地提交 `5568e15a7c017a1603a8043fef034cbabb34cd8c`，提交检查通过、未推送。
- 最终工具回归 `desktop-launcher-final-tools-20260911.xml`：**41 项通过，无失败或跳过**（133.87 秒）。最终真实 Docker 回归 `desktop-launcher-final-docker-20260911.xml`：**两项通过，无失败或跳过**（429.71 秒）；测试前后源码摘要保持，16 个最终测试容器全部停止并保留于独立项目。此前配置补强工具会话未产生 XML，未计入通过结果。

真实 Docker 测试执行实际容器检查、ID 启动和 SQL 版本门禁，覆盖全停、部分运行、全部运行幂等与错误版本拒绝；替换了机器权限/租约及应用健康外围，相关实际流程由工具测试和目标预检、入口验收补足。测试使用独立项目、无网络及宿主端口，未连接生产数据库。

## 安装与现场验收

16:55:24 至 16:55:43，目标预检通过，证据 `desktop-launcher-retained-preflight-7b6eaca303f540cab087fe37b7ae2d5e.json`。真实 Compose、镜像默认值与五服务实际配置匹配；留存容器闭集、应用健康及旧回执通过。两把维护租约实际取得并释放，13 个相关容器稳定快照摘要保持，原入口文件未变，未创建安装 stage。

17:00:45 安装完成，证据 `desktop-launcher-retained-install-e1ab425d9a0b4600860999544c0b9d9a.json`。安装 stage 为 `C:\Ruisheng\tools\desktop-launcher-retained-5568e15a7c017a1603a8043fef034cbabb34cd8c`。旧启动器、图标、快捷方式、保护回执各有唯一原子备份和 SHA256；旧回执摘要仍为历史 journal 记录的 `7d9141e5a74956aec783fdae67db0092141ffaef9ea5a3b10bfc4743fd3e705c`。新入口摘要与审查、测试源码一致，原升级 journal、marker、活动指针、环境、签名候选和备份保持；双租约释放。

17:01:08 开始管理员无 UI 入口验收，使用桌面快捷方式实际指向的 Windows PowerShell **5.1.26100.9444**，执行同一安装脚本 `-NoBrowser -NoUi`。17:01:18 返回 `READY candidate=deploy-20260911.1 url=http://127.0.0.1/`，退出码 0、stderr 为空。新增审计操作 `3dda949a-f0fe-4f41-8553-be7809f650be` 为 `launcher_completed/already_ready`，完整审计链校验通过。

证据 `desktop-launcher-acceptance-9aba93dafdb847a1b2e28ebc97c7bda3.json` 确认：五服务和六个辅助容器共 11 个项目容器的 ID、镜像、启动时间、运行状态、重启次数和核验配置前后保持，未重建或重启服务；API/GW ready、Web 200；数据库保持 0013，设备/点位/实时/历史计数仍为 `0|0|0|0`；维护锁释放，新回执与历史备份均有效。桌面快捷方式目标、参数和工作目录保持，截图所示误报已解除。

已将修复源码、测试及文档同步根工作区，保留其余已有修改；原文件备份位于 `tmp-test-logs/desktop-root-before-sync-5568e15`。

本轮不包含新增串口采集或物理设备读取。此前单次硬件读取授权已完成，正式点表、多设备和持续采集验收仍待确认。

## 普通用户桌面启动补充验收（17:40）

用户要求实际打开目标机桌面应用，并于锁屏检查后确认已解锁。会话工具没有可调用的鼠标或截图 API，因此通过一个无触发器、执行后删除的 InteractiveToken / Limited 计划任务，在既有 `lenovo` Console session 1 中使用 ShellExecute 打开原桌面 `.lnk`。此处是实际桌面进程启动，未执行鼠标双击，也未进行截图或页面内操作。启动保留 UI 和浏览器参数，未使用 `-NoBrowser -NoUi`。

17:29 的普通用户启动暴露第二个问题：`C:\Ruisheng` 及 `C:\Ruisheng\candidates` 的目录 ACL 只授予 Administrators 和 SYSTEM 访问权。站点和候选子目录虽已授予 `lenovo` 权限，启动器仍在 `Resolve-SiteRoot` 首个 `Test-Path` 调用即遭遇 `UnauthorizedAccessException / 拒绝访问`，尚未初始化启动审计。只读诊断在相同普通用户会话中复现该错误，证据 `diagnose-desktop-discovery.remote-d2557de7312d4bb89a33b61f4741302b.json`。此前管理员无 UI 验收不覆盖此权限边界。

已为上述两个目录本身增加 `lenovo` 的 `ReadAndExecute` Allow ACE（Windows 自动附加 Synchronize），不向子目录继承。保留原 owner、管理员/SYSTEM ACE 及继承设置；站点、维护状态、活动候选、启动器和审计目录 ACL 均保持。修复前 SDDL 保存在目标 `C:\Ruisheng\tools\desktop-interactive-eb30757963bb46f6936154dc53ff8a98\discovery-acl-before.json`；完成证据为 `repair-desktop-discovery-access.remote-618d4332ceb14b0c8b502366f72aa049.json`。初次修复检查未计入 Windows 自动增加的 Synchronize，完成第一个目录后停止；只读核实实际状态后修正核验并完成第二个目录，未覆盖原备份。核验完整 PID、启动时间、命令和无维护锁状态后，仅结束本次失败启动产生的进程 16348。

17:40:09 再次从普通用户桌面会话打开同一快捷方式，启动器 PID 18968，session 1，未提权；退出码为 0。随后在同一交互会话内观察到 Edge PID 11508 有响应的主窗口，标题包含“江苏润盛 SCADA”。17:40:17 新增审计操作 `4440433b-0cf4-48b1-8429-83d0f67b8d73` 为 `launcher_completed / already_ready`，错误码为空，活动候选仍为 `deploy-20260911.1`。

完整证据 `desktop-interactive-726eecb4c35048e4895e987a02b3fbc1.json` 确认：启动任务退出 0 且已删除；11 个项目容器 ID、镜像、状态、启动时间和重启次数前后保持；维护锁释放；`http://127.0.0.1/login` 返回 200；桌面保持解锁。此证据验证了真实桌面会话中的快捷方式启动、浏览器窗口出现和服务响应，不代表已登录或逐项验收页面功能。

本次补充修复为目标机持久目录 ACL 修复，后续又完成桌面入口迁移。安装脚本现已固定使用 PowerShell 7 路径并检查主版本；现场另外核验了目标运行时 7.6.5 及其有效签名。后续安装或重建部署目录时，应保留上述仅作用于目录本身的读取权限，并进行普通用户交互启动验收。

### PowerShell 7 入口迁移

安装器和入口说明提交 `8dfa124`，分支 `codex/desktop-powershell7-20260911`，41 项工具回归通过（`tmp-test-logs/desktop-powershell7-tools-20260911.xml`），提交检查通过、未推送。根工作区已同步局部修改；目标采用仅替换快捷方式的操作，新安装器供后续安装使用，历史候选及其安装器保留。

目标只读预检确认 `C:\Program Files\PowerShell\7\pwsh.exe` 版本 7.6.5、Authenticode 签名有效。受控切换保留旧快捷方式备份 `C:\Ruisheng\tools\desktop-powershell7-10470e720a554808876ac86bb10f26c4\shortcut-before.lnk`，只替换快捷方式 TargetPath，未修改启动器脚本、活动候选、数据库、容器或维护锁。旧快捷方式 SHA256 为 `996d92480326e9b6587f09cafba77ce05802c2bfedb5452208747bde67776e67`，新快捷方式 SHA256 为 `0c5b52e77a127cd71170015796ceb592e1df52f3b0be51b25420d062744c239a`。

20:36 普通 `lenovo` 会话复验通过：桌面快捷方式 TargetPath 为 `pwsh.exe`，辅助 PowerShell 报告 7.6.5，实际启动器进程可执行路径与该运行时一致，launcher exit 0，Edge SCADA 窗口存在，11 个项目容器完全不变，Web 200，维护锁释放，临时任务已删除。证据：`tmp-test-logs/switch-desktop-powershell7.remote-41bc7319a63543f8b57f1d2a0276f279.json`、`tmp-test-logs/desktop-interactive-bebcfd9ce5be450790065e7f9bc86f35.json`。首次验收脚本路径替换未生效，仍断言 5.1，在启动前失败；原证据 `desktop-interactive-5b10c169e8df4bbb8b13286fd73c5a6a.json` 保留，任务已删除，无服务变更。
