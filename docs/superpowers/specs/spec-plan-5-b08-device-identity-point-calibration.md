---
title: 'B-08 自研设备身份、点表与倍率证据固化'
type: 'feature'
created: '2026-08-27'
status: 'blocked'
baseline_commit: '8376308'
context:
  - 'D:/江苏润盛/docs/superpowers/specs/spec-plan-5-b06-modbus-protocol-validation.md'
  - 'D:/江苏润盛/docs/superpowers/specs/spec-plan-5-b06-modbus-protocol-evidence.md'
  - 'D:/江苏润盛/docs/superpowers/specs/spec-plan-5-b05-serial-hardware-onboarding.md'
---

<frozen-after-approval reason="human-owned intent - do not modify unless human renegotiates">

## Intent

**Problem:** B-06 只证明自研设备在 `9600/8N1`、地址 `1` 下两个 FC3 区间可读；设备型号、点名、signedness 和倍率仍未决。旧 MDF 同时包含 BCMM/CBMM 候选，旧换算公式互相冲突；按当前字段映射恢复的 FC3 行均写作 `s16`，而当前 GW 不支持该候选编码，因此不能把历史点表机械导入生产。

**Approach:** 只读解析原 MDF 的物理页并固化逐点候选、来源偏移、哈希和冲突；建立身份/语义/编码/倍率的字段级证据与三态分类规则。正式验收合同已将 B-07 分配给备份恢复，本故事使用新的 B-08，避免关闭错误 blocker。B-08 只交付不可自动应用的候选证据和后续校准契约，不触碰生产状态。

## Boundaries & Constraints

**Always:** 原 MDF 只读且保留 SHA-256；候选与已确认事实分开；CBMM 只标中等置信、BCMM 继续保留；每点记录 FC、寄存器/bit、类型、符号、字节/字序、单位、旧倍率、证据引用、反证和实现支持状态；只有 schema 派生的 `resolved AND implementation_supported` 可进入后续可应用集合。

**Ask First:** 任何新真机 TX、扩展 B-06 白名单、改变设备物理状态、安装或使用参照仪器、断电/重启目标机、写站点 Profile/生产数据库、生成或应用串口 override、重建/启用 GW、持续轮询、控制、告警或通知。

**Never:** 不 attach 或修改原 MDF；不把物理页写死为某个 SQL 表；不把地址可读、零值、两点拟合或视觉相关当作型号/倍率确认；不通过负倍率伪装 signed decode；不把 `s16` 直接写成 `字`；不使用写功能码、未知私有码、自动扫描或自动扩大范围。

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| LEGACY_PAGE_RECOVERY | 原 MDF 哈希匹配、页结构可解析 | 输出 46 条带页/文件偏移的候选，保留原大小写和空单位 | 结构/计数/偏移不一致即 BLOCKED，不补猜字段 |
| MODEL_ALIGNMENT | B-06 区间与某候选点表对齐 | 仅提升候选置信度并记录限制 | 不把对齐转成已确认型号 |
| UNSUPPORTED_ENCODING | FC3 行按候选字段映射为 `s16` 而当前 GW 仅无符号 | 将映射假设与实现阻断同时记录 | 禁止把历史文本提升为当前实物事实，或靠 scale/直接 SQL 绕过 |
| TWO_STATE_FIT_ONLY | 仅有 A/B 两个物理状态 | 可计算 provisional ratio/offset | 没有权威绑定或独立 C 时不得 `resolved` |
| VALID_CALIBRATION | 经独立规格批准的 A/B 求参、C holdout、A' 回返及身份/编码均通过 | 由校准 validator 派生逐字段 `resolved`，保留全部输入、误差和哈希 | 任一阈值失败保持 `ambiguous` |
| PRODUCTION_ISOLATION | B-08 规格/证据生成与审查 | 生产 DB/GW/Compose/串口状态不变 | 发现副作用立即失败并停止 |

</frozen-after-approval>

## Code Map

- `docs/superpowers/specs/spec-plan-5-b08-device-identity-point-evidence.md` -- 人可读证据、冲突、分类和移交边界。
- `docs/superpowers/specs/evidence/b08-20260827/legacy-point-candidates.json` -- 46 条机器可读旧点候选、MDF 哈希、解析契约和实现阻断。
- `tools/extract_legacy_mdf_points.py`、`tests/tools/test_extract_legacy_mdf_points.py` -- 只读单页提取器、源/page/slot/record 哈希和 canonical JSON 一致性回归。
- `schemas/point-profile/point-profile-v1.schema.json` -- 当前结构 Schema；只有与 validator policy identity、外置信任策略和分类证据契约共同形成 canonical gate digest 后，才能参与资格判定。
- `tools/validate_device_point_profile.py`、`tests/tools/test_validate_device_point_profile.py` -- 只读 `ELIGIBLE/BLOCKED/INVALID` 资格门禁；结构 Schema、semantic validator v5、外置信任、四角色签名、role/subject、分类证据、typed line readback、受信 runner 和发布回执共同决定结果，任何单项不能独自产生 `ELIGIBLE`。
- `tools/qualification_bootstrap.py`、`tools/release_artifacts.py`、`tools/release_verification_receipt.py`、`tests/tools/test_release_artifacts.py`、`tests/tools/test_release_verification_receipt.py` -- 候选包外 qualification 启动边界、v3 工具链归档、受保护 runtime/进程树、主机级 candidate-tag 锁、实际镜像与 Alembic migration 绑定及签名回执回归；bootstrap 本身不进入候选包。
- `tools/release_trust/verify-publisher.ps1`、`tools/release_trust/verify-publisher.sh` -- 系统级发布入口；只暴露闭集 `ValidatorSchema/ValidatorProfile/ValidatorLegacy/Receipt` qualification 模式，并在 Windows/POSIX 上分别实施 Job Object/gate 或进程组清理。
- `DataBase/DataBase/ModBus.mdf` -- 原始旧库，仅作为只读来源，不纳入生产发布物。
- `ModBusServer20210908/ModBusServer20210908/ModBusServer/DataBase.cs`、`ModBusServer.cs` -- 旧字段及解码/倍率冲突佐证。
- `ruisheng-gw/src/ruisheng_gw/ingest.py`、`domain/point.py`、`domain/registry.py` -- 当前解码、换算和启动加载边界。
- `ruisheng-api/src/ruisheng_api/api/schemas/points.py`、`schemas/devices.py`、`api/devices.py` -- 当前类型 allowlist 和无法原子 create-disabled 的边界。

## Status Model

| Workstream | Status | Exit condition |
|---|---|---|
| Legacy evidence extraction | `complete` | MDF/source/page/record hashes、46 条候选及字段级 claim matrix 可重复生成 |
| Parser and canonical artifact | `complete` | checked-in JSON 与提取器输出逐对象、逐字节一致，结构/槽/偏移/源不变测试通过 |
| Eligibility Schema and validator | `complete_offline` | v5 Schema/validator、canonical gate digest、四角色与 role/subject 门禁、分类/线路/runtime 证据验证、v1 freshness request/attestation、受保护 publisher 接入、v3 qualification toolchain 和 `ReleaseVerificationReceipt` 离线实现及正反回归完成；该状态不代表真实外部高水位、Windows verifier 或现场证据已 provisioned |
| Trust-root anti-rollback provisioning | `blocked_external_provisioning` | 在仓库外受保护地维护 root 与 policy 各自的 `(id, version, revocation_sequence, sha256)` 高水位；真实 TPM NV/等效硬件单调状态或独立远端 witness 的选型、enrollment、密钥/身份、读取及管理员替换/整盘回滚/并发/故障演练全部验收，仓库 mock 不得关闭此项 |
| Windows verifier runtime and key isolation | `blocked_external_provisioning` | provision 受保护、自包含且锁摘要匹配的 Python 3.11 runtime/依赖闭包，并把 verifier 签名密钥及 agent/channel 与调用者环境隔离；完成所有者/ACL、最终路径/文件身份、密钥身份和不可替换执行证据 |
| Physical identity and calibration | `blocked_external_approval` | 独立 calibration-run 规格、审批、实物身份和分类校准证据齐备 |
| Runtime implementation support | `blocked_new_story` | B-09（预留编号）完成 signed decode、原子禁用入库、共享串口锁和完整运行时证明 |

