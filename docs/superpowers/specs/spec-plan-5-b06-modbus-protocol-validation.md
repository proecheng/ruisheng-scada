---
title: 'B-06 自研设备 Modbus 协议只读验证'
type: 'feature'
created: '2026-08-25'
status: 'done'
baseline_commit: '8ab062079863837fb15e88dd8e87f53b473d3a7c'
context:
  - 'D:/江苏润盛/docs/superpowers/specs/spec-plan-5-b05-serial-hardware-onboarding.md'
  - 'D:/江苏润盛/需求清单/澄清证据档案.md'
---

<frozen-after-approval reason="human-owned intent - do not modify unless human renegotiates">

## Intent

**Problem:** 自研设备已通过 FTDI USB-RS485 接入目标机，但设备型号、串口参数、地址、点表和倍率未确认；生产数据库为空，演示种子不能作为现场依据。

**Approach:** 从旧 C# 程序和 SQL Server 数据库建立候选证据，用默认 dry-run 的只读 Modbus RTU 工具逐帧验证。首轮仅试 `9600/8N1/地址1/FC3` 的 `0..5`，结果支持时再试 `27..35`；参数证实前不接入生产 GW。

## Boundaries & Constraints

**Always:** 串行一问一答；设置 8N1、独占串口、短超时、有限重试、至少 500ms 帧间隔和请求上限；记录候选来源/冲突/置信度；校验地址、功能码、长度、CRC 和数量；五个容器保持运行，GW 无串口映射，生产库不写设备/点位。

**Ask First:** 扩展到证据白名单外的波特率、从站地址、功能码或寄存器区间；将验证结果写入站点配置/生产数据库；启用 GW 真实轮询；发送任何控制帧、告警或通知。

**Never:** 不发送 FC5/6/15/16/22/23 或私有/未知写码；不盲扫地址、波特率或寄存器；不把 demo、历史记录或一次超时当真值；不附加原始 MDF、不机械导入旧换算、不修改签名 Compose。

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| DRY_RUN | 合法配置，未给执行开关 | 输出 RTU 帧和预算，不打开串口 | 非法配置零 I/O 退出 |
| VALID_RESPONSE | 地址/FC/CRC/长度均匹配 | 记录原始寄存器、时延和证据结论 | 不自动写库或启动轮询 |
| MODBUS_EXCEPTION | 标准异常帧 | 记录异常码和请求 | 停止候选，不扩大范围 |
| TIMEOUT_OR_NOISE | 超时、CRC错、截断或错地址/FC | 记录原始接收和类型 | 有限重试后停止 |
| PORT_UNSAFE | GW 已占口、身份不匹配或无法独占 | 拒绝探测 | 不停止容器、不抢端口 |

</frozen-after-approval>

## Code Map

- `docs/superpowers/specs/spec-plan-5-b06-modbus-protocol-evidence.md` -- 旧代码、MDF、空库与真机响应证据。
- `deploy/site-modbus-probe.json.example` -- 不含现场结果的受限只读探测配置模板。
- `tools/probe_modbus_rtu.py` -- 默认 dry-run、显式执行、预算受限的一问一答探测器。
- `tools/run_modbus_probe.ps1` -- 认证安装、不可变镜像、GW 占口、前后状态和审计完整性的主机侧门禁。
- `tests/tools/test_modbus_probe.py` -- 配置边界、帧解析、超时/噪声和零写码回归测试。
- `tools/release_artifacts.py`、`tools/release_trust/verify-publisher.ps1`、`tools/release_trust/verify-publisher.sh` -- 将探测工具纳入签名清单和认证安装快照。
- `deploy/verify-candidate.ps1`、`deploy/verify-candidate.sh` -- 对候选中的探测文件执行精确 allowlist/hash 校验。
- `deploy/setup-customer.md` -- 远程执行、审计位置及保持生产采集关闭的操作说明。

## Tasks & Acceptance

