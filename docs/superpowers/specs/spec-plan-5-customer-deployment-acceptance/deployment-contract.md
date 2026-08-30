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
| B-08 资格制品（v3/B-08 qualification 必需） | 两层闭环：OpenSSH `SHA256SUMS` 候选固定携带源提交 Git blob 绑定的静态 qualification toolchain（含 `tools/trust_root_freshness.py`），但不携带可执行 launcher/bootstrap；包外受保护 bootstrap 使用隔离 runtime 执行闭集 `ValidatorSchema/ValidatorProfile/ValidatorLegacy/Receipt`。包外站点层保存不可变 point manifest/profile、canonical gate digest、validator source digest、v1 root+policy freshness request/attestation、事前 `CalibrationRunApproval` 与事后独立 `EligibilityApproval` 的四角色签名证明、逐点 role/subject 和线路/分类证据索引、FC2 专属地址转换证据、受信 runtime attestation、validator report，以及 trust-policy-recognized verifier 签名并由 `EligibilityApproval` 绑定的 `ReleaseVerificationReceipt`。动态现场制品不得塞回其证明的候选形成哈希环 |

候选版本由发布负责人按仓库发布规则确定。验收记录必须同时使用候选 ID、提交和镜像 ID/摘要识别候选，不能只记录 `latest` 或其他可变标签。SHA-256 只提供完整性，不提供发布者真实性；候选必须使用 Profile 批准的签名或受认证分发机制，并在加载镜像前验证。Manifest v2 保持一般候选验证和既有远程维护兼容，但不能执行 B-08 qualification、不能签发/替代 `ReleaseVerificationReceipt`，也不能满足 G0-06、G4-01、B-09 或 canary；这些路径必须使用完整 v3 候选和有效 receipt。所有候选验证路径必须在分配/解压前执行统一资源预算：JSON/config 至多 4 MiB、archive 至多 32,768 个成员、单成员至多 8 GiB、总扫描/展开至多 32 GiB，Docker metadata 聚合至多 64 MiB，超限、递归或内存异常均 fail-closed。Docker 外层归档和嵌套 layer 必须先扫描 raw tar header，在扩展 payload 分配前拒绝所有 PAX/GNU extension；receipt migration 检查还必须拒绝重复外层成员。Qualification toolchain 另要求 canonical gzip 头 `1f8b08000000000002ff`、恰好一个 gzip member、固定成员顺序的 deterministic strict USTAR、regular-file-only、零 padding 和 2..21 个尾部零 block；每个工具成员至多 64 MiB、内部 manifest/JSON 至多 4 MiB。OCI whiteout 仅接受非空且非 `.`/`..` 目标的零长度 regular file；API migration 只能来自实际加载镜像最终 overlay 的 `/app/alembic.ini` 与直接 source-only versions，至多 4,096 个、单个 2 MiB、合计 64 MiB，静态 literal graph 必须唯一 head 且不得执行 migration。重复构建只要求解析到相同源提交、Dockerfile 输入、目标架构和逻辑镜像身份；除非构建链已证明可复现，不以 `docker save | gzip` 归档逐字节相同作为门槛。每个实际交付归档仍必须有独立 SHA-256。

