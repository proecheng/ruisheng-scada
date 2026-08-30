# Plan 5 验收矩阵

## 证据规则

每项记录 Profile ID、候选 ID、提交、镜像 ID/摘要、目标机信息、执行人、开始/结束时间、命令或操作、原始输出位置、脱敏截图/日志、PASS/FAIL/BLOCKED/N/A 结论和关联缺陷。所有适用项必须 PASS；N/A 必须在执行前写入已批准的 Profile，FAIL/BLOCKED 不得通过“风险接受”或后续责任人绕过。

缺陷分级：P0 包括安全/租户边界破坏、不可恢复数据损失、未授权控制或真实 P0 告警遗漏；P1 包括不可重复部署/升级/恢复、主要业务链路失败或站点门槛不达标；P2 为不阻断批准范围且有明确规避措施的问题。任何 P0/P1 未关闭均为 No-Go。

## Gate 0：候选冻结

| ID | 级别 | 验收动作 | 通过条件 |
|---|---|---|---|
| G0-00 | 必需 | 批准 `site-acceptance-profile.md` | 所有适用字段有确定值和三方签署，N/A 有理由；后续变更可追踪并触发受影响项重测 |
| G0-01 | 必需 | 在候选提交运行后端与 Web CI | 静态检查、单元、集成、迁移、回放、构建、mock E2E 和真实后端 E2E 全部成功 |
| G0-02 | 必需 | 渲染根与离线 Compose 并按 allowlist 归一化比较 | 非 allowlist 差异为零；离线无 build/pull；通知、告警刷新、WAL、镜像、端口、依赖和卷契约完整 |
| G0-03 | 必需 | 生成候选包并验证发布身份、manifest/SHA256；从包外 bootstrap 执行四种 qualification；重复解析同一构建输入 | 使用批准的 OpenSSH 信任锚验证 `SHA256SUMS`、精确 v2/v3 文件集、Manifest 和逻辑身份，任一文件篡改后失败；v2 仅通过一般候选验证并可供既有远程维护，任何 B-08 qualification/receipt 请求必须拒绝；v3 候选只携带签名静态 toolchain（精确包含 `tools/trust_root_freshness.py`），不携带 launcher，候选包外受保护 publisher 仅接受 `ValidatorSchema/ValidatorProfile/ValidatorLegacy/Receipt` 及各自精确参数，Windows system qualification 经通用 Python bootstrap 必须拒绝并转向受保护 PowerShell publisher。`ValidatorProfile` 在 Windows/POSIX 上只调用 OS 固定 provider/config/root，生成一次性 challenge 并锁定 profile/policy/root/verifier；provider 失败或不可认证时在 validator 启动前返回 `BLOCKED/INVALID`，validator 消费同一 root 快照；公共 `validate` 缺 protected publisher context 时拒绝，caller provider/config/key/challenge 注入失败。Toolchain 必须为 canonical 单 gzip member、deterministic strict USTAR、精确 regular-file allowlist、零 padding/有界零 trailer；Docker 外层与嵌套 layer 在扩展 payload 分配前拒绝全部 PAX/GNU header，receipt migration 检查拒绝重复外层成员。一般 OCI 输入在分配/解压前拒绝超过 4 MiB JSON/config、32,768 外层成员、8 GiB 单成员、32 GiB 总展开或 64 MiB 聚合 metadata，whiteout 仅接受合法目标的零长度 regular file。Receipt 从实际加载 API 镜像最终 overlay 静态重建 source-only Alembic graph，迁移至多 4,096 个、单个 2 MiB、合计 64 MiB，禁止导入/执行 migration，唯一 head 与 Manifest/receipt 一致；构建与 receipt 对同候选共享主机级锁，receipt 发布后锁释放失败必须保留 receipt/tags 并明确失败。Windows runtime 限制总计 32,768 个实际文件（包含 manifest）、32,768 个目录、512 MiB 单文件、32 GiB 总量和 4,096-byte 路径；Windows gated kill-on-close Job 与 POSIX 进程组在所有出口有界回收。相同输入解析到同一逻辑身份，不强求未证明可复现的应用镜像 gzip 字节一致 |
| G0-04 | 必需 | 扫描制品、镜像历史、环境模板和日志 | 无真实密钥、令牌、联系人、客户数据或未批准样本 |
| G0-05 | 必需 | 在空白生产模式检查 bootstrap 与 seed | 服务首次监听时公开默认密码不可登录；无未批准 Demo 租户/用户/设备/点位；唯一管理员引导凭据已安全交接 |
| G0-06 | 必需 | 逐项关闭 `deployment-contract.md` 的当前阻断项 | B-01、B-02、B-03、B-04、B-07、B-08、B-08-T、B-08-W 有修复/控制证据且不得 N/A；B-08 只接受 manifest v3 与有效 `ReleaseVerificationReceipt`，canonical gate digest、validator source digest、事前 `CalibrationRunApproval` 和事后独立 `EligibilityApproval` 四角色签名、role/subject、线路/分类证据及受信 runner 全部闭合；每个 FC2 点另有 `FC2_ADDRESS_TRANSLATION`/`DISCRETE_INPUT_ADDRESS_TRANSLATION` 证据且未复用 FC1 coil evidence；receipt 早于 runtime、最终审批晚于 receipt/evidence/runtime；B-08-T 必须提交 live v1 attestation，精确绑定 root+policy 高水位、site/challenge/candidate/profile/payload/gate/validator/verifier、witness time/expiry/monotonic state，并以真实 TPM/远端 witness 完成选型、enrollment、key/identity、本地替换、整盘回滚、并发和故障验收；仓库 mocks/测试、v2、合成 ELIGIBLE、仅本地 ACL 或旧策略测试均不能关闭 B-08-T。B-08-W 的 Windows runtime/key isolation 也须通过 Profile 验收；B-05、B-06 由实现证据或已批准且可验证的站点约束解决；B-07 与 B-08 必须分别关闭 |

