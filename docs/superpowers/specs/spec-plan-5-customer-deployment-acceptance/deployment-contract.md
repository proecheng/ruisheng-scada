# 部署候选契约

## 候选包

每个候选包必须包含且相互匹配：

| 制品 | 必需属性 |
|---|---|
| `docker-compose.prod.yml` | 签名基础配置；离线模式无 `build`，应用和基础镜像均为 `pull_policy: never` |
| 站点配置/Compose override | 只保存现场差异，不修改签名基础配置；单独校验、备份和访问控制 |
| `.env.prod.example` | 仅占位符和安全默认值；与 Compose 引用变量逐项一致 |
| `images/*.tar.gz` | API、GW、Web、PostgreSQL、Redis 的目标架构镜像；归档内镜像 ID 与清单一致 |
| `SHA256SUMS` | 覆盖 Compose、环境模板、部署手册和所有镜像归档 |
| `MANIFEST.md` | 唯一候选 ID、源提交、目标架构、镜像标签与 ID/摘要、迁移 head、生成工具版本和时间 |
| 清单签名/可信分发证明 | 使用 Profile 批准的发布身份和信任锚验证 `MANIFEST.md` 与 `SHA256SUMS`；篡改可被拒绝 |
| `setup-customer.md` | 安装、升级、备份、恢复、回滚、端口和故障排查步骤 |
| `site-acceptance-profile.md` | 经批准的站点、网络、真机、负载、恢复和迁移参数 |
| 验收记录模板 | 对应 `acceptance-matrix.md`，不得包含密钥或明文联系方式 |

候选版本由发布负责人按仓库发布规则确定。验收记录必须同时使用候选 ID、提交和镜像 ID/摘要识别候选，不能只记录 `latest` 或其他可变标签。SHA-256 只提供完整性，不提供发布者真实性；候选必须使用 Profile 批准的签名或受认证分发机制，并在加载镜像前验证。重复构建只要求解析到相同源提交、Dockerfile 输入、目标架构和逻辑镜像身份；除非构建链已证明可复现，不以 `docker save | gzip` 归档逐字节相同作为门槛。每个实际交付归档仍必须有独立 SHA-256。

## 配置等价

根生产 Compose 用于构建，`deploy/` Compose 用于离线安装。契约测试必须渲染两者，对服务、镜像、环境变量、端口、卷、依赖和入口进行归一化比较；差异只允许来自评审过的 allowlist，例如根配置的 `build`、离线配置的 `pull_policy` 和独立站点 override 的硬件映射。至少校验：

- API 数据库、GW 数据库、Redis、JWT 和监听配置。
- 通知 provider 开关、endpoint/credential 占位、worker batch/concurrency/lease/timeout/retry/event-age 配置。
- GW 告警规则刷新、关联值 freshness、WAL 目录和持久卷配置。
- 镜像变量、端口、依赖顺序、迁移入口及数据卷。
- `docker compose config` 对环境变量无未解析值；所有引用变量在站点配置或模板中有定义，离线配置不会隐式拉取或构建。

当前基线存在已知漂移：根配置包含通知运行参数与 GW 告警刷新参数，`deploy/docker-compose.prod.yml` 和 `deploy/.env.prod.example` 尚未包含。候选发布前必须修复并由自动化契约测试锁定。

## 当前发布阻断项

| ID | 已验证事实 | 解锁条件 |
|---|---|---|
| B-01 | 离线 Compose/环境模板缺少通知 worker/provider 与 GW 告警刷新配置 | 同步配置并由归一化契约测试锁定 |
| B-02 | 生产迁移入口无条件执行 `seeds/*.sql`，创建 Demo 租户、两个固定密码用户、Demo 设备和点位 | 生产 bootstrap 与开发 Demo seed 分离；空白生产启动即无公开凭据和未批准 Demo 数据 |
| B-03 | 应用默认使用 `latest`，Redis 使用可变的 `7-alpine` 标签，尚无候选 manifest/SHA256 闭环 | 候选清单记录并校验不可变镜像 ID/摘要，交付归档与 Compose 标签精确匹配 |
| B-04 | Nginx 代理全部 `/api/`，GW health 绑定 `0.0.0.0:9090`；管理端点是否受限尚无证明 | 按批准网络设计落实 TLS/ACL/绑定或等效控制，并从各网段实测 |
| B-05 | 串口运行配置只表达 `port` 和 `baud_rate`；非默认 8N1 设备无法按规格配置 | 现场确认使用默认 8N1，或先实现并测试所需线路参数 |
| B-06 | GW WAL 只保存并重放遥测行，重放直接写库且不重新运行告警引擎；数据库不可用期间“P0 告警零遗漏”尚不成立 | 站点批准允许的告警缺口策略，或实现并验证可恢复的告警判定/事件路径 |
| B-07 | 现有手册只有单条 `pg_dump` 示例，未覆盖角色、TimescaleDB 扩展、空白机恢复、站点配置和回滚兼容性 | 完成空白环境备份恢复 runbook 与演练，证明 RTO/RPO 和旧版本兼容策略 |

