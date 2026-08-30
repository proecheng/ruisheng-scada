# B-08 自研设备身份、点表与倍率证据账本

## 结论摘要

- 旧 MDF 的 zero-based 第 `258` 页只读恢复出 `46` 条运行点形态候选：`BCMM` 6 条、`CBMM` 40 条。原件 `DataBase/DataBase/ModBus.mdf` 大小为 `1,362,690,048` 字节，SHA-256 为 `32BF2EAA2379B8D4B5BE58A3FFC833C245D0CEE8203CE97908D4E944D1C87A28`。
- 该页的物理行具有 21 列，最多 5 个 UTF-16LE 变长字段；末尾 `PointUint` 为空的行在物理记录中只保存 4 个变长列终止偏移。本轮没有 SQL catalog 证据，**不得把页或行写死为某个 SQL 表**。字段映射是高置信候选，不是已附加数据库的逻辑查询结果。
- `CBMM` 与 B-06 已认证可读的 FC3 `27..35` 精确对齐，因此提升为**中等置信候选**；`BCMM` 可解释 `0..5`，仍作为低置信候选保留。两者都不是已确认型号。
- 按当前高置信字段映射，46 条恢复行的候选 `ValueType` 文本均为 `s16`；这不证明当前实物固件采用该编码。42 条 FC3 候选若该映射成立，会被当前 GW 按无符号错误解码；4 条 FC1 候选另有 coil 地址与旧 `RBit` 语义冲突。故所有候选都不可直接导入，当前 `resolved=0`、可部署点 `=0`。
- canonical JSON 由 `tools/extract_legacy_mdf_points.py` 确定性生成并与测试逐对象、逐字节锁定：JSON SHA-256 `C7560A700B3FA3CCB47A098C560EB6F4E395ED3ECB268FE532EEF0404E3D7EB9`，extractor source SHA-256 `372955AD11F44652773BCA397FE7D57009F1EFECD924A030C63226F6E97F3F28`，parser algorithm 为 `b08-mdf-page-258-v3`。它保存正确解码的 SQL 页头 `page_id=258/file_id=1`、46 个 slot index/record length/record SHA-256、未映射固定字节、逐字段 claim matrix 和 12 个内容寻址佐证文件。
- point-profile Schema SHA-256 为 `93A23C65E786192B8855896C0C2EEAA4C755F0A94E4F2865D7EC27BF269A244F`，validator source SHA-256 为 `276281C9227CCEBB9981C2109D5A1C195506B5431D405E93F0B2AF46869F4225`，semantic validator 为 `ruisheng.device-point-profile-validator/v5`。当前 evidence 经 `validate-legacy` 内存映射后确定性为 `BLOCKED`，payload SHA-256 为 `BE7ECBC384973AA4DFE687240E4B8281296AF67CC65A2B4FBB0320C1473D3440`；没有把 B-06 地址 `1` 或 `9600/8N1` 补成部署 profile 默认值。完整 profile 的 `ELIGIBLE` 仍需现场批准的外置信任策略和真实证据，不由本账本产生。
- 本账本没有产生新的真机 TX、没有改变物理状态、没有写生产数据库、没有修改 Compose、没有重建或启用 GW。

## 证据边界

本账本把“证据等级”和“字段处置状态”分成两轴，并按字段保留支持、反证和未测试项，避免把 `unsupported` 误当证据质量：

| `evidence_grade` | 含义 | 能证明什么 |
|---|---|---|
| `source_candidate` | 旧 MDF、旧代码、注释或历史公式 | 只形成待验证候选 |
| `authenticated_raw_observation` | 签名工具在批准 scope 内得到且地址、FC、长度、CRC 有效的真机原始帧 | 只证明该范围在该时刻可读 |
| `physical_correlation` | raw 与独立物理参照在预声明状态和同步窗口内相关 | 支持寄存器与物理量关联 |
| `authoritative_definition` | 可验证发布者/批准人且绑定设备、固件和点表版本的权威定义 | 只支持其明确覆盖的字段和版本 |

