# B-06 Modbus 协议验证证据账本

## 证据边界

本账本只记录只读协议候选和可复核观察，不把旧 MDF、演示种子或单次真机响应当作设备定义。当前允许的唯一执行 scope 是 `9600/8N1`、从站地址 `1`、FC3，先读 `0..5`，首段有效后再读 `27..35`，每段最多重试一次、总 TX 不超过 4。任何写功能码、自动扫描、生产数据库写入或 GW 轮询均不在本账本授权范围内。

## 候选来源与冲突

| 候选 | 证据 | 等级 | 结论 |
|------|------|------|------|
| Modbus RTU CRC16 / 低字节在前 | `济南大学开发软件/设备录入工具/电能终端波形分析工具/Form1.cs:116` 的 `0xA001` CRC；新 GW `ruisheng-gw/src/ruisheng_gw/protocol/modbus_codec.py:17` 语义一致 | 代码佐证 | 可用于帧校验，不证明设备型号 |
| FC3 读保持寄存器 | `ModBusServer20210908/ModBusServer20210908/ModBusServer/ModBusServer.cs:1744`、`:1913` 存在 FC3 数据包分支 | 代码佐证 | 只允许本轮 FC3 白名单；旧代码同时接受私有码不能随同迁移 |
| `9600/8N1`、地址 1、`0..5 -> 27..35` | 旧程序/MDF 联合取证形成的首轮候选，并已由下面两帧响应支持 | provisional | 仅作为当前窄 scope，不外推其他设备 |
| BCMM / CBMM 型号 | 旧 MDF 中同时存在两种候选 | 冲突 | 未决；响应成功不能消除冲突 |
| 点名与倍率 | 旧点表/公式存在历史值，但缺少可绑定当前实物的型号和点表版本 | 冲突 | 未决；不得机械导入生产库 |

## 首次 provisional 真机观察

- 运行标识：`df3086aa-f2e2-4ddf-84e9-a20e24cec76b`
- 旧临时审计：`C:\Ruisheng\audit\modbus-probe-20260825T1238.jsonl`
- 旧审计 SHA-256：`E32836A76E3F9332589A01DF04D7DCF3D224D7463B1A47E814451B53B1264029`
- 该运行发生在修正版签名安装链和主机 runner 完成前，只能保留为 provisional 证据，不能作为最终发布验收。

```text
TX 010300000006c5c8
RX 01030c0003000000000000000000009c34
原值 [3,0,0,0,0,0]，110.216ms

TX 0103001b0009f5cb
RX 0103120003000000000000000000000000000000000272
原值 [3,0,0,0,0,0,0,0,0]，268.614ms
```

两组响应的地址、功能码、字节数和 CRC 均有效。固定结论：**仅证明区间可读，型号/点名/倍率未决**。

## provisional 前后状态证据

首次运行前后观察到以下脱敏状态：五个应用容器保持健康；运行中 GW 不含 `GW_SERIAL_*` 环境或设备映射；`devices/device_points` 计数为 `0/0`。这些状态没有认证 runner 的独立前后原始快照，因此同样不提升为最终验收。

修正版 runner 的最终证据必须包含：认证发布回执及其 SHA-256；运行中 GW 的 `.Image` `sha256:` ID；runner/probe/config SHA-256；审批 scope；五个容器的 ID、image ID、启动时间、重启计数和受限健康状态；`devices/device_points` 只读计数原文；GW 串口环境、device、bind、mount、privileged 和 device-cgroup 的脱敏结果；probe 唯一 JSONL 路径、run ID、退出码、即时 SHA-256 及前后状态一致性。probe 审计失效时以独立 runner JSONL 记录已知 TX 数或 `unknown` 和无效原因，不得宣称故障文件完整。不得记录容器完整环境、数据库 URL、密码或令牌。

## 认证候选与安装

