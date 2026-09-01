---
title: 'Plan 5 B-08-T Witness SYSTEM Autostart'
type: 'chore'
created: '2026-09-01'
status: 'done'
baseline_commit: '72608611a48d851cec207b22622ed177436c7e40'
context:
  - 'docs/superpowers/specs/spec-plan-5-b08t-trust-root-freshness.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Freshness witness 当前依赖 `CXG-PC\cxg` 登录触发并使用该用户目录中的 Python；机器重启后若无人登录，目标机 publisher 无法取得 freshness attestation，且交互用户与 witness 共用运行边界。

**Approach:** 将现有 witness 迁移为 Windows `SYSTEM` 身份的开机计划任务，使用从当前已验收 Python 精确复制到受保护 `C:\ProgramData\RuishengWitness\runtime` 的最小机器级运行时；任务 Action 固定为 Windows 保护的 `C:\Windows\System32\cmd.exe /d /s /c` wrapper，且只启动受保护的 `runtime\python.exe` 与 `freshness_witness.py`，stdout 指向 `NUL`，stderr 追加到受保护的 `C:\ProgramData\RuishengWitness\migration\witness-stderr.log`。保留现有 key、高水位、mTLS、监听地址和签名身份，不重新 enrollment、不推进高水位。迁移失败时恢复当前用户登录任务和脚本。

## Boundaries & Constraints

**Always:** 管理脚本必须固定 witness 脚本、源 Python、复制后运行时摘要和精确 `cmd.exe` wrapper Action；任务使用 `SYSTEM`、`ServiceAccount`、最高权限和开机触发器；允许电池供电时启动且不因切换到电池而停止；安装前备份旧任务定义和运行组件；新任务必须绑定预期 wrapper、Python 子进程、脚本、PID、地址和端口；stderr 日志、key、高水位、配置和 TLS 私钥保持受保护 ACL；迁移后重新执行 publisher、cleared-env provider 与负向测试。

**Ask First:** 更换 witness key、证书、site/provider ID、监听地址或高水位；下载或安装新软件；重启整台本机；将 witness 移至其他物理计算机；删除旧任务备份。

**Never:** 修改目标机生产容器、数据库、串口或设备；推进/重置高水位；以网络下载内容替换 witness；把当前登录用户或普通 Users 组授予 witness 文件写权限；在新任务未通过验收前永久删除可恢复的旧任务定义。

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|---------------|---------------------------|----------------|
| 正常迁移 | 当前用户任务运行，组件哈希正确 | 创建固定最小 runtime，注册并启动 SYSTEM 开机任务，listener PID 属于该任务 | 验收后停止使用旧用户任务 |
| 端口被占用 | 38475 由非预期进程监听 | 不宣布成功 | 回滚任务和运行组件，保留诊断信息 |
| 新任务未启动 | SYSTEM 任务超时或立即退出 | 不留下半完成迁移 | 恢复旧用户任务并重新确认旧 witness 可用 |
| 重启等价验证 | 手动停止后按 SYSTEM 任务重新启动 | 无用户进程依赖，publisher 仍取得 live attestation | 任一绑定或 publisher 失败即回滚 |

</frozen-after-approval>

## Code Map

- `tools/witness_system_autostart/freshness_witness.py` -- witness 实现，部署时按 SHA-256 固定。
- `tools/witness_system_autostart/runtime-source-manifest.json` -- 预先批准的完整源 runtime manifest，固定全部 1494 个文件。
- `tools/witness_system_autostart/install-witness-autostart.ps1` -- 当前用户登录任务安装器，作为迁移回滚来源。
- `tools/witness_system_autostart/install-witness-system-autostart.ps1` -- SYSTEM 迁移、固定 runtime、任务注册、持久化事务和失败回滚安装器。
- `tools/witness_system_autostart/rollback-witness-system-autostart.ps1` -- 仅针对活动事务或当前已提交安装的显式回滚器。
- `tools/witness_system_autostart/run-system-autostart-elevated.ps1` -- 在受保护目录重新校验固定 bundle 后执行管理员操作的 runner。
- `tools/witness_system_autostart/launch-elevated-operation.ps1` -- 固定 runner 摘要并发起 UAC 提升的统一入口。
- `tools/witness_system_autostart/test-witness-system-autostart.ps1` -- wrapper、父子进程、ACL、runtime manifest 和 listener 验收器。
- `tools/witness_system_autostart/diagnose-witness-system-start.py` -- 安装事务中的有限 SYSTEM 探针和临时真实 `serve` 跟踪预检；任务随后回收，且不修改高水位。
- `tools/witness_system_autostart/verify-witness-system-restart.ps1` -- 管理员停启等价验收，固定并比较高水位、witness 和验收脚本摘要，失败时执行完整绑定检查和恢复。
- `tools/witness_system_autostart/verify-witness-final-state.ps1` -- 最终管理员只读复核，验证任务、端口、高水位、audit 和完整联合验收结果。
- `tools/witness_system_autostart/read-witness-audit.py` -- 按 publisher 基线和 `/v1/attest` 路径读取本轮成功审计记录。
- `tools/witness_system_autostart/target/run-target-acceptance.ps1` -- 目标机 cleared-env、publisher、负向测试和生产边界联合验收入口。
- `tests/tools/test_witness_system_autostart.py` -- 身份材料、audit fail-closed、运行时 manifest 和固定哈希链回归测试。
- `C:\ProgramData\RuishengWitness` -- 本机 witness 配置、密钥、高水位、TLS 和目标运行目录。
- `C:\ProgramData\RuishengWitness\migration` -- 迁移脚本、任务 XML、哈希和验收记录的本地受保护目录。