B-08 总状态保持 `blocked`。`complete_offline` 只说明仓库内资格工具链已交付，不关闭 trust-root anti-rollback、Windows verifier runtime/key isolation、实物校准或 B-09。只有这些 blocker 全部关闭，实物型号/固件/点表身份、线路参数及所有拟部署点的 identity/semantic/encoding/unit/calibration 均由 validator 派生为 `resolved`，implementation 为 `supported`，全部 contradiction 关闭，canonical gate digest、外置信任签名、审批/profile/evidence/runtime/`ReleaseVerificationReceipt` 绑定闭合且独立实物校准证据通过，才允许转为 `done`。parser、Schema、离线 receipt 或合成 `ELIGIBLE` 测试通过都不能单独关闭 B-08。

## Evidence and Classification Contract

每个进入校准或部署候选阶段的点 profile 必须至少能表达：`model_candidate`、点 ID/名称、FC、寄存器起点/宽度、bit、signedness、byte/word order、单位、raw domain、参照方法、分类校准 profile、范围、逐字段证据、`semantic_status`、`calibration_status`、`implementation_status` 和禁止原因。当前 legacy JSON 是源证据层；validator 的 `validate-legacy` 只在内存中把它映射为候选 profile，所有缺证字段显式为 `unknown/candidate/ambiguous`，线路地址/9600/8N1 不从 B-06 自动补成部署参数。

状态由版本化 JSON Schema 和 validator policy 共同派生；canonical gate digest 必须覆盖 Schema、可执行 validator source digest、证据内容版本和外置信任策略 ID，结构 Schema 单独通过不能产生 `ELIGIBLE`。当前版本矩阵固定为 point-profile/evidence artifact schema `v1`、semantic validator `ruisheng.device-point-profile-validator/v5`、calibration content `v3`、reference content `v4`、raw observation content `v4`。调用者在任意层自报 `deployment_eligible/deployable/direct_import_allowed/eligible/eligibility` 均为 `INVALID`。字段状态使用闭集 enum并分开 `identity_status`、`semantic_status`、`encoding_status`、`unit_status`、`calibration_status`、`implementation_status`；每个逐点证据引用必须同时匹配允许的 role 和 `subject_point_ids`，全局身份/线路证据须显式建模，resolved contradiction 只能由 subject/role 相符的受信 resolution evidence 关闭。事前 `CalibrationRunApproval` 只绑定不含运行结果、终态、mapping 和 evidence 的 immutable `profile_input_sha256`、state plan、设备/线路、工具、仪器及安全/TX 预算；事后独立 `EligibilityApproval` 才绑定最终 canonical gate、完整 evidence role/subject、contradiction set、runtime target/evidence set 和受信 `ReleaseVerificationReceipt`。两类审批均由 Profile 批准的外置信任策略验证四角色签名。当前 legacy evidence 映射后固定为 `BLOCKED`。

线路地址、波特率、数据位、校验位、停止位和稳定设备路径必须具有逐字段证据引用并绑定同一设备身份；readback 必须是可重算的 POSIX termios/udev 或 Windows DCB/SetupAPI typed payload，包含 canonical readback hash、稳定路径 provenance、USB serial 和平台接口身份。Windows 接口路径按大小写不敏感的完整 `#{GUID}` 形式比较。B-06 的地址 `1` 和 `9600/8N1` 只描述已执行读请求，不能自动成为 profile 值。runtime target 的提交、候选逻辑身份、镜像摘要和迁移 head 必须来自受信 release verifier 在同一受保护快照内签发的 `ReleaseVerificationReceipt`；该回执绑定 OpenSSH 发布根、verifier 工具摘要、精确包哈希、实际加载镜像和独立 API-image migration head，且 `receipt_id` 必须由 `protected_snapshot_id` 派生，不能接受 profile、approval 或普通 JSON 自报。

FC1 coil 与 FC2 discrete-input 的地址语义是不同证据主体。只要 profile 含 FC2 点，runtime evidence 必须单独包含 `FC2_ADDRESS_TRANSLATION`，并由 role `DISCRETE_INPUT_ADDRESS_TRANSLATION` 证明 FC2 请求/响应与点地址的转换；FC1 的 `FC1_ADDRESS_TRANSLATION`/coil 证据不得替代、复用或通过改名满足 FC2。FC1/FC2 均只允许 width 1、无 register bit 的 `bit` 编码和 binary calibration，但这种共同结构不能消除两种功能码的证据隔离。

候选格式边界固定如下：manifest v2 继续兼容一般候选真实性验证和既有远程维护，但不得执行 B-08 qualification、不得生成或充当 `ReleaseVerificationReceipt`，也不得用于 B-08、G0-06、G4-01 或 B-09/canary 资格。B-08 qualification、有效 receipt、B-09 runtime target 和任何 canary 前置都必须来自通过完整 v3 精确文件集与 qualification toolchain 验证的候选。

Qualification 的信任入口必须位于候选包外：`tools/qualification_bootstrap.py` 或受保护的 `verify-publisher.ps1/.sh` 先验证 OpenSSH 链、受保护快照、v3 精确文件集和静态工具链，再以隔离 Python 3.11 runtime 执行 archive 中的 validator 或 receipt producer；候选包不得携带可执行 launcher/bootstrap。系统 publisher 只允许四种闭集模式：`ValidatorSchema` 只渲染/检查 Schema，`ValidatorProfile` 绑定 profile/root/trust policy，`ValidatorLegacy` 绑定 legacy evidence/root，`Receipt` 绑定输出目录、专用签名身份、verifier ID/key ID；模式外参数、v2 候选或不匹配的工具摘要均 fail-closed。只有 `ValidatorProfile` 由 Windows/POSIX 受保护 publisher 调用操作系统固定 provider executable、provider config 和 trust-root 路径，生成一次性挑战并锁定 profile/policy/root/verifier 快照；provider 缺失、失败或返回不可认证结果时必须在 validator 启动前以 `BLOCKED/INVALID` 结束。公共 `validate` 子命令不接受 freshness 参数且缺少 publisher context 时固定 fail-closed；内部 handoff 以操作系统固定 config/key 验证签名 attestation，因此调用者改写 challenge 或其他绑定参数不能伪造 trusted context。

Qualification archive 必须是确定性严格 USTAR：固定成员顺序和精确 allowlist、regular-file-only、canonical gzip 头 `1f8b08000000000002ff`、恰好一个 gzip member、确定性 USTAR header、零成员 padding、2 至 21 个尾部零 block 且无尾随数据。v3 精确 toolchain 包含 `tools/trust_root_freshness.py`，并由 Python、PowerShell 与 Shell 校验器锁定；每个工具成员至多 64 MiB，内部 manifest/JSON 至多 4 MiB，压缩及展开预算由固定成员集合预计算，各验证器必须给出相同拒绝结果。一般候选/OCI 预算另固定为 4 MiB JSON/config、32,768 archive 成员、8 GiB 单成员、32 GiB 总展开和 64 MiB 聚合 Docker metadata；Docker 外层归档及嵌套 layer 必须在 `tarfile` 分配扩展 payload 前扫描原始 header，任何 PAX/GNU 扩展 header 均 fail-closed，且外层成员名不得重复。API migration 只接受 `/app/alembic.ini` 与直接位于 `/app/alembic/versions` 的 source-only `.py`，至多 4,096 个、单个 2 MiB、合计 64 MiB。OCI layer 全局至多 1,000,000 成员；whiteout 只有零长度 regular file 合法，空目标、`.`/`..`、链接、目录、设备、稀疏类型或非零 payload 均在改变 overlay 状态前拒绝。

Receipt 的 migration head 必须从已验证且实际加载的 API 镜像归档静态重建，不能读取宿主源码、导入/执行 migration 或相信 manifest 自报。验证器按 OCI layer/whiteout 语义得到最终 `/app/alembic.ini` 与 migration source 集，限制 import-time AST 为安全 import、literal metadata 和无副作用函数定义，验证 `revision/down_revision/branch_labels/depends_on`、无缺失 revision/重复 revision/环且唯一 head，再要求该 head 与 manifest/receipt 一致。