- 最终签名候选：`deploy-20260827.2`
- 来源提交：`12a76fa37a10d7679b03604409ed912cb6009a99`
- 目标机安装目录：`C:\Ruisheng\candidates\deploy-20260827.2`
- 认证回执 SHA-256：`2a5a2f682446bf501c1be7330c2fd53a5c4584bcfcf7040d8c8ac10b3f8702c6`
- 安装的 runner SHA-256：`9537d7249ca50d853ccd8dfd998fff843e4d25b8d8a8551f8e546e27eaa35489`
- 运行中 GW 不可变 image ID：`sha256:74e16fda69b946aa67de99bd20167e63dc29e835bb7447cd924a7c91508584e8`

候选已完成签名、精确 allowlist、全包哈希、五镜像实际加载及 image ID 校验。Windows bootstrap 在 `-InstallSerialTools` 下完成认证工具安装，但候选校验仍按 B-04 设计返回退出码 `2`；没有借 B-06 解除生产启动边界，也没有重建或切换生产服务。

## 最终 dry-run

- runner 审计：`C:\Ruisheng\audit\modbus-runner-64d6e4bb-c6c0-411c-a193-0e80a5ce6f44.jsonl`
- runner 审计 SHA-256：`f703b8887a51622679443dcded0a1eb0ffa41bd6bf4c9e8e0ea39f11b1f4930d`
- 输出帧：`010300000006c5c8`、`0103001b0009f5cb`

dry-run 未映射或打开串口、未发送数据，也未生成 probe 审计；只生成了可复核计划和独立 runner 审计。

## 最终认证真机验收

- run ID：`9ec05b61-3081-49bd-8020-55fb78a9dcd7`
- 审批 scope：`b06-9600-8n1-unit1-fc3-r0-5-r27-35`
- 退出码：`0`
- 已完成 TX：`2`；尝试写入字节：`16`
- probe 审计：`C:\Ruisheng\audit\modbus-probe-execute-20260827T1314+0800.jsonl`
- probe 审计 SHA-256：`e39cfc742f724e864686595635373832bde48c10831ab08f6118e7cb376e489e`
- runner 审计：`C:\Ruisheng\audit\modbus-runner-9ec05b61-3081-49bd-8020-55fb78a9dcd7.jsonl`
- runner 审计 SHA-256：`e0a1459363e34d33a5d4b783c0f40d1fa8300f9f1556f2e9c41f9a0238b656dc`
- 本地证据副本：`docs/superpowers/specs/evidence/b06-20260827/`

| 请求 | TX | RX | 校验 | 原始寄存器 | 时延 |
|------|----|----|------|------------|------|
| FC3，地址 `0`，数量 `6` | `010300000006c5c8` | `01030c0003000000000000000000009c34` | 地址、FC、长度及 CRC 有效 | `[3,0,0,0,0,0]` | `275.509ms` |
| FC3，地址 `27`，数量 `9` | `0103001b0009f5cb` | `0103120003000000000000000000000000000000000272` | 地址、FC、长度及 CRC 有效 | `[3,0,0,0,0,0,0,0,0]` | `138.773ms` |

固定结论：**仅证明区间可读，型号/点名/倍率未决**。

## 生产隔离与停电恢复复核

runner 记录的探测前后生产状态完全一致：五个容器的 container ID、image ID、`StartedAt` 和 `RestartCount` 均未变化；postgres/redis 为 `running + healthy`，API/GW/Web 为 `running`；`devices/device_points` 为 `0/0`。GW 的 `GW_SERIAL_*`、devices、`/dev` bind/mount、device-cgroup rule 均为空，`Privileged=false`；运行结束后无 `ruisheng-modbus-probe-*` 残留容器。

目标机恢复供电后，于 `2026-08-27T13:24:04+08:00` 又执行了一次不触碰串口的只读复核，原始脱敏结果保存为 `docs/superpowers/specs/evidence/b06-20260827/post-recovery-state-20260827T1324+0800.json`，SHA-256 为 `111aff7410faa0ecbe567a02023173a29f936792eb7b9a847023669b9d8a1671`。五个生产容器仍是验收时相同的 container/image ID，状态和重启计数不变；数据库仍为 `0|0`，GW 权限仍为空且无残留探测容器。上述两份最终审计的目标机 SHA-256 也与本地证据副本一致。系统报告的最近启动时间为 `2026-08-25T00:35:03+08:00`，因此本次只表述为恢复供电/在线，不推断发生过操作系统重启。
