# 2026-09-11 管理员登录交付检查

目标为 `WIN-OAUCM8UQUGH`。截至 20:12，活动候选为 `deploy-20260911.2`，账号创建、日期输入修复发布、登录巡检和普通用户桌面启动验收均已完成。用户最初要求登录并逐项操作，随后说明不知道账号密码；以下保留当时 `deploy-20260911.1` 上的调查过程。

18:05 的只读数据库检查确认：用户 0、租户 0、有效管理员 0、首位管理员创建审计 0。不存在可恢复的既有应用账号；生产部署没有默认账号密码。本机 `C:\ProgramData\Ruisheng` 下未发现默认管理员 DPAPI 凭据目录。目标 API 镜像包含 `ruisheng_api.admin_bootstrap`。

固定版本维护入口 `remote_admin_bootstrap.ps1 -Action Plan -ExpectedCandidateId deploy-20260911.1` 通过，返回 `planned`；操作 `d6c9a11e-3363-4f38-ab69-11150927484a`，目标及发布者签名镜像核验通过。

用户明确回复“创建，继续”后，已执行一次 Create 并立即 Status：操作 `4e95c07e-6999-4a78-bfb0-14ef0df76025`，分别返回 `created` 和 `confirmed`。首个账号为 `rs_admin`，租户 `site-win-oaucm8uqugh`，角色 Administrators、设备控制权限 0。随机密码已由原工具保存在本机当前 Windows 用户专用的 DPAPI 文件，未打印、截图或保存明文。不可重复创建、删除或重新生成凭据。

本机原 Windows PowerShell 5.1 查看窗口启动遇到模块加载问题，改用本机已有 PowerShell 7.6.4 运行同一个受保护查看函数。交互检查通过（UserInteractive=true、stdin 未重定向），私密窗口随后记录为 `closed_by_user`；这仅证明窗口由用户关闭，不证明密码已另行保存。

另已通过 SSH 回环隧道，以本机无头 Chrome 访问目标实际 Web，确认登录页 HTTP 200、标题“江苏润盛 SCADA”、用户名/密码/登录按钮可见、未登录访问 dashboard 重定向到登录页、无 pageerror。浏览器和本次隧道已关闭。该检查不是目标桌面 Edge 的鼠标操作，未输入凭据或进入业务页面。

证据：

- `tmp-test-logs/inspect-admin-account-state.remote-676c25f2138c41b7b96faac8b91009ea.json`
- `tmp-test-logs/target-login-browser-5ea1669a-4e08-4bc4-b36f-c77b83807017.json`

18:27 和 18:31 已使用上述凭据在本机无头 Chrome 经 SSH 回环隧道登录目标实际系统。登录 HTTP 200，14 个顶级页面可打开；业务查询正常，健康诊断 `/api/health/ready` 的 403 为既定 ACL。刷新会话、侧栏、退出及退出后的受保护路由检查通过，WebSocket 最终 open。无 trace、录像或登录截图，未保存浏览器会话。该验收不等于接管目标桌面 Edge。

第二轮真实页面操作覆盖设备筛选/添加表单切换并取消、告警筛选、空日报生成、波形必填校验、计划表单、组态新建取消、用户筛选和编辑取消、非法手机号校验。发现两个真实缺陷：定时和保养表单清空日期时触发 Invalid time value，使全局 ErrorBoundary 取代页面。源代码已修复空日期转换和保存校验；四个浏览器回归验证新增/编辑、空值不提交、重新填写和时区转换，修复前四项失败、修复后四项通过。类型检查和计划 API 的三项单元测试通过。发布及现场复测待后续记录。

新增证据：

- `tmp-test-logs/target-authenticated-browser-168c08d4-ae92-46a3-8499-a1cfc156aaaa.json`
- `tmp-test-logs/target-authenticated-browser-6b68480c-be4f-4a5b-aa49-eb31722f49b2.json`（设备搜索断言文案不匹配另记为测试问题，日期两项为产品缺陷）
- `tmp-test-logs/plan-date-playwright-red-20260911/`

### 日期输入修复与候选发布准备

第二轮真实页面操作发现：定时计划的 `datetime-local` 与保养计划的 `date` 输入清空时直接调用 `toISOString()`，会抛出 `Invalid time value`，由全局 ErrorBoundary 替换整个页面。已在独立发布树 `C:\ProgramData\Ruisheng\publisher-build\plan-date-input-repair-20260911` 修复：空值保留为空，保存前验证有效日期，保养日期输入增加 `required`。提交 `221f81c4e8d4ef9346572b7045f648c55e2c95d5`；四项新增/编辑 Playwright 回归、类型检查和计划 API 单元测试均通过。