构建和 receipt 对同一逻辑候选必须共享主机级锁 `.<candidate_id>.candidate-tags.lock`，默认根在 Windows 为 `C:\ProgramData\Ruisheng\locks`、POSIX 为 `/var/lib/ruisheng/locks`；同候选并发 fail-closed，不同候选仍可并行，异常退出后锁可重新获取。若 receipt 已原子发布而候选锁释放失败，操作必须以专用 published-error 失败并保留完整 receipt 与已加载 candidate tags，禁止把已发布事实回滚成普通失败。Windows 固定 runtime 还必须在分配/读取前限制总计 32,768 个实际文件（包含 `qualification-runtime-manifest.json`）、32,768 个目录、单文件 512 MiB、总量 32 GiB 和 UTF-8 路径 4,096 bytes，并拒绝 reparse/hard-link/case-fold collision/allowlist 外文件；Windows system qualification 经 Python bootstrap 请求必须 fail-closed，并明确转交受保护 PowerShell publisher。所有 qualification 退出路径都必须回收完整进程树：POSIX 在正常退出、非零退出、异常与超时后统一终止并有界等待进程组；Windows 在候选代码执行前创建 kill-on-close Job Object 和命名 gate，将根进程纳入 Job 后才放行，使用完成/hold 事件保留可观测退出码，并在共享 30 秒预算内终止 Job、清理已固定及竞态后代。

外置信任根的真实性还必须具有仓库外 freshness 状态。仓库已经实现严格 v1 freshness request/attestation：签名请求绑定 site、一次性 256-bit challenge、候选逻辑身份、Profile ID/hash、payload、canonical gate、validator ID/source、verifier ID/tool hash，以及 root 与 policy 各自的 `(id, version, revocation_sequence, sha256)`；attestation 另绑定 provider/witness key、可信 `observed_at/expires_at`、monotonic state ID/counter 和外部高水位。validator 只消费 publisher 见证并持续锁定的同一 root 快照，完全相同状态才可幂等通过；root/policy 降级、撤销回退、同版本异 hash、未授权 ID 切换、旧 challenge 重放、时钟/期限或单调计数异常为 `INVALID`，本地状态领先外部高水位或 provider 不可用为 `BLOCKED`，资格路径只读且不得自动推进高水位。真实 provisioning 仍必须选择 TPM NV/等效硬件单调状态或独立远端 witness，并在仓库与可回滚系统盘外持久保存 root+policy 高水位。管理员替换本地文件、恢复系统盘或回滚整盘快照属于威胁模型，因此仓库测试/mocks、普通文件 ACL、nofollow 单读和本地磁盘状态都不足以关闭此 blocker；仍需现场 enrollment、witness key/identity、读取/比较及替换、整盘回滚、并发和故障证据。

Windows verifier 还必须把执行 runtime 与签名密钥链分别隔离：固定自包含 Python 3.11 runtime、依赖闭包和 bootstrap 具有受保护 owner/ACL、最终路径、handle/file identity 与锁摘要证据；receipt signer 使用 Profile 批准的专用密钥和受保护 agent/channel，不继承调用者可选的 Python/Docker/SSH 配置或用户 agent，不允许调用者替换公钥身份与实际签名主体的绑定。可接受 TPM/CNG/HSM 等不可导出密钥，或经批准且具有等价隔离证明的专用系统 agent。在这两项外部 provisioning 证据完成前，离线 validator 和 receipt producer 均不得被解释为站点资格已成立。

分类规则：

- `source_candidate` 只表示历史来源有此记录。
- `authenticated_raw_observation` 只表示批准地址可读。
- `ambiguous` 表示多个候选、单状态、跨度不足、状态不稳定、身份未绑定或存在未解释冲突。
- `unsupported` 表示语义即使查明，当前 GW/API 也不能正确表达。
- `resolved` 必须同时闭合身份、语义、编码、单位和换算，并且没有未解释反证。
- “语义 resolved”与“实现 supported”是两个独立维度；移交后续故事的可应用集合必须二者皆真。

## Physical Calibration Contract

物理校准不由本规格授权执行。执行前必须另建、审查并批准不可变 calibration-run 规格和 `CalibrationRunApproval`；审批至少由项目负责人、设备/固件负责人、现场工艺/安全负责人和测试负责人签署，四个身份及签名必须通过 Profile 批准的外置信任策略和信任锚验证，不能由 payload 中的字符串自证。事前审批绑定 approval ID/hash、immutable `profile_input_sha256`、validator source digest、设备序列号/USB 身份、型号候选、硬件修订、固件/点表版本、点位/寄存器、state IDs、动作风险、现场监护/急停、参照仪器、raw/reference collector tool ID/hash、精确只读 TX allowlist/预算、执行者、时间窗、有效期、nonce 和失效条件，但不得绑定尚未产生的结果。设备/固件/仪器/工具/计划/policy/hash 任一变化均使审批失效；运行结束后还必须由独立 `EligibilityApproval` 批准最终 gate/evidence/runtime 结果。

校准 profile 使用闭集 `unknown/analog/binary/counter`，且方法不能互换：连续模拟量使用 `analog + affine_holdout_return` 和下述 A/B/C/A' 规则；DI/DO/coil 等离散点使用 `binary + state_transition`，分别证明 inactive/active 新状态、回返、抖动/负对照和地址语义，不得出现仿射 mapping，且可表达经权威编码证据确认的 coil、register bit 或整寄存器离散量，不能把 binary 机械等同于 `bit`；累计量使用 `counter + monotonicity_rollover`，单独验证单位增量、单调性、modulus、回卷/饱和/复位语义及断电保持。validator 必须解析各分类的版本化证据内容而不是只检查文件哈希或 role；`unknown`、空占位 evidence 或分类不匹配只能保持 BLOCKED/INVALID。

每个拟校准点必须在看结果前批准一个 state plan，包含：设备/点位、改变状态的方法和安全条件、独立参照来源或仪器 ID、量程/分辨率/校准证书或校准状态、单位及显式单位换算、同步容差、每态样本数、聚合方法、稳定阈值、最小 raw/参照跨度、绝对/相对允许误差、uncertainty budget、异常样本规则、raw/reference collector tool ID/hash 和 TX 预算。Analog 可以使用事前声明的 affine 单位换算；Binary/Counter 的 reference unit 必须与 point unit 完全相同。参照不得由同一 raw、待验证旧公式或同一待校准传感链计算；所有样本使用 canonical UTC 时间戳和同一 run/state/sample/event ID 关联。每态 `N` 必须由不确定度分析给出且不得小于 3，并严格 exact-N，少采或多采都拒绝；状态跨度必须显著大于参照分辨率、设备量化和观测噪声的合成不确定度，允许误差必须同时受参照准确度和业务容差约束，不能用任意宽阈值换取通过。

有效状态至少执行 `A -> B -> C -> A'`：A/B 必须是能独立保持的不同物理状态，每态取得预声明的 N 组同步新样本并用预声明方法聚合；每一组必须由 calibration、independent reference 和 raw observation 三方以相同 run/state/sample/event/time/value 身份闭环，且每 `(point, role, run)` 全局 exactly-one，隐藏第二套 calibration/reference/raw evidence 直接拒绝。A/B 的 raw 与参照跨度都必须超过逐点批准阈值。C 必须在批准使用范围内，且与 A、B 的参照值分别超过预声明最小分离，不能贴近任一端点而只验证 offset。A' 必须经过完整状态转换和稳定等待，以新 state event/新样本采集，禁止复用 A；raw 与参照的回返误差都必须在批准阈值内，失败样本不得删除或重跑后覆盖。Counter 断电保持的 remove/restore 时间点分别按批准同步容差比较，不要求两个独立采集器报告完全相同的浮点持续时长。

两状态参数仅按下式形成 provisional 候选：

```text
ratio  = (reference_B - reference_A) / (raw_B - raw_A)
offset = reference_A - ratio * raw_A
```

