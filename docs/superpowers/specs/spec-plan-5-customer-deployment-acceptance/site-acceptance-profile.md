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
| 用户网段（CIDR，逗号分隔） | UNRESOLVED |
| 设备网段（CIDR，逗号分隔） | UNRESOLVED |
| 运维/监控网段（CIDR，逗号分隔） | UNRESOLVED |
| Docker 应用网络子网（CIDR） | UNRESOLVED |
| Docker 应用网络网关（IP） | UNRESOLVED |
| 管理端点容器观察来源（CIDR，逗号分隔） | UNRESOLVED |
| 外部服务网段（CIDR，逗号分隔或批准 N/A） | UNRESOLVED |
| 未批准探测源（CIDR，逗号分隔） | UNRESOLVED |
| Web 宿主绑定（IP:端口） | UNRESOLVED |
| GW 设备宿主绑定（IP:端口） | UNRESOLVED |
| GW 管理宿主绑定（IP:端口） | UNRESOLVED |
| Web 传输模式（LOOPBACK_ONLY / TRUSTED_HTTP / HTTPS_WSS） | UNRESOLVED |
| TLS 终止、证书及 Web 直连旁路防护 | UNRESOLVED 或 LOOPBACK_ONLY |
| API health/metrics 访问主体和 ACL | UNRESOLVED |
| GW health/ready/metrics 源 ACL/防火墙 | UNRESOLVED |
| 管理端点认证方案（固定 BEARER_SHA256） | UNRESOLVED |
| 管理端点令牌 SHA-256（64 位小写十六进制） | UNRESOLVED |
| 管理端点凭据生成、保管、轮换和恢复负责人 | UNRESOLVED |
| IPv4/IPv6 启用或禁用位置与证据 | UNRESOLVED |
| 防火墙平台、配置负责人、复核人及持久化 | UNRESOLVED |
| 用户、设备、监控和未批准源探测位置 | UNRESOLVED |
| 站点密钥生成、交接、轮换和恢复保管人 | UNRESOLVED |
| 发布签名/可信分发机制、验证密钥指纹和发布负责人 | UNRESOLVED |
| B-08 approval trust policy ID/版本、签名算法、四角色允许身份和验证密钥指纹 | UNRESOLVED |
| validator、runtime runner 和 release verifier 的信任锚、轮换/撤销及复核负责人 | UNRESOLVED |
| trust-root anti-rollback 高水位 `(root_id, root_version, revocation_sequence, root_sha256)` | UNRESOLVED |
| 高水位外部存储与 freshness authority（TPM NV/等效硬件单调状态或独立远端 witness） | UNRESOLVED |
| 管理员/整盘回滚威胁测试、旧 root/policy 拒绝证据与复核负责人 | UNRESOLVED |
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

## 设备证据与校准

