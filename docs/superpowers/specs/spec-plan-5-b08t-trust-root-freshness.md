---
title: 'Plan 5 B-08-T Trust-Root Freshness Gate'
type: 'feature'
created: '2026-08-30'
status: 'done'
baseline_commit: '08844a121125c5be72168a3595338b199f90063f'
context:
  - 'docs/superpowers/specs/spec-plan-5-b08-device-point-calibration/SPEC.md'
  - 'docs/superpowers/specs/spec-plan-5-customer-deployment-acceptance/deployment-contract.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** 当前 validator 只验证本机固定 trust root 的 ACL、身份、有效期和 policy 签名；管理员恢复旧文件或整盘快照后，旧 root/policy 仍可能自洽通过，B-08-T 因而保持阻断。

**Approach:** 建立签名 freshness attestation 契约。受保护 publisher 锁定 trust root 字节、为每次 `ValidatorProfile` 生成不可预测挑战，并通过 Site Profile 固定且不可由调用者替换的 TPM/独立远端 provider 取得只读证明；validator 只消费同一份 root 快照，并在任何 evidence/runtime/receipt I/O 前验证证明和外部高水位。仓库实现不自动推进高水位，也不把 mock 结果提升为现场资格。

## Boundaries & Constraints

**Always:** 高水位与签名主体位于仓库及可回滚目标系统盘之外；证明绑定 site、challenge、候选逻辑身份、Profile/payload/canonical gate、validator、verifier、root `(id, version, revocation, hash)`、policy `(id, version, revocation, hash)`、可信观察时间、有效期和单调状态身份。publisher 在见证前后保持 root 快照身份不变，validator 必须消费同一字节。相同状态允许幂等复核；降级、撤销回退、同版本异 hash、未授权 root ID 切换、旧响应重放和本机时钟回拨均 fail-closed。资格检查只读，不更新高水位。

**Ask First:** 选择或更换 TPM/远端 provider；首次 enrollment；root/policy/witness key 轮换；推进或恢复外部高水位；连接目标机或执行整盘回滚演练。

**Never:** 接受调用者提供的 endpoint、provider executable、witness key、challenge 或“已验证”布尔值；用本地文件、ACL、registry、数据库或缓存充当外部权威；provider 故障时回退到本地 root；授权 TX、GW 重建、canary、持续采集、生产切换或自动关闭 B-08/B-08-W/B-09。

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|---------------|----------------------------|----------------|
| Exact | attestation 与外部高水位、当前 root/policy 和本次挑战完全一致 | freshness gate 通过，继续既有资格门禁 | 不修改高水位 |
| Unavailable | provider 缺失、超时、损坏或无法认证 | 不读取业务证据，结论 `BLOCKED` | 不允许本地降级 |
| Rollback | root/policy 版本或撤销序列低于高水位，或同版本异 hash/未授权换 ID | 结论 `INVALID` | 记录 machine-readable 原因 |
| Ahead | 本地 root/policy 高于尚未 provision 的高水位 | 结论 `BLOCKED` | 不得自动 CAS/推进 |
| Replay | nonce 不匹配、签名错、过期、计数器回退或时钟偏差超限 | 结论 `INVALID` | 不继续 qualification |

</frozen-after-approval>

## Code Map

- `tools/trust_root_freshness.py` -- 严格 attestation/provider-response 模型、canonical signature message、比较和错误分类。
- `tools/validate_device_point_profile.py` -- 只接受 trusted publisher context，并在 authority/evidence 前执行 freshness gate。
- `tools/release_trust/verify-publisher.ps1` / `.sh` -- 生成挑战、调用固定 provider、锁定响应身份并把内部参数传给 validator。
- `tools/release_artifacts.py` -- 把 freshness verifier 纳入 v3 authenticated toolchain 精确文件集与摘要。
- `tests/tools/test_validate_device_point_profile.py` / `test_release_artifacts.py` -- validator、publisher、预算、顺序和跨平台负向回归。
- `docs/superpowers/specs/spec-plan-5-*` -- 同步 B-08-T 仓库完成边界与现场仍阻断项。

## Tasks & Acceptance

**Execution:**
- [x] `tools/trust_root_freshness.py` -- 实现版本化、严格、有界、可签名的 freshness request/attestation 契约和纯比较逻辑。
- [x] validator/publisher/release toolchain -- 接入不可调用者替换的 provider、一次性挑战、root 同字节快照、trusted context 和 validator-before-start/evidence-before-I/O 早退。
- [x] tests/specs -- 覆盖 exact、missing、rollback、same-version-different-hash、ID switch、ahead、replay、clock rollback、并发响应及外部 provisioning 边界。