| `field_status` | 含义 | 处置 |
|---|---|---|
| `unknown/candidate` | 无证据或只有来源候选 | BLOCKED |
| `ambiguous` | 多个候选仍可解释证据，或存在未解释冲突 | BLOCKED |
| `resolved` | 字段经独立验证并关闭反证 | 仍需 implementation/approval/runtime 门禁 |
| `unsupported` | 语义已查明但当前实现不能无损表达 | 先修实现，再重新验证 |

旧资料不得因“看起来合理”覆盖冲突；缺证据本身也是结论。机器可读逐点证据见 `evidence/b08-20260827/legacy-point-candidates.json`。

## MDF 只读恢复方法

- SQL Server 页大小按 `8192` 字节；读取原件绝对偏移 `258 * 8192 = 2,113,536`，未附加、复制或写回 MDF。
- 页 SHA-256 为 `56CFC72733B60C6C7CF321330F04F13A8A2883B7F742D8DA9A3B58F9EC4E5BD7`；保存 page `0..95` 的原始十六进制页头。页头 `page+22` 的 `m_slotCnt=46`、`page+30` 的 free-data offset=`6446`；页尾槽数组给出每条记录的实际 slot index 和页内偏移。
- 46 条记录在 `record+2` 的 fixed-length value 均为 `92`，因此固定区结束地址为 `record_offset + 92`；column count 均为 `21`，null bitmap 为 `3` 字节。variable-column count 观察到 `5` 或 `4`；4 列记录没有存储第 5 个变长字段的终止偏移，在缺少 SQL catalog 时其逻辑含义（空字符串、NULL 或其他布局）保持未决。
- 变长字段按 UTF-16LE 恢复为候选顺序 `DevType / PointName / UserPointName / ValueType / PointUint`。
- 固定区 `8..11`、`76..79`、`84..91` 没有足够证据命名，按每行原始十六进制保留；字段布局覆盖 `4..91` 且无重叠。旧代码中的 `DevAddr` 不能据此映射到任一未知区，因此当前 `dev_addr` 保持未决。
- 固定字段映射由整页稳定布局与旧 `PointData` 结构/读取代码交叉确认：
  - `ModBusServer20210908/ModBusServer20210908/ModBusServer/DataBase.cs:73-100`
  - `ModBusServer20210908/ModBusServer20210908/ModBusServer/DataBase.cs:891-917`
- 物理行的逻辑表身份、三段未可靠命名的固定字节，以及是否存在其他历史点表版本仍未决；这些字段不参与候选结论。
- 提取器只接受固定 source size/hash、zero-based page `258`、page hash、record status/fixed length/column count/slot count/model count/FC 集合；读取期间用文件身份和元数据前后快照拒绝换源。canonical 输出使用排他创建，拒绝覆盖现有工件或别名到 MDF。

## 候选点表摘要

### BCMM（6 点，低置信候选）

| FC | PointNumber | PointName / UserPointName | 类型 | 单位 | 旧倍率 |
|---|---:|---|---|---|---:|
| 3 | 0..4 | `T1..T5` / `1路温度..5路温度` | `s16` | ℃ | 0.1 |
| 3 | 5 | `R1` / `1路湿度` | `s16` | % | 0.1 |

支持项：B-06 的 FC3 `0..5` 可读。限制项：本页恢复的 BCMM 不含 `27..35`；但无法证明本页包含全部历史 BCMM 版本，因此不能据此完全排除 BCMM。

### CBMM（40 点，中等置信候选）

| 范围 | 候选语义 | FC / 编码 | 旧倍率 |
|---|---|---|---|
| 0..1 | DI、DO | FC3 / `s16` | 1 |
| 2..4 | 三相电压 | FC3 / `s16` | 0.1 V |
| 5..7 | 三相电流 | FC3 / `s16` | 0.01 A |
| 8..13 | 三相有功、无功 | FC3 / `s16` | 0.001 Kw/Kvar |
| 14..16 | 三相功率因数 | FC3 / `s16` | 0.001 |
| 17..22 | 三相电压、电流谐波 | FC3 / `s16` | 0.01 % |
| 23..25 | 频率、有功电量、无功电量 | FC3 / `s16` | 0.01 HZ / 1 Kwh / 1 Kwh |
| 26..35 | 八路温度、一路湿度、漏电电流 | FC3 / `s16` | 0.1 ℃ / 0.1 % / 0.001 A |
| 36..39 | `DI1`、`DI2`、`开关1`、`开关2` | 旧行写作 FC1，`PointNumber=0/0/1/1`、`RBit=0/1/0/1`、`ValueType=s16`；字段组合与当前 FC1 coil 语义冲突 | 1 |