**Execution:**
- [x] `docs/superpowers/specs/spec-plan-5-b06-modbus-protocol-evidence.md` -- 固化候选、冲突、前后状态原始证据和真机结果，区分事实与推断。
- [x] `deploy/site-modbus-probe.json.example`、`tools/probe_modbus_rtu.py` -- 固定 `0403:6001/AI06JYFW`、`400ms/1 retry/500ms after response/64B` scope；默认 dry-run；用 FD 的 `/sys/dev/char` 身份复核；严格解析标准响应/异常/噪声并输出独立终止摘要。
- [x] `tools/run_modbus_probe.ps1` -- 只读受保护发布回执而非调用者自报哈希；清除 Docker endpoint 环境并使用固定可信 CLI、受保护逐次快照、显式 Python entrypoint和有限超时；硬阻断 GW 串口/`/dev`/privileged/device-cgroup 权限及前后状态变化。
- [x] 发布与候选校验文件 -- 将 probe/runner/template 纳入签名精确 allowlist/hash；候选深检通过后才原子安装，并从认证 `SHA256SUMS`/`MANIFEST.json` 生成包含脚本哈希、GW image ID、candidate/source identity 的受保护回执。
- [x] `tests/tools/test_modbus_probe.py` 及发布/runner 测试 -- 覆盖矩阵、真实 CLI execute、FD alias、标准异常码、持续审计故障、PowerShell 行为门禁/审计闭合、固定 Docker/timeout、全部可生成请求属于 `{1,2,3,4}`。
- [x] `deploy/setup-customer.md` -- 记录认证安装、最终配置、受保护回执、唯一双通道审计及维护端远程执行流程。
- [x] 目标机 -- 用认证修正版复核空库/GW未占口，执行 dry-run 和最小真机验证；保存脱敏前后状态、退出码、哈希和逐帧证据。

**Acceptance Criteria:**
- Given 配置含写码、越界值、多波特率、过多请求或过快间隔，when 校验计划，then 打开串口前拒绝且无 TX。
- Given 有效或异常 RTU 响应，when 探测器接收帧，then 审计记录可关联请求并包含 CRC、原始十六进制、时延和分类结果。
- Given 无响应或仅有噪声，when 预算耗尽，then 停止且不自动更换地址、波特率或寄存器。
- Given 真机验证结束，when 复核目标机，then 五个容器仍健康、GW 无 `GW_SERIAL_PORTS`/设备映射、生产设备与点位表仍未被探测器修改。
- Given 探测文件不在签名清单、哈希不匹配或 GW 镜像不是运行容器的不可变 image ID，when runner 预检，then 映射串口前拒绝。
- Given GW 有串口环境、任何精确/子路径 `/dev` 映射、privileged/device-cgroup 权限、设备忙、审计路径已存在或前置状态不完整，when 请求执行，then runner 零 TX 退出并留下结构化拒绝证据。
- Given 五个容器进入预检，when 判定生产状态，then postgres/redis 必须 `running+healthy`，API/GW/Web 必须 `running` 且若定义 healthcheck 也必须 `healthy`；前后证据包含 container ID/image ID/StartedAt/RestartCount 并完全一致。
- Given transport/close 失败且 probe 审计仍可写，when 工具终止，then probe JSONL 含实际发送数及 `aborted` 且 CLI 非零；given probe 审计持续失败或缺失，then 独立 O_EXCL runner JSONL 记录子进程终止摘要、已知 TX 数或明确 `unknown`、审计无效原因并非零退出，绝不声称 probe JSONL 完整。
- Given runner 启动探测容器，when 镜像存在 ENTRYPOINT、Docker endpoint 被环境覆盖或 CLI 挂起，then 固定本机 Docker、显式 `python` entrypoint 和超时清理保证不会启动 GW 入口且产生非成功终态。
- Given probe 子进程返回，when runner 验收审计，then 规范化唯一路径、run ID、首尾事件、退出码、脚本/配置/image 绑定和即时 SHA-256 一致，否则失败。
- Given 收到有效响应，when 写入证据，then 记录固定保守结论“仅证明区间可读，型号/点名/倍率未决”。

## Spec Change Log