候选 build 和 receipt 必须对相同 candidate ID 共用主机级 `.<candidate_id>.candidate-tags.lock`，默认位于 Windows `C:\ProgramData\Ruisheng\locks` 或 POSIX `/var/lib/ruisheng/locks`；同候选互斥、不同候选可并行且异常后锁可重取。receipt 已原子发布后若锁释放失败，必须以 distinct published-error 失败并保留完整 receipt 与已加载 candidate tags。Windows qualification runtime 在任何内容读取前限制总计 32,768 个实际文件（包含 `qualification-runtime-manifest.json`）、32,768 个目录、单文件 512 MiB、总量 32 GiB 和 UTF-8 路径 4,096 bytes；Windows system qualification 经通用 Python bootstrap 必须 fail-closed，并明确转交受保护 PowerShell publisher。Windows/POSIX 受保护 publisher 只有在 `ValidatorProfile` 模式才调用操作系统固定、不可由调用者替换的 freshness provider executable/config/trust-root 路径，生成一次性 challenge 并锁定 profile、policy、root 与 verifier 快照；provider 缺失、失败或响应不可认证时必须在 validator 启动前以 `BLOCKED/INVALID` 结束，validator 只消费被见证的同一 root 快照。公共 `validate` 缺少受信 publisher context 时 fail-closed，调用者提供 provider/config/key/challenge 的路径已被拒绝。所有 publisher qualification 出口都清理完整进程树：POSIX 统一终止并有界回收进程组；Windows 先以命名 gate 将根进程纳入 kill-on-close Job，再放行候选代码，并在共享 30 秒预算内清理 Job、固定和竞态后代。

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
| B-08 | 离线 v5 Schema/validator 已完成，但当前真机型号、固件/点表版本、逐点语义、编码、单位和倍率均未闭合；旧 MDF 只产生不可部署候选，且候选 `s16` 解码、原子禁用态入库和共享串口锁均未关闭 | 只用 manifest v3：canonical gate digest 绑定 Schema、validator policy/source digest、证据契约和 trust policy；事前 `CalibrationRunApproval` 与事后独立 `EligibilityApproval` 的四角色签名、逐点 role/subject、设备线路证据、分类校准内容、受信 runtime attestation 均验证通过；FC2 点逐项具有 `FC2_ADDRESS_TRANSLATION`/`DISCRETE_INPUT_ADDRESS_TRANSLATION` 证据且不复用 FC1；trust-policy-recognized verifier 签名且由 `EligibilityApproval` 绑定的有效 `ReleaseVerificationReceipt` 证明 OpenSSH 发布根/verifier tool、完整候选、实际加载镜像及静态验证的 API-image migration head，所有拟部署点均 `resolved + supported` 且冲突关闭；不得 N/A |
| B-08-T | 仓库已实现 v1 signed freshness request/attestation、受保护 publisher 固定入口及 fail-closed validator handoff：请求精确绑定 site/challenge/candidate/profile/payload/gate/validator/verifier 和 root+policy 状态，attestation 绑定 witness time/expiry 与 monotonic state；但 mock/仓库回归不能抵抗真实管理员恢复旧文件或整盘/系统快照回滚 | 仓库及可回滚系统盘外维护 root 与 policy 各自 `(id, version, revocation_sequence, sha256)` 高水位；完成真实 TPM NV/等效硬件或独立远端 witness 的 Profile 选型、enrollment、provider/witness key/identity 固定，证明 exact 幂等、降级/撤销回退/同版本异 hash/ID switch/replay 拒绝、本地 ahead 和 provider 故障 fail-closed，并保存本地替换、整盘回滚、并发和故障演练证据；不得 N/A |
| B-08-W | Windows qualification 代码已具备固定 runtime 门禁，但站点尚未证明 runtime/依赖/bootstrap 身份和 receipt 签名密钥/agent 不受调用者环境替换 | provision 受保护自包含 Python 3.11 runtime、锁定依赖闭包和 bootstrap，验证 owner/ACL、最终路径、handle/file identity 与摘要；使用 Profile 批准的 TPM/CNG/HSM 不可导出密钥或等价隔离的专用系统 agent，证明不继承调用者 Python/Docker/SSH 配置或用户 agent；不得 N/A |

B-07 只关闭备份/恢复，B-08 只关闭设备身份、点表语义、编码、单位、倍率和实现资格；两项互不替代。B-08-T 与 B-08-W 是 B-08 的非 N/A 外部信任前置，不能由仓库 freshness mocks/测试、v2 兼容或本地管理员自证替代。B-08-T 在真实 TPM/远端 witness 选型与 enrollment、root+policy 高水位、witness key/identity 及整盘回滚/替换/并发/故障证据完成前保持 blocked；B-08-W、实物身份/校准和 B-09 仍分别阻断 B-08。B-08 validator 的 `ELIGIBLE` 结论仅表示 manifest 具备进入后续验收的资格，不验证或替代现场执行授权，也不授权真机 TX、GW 重建、canary、持续轮询或生产切换。

B-08 时间证据必须满足 signed receipt `verified_at` 不晚于绑定的 runtime raw start/签名 runtime observation，最终四角色 `EligibilityApproval.approved_at` 不早于 receipt、evidence 和 runtime。远程维护审计文件必须在任何服务生命周期变更前完整验证，并限制为 16 MiB、64 KiB/行和 50,000 条；仅当受保护文件身份、大小、时间元数据和内容 SHA-256 均未变时才可复用已验证快照。

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
