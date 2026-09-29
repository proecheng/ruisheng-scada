# 2026-09-24 CH340 自动 COM 适配部署记录

## 结论

目标机 `WIN-OAUCM8UQUGH` 已完成受保护部署。当前在线的 CH340 是 `COM5 / BusId 4-1 / InstanceId ...&0&1`；旧配置指向的 `COM6 / ...&0&2` 仍是 usbipd 残留，`BusId=null`，不会被选择。别名已经恢复为 `/dev/ruisheng-rs485`，应用发布 `deploy-20260922.1` 和源码 `68fd3a90df64185ac38c23953b81201040919d43` 保持不变。

部署后的短时复测显示 DEV001 在线，最近 24 个轮次全部为完整 38 点，空值 0，估算缺轮 0，采样中位间隔约 4.99 秒，P95 约 6.24 秒。五项服务均为 running，容器 ID 和镜像保持不变；网关/Web 是恢复原有 `Created` 容器后启动，没有重建或换镜像。

这解决的是“设备换 COM/USB 实例后，串口别名无法自动恢复、网关无法开始轮询”的平台侧缺陷。它不等于已经消除板端/RS485 的普通响应超时；部署后日志仍出现 3 次首次超时且补读恢复，既有物理归因和历史缺轮继续保留。

## 根因与修复

旧逻辑把无稳定序列号的 CH340 绑定到旧 Windows InstanceId。设备拔插或更换 USB 口后，旧实例可能仍出现在 `usbipd state`，但没有有效 `BusId`，导致当前在线设备没有候选，稳定别名被删除，网关只能离线。第一次候选还暴露出绑定/附加后立即读取 `usbipd state` 的瞬时枚举窗口，会把短暂无 BusId 误记为 `invalid_bus_id`。

本次候选只接受 VID/PID 匹配且 `BusId` 符合 `数字-数字` 的当前在线设备；配置使用 `identity_policy=single_present_device` 时，只有恰好一个同型号在线设备才自动重绑定到新的 InstanceId，多设备时 fail-closed。绑定和附加后的 `device_not_present`/`invalid_bus_id` 只在配置的 5 秒窗口内有限重读，歧义和其他错误立即失败。应用重试、响应超时和 CRC 没有调整。

第一次应用在硬件绑定成功后发现原有 gw/web 容器处于 `Created`，既有 Gateway Recovery 任务按设计只处理明确的“串口设备缺失启动失败”，因此按保护逻辑回退。第二次 `r4` 候选增加了受控的 `docker start`：只启动这两个预先核对过 ID/镜像的原容器，不创建替代容器；应用后状态已恢复正常。

## 证据与验证

- 隔离源码：`C:\ProgramData\Ruisheng\publisher-build\ch340-auto-com-20260923`，提交 `94972e486465a125af7c05517d64576521509d1e`。
- 候选阶段：`C:\Ruisheng\tools\ch340-support-20260923-auto-com-r4`；签名指纹 `SHA256:Go/TiuSZ89zJvTCzVick7GT6gP6yrOK+CRdYMRp07Fk`。
- 硬件部署回执：`D:\江苏润盛\tmp-test-logs\ch340-auto-com-20260923\installation-receipts.txt`。
- 目标机部署后核验：`D:\江苏润盛\tmp-test-logs\ch340-auto-com-20260923\post-deploy-validation.json`。
- 现场快照：`D:\江苏润盛\tmp-test-logs\field-acceptance-20260916\snapshots\post-auto-com-20260924035142.json` 和 `post-auto-com-recheck-20260924035303.json`。
- 观察状态：`D:\江苏润盛\tmp-test-logs\field-acceptance-20260916\observation-state.json`。
- 自动适配相关测试：35 项通过；PowerShell 语法检查通过。此前已有应用、API/GW/Web 健康和回归证据保持不变。

## 后续

保留当前任务和证据继续短时/现场观察。后续拔插或换 USB 口时，只要同时只有一个同型号 CH340，任务会按当前在线 InstanceId/BusId 重建别名并继续轮询；同时在线多个同型号设备时会拒绝盲选并写审计。普通串口超时若再次出现，仍需板端 RX/TX/DE、CRC、发送完成和 RS485 接线证据来区分固件、转换器和接线，不能把它归因给这次 COM 适配修复。
