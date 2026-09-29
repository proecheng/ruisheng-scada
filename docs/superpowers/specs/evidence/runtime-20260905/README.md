# 远程运行测试记录（2026-09-05）

## 验收结论

目标机 `WIN-OAUCM8UQUGH` 的已部署服务、启动器、远程调试和窄范围只读 Modbus 通信通过本轮复验，但不等于生产采集上线。生产库目前 `users=0`、`active_admins=0`、`devices=0`、`device_points=0`，尚无客户可用账号或已确认点表。未创建现场账号，未写现场业务表，未开启持续轮询、控制、告警通知或付款。

## 实机证据

- 活动候选仍为 `deploy-20260903.2`，源码 `1093e21a5172c7cb2be3bdb37fc157a70792b6aa`。五容器 ID、镜像、启动时间、重启次数及活动指针、站点配置保持不变。
- API 内部检查：database/redis/service/status 均为 ready。GW 内部检查：database/redis/batch/outbox/service/status 均为 ready。Web 和版本接口 HTTP 200；公开 GW 健康入口仍按 ACL 返回预期 403。
- 已安装桌面启动器以 `-NoBrowser -NoUi` 实际运行，返回 `READY candidate=deploy-20260903.2`，复用已有服务，无 Windows/Docker/应用重启。
- SSH 公钥连接保持；通过回环隧道执行真实浏览器测试，1440x900、390x844 均无页面异常或横向溢出，虚假凭据得到正确登录错误，未认证设备接口 HTTP 401。
- 适配器为 FTDI `0403:6001 / AI06JYFW`，USBIPD 总线 `1-9` 已附加；`/dev/ruisheng-rs485 -> /dev/ttyUSB0`。Windows COM 列表为空是适配器已交给 Linux 的表现，不据此判为 USB 缺失。
- 签名安装回执与运行中 GW 镜像一致，认证 runner dry-run 成功后，执行既有 `9600/8N1`、unit 1、FC3 两段白名单。共发送 2 个读请求，无重试、无控制/写寄存器帧。

| 范围 | 原始寄存器 | 校验 | 时延 |
|---|---|---|---|
| 0..5 | `[3,0,0,0,0,0]` | 地址、功能码、长度、CRC 正确 | 241.274 ms |
| 27..35 | `[3,0,0,0,0,0,0,0,0]` | 地址、功能码、长度、CRC 正确 | 166.912 ms |

数值与历史观察一致，只证明两个范围可读；不证明型号、寄存器语义、符号、单位或倍率。探测前后生产状态完全一致，临时探测容器已由认证 runner 清理。

本机证据：

- `C:\ProgramData\Ruisheng\publisher-output\entitlement-evidence\runtime-pre-20260905-01.json`
- `C:\ProgramData\Ruisheng\publisher-output\entitlement-evidence\runtime-post-20260905-01.json`
- `C:\ProgramData\Ruisheng\publisher-output\entitlement-evidence\runtime-hardware-20260905-01.json`
- `D:\江苏润盛\tmp-test-logs\runtime-remote-browser-20260905.json` 及同目录桌面/手机截图。
- `D:\江苏润盛\tmp-test-logs\modbus-probe-runtime-20260905-execute.jsonl`，SHA-256 `c0f9213622a14cb8d1208db1be59f7a8b6fb9c13e7ab3b7202afb32ff8bfd957`。
- `D:\江苏润盛\tmp-test-logs\modbus-runner-runtime-20260905.jsonl`，SHA-256 `67f50f99b9d20917f8bc376c585ab93d25d55105c52dc7ec8f571a54f9c41927`。运行 ID `91c072ac-f9c0-45ca-a89a-49de8cd23e19`。

## 修复与回归

1. `tools/remote_debug.ps1`：SSH 子进程原先被目标机 Restricted 策略拦截；只对受控子进程添加 `-ExecutionPolicy Bypass`，保留系统策略、订阅校验和 ACL。
2. 同一工具：一年租约的剩余毫秒数超过 Int32，PowerShell 5.1 选择 `Math.Min` 整数重载导致隧道退出；显式使用 double 截断后再转整数。远程调试定向回归 13 passed，包含长租期及不足 1 毫秒边界。
3. 真实后端 E2E：诊断页预期健康 ACL 403 被浏览器 console 监听器误报。仅豁免同源、精确路径、无查询/片段且精确文本匹配的该条资源错误，其他响应继续失败；新增 8 项白名单回归。
4. 历史 MDF 提取器：健康检查升级使受证据绑定的网关源码发生变动；审查差异后刷新摘要与定位，在 `../b08-20260905/` 生成新快照，原快照不变。新快照 SHA-256 `C95DA794A3CAAFCB8A5F2A1B35F4EA41FDA38CF7BB963B1E34C7EED0FB00B000`，提取器 SHA-256 `90757AA4DD0D0D320655DADFC5F37C5E849CB130AC19D836CE8A1A668EA7504E`。

测试记录：

- 完整 Python 首轮：1960 passed、18 skipped、1 failed、3 errors；全部失败来自同一旧证据摘要不匹配。修复后完整提取器测试 41 passed、1 POSIX-only skipped。没有把定向复验冒充第二次完整 Python 运行。
- 前端单测：98 passed；模拟后端页面 E2E：20 passed，2 个真实后端用例按条件跳过；随后隔离真实后端的这 2 个用例独立重跑，2 passed。
- 独立数据库集成：41 passed、7 Docker readiness timeout skipped；15 个回放用例在 Windows 的 Unix socket 初始化失败，随后以 Python 3.11 Linux 容器、锁定 workspace 依赖及独立 PostgreSQL/Redis 完整复验，15 passed（52.95 秒），验证实际接收、实时落库与历史记录数量。没有修改 Unix socket 安全边界来迁就 Windows 测试。
- Ruff 全项目、Python 格式检查、前端 ESLint、Vue typecheck/生产构建通过；构建仍有既有 ECharts 大分块警告。Mypy 按 174 个实际源码文件通过；全目录命令会被既有 `tmp-test-logs/b08-provision` 中复制的标准库干扰，此次未修改这些临时文件。
- 所有增删改、演示种子、计划、控制命令/支付订单的业务流程测试均在独立本机测试库进行；无采集网关连接该库，不会作用于现场设备或真实支付平台。
- 现场前后快照逐字段比较通过：活动版本、指针、配置、容器、业务表计数及 GW 串口权限均不变。测试结束后停止本次本机独立数据库/Redis，保留容器和数据供复查；一次性 Linux 回放容器由 `--rm` 自动移除，仅含可重新安装的测试依赖。

## 未解除的门禁

- 首位管理员引导和凭据交接仍未实现，需要单独授权设计与实施，不能向现场导入公开演示账号或读取此前聊天中的密码。
- 设备型号/固件/点表版本和物理量关联未决，`s16` 支持、原子禁用态入库、串口跨进程排他及受控配置应用仍需后续实现与验收。不能根据这次读取擅自命名温度、电压或启用生产轮询。
- 本轮修复未推送、未创建 PR、未合并、未重新发布应用镜像。调试修复位于本机控制工具并已在目标机实测；现有签名候选文件未改动。
