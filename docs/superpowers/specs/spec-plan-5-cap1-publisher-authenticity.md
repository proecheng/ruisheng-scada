---
title: 'Plan 5 CAP-1 发布者真实性'
type: 'feature'
created: '2026-08-22'
status: 'done'
baseline_commit: '30709c0f77d014f952b24e07152485b617c326b2'
context:
  - 'docs/superpowers/specs/spec-plan-5-customer-deployment-acceptance/SPEC.md'
  - 'docs/superpowers/specs/spec-plan-5-customer-deployment-acceptance/deployment-contract.md'
  - 'docs/superpowers/specs/spec-plan-5-b03-release-artifacts.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** 当前候选只有 SHA-256 自洽性，不能证明发布者身份；目标机也没有候选包外的信任锚，CAP-1/G0-03 必须保持 BLOCKED。

**Approach:** 使用已批准的专用 OpenSSH Ed25519 身份 `ruisheng-release` 对规范化包索引 `SHA256SUMS` 的原始字节签名，以包外固定公钥、指纹和受保护引导校验器在任何镜像加载前建立发布者真实性，并生成新的 `deploy-20260822.2`。

## Boundaries & Constraints

**Always:** principal 固定为 `ruisheng-release`，namespace 固定为 `ruisheng-candidate-v1`；私钥仅留在本机、使用随机口令加密，口令只以当前用户 DPAPI 密文保存且不进入参数、环境、Git 或日志；目标机只保存单一公钥 allowed-signers 和指纹，位于候选外并限制为管理员/SYSTEM 可写。`SHA256SUMS.sig` 进入文件 allowlist 但不进入 sums，sums 覆盖双 Manifest、脚本、Compose、配置和五个镜像。外置引导校验器先验签并核对全包哈希，包内 PowerShell/Shell 再重复验签；所有真实性验证均早于 Manifest 信任和 `docker load`。Manifest 只声明 `SIGNED`，只有包外信任锚验签成功的运行结果才能称 `VERIFIED`。旧候选 `.1` 不改，新候选原子生成且源提交干净。

**Ask First:** 轮换/撤销密钥、增加第二发布者、改变 principal/namespace/信任路径、改用 KMS/Sigstore/证书、放宽私钥保管或引入 release channel、过期时间、最低安全版本策略。

**Never:** 不上传私钥或 DPAPI 密文到目标机，不把候选内公钥或脚本当信任根，不用 `check-novalidate`，不让调用方从 Manifest/CLI 提供“期望指纹”，不在验签失败后加载镜像或启动服务，不把签名通过宣称为整个 Plan 5、生产上线或其他 Gate 完成。

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| 签名生成 | 已解锁专用私钥、严格单行 allowed-signers、干净 HEAD | schema 2 候选含 `SHA256SUMS.sig`，生成后立即用批准锚回验再原子发布 | 签名/回验失败清理 staging、锁和候选标签 |
| 离线验证 | 包外锚、principal、namespace、指纹和签名均匹配 | 外置校验器核对签名与全包哈希，包内校验器才可继续归档/镜像检查 | 缺工具、锚位置/ACL异常或身份不符均退出 1 |
| 篡改/替换 | 修改 payload、sums、sig、Manifest、包内校验器，或换 key/principal/namespace | 在任何 Docker 调用前拒绝 | 明确报告 publisher authenticity FAILED，Docker 调用数为 0 |
| 站点审批 | Profile 使用批准身份与固定指纹 | B-04 重新验证；签名字段可审计 | 未决字段继续退出 2/BLOCKED |

</frozen-after-approval>

## Code Map

- `tools/release_artifacts.py` -- schema 2、签名生成/回验、严格身份模型、原子候选入口。
- `deploy/verify-candidate.ps1` / `deploy/verify-candidate.sh` -- 包内跨平台纵深验签和加载前门禁。
- `tools/release_trust/verify-publisher.ps1` / `verify-publisher.sh` -- 安装到候选外的全包引导校验器。
- `deploy/export-images.sh` / `deploy/setup-customer.md` -- fail-closed 参数与操作手册。
- `tests/tools/test_release_artifacts.py` / `test_release_artifacts_docker.py` / `test_production_compose.py` -- 合成、真实 OpenSSH、Docker 29 与加载前失败证据。

## Tasks & Acceptance