**Acceptance Criteria:**
- Given 有效 live attestation，when `ValidatorProfile` 运行，then root/policy/profile/candidate/gate/verifier/site/challenge 全部精确绑定、validator 消费见证的同一 root 字节后才继续既有门禁，且不写高水位。
- Given provider 不可用或本地状态高于高水位，when qualification 运行，then 不启动 validator，并返回 `BLOCKED`；trusted context 内部失败则必须在 `_check_binding` 前早退。
- Given 任一回滚、冲突、重放或签名/时间异常，when qualification 运行，then 确定性返回 `INVALID`，公共 API 和 caller 参数不能伪造 trusted context。
- Given 仓库回归全通过，when 查看 Plan 5 状态，then B-08-T 仍为现场 provisioning `blocked`，直至真实 provider、外部高水位和整盘回滚演练验收。

## Spec Change Log

- 2026-08-30: 实现 v1 root+policy freshness gate、Windows/POSIX protected publisher handoff、v3 toolchain 绑定和负向回归；仓库完成不改变 B-08-T 的现场 `blocked` 状态。
- 2026-08-30: 第二轮对抗审查修正 provider 认证失败的 `BLOCKED` 分类、实际 verifier 请求绑定、qualification 结束时墙钟回拨与单调期限检查、POSIX provider 执行前锁定源复核及含复杂 `comm` 的 `/proc` 身份解析，并以 Windows/POSIX 行为回归证明 freshness 失败不会启动 qualification；保留固定入口、只读高水位、同字节 root 和现场 provisioning 继续阻断的既有约束。

## Design Notes

Provider 传输保持适配器边界，但运行时机制由 Site Profile 冻结。远端 witness 必须使用 live nonce 与签名响应；TPM 实现必须提供等价的单调状态和 attestation。root/policy 轮换与高水位 CAS 是独立管理动作，不属于 validator 子命令。

## Verification

**Commands:**
- `uv run pytest tests/tools` -- freshness 与既有发布/资格回归全部通过。
- `uv run pytest` -- 全量 Python 通过。
- `uv run ruff check . && uv run ruff format --check . && uv run mypy .` -- 静态门禁通过。
- `pre-commit run` -- 暂存文件钩子全部通过。

## Suggested Review Order

**Freshness Contract**

- 从单次快照认证入口理解全部绑定与 fail-closed 顺序。
  [`trust_root_freshness.py:496`](../../../tools/trust_root_freshness.py#L496)

- 纯状态比较固定 rollback、ahead 与 exact 的确定性语义。
  [`trust_root_freshness.py:230`](../../../tools/trust_root_freshness.py#L230)

- qualification 结束同时拒绝墙钟回拨和单调期限超时。
  [`trust_root_freshness.py:632`](../../../tools/trust_root_freshness.py#L632)

**Protected Publishers**

- POSIX publisher 锁定输入、验证实际 verifier 并先做 preflight。
  [`verify-publisher.sh:1746`](../../../tools/release_trust/verify-publisher.sh#L1746)

- Windows publisher 对等实施固定 provider、句柄锁与失败分类。
  [`verify-publisher.ps1:3031`](../../../tools/release_trust/verify-publisher.ps1#L3031)

- 公共 validator API 缺 freshness context 时在业务 I/O 前拒绝。
  [`validate_device_point_profile.py:7094`](../../../tools/validate_device_point_profile.py#L7094)

- v3 精确工具链把 freshness verifier 纳入签名身份。
  [`release_artifacts.py:90`](../../../tools/release_artifacts.py#L90)

**Evidence And Boundaries**

- 核心回归覆盖时间、重放、状态冲突和 evidence-before-I/O。
  [`test_validate_device_point_profile.py:2023`](../../../tests/tools/test_validate_device_point_profile.py#L2023)

- 跨平台行为回归证明 freshness 失败不会启动 qualification。
  [`test_release_artifacts.py:3937`](../../../tests/tools/test_release_artifacts.py#L3937)

- 部署契约保留真实 TPM/远端 witness 的现场阻断边界。
  [`deployment-contract.md:49`](spec-plan-5-customer-deployment-acceptance/deployment-contract.md#L49)

- B-08 主规格明确仓库 mock 不能关闭 B-08-T。
  [`SPEC.md:55`](spec-plan-5-b08-device-point-calibration/SPEC.md#L55)