`raw_A == raw_B`、非有限结果、未声明单位换算或变化测量点得到 ratio=0 均拒绝。候选解码必须先于拟合确定并按候选分别计算；不得在多种 signedness/字序中选择拟合最好的一个当作事实。两点总能唯一拟合一条直线，不能用两点残差或 R² 证明正确；正式 `resolved` 采用以下任一途径：

1. **推荐：** A/B 求 engineering 层的总仿射映射，另取未参与拟合且与 A/B 均有有效分离的独立状态 C 作 holdout；C 的预测误差必须同时低于业务容差并在合成不确定度之外可判别，且 A' 回返通过。保留未舍入计算值、舍入规则和 uncertainty budget。
2. **例外：** 只有两态时，必须另有可验证发布者/批准人、文档 hash、型号、硬件 revision、固件适用范围和点表修订号绑定的权威定义，明确寄存器、编码、单位、ratio/offset；A/B/A' 全部与其一致。文档与真机观测冲突时降级 `ambiguous`，没有权威绑定时最多 `provisional`。

两态只能识别一个 engineering 总仿射映射，不能唯一拆分四个运行参数。除非另有显示层独立批准证据，canonical 分配固定为 `point_ratio=engineering_ratio`、`point_offset=engineering_offset`、`user_ratio=1`、`user_point_offset=0`。

点名不能仅靠数值拟合 resolved。物理干预必须 one-at-a-time，保留未干预通道负对照并主动反证竞争候选；它只能支持点语义，不能单独确认设备型号。型号必须由可验证铭牌/设备身份、固件身份和适用点表版本共同绑定。

校准执行证据必须达到 B-06 同等级的不可覆盖链：独立 calibration runner/reference/raw observation JSONL，O_EXCL 创建和 fsync，run/state/sample/event ID，事前 approval/profile-input/script/config/image/point-map/validator-source hash，时钟源和同步误差，原始 RTU 帧与 reference reading，首尾/失败终态、排除日志、即时 SHA-256，以及生产前后容器/DB/GW 权限快照。artifact schema 使用 `v1`，calibration content 使用 `v3`，reference/raw content 使用 `v4`；runner 与两类 collector 必须由外置信任策略认证并绑定工具摘要、原始报告和签名 attestation。普通 JSON 自报 `PASS`、空 evidence 或可编辑人工 CSV 不能产生 `resolved/supported/ELIGIBLE`。

## Tasks & Acceptance

**Execution:**

- [x] 对原 MDF 建立 SHA-256，按物理页只读恢复 46 条候选，不附加或修改数据库。
- [x] 保存 BCMM 6 点、CBMM 40 点的逐项字段、页偏移、文件偏移和旧倍率。
- [x] 将 B-06 两个已认证 FC3 区间与候选对齐，并保持“只证明可读”的解释。
- [x] 记录旧公式冲突和当前 signed/type/onboarding/hot-reload/serial-lock 阻断。
- [x] 定义未来物理状态校准、`resolved/ambiguous/unsupported` 和后续故事前置规则。
- [x] 完成第一轮 JSON/文档校验与对抗审查，保持校准执行和生产动作 BLOCKED。
- [x] 交付可从原 MDF 重建 canonical JSON 的只读 parser、source/page/record hash、原始页头和结构/槽/偏移/源不变回归；checked-in JSON 与生成输出逐对象、逐字节一致。
- [x] 交付字段级 `supports/refutes/does_not_test` claim matrix、全局 contradiction register，以及绑定外置信任、canonical gate digest、分类证据内容、线路证据、受信 runtime runner 和 `ReleaseVerificationReceipt` 的派生 eligibility validator；当前 evidence 映射后保持 `BLOCKED`。
- [x] 交付候选包外 bootstrap 和四种 qualification 模式；候选内不含可执行 launcher，v2 或参数混用均拒绝。
- [x] 交付 v1 freshness request/attestation 纯验证模块并纳入 v3 toolchain；Windows/POSIX 受保护 publisher 的 `ValidatorProfile` 使用 OS 固定 provider/config/root，锁定同一 root 快照，provider 失败在 validator 启动前 fail-closed；公共 `validate` 不接受 freshness 参数，内部 handoff 的绑定参数不能绕过固定 config/key 的签名验证。
- [x] 交付 FC2 专属地址转换 evidence 门禁，FC1 coil 证据不能满足 FC2 discrete-input。
- [x] 交付 receipt 的实际 API 镜像 migration graph 静态绑定、严格 gzip/USTAR/OCI whiteout 与 runtime/metadata 预算、主机级候选锁和跨平台所有出口进程树回收回归。
- [ ] 另行审查并批准 calibration-run 规格后，执行设备身份和 A/B/C/A' 实物校准。
- [ ] 另建并批准 B-09 runtime/onboarding 实现规格，关闭 signed decode、原子禁用入库、共享串口锁及运行时证明阻断。

**Current external blockers:**

- [ ] Provision 并验收 trust-root anti-rollback 高水位与 freshness witness；本地文件、管理员权限或整盘回滚不得把旧 root/policy 重新变成当前信任。
- [ ] Provision 并验收 Windows verifier 的固定 runtime、依赖闭包、bootstrap 及专用签名密钥/agent 隔离；在此之前不得签发用于 B-08、B-09 或 canary 的有效 receipt。

### Review Findings

#### 2026-08-29 full adversarial review (closed offline findings)

- [x] [Review][Patch] 证据容器缺少 owner/role 闭集与全局基数约束，错误挂载或隐藏的第二套 calibration/reference/raw evidence 可绕过逐点 exact-one 门禁 [tools/validate_device_point_profile.py:5194]
- [x] [Review][Patch] identity 与 authoritative map 采用 existential `any`，允许同一 profile 中存在另一份受信但冲突的身份或点表证据 [tools/validate_device_point_profile.py:5346]
- [x] [Review][Patch] Windows 稳定接口路径大小写敏感且接受缺少 `#{GUID}` 的截断伪路径 [tools/validate_device_point_profile.py:196]
- [x] [Review][Patch] line configuration readback 只有任意 SHA-256 和自报字段，必须加入可重算的 POSIX termios/udev 或 Windows DCB/SetupAPI payload，并把路径 provenance 与设备序列号绑定 [tools/validate_device_point_profile.py:1886]
- [x] [Review][Patch] Binary/Counter 的 reference unit 未与 point unit 绑定，错误物理单位可通过事前计划 [tools/validate_device_point_profile.py:2659]
- [x] [Review][Patch] Counter 两个独立采集器的断电持续时长被要求浮点精确相等，应按 remove/restore 各自同步容差判断并补非对称偏移回归 [tools/validate_device_point_profile.py:4510]
- [x] [Review][Patch] Counter exact-N 缺少 `plan N=3 / evidence=4` 的超额样本回归 [tests/tools/test_validate_device_point_profile.py:3829]
- [x] [Review][Patch] calibration-run 事前审批绑定最终结果 payload，和“看结果前批准”约束不可同时满足；应绑定不含结果状态/映射的 immutable pre-run input，再由 eligibility approval 绑定最终 gate [tools/validate_device_point_profile.py:2777]
- [x] [Review][Patch] canonical gate 只绑定 validator 字符串 ID，未绑定可执行 validator source digest，代码变化后旧审批仍可能复用 [tools/validate_device_point_profile.py:2965]
- [x] [Review][Patch] 固定 policy trust root 只做 nofollow 单读，未验证文件及祖先 owner/ACL/替换权限，也没有认证 provisioning 边界 [tools/validate_device_point_profile.py:30]
- [x] [Review][Patch] evidence/runtime/release trust key 缺少按 artifact observation time 的 policy/key 有效期与撤销校验 [tools/validate_device_point_profile.py:3563]
- [x] [Review][Patch] release receipt 的 `receipt_id` 未由 `protected_snapshot_id` 强制派生，两个身份字段可互相矛盾 [tools/validate_device_point_profile.py:2391]
- [x] [Review][Patch] resolved point 未拒绝 Modbus coil/register/bit 范围重叠，冲突解释可同时获得资格 [tools/validate_device_point_profile.py:2272]
- [x] [Review][Patch] legacy mapper 对布尔、小数和数字字符串使用宽松 `int()` 强转，并在唯一型号时丢失候选型号值 [tools/validate_device_point_profile.py:5811]
- [x] [Review][Patch] timestamp 接受非规范等价时区文本并直接进入 hash/signature，需固定 canonical UTC 表示以避免跨实现摘要分裂 [tools/validate_device_point_profile.py:152]
- [x] [Review][Patch] release receipt producer 继承调用者 Docker endpoint/context/config，且按全局 tag load/inspect 存在 daemon 重定向和并发 tag 竞态 [tools/release_verification_receipt.py:391]
- [x] [Review][Patch] Alembic 镜像验证缺少 pids/memory/cpu 限制和有限超时，已签但恶意镜像可耗尽 verifier 主机 [tools/release_verification_receipt.py:391]
- [x] [Review][Patch] `verified_at` 在耗时检查前捕获，回执可能声称在 policy 窗口内验证而实际完成时已过期 [tools/release_verification_receipt.py:1091]
- [x] [Review][Patch] receipt output 与 candidate package 只阻止单向嵌套，反向祖先关系仍允许验证临时目录落入候选路径 [tools/release_verification_receipt.py:1112]
- [x] [Review][Patch] qualification toolchain 只被打包和校验，没有从已认证 archive 执行 producer/validator 的离线入口与包起点 E2E [tools/release_artifacts.py:74]
- [x] [Review][Patch] qualification toolchain 源文件读取缺少前后身份/元数据复核，同长度并发改写可生成混合快照 [tools/release_artifacts.py:809]
- [x] [Review][Patch] Windows candidate verifier 对 qualification 成员使用 16 MiB，而发布者链允许 64 MiB，导致跨平台验收分裂；PowerShell 数值 schema_version 还需显式拒绝 bool [deploy/verify-candidate.ps1:566]
- [x] [Review][Patch] Windows artifact/trust-root 读取未通过真正 no-follow Win32 handle 和最终路径/ACL 绑定，路径组件在检查后可替换 [tools/validate_device_point_profile.py:3423]
- [x] [Review][Patch] MDF extractor 的 B-06/源码佐证读取缺少 handle-bound nofollow 与读前后元数据复核，同长换源可污染 content-addressed claims [tools/extract_legacy_mdf_points.py:1365]
- [x] [Review][Patch] extractor stdout 在最后一次 parser source 复核前已输出完整工件，失败进程仍可能泄露可误用的 canonical bytes [tools/extract_legacy_mdf_points.py:2003]
- [x] [Review][Patch] 主规格已停止把 validator v3、旧 Schema/validator hash 和旧测试计数声明为当前验收结果；evidence ledger 按本次范围保留历史快照，稳定后必须另行重算而不得复用 [docs/superpowers/specs/spec-plan-5-b08-device-identity-point-evidence.md:10]