## Gate 1：断网全新安装

| ID | 级别 | 验收动作 | 通过条件 |
|---|---|---|---|
| G1-01 | 必需 | 在断网、无源码、无现有镜像/卷的目标等价机加载候选 | 只用候选包即可加载全部镜像，无网络拉取 |
| G1-02 | 必需 | 使用新生成站点密钥和受控站点 override 启动离线 Compose | 签名基础文件未变；`migrate` 退出 0，其余服务按依赖顺序启动，无占位符、弱密钥或 Demo 数据 |
| G1-03 | 必需 | 检查 API/GW live、ready、metrics 与 Web | 所有端点符合 `deployment-contract.md`，浏览器加载、登录和 WebSocket 建连成功 |
| G1-04 | 必需 | 重启应用容器和宿主机 | 数据卷、配置和 GW WAL 保持；服务自动恢复且 ready |
| G1-05 | 必需 | 核对监听端口和防火墙 | 仅批准网络可访问 Web、设备端口和管理端点，DB/Redis/API 不对外暴露 |
| G1-06 | 必需 | 从用户、设备、运维和非批准网段分别探测入口 | TLS/ACL/绑定与 Profile 一致；health/metrics 不可从未批准网段访问，认证和控制流量不经不受信网络明文传输 |
| G1-07 | 必需 | 检查时钟同步、日志轮转、磁盘/卷/WAL 容量和告警 | 时钟偏差、保留和容量均在 Profile 门槛内，模拟越阈能产生运维可见信号 |

## Gate 2：升级、备份与恢复

| ID | 级别 | 验收动作 | 通过条件 |
|---|---|---|---|
| G2-01 | 必需 | 用实际备份在空白隔离环境恢复完整栈 | 角色/权限、TimescaleDB 扩展、schema、数据、迁移 head 和受控站点配置恢复；RLS、readiness 与关键业务抽样通过 |
| G2-02 | 必需 | 从 Profile 指定的实际版本和代表性数据升级到候选 | 源制品可复现；迁移成功，用户、租户、设备、点位、历史、告警、订阅和审计保持 |
| G2-03 | 必需 | 在迁移中断、重复启动和应用启动失败点执行重试/回退 | 重试幂等且不重复 bootstrap；按兼容性决策切回旧版本或整库恢复，在批准 RTO/RPO 内回到已验证状态 |
| G2-04 | 必需 | 在 Redis/DB 短时不可用及 GW 重启后检查恢复 | WAL/outbox 重放收敛，ready 恢复，无不可解释的丢数、重复告警或永久积压 |
| G2-05 | 必需 | 审核并恢复加密保管的站点配置/密钥材料 | 新主机可恢复服务，秘密未进入证据包，轮换与保管责任符合 Profile |

## Gate 3：业务与租户边界

| ID | 级别 | 验收动作 | 通过条件 |
|---|---|---|---|
| G3-01 | 必需 | 用管理员和普通用户执行登录、设备/点位、实时、历史和报表流程 | 权限与页面行为符合角色，实时和历史数据一致 |
| G3-02 | 必需 | 建立 A/B 两租户并交叉访问设备、历史、告警、联系人和通知审计 | 外租户资源按契约拒绝或不可见，不泄漏标识与联系方式 |
| G3-03 | 必需 | 执行已批准的设备控制并观察结果 | 命令有幂等身份、状态和审计；超时/失败明确，不发生未授权或重复控制 |
| G3-04 | 必需 | 浏览器刷新、断网重连、损坏会话与换账号 | 无跨账号缓存、未捕获异常或遗留 WebSocket/定时器 |

## Gate 4：RS485 真机