使用未修改的 API/GW/数据库源码和镜像身份、仅替换 Web 镜像生成签名候选 `deploy-20260911.2`。候选签名验证通过，Web 镜像为 `sha256:dfc4f2cfa84d4948a46fed955b12d617de2fe867ef1a0eb8d0f04f0bd140b343`，候选逻辑身份 `sha256:b02ffa7b539311af7229f273409b30c5f244ee3f84e42d94c26cb1a97ab8366e`。从候选镜像提取的 125 个静态资源通过路径、大小和层哈希检查；使用候选静态 Web + SSH 回环到目标后端的真实浏览器复测通过，证据 `tmp-test-logs/target-authenticated-browser-20305040-a46f-4e2a-b93c-a74e57dc9f21.json`。

本机 Docker Desktop 在隔离全栈验收启动 API 容器时返回 502，随后日志显示 `dockerInference` reparse socket 无法访问并退出；该验收结果为失败并保留于 `tmp-test-logs/plan-date-apps-bb7eb26a0ed24b6d895fe7565c2b8fad/result.json`，不把候选标记为全栈通过，不影响目标机当前运行版本。候选未上传、未切换、未重启目标服务。

19:13 目标管理员只读核验为 `users=1/groups=1/active_administrators=1/bootstrap_audits=1`。19:29 目标五项服务健康、Web 200，设备/点位/历史仍为 0，原 11 个容器和维护锁状态正常。

本机 Docker 故障已修复：正常停止 Docker 后，仅将包含零字节 AF_UNIX 套接字的运行时目录备份重建，未删除镜像、数据卷或其他项目文件。第一次失败的 5 个隔离容器均核验为已停止或 created，补充 `recovered-owned-assets.json`；随后重跑的 `plan-date-apps-55cdf90473a54774ab4f0800b407e6ff/result.json` 全部通过，候选签名/镜像、内部网络、API/GW readiness、DB0013、Web200/管理ACL403均符合预期，容器全部停止，存储保留。

只读 Plan 通过，操作 `7c9a272f-1bd5-4527-873f-090a9fc159c4`，结构为 `same_head`，不需要数据库结构升级，资源足够且两锁不存在。用户在修复任务中再次明确“继续”；沿用用户要求修复目标系统的授权，完成全部前置验收后于 19:34:44 启动一次新的受控 Apply。证据目录 `tmp-test-logs/plan-date-deployment-20260911`。首次本机派发助手因 Python 默认 GBK 读取 UTF-8 浏览器证据而在派发前退出，修正显式 UTF-8 后才执行；没有重复远端 Apply。经 Tailscale 中继上传后，该操作于 20:07 完成。

### 日期修复已部署并完成现场验收

受控 Apply 操作 `7c9a272f-1bd5-4527-873f-090a9fc159c4` 返回 `ok=true/status=committed`，目标于 20:07:32 提交 `deploy-20260911.2`，源提交 `221f81c4e8d4ef9346572b7045f648c55e2c95d5`。无数据库结构迁移，数据库和角色备份、原环境备份及 journal 保留。

20:09 目标只读复核、20:11 本地证据校验确认五项运行服务镜像均与签名候选一致，API/GW ready、Web 200、数据库 `0013_serial_polling_profile`，用户/有效管理员为 1/1，设备/点位/历史为 0/0/0，维护锁已释放。证据：`tmp-test-logs/plan-date-deployment-20260911/apply-result.json`、`tmp-test-logs/plan-date-deployment-20260911/post-deploy-verification.json`、`tmp-test-logs/inspect-plan-date-target.remote-770cdb62b9d84cec9d3d49c59241f985.json`。

20:10 至 20:12，本机无头 Chrome 经 SSH 回环访问目标实际新 Web 和后端，完成 14 个页面、10 类交互、刷新会话、WebSocket open 和退出登录；无 pageerror，定时及保养计划清空日期回归通过。本轮未设置候选静态资源模式。该证据不是接管目标桌面 Edge：`tmp-test-logs/target-authenticated-browser-59985c9e-b489-4bfb-917a-c2226e50d5e8.json`。

20:12 在目标 Console session 1 的普通 `lenovo` 用户下 ShellExecute 桌面快捷方式，启动器 PID 16796 退出 0，Edge SCADA 窗口存在且有响应。11 个项目容器的身份、状态、启动时间和重启次数保持，维护锁释放，Web 200，临时任务已删除。新审计 `a7aa00b9-165c-4053-b0f0-422de39a26fd` 为 `launcher_completed/already_ready`，候选为 `deploy-20260911.2`。证据：`tmp-test-logs/desktop-interactive-efbd2e73c9fb48f7af741db51cb3c820.json`。这是快捷方式启动验收，未实施目标 Edge 内的鼠标双击或登录输入。

本次登录巡检和日期修复闭环完成。当前数据为空，真实设备点位、校准和持续采集仍未完成验收。随后已将桌面快捷方式切换到签名有效的 PowerShell 7.6.5，并完成普通用户启动复验；SSH 维护入口仍按既有受控脚本使用 Windows PowerShell 5.1。