- [x] [Review][Patch] 审批身份目前可由调用者自造，必须用外置信任策略和可验证签名建立四角色真实性 [tools/validate_device_point_profile.py:369]
- [x] [Review][Patch] calibration/reference 绑定只验哈希不解析内容，必须按 analog/binary/counter 校验状态、样本、阈值、终态和 runner/reference 证据 [tools/validate_device_point_profile.py:586]
- [x] [Review][Patch] runtime `PASS` 可由普通 JSON 自报，必须绑定受信 runner、工具摘要、原始报告和签名 attestation [tools/validate_device_point_profile.py:768]
- [x] [Review][Patch] approval 只绑定 payload 和摘要集合，必须同时绑定 evidence role/subject、contradiction、runtime binding 及 validator policy 的 canonical gate digest [tools/validate_device_point_profile.py:739]
- [x] [Review][Patch] runtime target 未对签名发布 manifest 重算逻辑身份和镜像/迁移字段，虚构候选也可通过 [tools/validate_device_point_profile.py:755]
- [x] [Review][Patch] 已 resolved 的线路参数没有逐字段证据引用和设备身份绑定，可把 B-06 的 `1/9600/8N1` 直接自报为部署参数 [tools/validate_device_point_profile.py:220]
- [x] [Review][Patch] binary 校准被错误限定为 `bit` 编码，需允许经证据确认的整寄存器离散量并分别约束 FC1/2 与 FC3/4 [tools/validate_device_point_profile.py:684]
- [x] [Review][Patch] 点位及 contradiction 引用未强制匹配 evidence subject/role，其他点的证据可被跨点复用 [tools/validate_device_point_profile.py:696]
- [x] [Review][Patch] 寄存器宽度、地址上界及 signed `s32` 运行时边界证明不完整，无效读区或未测解码可获得资格 [tools/validate_device_point_profile.py:247]
- [x] [Review][Patch] artifact 在路径解析、hash 和 JSON 解析之间被重复打开，存在 hash 后换源 TOCTOU [tools/validate_device_point_profile.py:519]
- [x] [Review][Patch] 超大整数、未配对 Unicode surrogate 和畸形 legacy 字段可让异常逃逸而不是确定性返回 INVALID [tools/validate_device_point_profile.py:122]
- [x] [Review][Patch] Schema hash 未绑定自定义 validator policy，发布的 JSON Schema 也未明确结构校验不足以产生 ELIGIBLE [tools/validate_device_point_profile.py:997]
- [x] [Review][Patch] B-08 frontmatter 为 `in-progress`、正文总状态为 `blocked`，需统一为不可误读的 blocker 状态 [docs/superpowers/specs/spec-plan-5-b08-device-identity-point-calibration.md:5]
- [x] [Review][Patch] Windows NTFS ADS 输出可绕过 MDF 别名检查并写入源文件对象 [tools/extract_legacy_mdf_points.py:884]
- [x] [Review][Patch] SQL Server 页头 page ID/file ID 的偏移和宽度解码反转，错误值已进入 canonical JSON [tools/extract_legacy_mdf_points.py:658]
- [x] [Review][Patch] 输入未绑定预期原件路径且父 reparse/检查后换源未被拒绝，artifact 可能伪报来源 [tools/extract_legacy_mdf_points.py:112]
- [x] [Review][Patch] 输出父目录的 reparse 与检查后替换未被绑定，artifact 可能被重定向到意外目录 [tools/extract_legacy_mdf_points.py:880]
- [x] [Review][Patch] canonical 直接以最终文件名流式写入，崩溃时会暴露半文件并永久阻断重试 [tools/extract_legacy_mdf_points.py:890]
- [x] [Review][Patch] stdout 使用环境编码和平台换行，不能保证 canonical UTF-8/LF 字节 [tools/extract_legacy_mdf_points.py:914]
- [x] [Review][Patch] 未存储第五变长字段的记录仍声称观察到单位字段，需标为 NOT_STORED/UNRESOLVED [tools/extract_legacy_mdf_points.py:335]
- [x] [Review][Patch] extractor SHA 在扫描后重读脚本，运行中替换会把旧执行逻辑归因于新源码 [tools/extract_legacy_mdf_points.py:667]
- [x] [Review][Patch] C# 字段映射、旧换算公式和当前实现佐证没有逐文件内容摘要，关键 claim 无法内容寻址复核 [tools/extract_legacy_mdf_points.py:275]
- [x] [Review][Patch] legacy runtime claim 漏记当前设备身份和固件点表均未验证，与顶层限制不一致 [tools/extract_legacy_mdf_points.py:357]
- [x] [Review][Patch] 提取器测试缺少独立页头 oracle、内部页/记录边界分支及 ADS/reparse/原子中断安全回归，现有同源生成对比未发现页头错误 [tests/tools/test_extract_legacy_mdf_points.py:90]
- [x] [Review][Patch] 外置信任策略仍由调用者通过任意 `--trust-policy` JSON 提供且只校验结构，调用者可自建四角色、evidence、runtime 和 release 全套密钥后自签得到 `ELIGIBLE`；必须由受保护站点根或已验证 Site Profile 建立 policy authority，并绑定 Profile ID/版本/hash、有效期、轮换和撤销状态，增加“自建 policy + 全套自签仍拒绝”回归 [tools/validate_device_point_profile.py:298]
- [x] [Review][Patch] 事前 calibration-run 审批与事后 eligibility 批准未分离，当前时序检查反而拒绝晚于审批产生的 evidence/runtime，并允许测试证据先生成再签批；必须让不可变事前审批绑定设备/固件、state plan、仪器、样本/阈值、TX/安全预算且所有角色签名早于 run，再由独立事后资格批准绑定最终 gate/evidence/runtime 结果 [tools/validate_device_point_profile.py:1696]
- [x] [Review][Patch] line evidence 只包含 `unit_id/baud/data/parity/stop` 单一五元组，缺稳定设备路径、逐字段 evidence refs/claims 及与同一 USB/设备身份的绑定，仍可跨设备复用并把自报 `1/9600/8N1` 提升为 resolved；必须逐字段验真并绑定稳定路径和 DeviceIdentity [tools/validate_device_point_profile.py:629]
- [x] [Review][Patch] authoritative map 不表达点名和单位，也不绑定 hardware revision/firmware 适用范围；validator 对 `point_name/unit` 未做证据内容比对且只用 model/map version 对齐身份，因此任意名称/单位或跨固件 map 可自报 resolved；必须把 semantic name、unit 和完整 hw/fw/map scope 纳入受信内容及逐字段匹配 [tools/validate_device_point_profile.py:602]
- [x] [Review][Patch] analog evidence 只有每态单个聚合值和调用者自定 tolerance，缺 N>=3 原始同步样本、run/state/sample ID 与时区时间、A/B 最小跨度、C 双端分离、uncertainty/business tolerance 来源、参照仪器量程/分辨率/证书、排除日志及 runner/reference 终态；必须发布并解析完整版本化 analog run schema，拒绝单样本或任意宽阈值 [tools/validate_device_point_profile.py:449]
- [x] [Review][Patch] binary evidence 只验证一次 initial/active/return 数值和一个越界 negative control，未表达新 state event/样本、稳定/抖动门槛、未干预通道负对照、地址语义、参照来源和双通道终态；必须发布并解析适用于 coil、register bit 和已证明整寄存器离散量的完整 state-transition schema [tools/validate_device_point_profile.py:480]
- [x] [Review][Patch] counter evidence 只验证简化序列、modulus 和终值，未证明单位增量、采样/时间身份、饱和/复位条件、断电保持、参照独立性和 runner/reference 终态；必须发布并解析完整 monotonicity/rollover/persistence schema，缺任一项不得 resolved [tools/validate_device_point_profile.py:499]
- [x] [Review][Patch] runtime raw report 对所有 `RuntimeCheckId` 共用任意 assertion/detail 的通用 PASS 结构，release target 也只重算调用者提供的 manifest JSON，未绑定每项检查的闭集原始观测、实际加载镜像 ID 和真实迁移 head；必须使用 check-specific 报告 schema，并消费受信 runner 与 release verifier 对实际制品重算的不可伪造回执 [tools/validate_device_point_profile.py:1033]
- [x] [Review][Patch] B-08 自定义 raw-MANIFEST Ed25519 签名和新增资格制品无法由现有 OpenSSH `SHA256SUMS` 发布真实性链生成或验真，且现有候选精确 allowlist/Manifest schema 会拒绝 point profile、trust/runtime/validator 工件；必须复用已批准的 OpenSSH 包外信任锚/验证回执并扩展候选 allowlist、hash/Manifest 覆盖及生产依赖，增加真实 candidate 到 B-08 gate 的 E2E [tools/release_artifacts.py:48]
- [x] [Review][Patch] evidence 与 runtime artifact 顶层 Pydantic/JSON 解析未捕获 `RecursionError`，深度恶意 artifact 可让 CLI 异常逃逸而不是确定性返回 `INVALID`；必须统一所有外部 artifact loader 的递归边界和异常收敛并增加深嵌套回归 [tools/validate_device_point_profile.py:1378]
- [x] [Review][Patch] 临时输出句柄在发布前关闭，随后按可替换路径做 stat/link/move/content 信任，且临时 stat 尚未取得时 finally 会无身份约束地删除同名路径；必须让临时句柄、内容 hash 和身份贯穿 no-replace publish，并只做 handle/identity-bound cleanup，覆盖关闭句柄后换内容与删替身竞态 [tools/extract_legacy_mdf_points.py:1281]
- [x] [Review][Patch] source/output 父目录仅通过路径遍历和间歇性 stat 快照校验，实际 open/mkstemp/link/move/fsync 仍按可在检查后替换的路径名执行；必须持有父目录 handle 并用 handle-relative no-follow 操作绑定 source 与 output 全流程，覆盖检查与使用之间 rename/reparse 替换 [tools/extract_legacy_mdf_points.py:1202]
- [x] [Review][Patch] 内容寻址佐证只纳入 Web `DataBase.cs`，但字段映射和旧实时公式结论还引用 Server `DataBase.cs` 与 `ModBusServer.cs`；必须把两份 Server 源文件的路径、SHA-256、evidence IDs 和精确 locators 纳入 canonical `evidence_sources` 并锁定回归 [tools/extract_legacy_mdf_points.py:64]