B-06 第二段 FC3 `27..35` 对应 CBMM 的 `T2..T8 / R1 / LD`，区间长度和位置精确一致；这是支持 CBMM 的间接证据，但响应值 `[3,0,0,0,0,0,0,0,0]` 没有物理参照，不能确认这些点名或倍率。

## 已认证真机观察的继承

- B-06 run ID：`9ec05b61-3081-49bd-8020-55fb78a9dcd7`
- approval scope：`b06-9600-8n1-unit1-fc3-r0-5-r27-35`
- FC3 `0..5`：`[3,0,0,0,0,0]`
- FC3 `27..35`：`[3,0,0,0,0,0,0,0,0]`
- 原始审计及恢复供电后的隔离复核：`docs/superpowers/specs/evidence/b06-20260827/`

固定解释仍为：**只证明两个区间可读，不证明型号、点名、符号、单位或倍率。** 本轮没有重放这些请求。

## 旧公式冲突

旧系统不同路径对同一字段使用了不一致公式，旧倍率不能机械继承：

- 初载路径：`(raw * PointRatio - UserPointOffset) * UserRatio`，见旧 `DataBase.cs:919-920`。
- 实时 `s16` 路径不使用 `PointOffset`，见旧 `ModBusServer.cs:1765-1773`。
- 实时 `u16` 路径忽略两个 offset，见旧 `ModBusServer.cs:1776-1784`。
- 实时 `u32` 路径使用另一套两级 offset 顺序，见旧 `ModBusServer.cs:1788-1798`。
- Web 历史路径又统一先减 offset 再乘 ratio，见旧 Web `DataBase.cs:1614-1668`。

当前 GW 的唯一公式是：

```text
engineering = raw * point_ratio + point_offset
display     = engineering * user_ratio + user_point_offset
```

因此 B-08 将 MDF 中的 `Ratio/Offset` 仅保存为旧候选，不把它翻译成生产值。

## 当前实现阻断

1. **P0：signed decode 缺失。** API 仅允许 `字/双字/bit`；GW 单字按无符号 16 位，双字按高字在前的无符号 32 位。若 42 条 FC3 候选的 `s16` 映射成立，直接 SQL 写 `s16` 会被静默当无符号单字解码，不能用负倍率替代 two's-complement 解码。4 条 FC1 候选则因当前 API 要求 FC1/FC2 使用 `bit` 且禁止 `r_bit`，必须先解释旧字段组合，不能直接翻译。
2. **P0：安全禁用态入库缺失。** 当前创建设备请求不接收 `is_enabled`，ORM/数据库默认 `true`；设备、点位也没有一个原子事务的 onboarding API。此外 `Registry.load_from_db` 会加载全部 `device_points` 后按 `dev_number` 关联到已启用设备，故不能把孤立点位或“随后再禁用”视为隔离机制。
3. **P0：生产串口跨进程排他锁缺失。** GW、探测器和维护工具没有共享的主机级所有权协议。
4. **配置不热加载。** 设备、点位、poller 和串口绑定仅在启动时加载；现有 update subscriber 只刷新告警规则。以后应用点表需受控重建/重启 GW。
5. **字段语义差异。** 点位 `dev_addr` 会被加载，但轮询实际使用设备 `modbus_addr`；运行波特率来自 `GW_SERIAL_PORTS`，不是数据库 `baud_rate`。当前串口配置只显式表达端口和波特率，数据位/校验位/停止位依赖 pyserial 默认 8N1。

## 当前分类与未决项