**Execution:**
- [x] `tools/release_artifacts.py` -- 实现签名契约、固定且受保护的系统验签工具、不可竞态的锚副本、严格锚解析、完整候选快照、容量预检、原始字节签名/验签与失败清理。
- [x] `deploy/verify-candidate.{ps1,sh}` -- 在解析可信 Manifest 和加载镜像前以净化的提权环境验证包外身份及不可变候选快照；真实性失败退出 1，且本校验器无条件保留 B-04 退出 2。
- [x] `tools/release_trust/*` -- 提供候选外 bootstrap，全包哈希核对，使用固定受保护工作区/工具/Docker 配置，并拒绝包内锚、链接、不安全 ACL、磁盘不足及复制中增长。
- [x] `deploy/export-images.sh` / `deploy/setup-customer.md` -- 记录密钥代理、受控锚参数、固定工作区和新候选流程，不暴露口令。
- [x] `tests/tools/*` -- 覆盖正常签名、身份/字节/换行/脚本篡改、错误锚、原子清理、资源门禁和可执行平台上的 Docker 前拒绝；真实三端证据保留至 rollout gate。

**Post-review rollout gate:** 本地代码审查和干净提交完成后，才可生成密钥与 `deploy-20260822.2`。随后在目标机更新 `C:\Ruisheng\site\site-acceptance-profile.md`，以管理员/SYSTEM ACL 安装 `C:\ProgramData\Ruisheng\trust\release-allowed-signers`、`release-key-fingerprint` 和 `C:\ProgramData\Ruisheng\bin\verify-publisher.ps1`，上传并验证 `.2`；该发布步骤必须留下脱敏 ACL、指纹、候选 allowlist 和退出码证据，且不启动服务。

**B-04 boundary:** 本规格不定义也不消费跨网段探测/签署制品，因而 CAP-1 校验器即使完成静态网络配置检查也不得返回 B-04 PASS；签名、完整性、归档和 Compose 身份全部通过后仍固定退出 `2/BLOCKED`。B-04 只能由其独立验收流程依据实际 IPv4/IPv6、正反向探测、重启复测和签署证据关闭。

**Acceptance Criteria:**
- Given 任一候选字节或身份元数据被改变，when 外置或包内验证运行，then 退出 1 且没有任何 `docker load`。
- Given `.2`、批准信任锚和已填写签名审批字段，when 目标机运行 bootstrap 和候选校验，then 发布者身份显示 VERIFIED，完整性/归档身份通过；若 B-04 独立要求的网络字段、跨网段探测或签署仍未完成，则明确退出 2/BLOCKED，且服务始终未启动。
- Given 私钥、口令存储和目标信任目录，when 检查路径、ACL、Git 与候选内容，then 私密材料仅本机当前用户可读，目标机只有公钥且候选不自带信任根。

## Spec Change Log

- 2026-08-22 review loop 1：三路审查发现初版把尚未执行的目标机安装/`.2` 验收误标完成，并把静态网络 validator 当成完整 B-04 现场 PASS；同时没有要求 Python 固定系统 `ssh-keygen`、Windows 系统信任 ACL 门禁和完整候选 TOCTOU 防护。已将目标机动作移至审查后 rollout gate，补充 `release-key-fingerprint` 与证据要求，纠正 B-04 为独立 BLOCKED/PASS 结果，并强化本地实现任务。避免保留“未部署即完成”、PATH 可劫持、可写信任锚和哈希后换包的已知坏状态。KEEP：保留 schema 2、原始 `SHA256SUMS`、单一 Ed25519 发布者、包外信任锚、规范 SSHSIG、真实性失败先于 Docker、B-04 退出 2、Docker 29 解析和 116 项既有回归。
- 2026-08-22 review loop 2：复审发现 B-04 缺少本规格可消费的现场证据 schema，校验器仍可能把静态 validator 当完整 PASS；提权执行还继承 Linux `TMPDIR`、Windows 用户级 Python/Compose 插件，ACL 复合权限位会误拒只读主体，快照复制缺少磁盘/增长门禁，且非系统锚存在验签时替换窗口。已明确 CAP-1 永远不关闭 B-04，并补充净化环境、固定保护工作区/工具/Docker 配置、准确 ACL、锚快照和资源门禁。避免保留“静态配置即现场 PASS”、提权插件劫持、正常 Windows ACL 假失败、系统盘耗尽和换锚验签。KEEP：保留 loop 1 全部正向约束、完整候选快照、发布已验证 snapshot、系统锚 fail-closed、签名超时、Linux `-I -S` 和 121 项回归。
- 2026-08-22 review loop 3：复审发现 Windows Docker 调用仍可继承 `DOCKER_HOST`/`DOCKER_CONTEXT` 并连接非预期 daemon，`C:\ProgramData` 的继承 ACL 会被误判为可替换，系统 Python 验证会继承 `TMPDIR`，且可被其他主体替换的构建输出根会在原子发布前留下换包窗口。已在全部 Windows 验证路径清除远端 Docker 变量，仅以真实替换权限检查祖先 ACL 并跳过 `InheritOnly` ACE，将系统验证工作区固定为受保护的 `/var/lib/ruisheng/work`，并要求显式、所有权/权限受控的输出根及其祖先通过原子发布门禁。避免保留远端 daemon 劫持、正常 Windows 部署假失败、临时目录注入和已认证 staging 被替换。KEEP：保留 loop 1/2 的单一发布者、候选外信任锚、完整私有快照、Docker 前认证、资源门禁和 B-04 固定 `2/BLOCKED` 语义。
- 2026-08-23 rollout correction：首次签发的不可变候选 `.2` 在目标 Windows 11 的 Docker 前 ACL 门禁正确停止，但原因是系统 `ssh-keygen.exe` 含本地化、无法回译 SID 的只读 App Package ACE，校验器在判断危险权限前翻译所有主体而产生误拒。已改为通过 `GetOwner(SecurityIdentifier)` 直接读取 owner SID，仅对真正具备写入/替换权限的 Allow ACE 解析 SID，且危险主体无法解析时继续 fail-closed；补丁由 122 项核心回归和 38 项部署契约覆盖。`.2` 保持不可变审计证据，现场改用补丁候选 `.3`。KEEP：不放宽任何危险权限集合、Approved SID、包外锚、Docker 前认证或 B-04 `2/BLOCKED` 边界。