#### 2026-08-29 final resource and chronology review (closed offline findings)

- [x] [Review][Patch] 发布链在读取 JSON/config 或扫描 archive 前没有统一资源预算，恶意计数/尺寸可造成内存、磁盘或 inode 耗尽；现统一为 4 MiB JSON/config、32,768 成员、8 GiB 单成员和 32 GiB 总扫描/展开，并在分配/解压前 fail-closed [tools/release_artifacts.py]
- [x] [Review][Patch] 远程维护审计可在生命周期变更前无界读取并重复完整验证；现限制为 16 MiB、64 KiB/行、50,000 条，先完整验证一次哈希链，再仅在受保护元数据未变时复用快照 [tools/remote_maintenance.ps1]
- [x] [Review][Patch] validator 对 top-level 与嵌套 evidence/approval/receipt/runtime/raw report 缺少统一总预算，声明小文件仍可触发超量读取；现绑定制品总预算 256 MiB，单次真实读取不超过 `min(64 MiB, size_bytes)` [tools/validate_device_point_profile.py]
- [x] [Review][Patch] extractor 固定佐证/源码在真实大小检查前可被分配，且 parser source 摘要采用 path `stat` 后重新打开，存在换文件窗口；现统一 1 MiB 上限并通过同一 no-follow 句柄完成身份、大小、读取、摘要和读后复核，覆盖同长路径替换回归 [tools/extract_legacy_mdf_points.py]
- [x] [Review][Patch] 已签 `ReleaseVerificationReceipt` 可以晚于 runtime raw start，最终 `EligibilityApproval` 也可早于 receipt；现强制 receipt 不晚于 raw start/runtime observation，且所有最终签批不早于 receipt/evidence/runtime，并加入完整签名合同负向回归 [tools/validate_device_point_profile.py]
- [x] [Review][Boundary] 进程内读取当前源码摘要不能证明已加载/cached executable bytes 与该路径完全相同；该风险不得伪装成已由 validator 自证关闭，保留为 B-08-W 的受保护 bootstrap、不可替换 runtime/dependency、pyc 禁用或隔离及签名 key/agent 外部验收项 [tools/validate_device_point_profile.py]

#### 2026-08-30 final review corrections (closed offline findings)

- [x] [Review][Patch] Docker 外层归档和嵌套 layer 的 PAX/GNU 扩展 payload 可在高层 member budget 生效前被 `tarfile` 分配；现先扫描 raw tar header 并在读取扩展 payload 前拒绝全部 PAX/GNU extension [tools/release_artifacts.py]
- [x] [Review][Patch] receipt 的实际镜像 migration 检查未拒绝重复外层 Docker 成员；现于 overlay/migration 解析前 fail-closed，并有重复 `manifest.json` 回归 [tools/release_verification_receipt.py]
- [x] [Review][Patch] receipt 已发布后 candidate lock 释放失败会进入普通失败清理语义；现抛出 `_ReceiptPublishedError`，保留完整 receipt 和已加载 tags，并留下明确恢复说明 [tools/release_verification_receipt.py]
- [x] [Review][Patch] 远程审计快照缓存只比较路径身份、大小和时间元数据，同长同时间内容替换可错误复用；现每次复用前比较内容 SHA-256 [tools/remote_maintenance.ps1]
- [x] [Review][Patch] Windows runtime 的 32,768 文件门槛未统一计入 runtime manifest；现固定为总计 32,768 个实际文件且包含 manifest，Python/Shell/PowerShell 门禁与边界回归一致 [tools/release_artifacts.py]
- [x] [Review][Patch] Windows system qualification 仍可从通用 Python bootstrap 进入未受保护路径；现通用 bootstrap fail-closed，并指向唯一受保护 PowerShell publisher [tools/qualification_bootstrap.py]

