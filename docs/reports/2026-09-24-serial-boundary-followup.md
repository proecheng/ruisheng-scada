# 2026-09-24 串口边界取证跟进

## 结果

目标机身份已重新核对为 `WIN-OAUCM8UQUGH / 100.109.90.21`。此前出现 `CXG-PC` 是本地 PowerShell 双引号命令把本地 `$env:COMPUTERNAME` 展开后显示的误读；没有连接到错误主机，也没有对目标机做修改。

受保护只读快照已保存到 `tmp-test-logs/field-acceptance-20260916/snapshots/controlled-before-20260924122224.json`。在 2026-09-24 11:51 左右网关恢复运行后的当前窗口内，数据库仍是完整 38 点、空值 0；最近窗口 367 个完整轮次，中位间隔 5.0669 秒，P95 6.1860 秒，估算缺轮 7。同期网关日志有 58 次首次 `serial response timeout`、50 次补读尝试、42 次补读恢复、8 次补读耗尽；五项服务均为 running，维护锁已释放。

这说明 COM 自动适配已恢复轮询入口，但普通首帧超时和少量耗尽仍在发生。完整帧能够入库，当前没有证据表明平台收到合法帧后漏收或漏入库；也不能把这 7 个估算缺轮全部归给单一硬件部件。

## 已核对的硬件边界

部署回执 `tmp-test-logs/ch340-auto-com-20260923/post-deploy-validation.json` 显示：当前唯一有效 CH340 为 `USB\\VID_1A86&PID_7523\\6&BF117A6&0&1`、`BusId 4-1`、`COM5`，映射 `/dev/ttyUSB0` 和 `/dev/ruisheng-rs485`；旧 `...&0&2 / COM6` 保留为 `BusId=null`，没有被选择。目标机当前应用版本、数据库和设备配置未改变。

本轮尝试继续读取目标侧 USBIP/CH340/PnP/串口边界时，`usbipd state/list` 的原生命令返回非零状态，使有界诊断在生成回执前退出。该诊断没有打开串口、没有发送数据、没有留下维护锁；失败证据和脚本保留在 `tmp-test-logs/field-acceptance-20260916/investigation/serial-boundary-diagnostic-20260924.ps1`，状态也写入 `observation-state.json`。既有部署回执中的 USBIP 枚举仍是当前有效证据。

## 归因与下一步

目前结论分两层：COM/USB 实例变化导致别名缺失、网关不轮询是已确认并已修复的平台缺陷；剩余普通超时在 USB 接收端表现为无完整合法帧或帧错误，尚未隔离为采集板固件、RS485 转换器还是接线/终端电阻问题。没有新的完整合法帧到达但平台未接受的证据，不调整应用超时、重试或 CRC，也不重复长时间抓包。

现场需要从同一次请求记录四类数据：板端 TX/RX 原始字节和长度、UART framing/overrun 错误及 RX 恢复、CRC 校验结果、发送完成 `TC` 与 RS485 `DE/RE` 切换时间。对照规则是：板端 TX 已完整而 USB 端无帧，优先查转换器/接线/DE；板端 TX 截断或 CRC/长度错误，优先查固件；两端都有完整合法帧而网关未入库，才回到平台软件复现。DO 空载不能替代这组只读采集证据。

## 证据与限制

- [CH340 自动适配部署记录](2026-09-24-ch340-auto-com-deployment.md)
- `tmp-test-logs/ch340-auto-com-20260923/post-deploy-validation.json`
- `tmp-test-logs/field-acceptance-20260916/snapshots/controlled-before-20260924122224.json`
- `tmp-test-logs/field-acceptance-20260916/observation-state.json`

本次跟进没有修改应用、配置、重试/超时参数或固件，也没有宣称新的 24 小时稳定性验收通过。历史停采、缺轮和原验收失败结论继续保留。