| ID | 级别 | 验收动作 | 通过条件 |
|---|---|---|---|
| G4-01 | 必需 | 核对真机点表、地址、完整线路参数和容器设备映射 | 只接受 manifest v3、绑定 B-08 canonical gate digest、validator source digest、live v1 root+policy freshness attestation、事前 `CalibrationRunApproval` 与事后独立 `EligibilityApproval` 四角色受信签名、逐点 role/subject、设备线路证据、分类校准证据内容、受信 runtime attestation，以及 trust-policy-recognized verifier 签名并由 `EligibilityApproval` 绑定的有效 `ReleaseVerificationReceipt` 运行目标的不可变 manifest；每个 FC2 点必须有专属 `FC2_ADDRESS_TRANSLATION` discrete-input 证据，FC1 coil evidence 不可替代；B-08-T 已由真实 provider/high-water 与整盘回滚证据关闭，B-08-W 也已关闭，validator 派生全部 `resolved + supported`，系统能表达并实际应用全部参数，配置与设备协议一致，同一总线无地址冲突且只允许单并发轮询；v2 或任一 `unknown/candidate/ambiguous/unsupported` 点禁止进入 canary |
| G4-02 | 必需 | 按 Profile 批准时长连续采集并抽样与设备侧对账 | 实时、历史和现场读数在批准误差内一致，无持续队列/WAL/outbox 增长 |
| G4-03 | 必需 | 拔插串口、重启 GW、制造短时断线 | 自动重连，状态变化可见，恢复后不重复入库且缺口符合批准策略 |
| G4-04 | 必需 | 在隔离台架/仿真链路注入 CRC 错误、截断、重复和乱序帧 | 未向生产总线或未授权真机注入；坏帧被拒绝并计数，后续正常帧可恢复，不触发伪告警或失控 |
| G4-05 | 条件 | 执行客户批准的真实控制命令 | 设备动作、响应、API 状态和审计一致；急停/回退步骤有效 |

进入 G4-01 前必须另有经审查的 B-09 implementation/canary 规格和不可变现场执行审批，同时满足适用的 B-02/B-04、Profile、manifest v3 候选发布身份、有效 receipt、真实 trust-root freshness provisioning、Windows verifier isolation 和回退前置。validator `ELIGIBLE` 不自动授权 canary；设备、固件、点表、Profile、Schema、validator policy、trust policy/high-water、freshness provider/config/witness key/monotonic identity、签名人/密钥、证据 role/subject、runner、`ReleaseVerificationReceipt`、OpenSSH 发布根、verifier 工具/runtime 或运行镜像任一身份变化都会使结论失效并要求重验。仓库 mock attestation 不得成为现场证据，canary 结果只形成 G4-01/G4-02 证据，不能自动启用持续生产轮询。

## Gate 5：告警与通知

| ID | 级别 | 验收动作 | 通过条件 |
|---|---|---|---|
| G5-01 | 必需 | 为真机点位创建阈值和显式订阅，触发首次、持续、恢复、再触发 | 首次只建一个告警身份，持续不重复，恢复后可再触发；页面、记录与审计一致 |
| G5-02 | 必需 | 在 provider 全关闭时触发已订阅渠道 | 不发生外部调用；delivery 为可解释的 skipped/终态，pending 与 outbox 最终收敛 |
| G5-03 | 必需 | 在 Redis 短时中断、consumer/worker 重启时触发告警 | outbox/PEL/租约恢复，幂等物化成立，失败有界重试且指标可观测 |
| G5-04 | 条件 | 经单独授权对批准收件人测试每个真实 provider | 每渠道成功或按分类失败并脱敏审计；无未批准收件人和额外发送 |

## Gate 6：容量、恢复与遗留切换

| ID | 级别 | 验收动作 | 通过条件 |
|---|---|---|---|
| G6-01 | 必需 | 按 Profile 执行采集、API、WebSocket 和数据库负载测试 | 达到批准负载和持续时间，延迟、错误率、CPU/内存/磁盘与积压均在明确门槛内 |
| G6-02 | 必需 | 按 Profile 中断网络或关键依赖后恢复 | RTO、遥测缺口/重复、告警缺口、通知缺口、WAL/outbox/队列收敛均符合批准策略；零 P0 遗漏要求须证明告警恢复路径 |
| G6-03 | 条件 | 执行需求基准：3000 points/s、API 200 QPS P95<300ms、实时<1s、5000 在线等 | 每项目标在声明环境下有原始报告；未采用的指标有负责人书面决策 |
| G6-04 | 条件 | 回放不少于 10000 条真实帧并与旧解析/告警结果对账 | 注册、数据、控制、告警均覆盖，差异有归因和批准结论 |
| G6-05 | 条件 | 新旧系统双跑不少于 7 天并每日报告 | 差异不超过 0.1%，无严重丢数和 P0 告警漏发；否则立即 No-Go/回滚 |
| G6-06 | 条件 | 迁移 Profile 指定的遗留静态配置和历史数据 | 每类对象有源/目标计数、身份和抽样对账；双跑数据可区分来源且不向生产设备重复下发控制/通知 |

## Gate 7：签署与交接

| ID | 级别 | 验收动作 | 通过条件 |
|---|---|---|---|
| G7-01 | 必需 | 汇总结果、缺陷、范围变化和 N/A 决策 | 所有适用项 PASS，P0/P1 和 FAIL/BLOCKED 为零；N/A 均在开测前批准，P2 有规避、责任人和截止时间 |
| G7-02 | 必需 | 由另一名运维按手册执行安装、备份和恢复抽查 | 不依赖开发者口头知识即可完成，命令和预期结果与候选一致 |
| G7-03 | 必需 | 项目负责人、运维和客户代表审阅证据 | 三方签署 Go/No-Go；签署对象精确到候选 tag、提交和镜像摘要 |