## Tasks & Acceptance

**Execution:**
- [x] `tools/witness_system_autostart/install-witness-system-autostart.ps1` -- 创建固定最小 runtime、校验输入、备份旧任务、注册 SYSTEM/AtStartup 任务并实现持久化事务和回滚。
- [x] `tools/witness_system_autostart/test-witness-system-autostart.ps1` -- 校验 principal、trigger、action、ACL、listener 归属、脚本/runtime 摘要和电池策略。
- [x] 上一现场版本验收基线 -- 本机 15 项 SYSTEM 验收、目标机 cleared-env、publisher 和三组负向测试均通过；该结果仅作为回归基线。
- [x] 审查修复候选现场验收 -- 当前仓库候选已完成本机 SYSTEM 安装、停启、最终复核和目标机联合验收，规格恢复 `done`。

**Acceptance Criteria:**
- Given 当前用户任务可用，when 管理员执行迁移，then 新任务以 `NT AUTHORITY\SYSTEM` 在开机时启动，action 精确使用 `C:\Windows\System32\cmd.exe /d /s /c`，只启动受保护目录中的固定 runtime 和 witness 脚本，stdout 为 `NUL` 且 stderr 写入受保护 migration 日志。
- Given 用户任务已停止且 SYSTEM 任务重新启动，when 目标机执行 publisher，then publisher 签名验证通过并仍按真实业务证据返回 `2/BLOCKED`，witness audit 产生新的成功记录。
- Given 任一安装或验收步骤失败，when 回滚完成，then 旧用户任务重新运行、38475 可达且关键材料哈希不变。
- Given 迁移成功，when 检查目标机和本机，then生产容器均正常、38477 未监听、高水位未变化、Git 不包含现场密钥或运行制品。

### Review Findings