**Acceptance Criteria:**

- Given 原 MDF，when 只读解析第 258 页，then 输出恰好 46 条、BCMM=6/CBMM=40，逐行偏移在页内唯一且原件哈希不变。
- Given 没有 SQL catalog，when 描述物理页，then 只称“运行点形态候选”，不得命名具体 SQL 表。
- Given B-06 `27..35` 与 CBMM 对齐，when 分类型号，then CBMM 仅为中等置信候选，BCMM 保留，二者均未确认。
- Given 恢复行的候选 `ValueType` 文本为 `s16`，when 对照当前 API/GW，then 所有点 `direct_import_allowed=false`；42 条 FC3 点记录“字段映射若成立则 signed decode 为 P0 blocker”，4 条 FC1 点记录 coil/RBit/type 语义冲突，不允许用负倍率、未知 `value_type` 或机械转成 `bit` 绕过。
- Given 只有 A/B 两个拟合状态且无权威点表绑定或独立 C，when 分类倍率，then 最多 `provisional`，不得 `resolved`。
- Given A/B 求参、C holdout、A' 回返均通过预声明阈值，且身份、编码、语义无冲突，when 生成账本，then 才可把对应字段标 `resolved`，并记录全部样本、排除项、计算版本、误差和证据哈希。
- Given 查明为 signed/float/BCD/交换字序而当前 decoder 无法表达，when 分类，then 标 `unsupported`，不得选择当前代码恰好支持的错误解释。
- Given signedness、字序或点语义存在多个仍可解释 A/B/C 的候选，when 分类，then 保持 `ambiguous`；不得以最小拟合误差或当前实现便利性消除歧义。
- Given B-08 证据阶段完成，when 检查工作范围，then 没有新真机 TX、物理状态变化、生产数据库写入、Compose 修改、GW 重建/启用、控制、告警或通知。
- Given 当前 REST 不能原子 create-disabled，when 规划落地，then 后续实现故事必须先提供安全事务流程，不允许“create 后立即 disable”的已启用窗口。
- Given 设备/点位和串口绑定不会热加载，when 规划首次应用，then 后续实现故事明确 validator、精确 diff、受控 force-recreate 和回退；B-08 不自动执行。
- Given legacy JSON 的状态或布尔值被人工编辑，when eligibility validator 运行，then 因缺少 schema、派生字段、approval/profile/evidence hash 或 contradiction closure 而拒绝，不能获得部署资格。
- Given 当前 legacy evidence，when `validate-legacy` 在内存映射候选 profile，then 返回确定性 `BLOCKED`，线路地址/9600/8N1 不被补成部署默认值，analog/binary/counter 分类保持 `unknown`。
- Given 一个 profile 的 calibration kind 为 analog、binary 或 counter，when validator 运行，then 分别要求 affine holdout/return、binary state transition 或 counter monotonicity/rollover 合同；分类错误或字段不完整保持 BLOCKED/INVALID，离散点不得要求或接受仿射 mapping。
- Given calibration evidence 来自可覆盖 CSV、缺少 runner/reference 双通道终态或未绑定 approval/profile hash，when 分类，then 保持 BLOCKED，不得 `resolved`。
- Given approval 使用调用者自造身份、自签内容或未获 Profile 信任的密钥，when validator 验证，then 返回 BLOCKED/INVALID，四角色字符串、nonce 和文件哈希不能替代签名真实性。
- Given evidence 的 role 与 subject 不匹配、跨点复用、内容为空或不满足其 analog/binary/counter schema，when validator 验证，then 返回 BLOCKED/INVALID，不能仅凭路径、摘要或 role 获得 resolved。
- Given line protocol 只有调用者填写的地址或 `9600/8N1` 且没有逐字段设备绑定证据，when validator 验证，then 保持 BLOCKED，不得继承 B-06 请求参数。
- Given runtime evidence 只是普通 JSON 自报 `PASS`，或 runtime target 没有受信 `ReleaseVerificationReceipt` 证明 OpenSSH 候选、实际加载镜像和 migration head，when validator 验证，then 返回 BLOCKED/INVALID。
- Given JSON Schema 不变但 validator policy、证据契约或 trust policy identity 变化，when 复用旧 approval/report，then canonical gate digest 不匹配并使资格失效。
- Given 受保护 publisher 为 `ValidatorProfile` 生成 v1 freshness request，when 验证 live attestation，then site/challenge/candidate/profile/payload/gate/validator/verifier、root+policy 高水位、witness observation/expiry 和 monotonic state 全部精确匹配，且 validator 消费见证的同一 root 快照后才进入既有资格门禁。
- Given 调用者尝试替换 provider/config/key、改写内部 challenge/binding 参数、公共 `validate` 缺少 protected publisher context，或固定 provider 缺失/失败，when qualification 运行，then 无法伪造通过固定 key 验证的 attestation，资格在既有门禁或 validator 启动前确定性返回 `BLOCKED/INVALID`，不得回退本地 root。
- Given 仓库 freshness mocks 与回归通过，when 评估 B-08-T/B-08 状态，then 仍保持 `blocked`，直至真实 TPM/远端 witness 选型与 enrollment、root+policy 高水位、witness key/identity 及整盘回滚/本地替换/并发/故障演练全部取得现场证据；B-08-W、实物校准和 B-09 也仍分别阻断。

## Required Handoff to B-09 Runtime and Onboarding

预留 `B-09 device-runtime-onboarding-safety` 作为后续实现故事；它只能接收以下前置，**本规格不授权实施、重建、映射、canary 或任何新 TX**。B-09 必须另建并获批规格，且站点 Profile 与既有部署 Gate 仍分别有效。其技术资格只接受 manifest v3 完整 qualification toolchain 和通过 trust-root freshness、Windows verifier runtime/key isolation 及 trust policy 验证的有效 `ReleaseVerificationReceipt`；v2 候选不能作为 B-09 或 canary 起点：

1. 实现并测试完整 `s16` surface：ORM、Alembic/CHECK、API create/update/output、CSV import/export、DeviceTemplate、Web 编辑/显示、Registry、ingest/decode 和边界回归；未知值不得静默回退，并覆盖 `0x7fff/0x8000/0xffff`、API/DB/CSV/template round-trip 和旧 `字/双字/bit` 回归。
2. 实现 GW、探测器和维护工具共享的主机级串口排他锁、超时/所有者身份、崩溃后 stale-lock 恢复和双进程竞争测试；未持锁者必须在打开串口前失败。
3. 实现原子 create-disabled device+points 或等价的受审计单事务入库；失败全量回滚，读回逐字段一致后仍保持 disabled，并以并发、幂等/重放、租户/RLS、唯一串口地址竞争和故障注入证明没有 enabled 窗口、跨租户写入或孤立点位污染。
4. 生成哈希化、不可自动启用的点表 manifest 和 Compose/DB exact-diff dry-run；`dev_addr` 作为新的设备级输入绑定到批准的 `devices.modbus_addr`，不得从旧候选补默认值；DB/`GW_SERIAL_PORTS` 波特率和稳定设备路径必须一致，并固定 8N1 线路参数、点表 schema/version、审批 ID 和 evidence refs。
5. 在新规格中定义未来 canary 的只读 allowlist、TX/超时/重试/帧间隔/总时长预算、service gap 门槛、原始帧与 DB/API 对账及先禁用设备、再 force-recreate/撤销 override 的回退；该新规格还必须单独获得执行批准。持续采集、控制、告警和通知属于更后的独立 Gate。

## Spec Change Log