- 2026-08-25 review loop 1: 三路审查发现初版未把探测脚本/配置/运行镜像绑定到认证发布物，也没有主机侧硬门禁证明 GW 未占口和保存可复核的前后生产状态。Code Map、任务、AC 和验证已补入签名 allowlist/hash/认证安装、不可变 image ID、runner 预检、唯一持久审计及异常终止要求，避免以可变标签或可替换 bind 脚本获得串口权限。KEEP：硬编码的 `9600/8N1/地址1/FC3` scope、`0..5 -> 27..35` 依赖、最多 4 TX、默认 dry-run、打开后 USB 身份复核、原始帧/CRC/时延、零数据库写入；保留首次 provisional 真机证据：run `df3086aa-f2e2-4ddf-84e9-a20e24cec76b`，两帧均首问有效，RX 原值分别为 `[3,0,0,0,0,0]` 与 `[3,0,0,0,0,0,0,0,0]`，旧审计 SHA-256 `E32836A76E3F9332589A01DF04D7DCF3D224D7463B1A47E814451B53B1264029`；这些只证明通信，修正版认证链验收前不提升为最终发布证据。
- 2026-08-25 review loop 2: 三路审查证明第二版在真实容器中会因缺少 `/dev/ttyUSB*` 而无法完成 FD 身份复核，PowerShell 使用不存在的 `File.Open` 重载，且 runner 可接受 `/dev:/dev`、`starting`、缺失 probe 审计；更根本地，原 AC 要求持续故障的同一 JSONL 仍写入 `aborted`，物理上不可兑现。任务/AC 已改为 `/sys/dev/char` FD 身份、固定 USB/预算、发布回执、候选深检后安装、固定 Docker/entrypoint/timeout、完整设备权限和生产快照、probe+runner 双通道终止证据；持续 sink 故障必须由 runner 标记 probe 审计无效和 TX 已知/未知，禁止伪造完整性。KNOWN-BAD：只做字符串测试、调用者自报哈希、先哈希后挂载可变文件、仅拒绝 `unhealthy`、不验收审计文件。KEEP：两条固定 FC3 帧、依赖顺序、最多 4 TX、默认 dry-run、O_EXCL+fsync、完整写后 `request_tx`、有效帧保守结论、签名精确 allowlist、provisional 真机帧及旧审计哈希。
- 2026-08-26 review loop 3 patches: 三路审查发现串口写异常可能把未知 TX 误报为零、执行容器可写全部历史 probe 审计、失败运行缺少后置状态、Docker kill 后仍可无限等待、审计新目录项未 fsync、`Tmpfs`/根挂载及不完整事件序列未被 runner 拒绝，逐文件发布也没有异常回滚。实现已改为 unknown TX 语义、逐次审计 staging/发布、失败前后状态、有限 kill wait、父目录 fsync、根路径/`Tmpfs` 门禁、固定事件状态机、`create -> ID check -> start --attach` 和稳定清理观察；发布使用全局互斥、全量 staging/预哈希、逆序回滚及回执最后提交。KEEP：冻结的两段 FC3 scope、默认 dry-run、有效响应保守结论、认证镜像/脚本/配置绑定、生产 GW/空库不变和不记录敏感 inspect 原文。
- 2026-08-27 review loop 4 patches: 目标机认证验收发现包内候选校验器按设计以退出码 2 保留 B-04 启动阻断，而外置 Windows bootstrap 只在退出码 0 时执行 `-InstallSerialTools`，使 B-06 工具安装分支永远不可达；安装后又把候选 Manifest 的 GW ID 写入回执，而非规格要求的当前运行容器 `.Image`，在不重建生产服务时必然阻断 runner；首次修正还以逗号分隔的 PowerShell 原生命令实参调用 Docker，真实 Windows CLI 将其安全拒绝；runner 把 Docker JSON 的 `Devices: null`/`DeviceCgroupRules: null` 包成单元素数组并误判为设备权限，且预检拒绝后未补采后置生产状态；容器 ID 查询的单元素 PowerShell 输出又退化为标量字符串，`ids[0]` 只取首字符而拒绝了已正确创建的容器。bootstrap 现仅将 0 和 2 视为完成候选深检的可识别终态；显式请求时可在 2 下安装认证工具并仍原样返回 2，其他非零码继续立即拒绝；回执通过固定 Docker CLI、参数数组、named-pipe endpoint 和受保护空配置绑定运行中 `ruisheng-gw` 的不可变 `.Image`；runner 过滤空 Docker 项、强制 ID 列表为 `string[]`，并在已有前置状态的失败路径尽力补采后置状态和采集错误。B-04 不被解除。KEEP：签名/allowlist/全包哈希/五镜像加载身份校验必须先完成，安装仍只从受保护快照提交，生产服务不重建，runner 继续拒绝真实设备权限、身份失配和回执与运行 GW 不一致。
- 2026-08-27 final acceptance: `deploy-20260827.2` 已在目标机完成签名安装、dry-run 和最小真机验证。run `9ec05b61-3081-49bd-8020-55fb78a9dcd7` 的两条固定 FC3 请求均首问获得 CRC 有效响应，TX 为 2、写入 16 字节、退出码 0；probe/runner 审计 SHA-256 分别为 `e39cfc742f724e864686595635373832bde48c10831ab08f6118e7cb376e489e` 和 `e0a1459363e34d33a5d4b783c0f40d1fa8300f9f1556f2e9c41f9a0238b656dc`。前后五容器身份和运行状态不变，数据库仍为 `0/0`，GW 无串口能力或 `/dev` 权限且无探测容器残留；恢复供电后的只读复核再次确认这些边界。结论保持“仅证明区间可读，型号/点名/倍率未决”。

## Design Notes