## 安全边界

- Web 仅向批准的用户网络开放；认证、控制或带令牌的流量一旦经过不受信/共享网络，必须使用 HTTPS/WSS 并禁止绕过 TLS 入口。
- `gw:5020` 仅向批准的设备网络开放；`gw:9090` 仅向运维/监控网络开放。
- PostgreSQL、Redis 和 API 不直接暴露到宿主机外部网络。
- `.env.prod`、站点 override、备份、日志和验收附件不得进入 Git、镜像层或共享聊天；所有站点密钥唯一生成，加密保存恢复副本并记录保管人。
- 当前 seeds 会创建 Demo 租户、两个文档公开账号、Demo 设备和点位。候选必须让生产 bootstrap 与开发 Demo 数据分离，并保证服务首次对外监听时公开密码即不可登录；禁止依赖“启动后再人工改密码”的暴露窗口。
- 通知 provider 在所有候选和首次部署中保持关闭。真实凭据只能在独立授权的现场步骤注入，且不得出现在命令历史或证据包。

## 数据与恢复

- PostgreSQL、Redis 和 GW WAL 必须使用持久卷，容器重建不得删除数据；卷容量、WAL 上限和磁盘告警按站点负载配置。
- 每次升级前生成带时间、候选/源版本和校验值的数据库备份，并在空白隔离环境执行恢复验证。
- 数据库迁移失败时保持原数据不被清理；回滚优先恢复已验证备份或运行兼容旧版本，数据库 downgrade 必须经过单独评审。
- 恢复必须覆盖 PostgreSQL 角色/权限、TimescaleDB 扩展、schema、数据及迁移 head；Redis 运行状态和 GW WAL 按批准 RPO 明确保留或可重建策略。
- 站点配置和密钥恢复材料与数据库备份分开加密保管；验收证据只记录引用和校验值，不复制秘密。
- 备份和恢复证据至少包含角色/RLS 检查、扩展和 schema head、关键表行数、租户/设备/告警抽样、API/GW readiness 及恢复后业务抽查。
- 升级前逐项判断 schema 对上一应用版本的兼容性；若不兼容，回滚必须使用已演练的整库恢复，不能只切换旧镜像。

## 运行观测

| 目标 | 端点/证据 | 通过条件 |
|---|---|---|
| API 存活 | `/api/health/live` | HTTP 200，状态为 live |
| API 就绪 | `/api/health/ready` | HTTP 200，数据库和 Redis 均可达 |
| API 通知指标 | `/api/health/metrics` | pending、oldest age、失败分类和 stale completion 可读取且无异常增长 |
| GW 存活 | `:9090/health` | HTTP 200，状态为 alive |
| GW 就绪 | `:9090/ready` | HTTP 200，DB、Redis、批处理和 outbox relay 满足门槛 |
| GW 指标 | `:9090/metrics` | outbox pending/失败指标可读取且故障恢复后回落 |

日志采集应覆盖 `migrate`、`api`、`gw`、`web`、`postgres` 和 `redis`，时间统一且可关联，但不得保存访问令牌、密钥、明文联系方式或供应商原始响应。

目标主机和容器必须使用批准的时钟源；验收记录包含时钟偏差。日志需启用轮转与容量上限，磁盘剩余、PostgreSQL 数据卷、Redis 数据卷和 GW WAL 达到站点阈值时必须可告警。

## 现场串口配置

候选包保持 `GW_SERIAL_PORTS` 为空；站点配置通过独立 override 在批准参数后注入，不编辑签名基础 Compose。Linux 使用稳定的设备路径或 udev 别名并显式挂载；Windows Docker Desktop 必须先验证 usbipd/WSL2 设备在容器及宿主机重启后仍可识别。数据库设备记录的 `transport_type`、`serial_port` 和 `modbus_addr` 必须与站点配置一致。

当前实现只向 pyserial 传入端口与波特率，其余线路参数使用库默认值。若现场不是 8N1，必须先扩展配置和串口打开路径并补自动化/真机验证。每条 RS485 总线保持单并发轮询。控制验收必须使用客户批准的点位和值域，并在执行前确认现场设备处于安全状态。

## 中断与重放边界

- 遥测 BatchWriter 在数据库写失败后可把行写入 GW WAL，启动时重放直接落库；这不等于告警判定或通知事件已重放。
- 告警 CAS 与 outbox 依赖数据库事务。数据库不可用期间的遥测若只经当前 WAL 恢复，不会自动重建当时应产生的告警。
- 因此 Gate 6 必须分别度量遥测缺口、重复、告警缺口、通知缺口和积压。只有实现并验证告警恢复路径后，才能声明依赖中断期间 P0 告警零遗漏。
- WAL 达到容量上限会删除最旧文件；容量测试必须证明批准中断窗口内不会触发该路径，或把数据损失作为明确的 No-Go 条件。