| 字段 | 决定 |
|---|---|
| B-08 point-profile ID / artifact Schema v1 / payload SHA-256 | UNRESOLVED |
| 候选 manifest 版本、完整 v3 qualification toolchain 身份与 v2 rejection 结果 | UNRESOLVED；B-08/B-09/canary 必须为 v3 |
| 候选包外 bootstrap 身份、四种 qualification 模式及候选内无 executable launcher 的验证结果 | UNRESOLVED；仅 `ValidatorSchema/ValidatorProfile/ValidatorLegacy/Receipt` |
| qualification canonical gzip/strict USTAR、单 member、零 padding/trailer 及跨 Python/PowerShell/Shell rejection 结果 | UNRESOLVED |
| semantic validator `ruisheng.device-point-profile-validator/v5`、source 路径/SHA-256 与 validator report 路径/SHA-256 | UNRESOLVED |
| calibration content v3 / reference content v4 / raw-observation content v4 | UNRESOLVED |
| validator policy identity、trust policy ID 及 canonical gate digest | UNRESOLVED |
| immutable pre-run `profile_input_sha256` | UNRESOLVED |
| 逐点 evidence role/subject matrix 与 contradiction resolution binding | UNRESOLVED |
| FC2 `FC2_ADDRESS_TRANSLATION` / `DISCRETE_INPUT_ADDRESS_TRANSLATION` 专属证据及 FC1 substitution rejection | UNRESOLVED 或确认没有 FC2 点 |
| 设备序列号/USB 身份、型号、硬件 revision、固件和点表版本 | UNRESOLVED |
| Modbus 地址、完整线路参数及稳定设备路径的逐字段证据与设备身份绑定 | UNRESOLVED |
| 事前 `CalibrationRunApproval` ID、artifact 路径/SHA-256、nonce、有效时间窗、四角色签名、签署时间和失效条件 | UNRESOLVED |
| 事后独立 `EligibilityApproval` ID、artifact 路径/SHA-256、四角色签名、签署时间及最终 gate/evidence/runtime/receipt binding | UNRESOLVED |
| 项目、设备/固件、现场工艺/安全、测试审批角色的受信身份与两层签名验证结果 | UNRESOLVED |
| 分 analog/binary/counter 的 evidence schema/state plan、参照方法、同步策略和 runner/reference 终态 | UNRESOLVED |
| 参照仪器 ID、量程/分辨率、校准证书或校准状态 | UNRESOLVED |
| 样本数、稳定/跨度/回返阈值、业务容差和 uncertainty budget | UNRESOLVED |
| 精确只读 TX allowlist、超时/重试/帧间隔/总时长预算 | UNRESOLVED |
| open contradiction 为零及逐点 `resolved + supported` 证明 | UNRESOLVED |
| 受信 runtime runner identity、工具摘要、原始报告和签名 attestation | UNRESOLVED |
| 有效 `ReleaseVerificationReceipt` 路径/摘要/签名、OpenSSH 发布根、verifier 工具 ID/摘要，以及受保护 v3 快照观测的提交、逻辑身份、API/GW 镜像和 migration head | UNRESOLVED |
| 实际加载 API 镜像 overlay、Alembic source-only config、静态 literal revision graph/唯一 head 与 Manifest/receipt binding | UNRESOLVED |
| OCI 4 MiB JSON、32,768 成员、8 GiB 单成员、32 GiB 展开、64 MiB metadata，raw outer/layer tar PAX/GNU extension 事前拒绝、重复外层成员拒绝及 migration 4,096/2 MiB/64 MiB 预算 rejection 结果 | UNRESOLVED |
| OCI whiteout 零长度 regular-file、合法 target 及 link/directory/device/sparse/nonzero rejection 结果 | UNRESOLVED |
| receipt `verified_at`、runtime raw started/completed、runtime signed observed 及四角色最终 approved 时间顺序验证结果 | UNRESOLVED |
| 候选包外 publisher bootstrap 路径/SHA-256、所有者/ACL、固定信任根与 provisioning 记录 | UNRESOLVED |
| 受保护自包含 Python 3.11 runtime 路径/版本/manifest SHA-256、`uv.lock` SHA-256、精确依赖闭包及文件身份/ACL 验证结果 | UNRESOLVED |
| Windows verifier bootstrap/runtime 最终路径、owner/ACL、handle/file identity、锁摘要与调用者环境隔离证明 | UNRESOLVED |
| Windows runtime 总计 32,768 个实际文件（包含 runtime manifest）、32,768 个目录、512 MiB 单文件、32 GiB 总量、4,096-byte 路径预算与超限拒绝结果 | UNRESOLVED |
| Windows system qualification 经通用 Python bootstrap 的 fail-closed 结果及转交受保护 PowerShell publisher 的证据 | UNRESOLVED |
| build/receipt 主机级 `.<candidate_id>.candidate-tags.lock` 根、同候选互斥、异候选并行、异常后重取，以及 receipt 发布后锁释放失败时 receipt/tags 保留证据 | UNRESOLVED |
| POSIX process-group 与 Windows gated kill-on-close Job 在正常/非零/异常/超时后的完整后代回收证据 | UNRESOLVED |
| 远程维护审计快照缓存对受保护身份/大小/时间和内容 SHA-256 的复用前一致性验证及同长同时间替换拒绝结果 | UNRESOLVED |
| Windows receipt signing key ID/provider、不可导出或专用系统 agent 证明、agent/channel ACL 与调用者 SSH 配置隔离 | UNRESOLVED |
| trust-root 高水位读取/更新/回滚拒绝记录与 TPM NV/远端 freshness witness attestation | UNRESOLVED |
| 后续 implementation/canary 规格、现场执行审批及回退证据 | UNRESOLVED |

本节只记录受保护 artifact 的引用、摘要和验证结果，不复制设备秘密、私钥、签名材料或敏感现场数据。Manifest v2 仅可用于一般候选验证和既有远程维护，不得填写为 B-08 qualification/receipt、G0-06、G4-01、B-09 或 canary 证据。validator 的 `ELIGIBLE` 结论不是执行授权；任何设备、固件、点表、仪器、Profile、Schema、validator policy、证据契约、trust policy/high-water、签名人/密钥/agent、verifier runtime、证据 role/subject、runner、`ReleaseVerificationReceipt`、OpenSSH 发布根、verifier 工具、提交、镜像或数据库 schema 身份变化都使既有资格和相关 Gate 证据失效。

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
