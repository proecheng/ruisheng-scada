# 点位数据类型：有符号字节 / 无符号字节

## 语义确认

用户已确认这两个类型读取完整的 Modbus 16 位寄存器：

- 有符号字节：`0x0000..0x7FFF` 为 `0..32767`，`0x8000..0xFFFF` 按二补码解释为 `-32768..-1`。
- 无符号字节：范围 `0..65535`。
- 示例：`0xFFFF` 解析为有符号 `-1`，无符号 `65535`。

## 实现

隔离工作区 `C:\Users\admin\AppData\Local\Temp\ruisheng-point-type-hotfix-20260924` 的提交 `45e656d15d5c629a74d4946e469d02575ea5df50` 已完成：

- API `PointValueType` 和网关轮询白名单支持“有符号字节/无符号字节”。
- 网关按完整 16 位寄存器解码，不再只取低 8 位。
- 点位编辑页面显示“完整16位”、取值范围和 `0xFFFF` 示例。
- 回归覆盖 `0xFFFF`、`0x8000`、`0x7FFF` 边界。

验证结果：API/GW 定向 74 passed；网关发布前全量 646 passed、8 skipped；Web 25 个文件、146 passed；Web 类型检查、Lint、生产构建和 `git diff --check` 均通过。

## 受保护部署

目标机 `WIN-OAUCM8UQUGH / 100.109.90.21` 已完成 API 不变、GW/Web 服务级热更新：

- 网关：`ruisheng-hotfix/gw:e49391089f18`，镜像摘要 `sha256:02cd553022d68ff95d7585fb005faf8328008ecddf29084fcbe82b32b2e82bd7`。
- Web：`ruisheng-hotfix/web:45e656d15d5c`，镜像摘要 `sha256:f5136a9a4947d9146d60c14c995c30f37519fca8d8f4cc3d3dbf079e10843036`。
- 活动发布指针仍为 `deploy-20260922.1`，API、数据库、Redis 和 DEV001 点位配置未切换；维护锁已释放。

## 部署期间发现并修复的串口挂载问题

首次只替换网关时，隔离工作区的旧热更新入口没有嵌入受保护的 `site-serial.override.json` 合并逻辑。网关容器虽然健康，但重建后没有 `/dev/ruisheng-rs485`，采集停在 11:32:14。该维护缺口已保留在验收数据中，没有清零或剔除。

已在提交 `e49391089f181be29fa28599ad5be60e30762191` 恢复并校验串口 override 合并逻辑，随后用已验证网关镜像重新部署。恢复快照显示：

- `/dev/ruisheng-rs485` 已挂载，`GW_SERIAL_PORTS` 为 9600 波特率。
- DEV001 已在线，最新轮次完整 38 点，空值 0，末次采样距快照约 5 秒。
- 五项服务运行，维护锁释放。

证据：

- [部署恢复快照](../../tmp-test-logs/field-acceptance-20260916/snapshots/point-byte-types-serial-restore-20260925-115800.json)
- [串口挂载证据](../../tmp-test-logs/field-acceptance-20260916/investigation/point-byte-types-device-mount-20260925.json)
- [观察状态](../../tmp-test-logs/field-acceptance-20260916/observation-state.json)
- 修改前状态备份：[observation-state.pre-point-byte-types-final-20260925.json](../../tmp-test-logs/field-acceptance-20260916/observation-state.pre-point-byte-types-final-20260925.json)

恢复后的最近一小时有 411 个完整轮次、0 空值，但估算缺轮 304，主要包含本次约 26 分钟串口挂载恢复缺口。因此这次发布不能宣称新的 24 小时稳定性通过；原有板端/RS485 超时归因和验收历史继续保留。

现有 DEV001 点位类型没有被修改。后续在点位编辑页选择新类型并保存后，才会对该点位按完整 16 位解释；普通响应超时仍需按已有板端 RX/TX/DE、CRC 和发送完成证据区分固件、转换器与接线。

## 2026-09-26 按建议顺序复核

已对 `WIN-OAUCM8UQUGH / 100.109.90.21` 完成一次受限只读现场快照：活动发布仍为 `deploy-20260922.1`，源码 `68fd3a90df64185ac38c23953b81201040919d43`，五项服务运行，维护锁已释放，DEV001 仍为完整 38 点且空值为 0。

- 最近 1 小时：699 个完整轮次、0 空值、估算缺轮 15，最新样本距快照约 4.17 秒。
- 最近 24 小时：16,795 个完整轮次、0 空值、估算缺轮 394；窗口仍包含缺口，不能宣称新的 24 小时稳定性通过。
- 本次未修改任何现有 DEV001 点位，也未人为写入 `0xFFFF`。完整 16 位语义和 `0xFFFF -> -1/65535` 边界已由隔离回归验证；现场只需 Ctrl+F5 后打开点位编辑页确认两个选项及提示，不要保存现有点位。
- 快照中的日志仅保留最近 4000 行并标记可能截断；其中的超时、补读和耗尽计数不作为窗口总量。普通串口超时/缺轮仍没有完整合法帧到达后被平台漏收的证据，继续按板端固件、转换器和 RS485 接线取证，不调整重试或超时参数。

证据快照：`tmp-test-logs/field-acceptance-20260916/snapshots/point-byte-types-followup-2026-09-26T08-50-00-813Z.json`；状态备份：`tmp-test-logs/field-acceptance-20260916/observation-state.pre-point-byte-types-followup-20260926-1650.json`。
