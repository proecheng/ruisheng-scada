# 站点验收参数

本文件在 Gate 0 开始前由项目负责人、运维负责人和客户代表共同批准。`UNRESOLVED` 不是默认值；任一适用字段未决时，对应 Gate 为 BLOCKED。批准后的字段发生变化时，记录版本并重跑受影响 Gate。

## 审批

| 字段 | 决定 |
|---|---|
| Profile ID / 版本 | UNRESOLVED |
| 项目负责人 | UNRESOLVED |
| 运维负责人 | UNRESOLVED |
| 客户代表 | UNRESOLVED |
| 安全/合规负责人 | UNRESOLVED 或批准 N/A |
| 批准时间 | UNRESOLVED |

## 目标环境

| 字段 | 决定 |
|---|---|
| 站点/机房/云环境 | UNRESOLVED |
| CPU 架构 | UNRESOLVED |
| OS 及版本 | UNRESOLVED |
| CPU / 内存 / 可用磁盘 | UNRESOLVED |
| Docker Engine/Desktop、Compose、WSL2/usbipd 版本 | UNRESOLVED |
| NTP/时钟源、时区与允许偏差 | UNRESOLVED |
| 日志保留、轮转与磁盘告警阈值 | UNRESOLVED |

## 网络与安全

| 字段 | 决定 |
|---|---|
| 用户、设备、运维和外部服务网段 | UNRESOLVED |
| 暴露端口及源地址白名单 | UNRESOLVED |
| HTTP 仅限隔离可信网，或 HTTPS/WSS 域名与证书方案 | UNRESOLVED |
| health/metrics 访问主体和防护方式 | UNRESOLVED |
| 站点密钥生成、交接、轮换和恢复保管人 | UNRESOLVED |
| 发布签名/可信分发机制、验证密钥指纹和发布负责人 | UNRESOLVED |
| 合规、渗透测试和审计保留要求 | UNRESOLVED 或批准 N/A |
| 验收证据存储位置、访问控制和保留期 | UNRESOLVED |

## 真机与控制

| 字段 | 决定 |
|---|---|
| 设备型号、数量、协议/点表版本 | UNRESOLVED |
| 稳定设备路径或 Windows USB 映射 | UNRESOLVED |
| 波特率、校验位、数据位、停止位 | UNRESOLVED |
| 每总线 Modbus 地址清单 | UNRESOLVED |
| 允许控制的设备、点位、值域和时间窗 | UNRESOLVED 或批准 N/A |
| 人工急停、复位和现场监护人 | UNRESOLVED 或批准 N/A |
| 连续稳定运行时长与抽样频率 | 建议至少 24 小时，待批准 |

若设备不是 pyserial 默认的 8N1，当前 `SerialPortConfig` 只有 `port` 和 `baud_rate`，对应 Gate 在实现并验证完整线路参数前为 BLOCKED。

## 负载与恢复

| 字段 | 决定 |
|---|---|
| 设备数、每设备点位、轮询周期 | UNRESOLVED |
| 用户/API/WebSocket 并发与日报文/告警量 | UNRESOLVED |
| 采集、API、实时推送、数据库和积压门槛 | UNRESOLVED |
| 压测持续时间与允许错误率 | UNRESOLVED |
| 网络、DB、Redis、进程中断时长 | 建议包含 10 分钟场景，待批准 |
| RTO / RPO | UNRESOLVED |
| 数据缺口、重复和 P0 告警遗漏策略 | UNRESOLVED |
| 备份频率、保留期、加密和恢复保管人 | UNRESOLVED |

需求文档中的 3000 points/s、API 200 QPS 且 P95<300ms、实时推送小于 1 秒、5000 在线等指标只有在本节明确采用后才是本站点硬门槛。

## 遗留迁移与外部依赖

| 字段 | 决定 |
|---|---|
| 当前生产版本及可用部署制品 | UNRESOLVED 或批准 N/A |
| 需迁移的组织、用户、设备、点表、告警配置和历史范围 | UNRESOLVED 或批准 N/A |
| 真实帧/24 小时抓包来源与脱敏规则 | UNRESOLVED 或批准 N/A |
| 影子数据库/总线和来源标记方案 | UNRESOLVED 或批准 N/A |
| 双跑周期、差异阈值、切换窗口和通知提前量 | UNRESOLVED 或批准 N/A |
| 真实通知渠道、供应商、测试收件人和时间窗 | UNRESOLVED 或批准 N/A；另需明确授权 |

## 结果分类

- `PASS`：按批准参数执行，证据满足通过条件。
- `FAIL`：已执行但不满足通过条件，禁止 Go。
- `BLOCKED`：缺参数、授权、环境、工具或前置修复，禁止 Go。
- `N/A`：在执行前由本 Profile 的对应负责人批准为不适用；不得用于掩盖失败或缺失证据。
