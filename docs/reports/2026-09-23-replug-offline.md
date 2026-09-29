# DEV001 拔插后离线复核（2026-09-23）

## 现象

目标机 `WIN-OAUCM8UQUGH` 在设备拔插及软件重启后仍显示 DEV001 离线。2026-09-23 19:45:04（北京时间）使用既有有界只读采集入口保存快照 `D:/江苏润盛/tmp-test-logs/field-acceptance-20260916/snapshots/20260923-replug-followup.json`。

## 证据

- 活动版本为 `deploy-20260922.1`，未发生应用发布或源码漂移。
- 受保护配置 `C:\Ruisheng\site\serial-hardware.json` 绑定 `USB\\VID_1A86&PID_7523\\6&BF117A6&0&2`（原 COM6 实例）。
- `usbipd state` 当前仅有 `USB-SERIAL CH340 (COM5)` 在线，实例为 `USB\\VID_1A86&PID_7523\\6&BF117A6&0&1`、BusId `4-1`；配置的 COM6 实例无 BusId，未处于可附加状态。
- `Ruisheng-Serial-Hardware-Attach` 任务仍在运行，最近状态文件结果为 `failed`；错误对应 `device_not_present`。`Ruisheng-Serial-Gateway-Recovery` 最近结果为 0，但没有可恢复的串口别名。
- `ruisheng-gw` 和 `ruisheng-web` 均为 `created`，网关没有启动；DEV001 最后完整 38 点样本为 2026-09-23 19:15:17（北京时间），之后无新采样。
- 配置 ACL 仍受保护，仅 SYSTEM、Administrators 可写，`lenovo` 只有读取权限；本次没有改配置、没有重启容器、没有发送现场指令。

## 结论

直接根因是 CH340 无唯一序列号时依赖 Windows USB InstanceId 的精确身份绑定发生变化。当前 COM5 可能是同一转换器换了 USB 插口，也可能是另一台同型号转换器；在没有现场确认前不能把 COM5 自动当作原 COM6。应用层轮询代码没有机会运行，因为网关容器缺少 `/dev/ruisheng-rs485`。

## 下一步

1. 现场确认当前 COM5 是否为原来 COM6 的同一台 RS485 转换器；若不是，插回原来显示 COM6 的 USB 插口。
2. 确认后按受保护硬件配置流程备份配置、更新 `instance_id`、运行硬件附加任务和网关恢复任务。
3. 复核 `/dev/ruisheng-rs485`、五项服务、DEV001 在线状态及新的 38 点样本；恢复前不宣称采集通过。