- 2026-08-27 initial evidence pass: recovered 46 physical rows from the original MDF and preserved page/file offsets without naming an unproven SQL table; retained BCMM and promoted CBMM only to medium-confidence because B-06 range `27..35` aligns exactly. All candidates remain non-deployable because the legacy encoding is `s16`, identity and scaling are unresolved, and current onboarding/runtime safety gaps remain.
- 2026-08-27 calibration hardening: clarified that two states only fit a line and cannot validate it. `resolved` now requires A/B fitting plus independent C holdout and A' return, or an authoritative device/firmware-bound point map plus A/B/A' agreement. Signed or otherwise unrepresentable semantics must be `unsupported`, and only schema-derived `resolved AND implementation_supported` points may enter a later implementation story.
- 2026-08-27 adversarial review: changed status from done to blocked because evidence discovery is complete but reproducible parsing, schema-derived eligibility and physical calibration are not. Added immutable scoped approval, minimum sample/uncertainty quality, meaningful C separation, fresh A' rules, canonical engineering/user scaling separation, authenticated authority requirements, field-level machine evidence and B-06-grade calibration audit requirements. Renumbered the story from B-07 to B-08 because the canonical deployment contract already uses B-07 for backup/restore, and removed any reading that this story authorizes a canary.
- 2026-08-27 offline gate implementation: added an authenticated read-only MDF extractor, canonical JSON byte/object lock, field-level claim matrix, open contradiction register, generated fail-closed point-profile Schema and validator. The validator forbids caller eligibility booleans, binds actual payload/Schema/evidence/approval/runtime hashes and separates analog/binary/counter calibration. Current evidence remains BLOCKED; the formal Plan 5 contract now treats B-08 as a non-waivable Gate 0 blocker and reserves B-09 for runtime/onboarding work.
- 2026-08-27 acceptance review hardening: the first eligibility implementation was not fail-closed enough because caller-authored approvals, placeholder calibration files and self-reported runtime PASS artifacts could construct ELIGIBLE. The hardened gate now requires an external trust policy, Ed25519 signatures for all four approval roles and trusted evidence/runtime identities, a dedicated OpenSSH-SSHSIG `ReleaseVerificationReceipt`, a canonical gate digest, exact role/subject binding, parsed classification evidence and device-bound line evidence. The receipt binds an approved verifier tool and publisher fingerprint to the protected candidate snapshot, actual loaded images and independently observed API-image migration head. The MDF extractor also corrected SQL page/file header decoding and hardened source/output identity and atomic publication. B-08 remains blocked on external physical evidence and B-09 runtime implementation.
- 2026-08-29 offline acceptance closeout: marked the v5 eligibility Schema/validator and historical adversarial findings complete at the repository/offline layer without changing B-08's overall `blocked` status. Manifest v2 remains supported only for general verification and existing remote maintenance; B-08 qualification/receipt, G0-06, G4-01, B-09 and canary require v3 plus a valid receipt. Added explicit external blockers for trust-root anti-rollback freshness and Windows verifier runtime/signing-key isolation.
- 2026-08-29 final hardening and verification: bounded release archives/config, remote audit input, validator-bound artifacts and extractor support files before allocation; removed the extractor parser-source path-reopen window; enforced receipt-before-runtime and receipt-before-final-approval chronology; regenerated the canonical ledger and reran all offline backend/frontend/static gates. Loader-byte identity remains explicitly external to in-process source hashing, so B-08 stays blocked on B-08-T, B-08-W, physical calibration and B-09.
- 2026-08-30 final adversarial corrections: raw PAX/GNU extension preflight, duplicate Docker outer-member rejection, post-publication receipt preservation, content-digest audit-cache validation, the manifest-inclusive 32,768-file runtime ceiling and Windows Python-bootstrap fail-closed routing were added with regressions. The 13-page Playwright navigation sweep received a test-local 60-second budget and the full suite passed without weakening assertions. Strict pre-commit Ruff/Mypy compatibility then added equivalent union-type checks and explicit descriptor narrowing; the canonical ledger was regenerated from the original MDF and differed only in its bound extractor source digest. External blockers and B-08's `blocked` status are unchanged.
- 2026-08-30 B-08-T repository gate: added the strict v1 root+policy freshness request/attestation verifier to the authenticated v3 toolchain and restricted `ValidatorProfile` to the protected Windows/POSIX publisher's OS-fixed provider/config/root path. The publisher binds the live challenge and protected snapshots before validator startup, the validator consumes the witnessed root bytes, public validation and caller-selected provider/config/key inputs fail closed. Repository mocks do not establish real site freshness; B-08-T and B-08 remain blocked on external enrollment/high-water/witness and rollback evidence, while B-08-W, physical calibration and B-09 remain separate blockers.

## Verification

**Final offline evidence (2026-08-29):**

- canonical legacy JSON SHA-256 `C7560A700B3FA3CCB47A098C560EB6F4E395ED3ECB268FE532EEF0404E3D7EB9`；extractor source SHA-256 `372955AD11F44652773BCA397FE7D57009F1EFECD924A030C63226F6E97F3F28`；point-profile Schema SHA-256 `93A23C65E786192B8855896C0C2EEAA4C755F0A94E4F2865D7EC27BF269A244F`；validator v5 source SHA-256 `276281C9227CCEBB9981C2109D5A1C195506B5431D405E93F0B2AF46869F4225`；release artifacts source SHA-256 `2F6DE3DAB9FC4862DDBC8B3FD114BB6C44E8C71F2FC0D6E0B1650653642A5684`；receipt producer source SHA-256 `F4BC055B257B05960E6B8E35B3B7B17DD2E8AFF001851D502BC14B819ECAF1AC`；package-external bootstrap SHA-256 `ABE132265D48FF92EE48844352317A34EE5486432795EC2736A3E7894CB55005`。Schema checked-in bytes 与 `render_schema_document()` 逐字节一致，canonical JSON 与 extractor 输出逐字节同摘要；本轮只同步 CI 跨平台适配、MDF 导入边界和认证测试 harness 引起的内容摘要，设备候选与 B-08 阻断结论不变。
- `validate-legacy` 实测退出码 `2`、decision `BLOCKED`、`292` 条原因，payload SHA-256 保持 `BE7ECBC384973AA4DFE687240E4B8281296AF67CC65A2B4FBB0320C1473D3440`；未补地址 `1` 或 `9600/8N1` 默认值。
- 2026-08-30 `tests/tools` 为 `799 passed, 7 skipped`；完整 Python 为 `1527 passed, 15 skipped`。跳过项均为 POSIX/Docker/Stage-D 外部集成环境，不改变离线门禁结论。
- `ruff check .` 通过，`ruff format --check .` 为 `317 files already formatted`，`mypy .` 为 `168 source files` 无问题，`uv lock --check --offline` 通过；PowerShell 7、Windows PowerShell 5.1 对 3 个修改脚本解析通过，Git Bash 对 2 个修改脚本 `bash -n` 通过。
- 前端 Vitest `83 passed`，ESLint、Vue typecheck、Vite production build 通过；13 页串行导航巡检使用 test-local 60 秒预算后，完整 Playwright `20 passed, 2 skipped`，两项跳过要求真实后端。mock E2E 中未启动真实 API 产生的 WebSocket proxy `ECONNREFUSED` 日志不构成用例失败。
- 第二轮对抗复审确认 receipt chronology 已关闭；未发现其他可在仓库/offline 边界内修复的当前缺陷。没有连接目标机、发送 Modbus 请求、修改生产数据库或执行联网部署。trust-root freshness、Windows loader/runtime/key isolation、实物身份/校准和 B-09 仍未验收，因此 B-08 总状态继续 `blocked`。

## Suggested Review Order

1. Verify the MDF hash, page parser contract, 46 record offsets and candidate counts.
2. Review CBMM/BCMM confidence language against B-06 raw observations.
3. Review signed decode, default-enabled onboarding, hot-reload and serial-lock blockers against current code.
4. Review A/B/C/A' calibration rules and ensure no two-point fit can become resolved without independent evidence.
5. Confirm a new implementation story receives only blockers and future eligibility rules, not an enabled production profile.