探测器复用现有 CRC/读响应语义，但在调用会按位截断参数的编码器前严格校验。执行工具必须由已签名候选认证安装；runner 仅信任候选深检后生成的受保护回执，使用运行中 GW 的 `.Image` sha256，不接受 `.Config.Image` 标签，并从受保护逐次快照挂载 probe/config。串口 FD 身份从 `/sys/dev/char/<major>:<minor>` 解析，不依赖容器内额外 tty 节点；请求间隔从上一响应读取结束后计算。旧 MDF 同时含 BCMM、CBMM 候选；倍率需结合真机原值、物理范围和旧公式确认，不能仅凭响应成功判定。生产 GW 的协调排他锁仍按延期项另行实现，本故事以拒绝任何 GW 设备能力并由探测器独占打开隔离现有生产路径。

runner 使用显式本机 named-pipe endpoint 和空的受保护 Docker 配置，避免用户当前 context 改写目标 daemon；Windows 参数按 `CommandLineToArgvW` 规则引用。探测容器名称在启动前验证为空，结束或超时后强制清理并再次确认不存在。逐次目录保留认证 probe/config 快照以供复核，但不持久化完整 `docker inspect` stdout，避免把数据库 URL、密码或令牌带入证据目录；runner JSONL 只保存脱敏生产状态。

执行态只把逐次审计 staging 映射为 `/audit`，容器退出并确认不存在后才将唯一文件移动到最终审计路径，因此无法改写历史证据。串口 `write()` 抛异常时不能知道内核已接受多少字节，probe/runner 必须把 TX 与字节数记为 `unknown`。发布安装的多文件提交由全局互斥串行化，所有候选文件在受保护 staging 中先验哈希；普通失败逆序恢复备份，回滚失败则移除回执并保留恢复目录，任何混装状态都不能被 runner 认证。

## Verification

**Commands:**
- `uv run pytest tests/tools/test_modbus_probe.py tests/tools/test_serial_hardware.py` -- expected: all pass。
- `uv run pytest tests/tools/test_release_artifacts.py tests/tools/test_publisher_authenticity.py` -- expected: all pass。
- `uv run ruff check tools/probe_modbus_rtu.py tests/tools/test_modbus_probe.py` -- expected: clean。
- `uv run mypy tools/probe_modbus_rtu.py` 及 PowerShell 5.1/7 parser -- expected: clean。
- 构建候选并运行 PowerShell/Shell candidate verifier -- expected: 探测文件只来自签名清单且哈希匹配。
- 目标机通过 runner 执行 dry-run/受限读取并复核脱敏前后状态 -- expected: 审计可独立复核且生产状态不变。

## Suggested Review Order

**只读执行边界**

- 从固定配置白名单开始，确认只有两条 FC3 帧可生成。
  [`probe_modbus_rtu.py:190`](../../../tools/probe_modbus_rtu.py#L190)

- 核对串口独占、响应后间隔、有限重试和 unknown TX 终止语义。
  [`probe_modbus_rtu.py:593`](../../../tools/probe_modbus_rtu.py#L593)

- 核对 O_EXCL、文件及父目录持久化的 probe 审计。
  [`probe_modbus_rtu.py:489`](../../../tools/probe_modbus_rtu.py#L489)

**主机门禁与证据**

- 从认证回执读取不可变脚本和运行镜像身份。
  [`run_modbus_probe.ps1:329`](../../../tools/run_modbus_probe.ps1#L329)

- 采集脱敏容器、GW 权限和空生产库前后状态。
  [`run_modbus_probe.ps1:382`](../../../tools/run_modbus_probe.ps1#L382)

- 验证逐帧事件状态机、真实退出码和即时审计哈希。
  [`run_modbus_probe.ps1:548`](../../../tools/run_modbus_probe.ps1#L548)

- 先 create/核对 ID，再 start，隔离审计并稳定确认清理。
  [`run_modbus_probe.ps1:765`](../../../tools/run_modbus_probe.ps1#L765)

**认证发布**

- 事务 staging、预哈希、互斥、回滚和回执最后提交。
  [`verify-publisher.ps1:141`](../../../tools/release_trust/verify-publisher.ps1#L141)

- 精确签名包 allowlist 纳入 probe、runner 和配置模板。
  [`release_artifacts.py:48`](../../../tools/release_artifacts.py#L48)

**验证与运维**

- 回归固定帧、写异常 unknown TX 和真实 PowerShell 自检。
  [`test_modbus_probe.py:156`](../../../tests/tools/test_modbus_probe.py#L156)

- 从认证安装到远程 dry-run/execute 的维护端操作顺序。
  [`setup-customer.md:221`](../../../deploy/setup-customer.md#L221)

- 区分 provisional 真机帧与最终认证验收，并核对双通道审计哈希。
  [`spec-plan-5-b06-modbus-protocol-evidence.md:1`](spec-plan-5-b06-modbus-protocol-evidence.md#L1)