## Design Notes

签名对象固定为 `SHA256SUMS`，它是覆盖全部候选 payload 的规范索引；`SHA256SUMS.sig` 排除在索引外以避免循环。Manifest `authenticity` 使用固定字段：`status=SIGNED`、`scheme=openssh-sshsig`、publisher、namespace、`key_type=ssh-ed25519`、key fingerprint、`signed_object=SHA256SUMS`、signature file。密钥轮换与防回放策略留作另行批准，不能隐式扩展本任务。

## Verification

**Commands:**
- `uv run pytest tests/tools/test_release_artifacts.py tests/tools/test_production_compose.py -q` -- expected: 签名与部署契约全部通过。
- `uv run pytest tests/tools/test_release_artifacts_docker.py -q` -- expected: Docker 29 五镜像、三端验签和加载前拒绝通过。
- `uv run ruff check tools/release_artifacts.py tests/tools` -- expected: lint 通过。
- `pwsh -NoProfile -Command "[void][scriptblock]::Create((Get-Content -Raw deploy/verify-candidate.ps1))"` 与 `bash -n deploy/*.sh tools/release_trust/*.sh` -- expected: 语法通过。

## Suggested Review Order

**原子签名与发布**

- 从唯一发布入口确认只发布已认证的完整快照。
  [`release_artifacts.py:1913`](../../../tools/release_artifacts.py#L1913)

- 签名身份先复制为受控公钥快照，关闭路径替换竞态。
  [`release_artifacts.py:469`](../../../tools/release_artifacts.py#L469)

**三端快照与信任边界**

- Python 验证和加载共用同一完整私有快照。
  [`release_artifacts.py:1411`](../../../tools/release_artifacts.py#L1411)

- Linux bootstrap 隔离解释器环境并交接已认证完整快照。
  [`verify-publisher.sh:1`](../../../tools/release_trust/verify-publisher.sh#L1)

- Windows bootstrap 只允许 SYSTEM/Administrators 控制固定工作目录。
  [`verify-publisher.ps1:107`](../../../tools/release_trust/verify-publisher.ps1#L107)

- 包内 PowerShell 固定系统工具并保留 B-04 独立退出语义。
  [`verify-candidate.ps1:332`](../../../deploy/verify-candidate.ps1#L332)

- 包内 Shell 固定工具、隔离 Python，并在 Docker 前完成认证。
  [`verify-candidate.sh:1`](../../../deploy/verify-candidate.sh#L1)

**回归证据**

- 行为测试证明所有 Docker 调用只读取同一认证快照。
  [`test_release_artifacts.py:1087`](../../../tests/tools/test_release_artifacts.py#L1087)

- 静态契约锁定提升权限环境、ACL 和固定工具路径。
  [`test_production_compose.py:444`](../../../tests/tools/test_production_compose.py#L444)

- 发布回归禁止重新发布未验证的原 staging。
  [`test_production_compose.py:467`](../../../tests/tools/test_production_compose.py#L467)