| 对象 | 身份 | 语义/倍率 | 当前实现 | 是否可部署 |
|---|---|---|---|---|
| BCMM 6 点 | 低置信候选 | 未决 | 候选 `s16` 映射若成立则不支持 | 否 |
| CBMM FC3 36 点 | 中等置信候选 | 未决 | 候选 `s16` 映射若成立则不支持 | 否 |
| CBMM FC1 4 位点 | 中等置信候选 | 未决，旧类型与 FC 冲突需显式翻译验证 | 不能直接导入 | 否 |

必须继续解决：实物型号、固件/点表版本、每点语义、signedness/字节序/字序、独立物理参照、允许误差，以及权威换算公式。任何一项都不能从单次零值或地址对齐中补猜。

### Schema 门禁结果

- 当前 point-profile Schema 由 strict/`extra=forbid` 模型生成并可锁定结构，但 JSON Schema hash 不覆盖自定义 validator 语义。正式资格必须使用覆盖 Schema、validator policy identity、分类证据 schema 版本和 trust policy ID 的 canonical gate digest；结构校验本身不能产生 `ELIGIBLE`。
- 原 legacy JSON 不是 point-profile，直接当 profile 校验会因调用者派生布尔值而 `INVALID`；`validate-legacy` 只在内存中把 46 条候选映射成 schema-valid profile，再派生 `BLOCKED`，不写新工件。
- 当前 BLOCKED 报告有 292 条稳定排序原因：1 条全局设备身份及 46 点 identity 未闭合，46 点各自的 semantic/encoding/unit/calibration 未闭合，46 点 implementation unsupported，线路参数未决，3 个 contradiction open，approval、trust root、trust policy、runtime target 各缺 1 项，另缺 7 类 runtime evidence。
- profile 线路地址和 8N1 参数必须作为新批准的设备级输入，具有逐字段证据引用并绑定同一设备身份，不能从旧行或 B-06 请求地址补默认值。校准 profile 以闭集分为 analog/binary/counter；离散点不接受仿射 mapping，但可以是经证据确认的 coil、register bit 或整寄存器离散量；累计量单独要求单调性、modulus 和 rollover 语义。

### 资格门禁信任边界

- approval 的项目、设备/固件、现场工艺/安全和测试四角色必须通过 Profile 批准的外置信任策略与签名信任锚验证；调用者填写的身份字符串、nonce 或文件摘要不能证明审批真实性。
- 每条逐点 evidence 引用必须同时匹配允许 role 和 `subject_point_ids`；全局身份/线路证据须单独表达。resolved contradiction 必须引用 subject/role 相符的受信 resolution evidence，不能靠任意哈希文件关闭。
- analog 证据必须包含 A/B/C/A' 样本、阈值、不确定度和终态；binary 证据必须包含状态转换、回返、抖动/负对照和地址语义；counter 证据必须包含增量、单调、modulus、回卷/饱和/复位及保持语义。validator 必须解析证据内容，不能只校验路径、摘要或 role。
- runtime `PASS` 只能来自外置信任策略认证的 runner，并绑定工具摘要、原始报告和签名 attestation。runtime target 必须来自受信 release verifier 在同一受保护快照中生成的 `ReleaseVerificationReceipt`；该回执须证明 OpenSSH `SHA256SUMS` 链、精确文件集与包哈希、Manifest/逻辑身份、实际加载镜像和 API 镜像迁移 head，不能由 profile、approval 或普通 Manifest JSON 自报。
- FC2 discrete-input 点必须具有专属 `FC2_ADDRESS_TRANSLATION` runtime evidence，并由 `DISCRETE_INPUT_ADDRESS_TRANSLATION` role 绑定 FC2 地址和响应位；FC1 coil 的地址转换证据即使结构相似也不能满足 FC2。
- Qualification 从候选包外的 `tools/qualification_bootstrap.py` 或受保护 publisher 启动；候选包只携带 OpenSSH 链覆盖的静态 v3 toolchain，不携带可执行 bootstrap。系统 publisher 的闭集模式为 `ValidatorSchema`、`ValidatorProfile`、`ValidatorLegacy` 和 `Receipt`，任何模式/参数混用及 v2 请求均拒绝；Windows system qualification 经通用 Python bootstrap 请求必须 fail-closed，并指向受保护 PowerShell publisher。
- Toolchain archive 必须是 canonical gzip 头 `1f8b08000000000002ff`、恰好一个 gzip member、固定成员顺序的严格确定性 USTAR、regular-file-only、零 padding 和有界零 trailer；Python/PowerShell/Shell 都拒绝第二个 gzip member、非零 padding 或 allowlist 外成员。Docker 外层归档和嵌套 layer 在 `tarfile` 分配扩展 payload 前扫描 raw header，拒绝全部 PAX/GNU extension；OCI 验证还拒绝重复外层成员、执行 64 MiB 聚合 metadata 预算，并只接受零长度 regular-file whiteout。空/`.`/`..` 目标、链接、目录、设备、稀疏类型或非零 payload 均 fail-closed。
- Receipt 从实际加载 API 镜像的最终 overlay 静态重建 `/app/alembic.ini` 与直接 migration source 集，拒绝重复 Docker 外层成员、import-time 执行、缺失/重复 revision、环或多 head，且唯一 head 必须同时匹配 manifest 和 receipt；不执行 migration，也不相信宿主源码或调用者自报。
- 构建与 receipt 使用同一主机级 `.<candidate_id>.candidate-tags.lock`（Windows `C:\ProgramData\Ruisheng\locks`，POSIX `/var/lib/ruisheng/locks`）；同候选互斥、不同候选可并行、异常后可重取。receipt 已原子发布后若锁释放失败，必须报告 published-error 并保留完整 receipt 与已加载 tags。Windows runtime 在读取前限制总计 32,768 个实际文件（包含 runtime manifest）、32,768 个目录、单文件 512 MiB、总量 32 GiB 和路径 4,096 bytes；所有 POSIX/Windows qualification 出口都必须在有界时间内清理完整进程组/Job 及竞态后代。远程审计快照只有在受保护文件身份/大小/时间与内容 SHA-256 均未变化时才可复用。
- `ELIGIBLE` 仅表示资格门禁通过，不授权真机 TX、GW 重建、canary、持续轮询或生产切换；这些动作继续需要独立规格、现场执行审批和适用 Plan 5 Gate。