- [x] [Review][Patch] [P1] 将可复现的 witness 源码、迁移和验收脚本移出仅本机 `.git/info/exclude` 的 `tmp-test-logs`，纳入可审查、可发布的仓库路径 [tools/witness_system_autostart/freshness_witness.py]
- [x] [Review][Patch] [P1] 使用预先批准的完整源运行时 manifest 固定标准库、DLL 和依赖，禁止把安装时读取的用户目录内容自签为可信 manifest [tools/witness_system_autostart/runtime-source-manifest.json]
- [x] [Review][Patch] [P1] 消除提升权限入口和已校验 helper 的 TOCTOU，在受保护副本上复核摘要后再以管理员或 SYSTEM 执行 [tools/witness_system_autostart/run-system-autostart-elevated.ps1]
- [x] [Review][Patch] [P1] diagnostic task 必须使用已固定的系统 `cmd.exe`，不得信任调用者可变的 `ComSpec` 环境变量 [tools/witness_system_autostart/install-witness-system-autostart.ps1]
- [x] [Review][Patch] [P1] 为迁移增加全局互斥，拒绝两个提升权限的事务同时替换同一 task、runtime 和 witness [tools/witness_system_autostart/install-witness-system-autostart.ps1]
- [x] [Review][Patch] [P1] 为任务注销后的进程终止或断电增加持久化事务日志与下次启动恢复，避免旧任务和新任务同时缺失 [tools/witness_system_autostart/install-witness-system-autostart.ps1]
- [x] [Review][Patch] [P2] 为最小 runtime smoke 子进程设置硬超时，超时必须进入事务回滚并写出状态 [tools/witness_system_autostart/install-witness-system-autostart.ps1]
- [x] [Review][Patch] [P1] 成功 attestation 的 audit 写入失败时必须 fail-closed，不能吞掉 SQLite 错误后继续返回 200 [tools/witness_system_autostart/freshness_witness.py]
- [x] [Review][Patch] [P1] 最终 audit 验收必须绑定本次 publisher 窗口和 `/v1/attest`，不能由固定日期后的旧记录或 `/health` 200 满足 [tools/witness_system_autostart/read-witness-audit.py]
- [x] [Review][Patch] [P1] 最终 machine-readable 验收必须纳入 publisher、cleared-env、负向测试、生产容器和 Git 边界证据 [tools/witness_system_autostart/verify-witness-final-state.ps1]
- [x] [Review][Patch] [P1] 为安装成功后发生的 publisher 或目标机验收失败提供显式回滚命令，并复核旧 witness 恢复 [tools/witness_system_autostart/rollback-witness-system-autostart.ps1]
- [x] [Review][Patch] [P2] 回滚完成前重新散列 key、高水位、配置和 TLS 材料；只有全部不变时才能报告 `rolled_back=true` [tools/witness_system_autostart/rollback-witness-system-autostart.ps1]
- [x] [Review][Patch] [P1] ACL 验收必须校验 owner、SYSTEM/Administrators 有效权限、Deny 规则和全部 runtime 子项 [tools/witness_system_autostart/test-witness-system-autostart.ps1]
- [x] [Review][Patch] [P2] listener 验收必须证明进程实例由本次计划任务启动，不能由时间接近的手工同命令 wrapper 冒充 [tools/witness_system_autostart/test-witness-system-autostart.ps1]
- [x] [Review][Patch] [P1] 停启失败恢复必须验证完整 witness 进程绑定，不能因端口上恰有一个无关 listener 而跳过恢复 [tools/witness_system_autostart/verify-witness-system-restart.ps1]
- [x] [Review][Patch] [P2] 将重启验收的管理员、摘要和初始 listener 预检纳入状态写入保护，所有失败都必须生成 machine-readable status [tools/witness_system_autostart/verify-witness-system-restart.ps1]
- [x] [Review][Patch] [P1] TLS 握手必须有 accept 阶段超时，避免 Handler 创建前的慢握手阻塞整个 witness [tools/witness_system_autostart/freshness_witness.py]
- [x] [Review][Patch] [P2] 为 ThreadingHTTPServer 增加并发上限，避免已认证或异常连接耗尽线程和内存 [tools/witness_system_autostart/freshness_witness.py]
- [x] [Review][Patch] [P1] witness 启动时必须验证配置中的 signing public key 和 server certificate 摘要与实际加载材料一致 [tools/witness_system_autostart/freshness_witness.py]

## Spec Change Log

- 2026-09-01: 现场安装发现 Windows PowerShell 5.1 无法绑定未显式转换的 `HashSet.SetEquals()` 参数，且旧任务使用 `cmd.exe` wrapper 时回滚器错误地把 listener Python 与任务 Action 直接比较；已增加 5.1 类型转换、wrapper 进程链验收和可验证的幂等恢复。成功事务 `a11b9650b1774131b5bb7a8366d754d1` 及完整联合验收通过，规格恢复 `done`。
- 2026-09-01: 修复代码审查提出的 19 项问题；实现迁入可发布路径，加入完整批准 runtime manifest、安全提升 bundle、持久化事务、显式回滚、联合验收和定向回归。当前候选尚未重新部署，规格状态改为 `in-progress`。
- 2026-09-01: 经人工批准，将最终任务 Action 从直接 `python.exe` 改为已通过完整 SYSTEM 预检的固定 `cmd.exe /d /s /c` wrapper；直接 Action 会保持 Running 却不监听，wrapper 仍严格绑定受保护 Python/witness，并持久化 stderr。
- 2026-09-01: 独立复核增加 listener Python 到固定 `cmd.exe` 的父子进程验证，并确保失败回滚不遗留本轮新建的 stderr 文件。
- 2026-09-01: 现场诊断证明真实 SYSTEM 服务已进入 `serve_forever()`；修复 Windows PowerShell 5.1 将单个 CIM 结果解包为无 `.Count` 标量造成的 listener 误判，成功事务 `6d7c1793d5f3411497f71fdab7751840` 及随后停启验收均通过。
- 2026-09-01: 目标机全量现场验收通过：cleared-env exit `0`，publisher 签名和完整哈希 `VERIFIED` 且 exit `2`，freshness 8/8、replay 和 publisher 3/3 负向测试均通过；迁移后新增 3 条成功 witness audit，生产容器、高水位和关键哈希保持不变。