## B-09 后续实现移交边界

本账本向预留的 B-09 runtime/onboarding 故事移交的不是生产点表，而是阻断清单和候选证据。只有未来满足 Schema 派生的全部 `resolved` 且 implementation `supported` 的点才可进入不可变 manifest。B-09 必须覆盖 ORM/Alembic/API/CSV/DeviceTemplate/Web/Registry/ingest 的完整 `s16` surface、严格 type 约束、原子 create-disabled device+points、并发/幂等/租户-RLS/地址竞争、共享串口锁和受控 GW force-recreate dry-run；生产 canary 和持续轮询仍需独立规格与审批。

## 本轮校验

- 原 MDF 仅通过 Python `rb` 流顺序读取，读取前后文件身份/大小/mtime 必须一致；未 attach、未复制、未写回。
- 2026-08-30 完整 `tests/tools` 回归为 `799 passed, 7 skipped`；完整 Python 为 `1527 passed, 15 skipped`。skip 来自 POSIX publication、symlink、显式 Docker E2E 和 Stage-D 外部集成条件，不影响本轮只读门禁结论。
- Ruff、格式、Mypy、离线 lock、前端 Vitest/ESLint/Vue typecheck/Vite build 和 Playwright 均通过；13 页串行导航巡检使用 test-local 60 秒预算后，Playwright 为 `20 passed, 2 skipped`，两项 skip 要求真实后端。
- 最终复审回归覆盖 raw PAX/GNU preflight、重复 Docker 外层成员、receipt 发布后锁释放失败、审计缓存内容摘要、包含 manifest 的 runtime 文件上限及 Windows bootstrap 路由；这些离线修复不关闭任何外部 blocker。
- Schema 与权威模型逐字一致；最终 eligibility gate verdict 仍以 Review Findings 全部关闭、完整工具回归和第二轮对抗复审为准。
- canonical JSON 满足 `46 = 6 + 40`、42 条 FC3/4 条 FC1、46 个唯一 slot/record hash、所有 `direct_import_allowed=false`、`resolved=0`；validator `validate-legacy` 返回 BLOCKED。
- B-06 证据文件未修改，生产状态未触碰。