## Design Notes

Windows `LocalSystem` 无法可靠使用用户 Profile 中的 Python，因此仅复制 witness 必需的 Python 标准库、DLL 和已安装的 `cryptography`/`cffi` 依赖到受保护 runtime，并固定源/目标 manifest；不下载或安装新软件。有限探针与完整服务预检证明固定 `cmd.exe` wrapper 可在 SYSTEM 下启动该 runtime，而直接 Task Scheduler Python Action 保持 Running 却不监听，因此最终任务复用相同 wrapper，不再重复完整服务预检。任务使用开机触发器，同时允许按需启动用于无重启验收；本轮不执行整机重启，避免扩大现场影响。

## Verification

**Commands:**
- `uv run pytest -q tests/tools/test_witness_system_autostart.py` -- expected: identity、audit、manifest、哈希链和可发布边界回归全部通过。
- `uv run pytest -q tests/tools` -- expected: 工具层完整回归无失败。
- PowerShell 5.1 parser、Ruff、Ruff format 和 Mypy -- expected: 新候选无解析、格式、lint 或类型错误。
- SYSTEM 安装脚本输出 JSON -- expected: principal、trigger、listener PID、脚本和 runtime 摘要全部匹配。
- cleared-env provider -- expected: exit `0` 且生成 attestation。
- protected publisher `ValidatorProfile` -- expected: 签名/候选验证通过，最终 exit `2`。
- freshness、replay、publisher negative suites -- expected: `all_passed=true` 且进程 exit `0`。
- target `docker ps` 与本机端口检查 -- expected: 生产容器正常，临时端口 `38477` 未监听。

**Observed 2026-09-01 (reviewed candidate, local only):** 定向回归 `6 passed`；完整 `tests/tools` 回归 `889 passed, 9 skipped`，跳过项均为 POSIX、Docker E2E 或当前 Windows 符号链接条件；21 个 PowerShell 文件解析 0 错误；Ruff、格式和 Mypy 全部通过。固定哈希链已闭合，runtime manifest 含 1494 个文件且 SHA-256 为 `301172759e6269bcd1b04d7aed04c9b4df78f32150d34dd1a4c5d0cd7be329d0`。尚未对当前候选执行 UAC 安装或现场联合验收。

**Observed 2026-09-01 (reviewed candidate, deployed):** 提交 `291cfe32cac9cc4dab1f070e25f376bde5767689`，部署包 SHA-256 `06220101deaef8f1a657a31f9a988b30c7486db7d7ce9ed832629ba3f1adb17e`，成功事务 `a11b9650b1774131b5bb7a8366d754d1`。SYSTEM 安装验收全通过；停启 PID `11224 -> 20296`，高水位 SHA-256 前后均为 `134b160de987a102518105ca0feb32876c0b6f0d315f0dee8ca8d8d652cbe9db`。目标机 cleared-env、publisher `2/BLOCKED` 和三组负向测试通过，五个生产容器 ID 不变且 `38477` 无 listener。最终证据 SHA-256 `3ac613df2f7cbc7c9b2042e0e38cee2b49724f241c8c287aebd1231b70096245`，基线 ID `49` 后新增 2 条成功 `/v1/attest`，最新 ID `59`；最终管理员复核 `passed=true`。

**Observed 2026-09-01 (previous deployed baseline):** 成功事务 `6d7c1793d5f3411497f71fdab7751840`；SYSTEM 验收 15/15、停启与最终复核均通过；cleared-env `0`；publisher `2` 且输出签名/完整哈希 `VERIFIED`；freshness 8/8、replay、publisher 3/3 负向测试全部通过。高水位 SHA-256 保持 `134b160de987a102518105ca0feb32876c0b6f0d315f0dee8ca8d8d652cbe9db`，迁移后成功 audit 3 条，最新 ID `49`；五个 `ruisheng-prod` 容器 ID 前后不变，`38477` 未监听。该证据不替代当前修复候选的现场复验。
