---
title: '目标机远程启动与运行验收修复'
type: 'bugfix'
created: '2026-09-03'
status: 'done'
baseline_commit: '168cfebc146d4559dafe84acf866ff6c881232ad'
context:
  - 'D:/江苏润盛/docs/REMOTE_DEBUG.md'
  - 'D:/江苏润盛/docs/superpowers/specs/spec-plan-5-customer-deployment-acceptance/SPEC.md'
  - 'D:/江苏润盛/docs/superpowers/specs/spec-plan-5-b05-serial-hardware-onboarding.md'
---

<frozen-after-approval reason="human-owned intent - do not modify unless human renegotiates">

## Intent

**Problem:** 已部署目标机的五个应用服务正在运行，但真实远程验收暴露出三类问题：维护工具默认站点根已过期且会把业务拒绝掩盖为 SSH 失败；调试健康检查把 GW ACL 的预期 403 误判为故障；首次登录失败被误报为会话过期。与此同时，串口自动附加工具把 CRLF 脚本直接交给 WSL `sh`，导致 FTDI 已附加且驱动存在时仍无法恢复稳定设备别名。

**Approach:** 修复上述运行路径并增加与真实返回一致的回归测试；通过现有受控维护入口验证应用停止后可重新启动，通过回环 SSH 隧道验证 Web/API/GW；生成新的签名候选并部署 Web 修复，同时以认证的主机工具更新流程恢复串口别名。所有验收保持真实设备轮询关闭。

## Boundaries & Constraints

**Always:** 维护操作从受保护活动版本指针解析候选，显式参数仍可覆盖默认目标；缺失或错误站点必须返回可诊断、非敏感、fail-closed 的业务结果。GW 只把精确的 200 或站点 ACL 403 视为可达，其他响应、连接失败和超时仍失败。登录端点的无效凭据不得触发全局会话过期事件，其他受保护端点的 401/-101 仍必须清理会话。WSL 输入统一为无 BOM 的 LF 文本，并继续按 VID/PID/USB 序列号识别设备。

**Ask First:** 目标 Windows 重启、启用 GW 串口映射或持续轮询、发送任何 Modbus/控制报文、修改点表/数据库、使用真实账号密码、触发告警或通知、删除现场文件。

**Never:** 不放宽 SSH 公钥、ACL、签名、镜像身份、共享锁、审计或健康门禁；不把 USB 可见或别名恢复标记为 B-08/B-09、校准或生产采集通过；不读取或暂存用户文件 `D:\江苏润盛\3`。

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| MAINTENANCE_DEFAULT | 当前目标机使用已部署稳定站点根 | 无长参数执行 Status/Start/Restart 并绑定活动候选 | 指针缺失或漂移返回具体拒绝码，不伪装成传输失败 |
| GW_ACL_HEALTH | `/ready` 返回 403 | 报告 GW 可达且 ACL 生效 | 非 200/403、超时或连接失败均失败 |
| LOGIN_INVALID | `/auth/login` 返回 HTTP 401 与 code -101 | 页面显示“用户名或密码错误”，不广播会话过期 | 受保护 API 的同类响应仍广播过期并清理会话 |
| WSL_CRLF | Windows CRLF 脚本交给 `sh -s` | 子进程收到 LF，创建绑定正确 FTDI 的稳定别名 | 身份不符、节点缺失或命令失败时清理别名并保持轮询关闭 |

</frozen-after-approval>

## Code Map

- `tools/remote_maintenance.ps1` -- 目标机生命周期入口、活动指针和安全结果封装。
- `tools/remote_debug.ps1` -- SSH 隧道及 API/GW/Web 语义健康检查。
- `tools/serial_hardware_attach.ps1` -- USBIPD/WSL 驱动和稳定别名恢复。
- `ruisheng-web/src/api/client.ts`、`ruisheng-web/src/api/auth.ts` -- 401 分类、会话过期事件和登录错误语义。
- `tests/tools/test_remote_operations.py`、`tests/tools/test_serial_hardware.py`、`ruisheng-web/tests/unit/api/*.test.ts`、`ruisheng-web/e2e/login.spec.ts` -- 回归覆盖。
- `docs/REMOTE_DEBUG.md` -- 当前目标机的短命令和验收解释。

## Tasks & Acceptance

**Execution:**
- [x] 修复维护默认站点与异常结果，保持活动指针、锁和审计校验不变。
- [x] 修复 GW 403 健康语义和登录端点 401 分类，补齐单元/E2E 回归。
- [x] 在写入 WSL stdin 前规范化换行，并覆盖 CRLF、错误清理与身份匹配。
- [x] 运行前后端及工具定向测试、静态检查和 PowerShell 5.1/7 解析。
- [x] 受控重启应用并复验五服务、Web/API/GW、登录失败与串口稳定别名。
- [x] 构建、验签和部署不可变候选；复验活动指针、镜像、网络、备份、审计及清理。

**Acceptance Criteria:**
- Given 目标机当前签名活动版本和纯公钥 SSH，when 执行受控 RestartApp，then 五个服务按顺序恢复且所有语义健康检查通过。
- Given FTDI 已由 USBIPD 附加，when 自动附加任务重试，then `/dev/ruisheng-rs485` 指向身份匹配的 tty 节点，同时 GW 保持无串口映射和无 Modbus TX。
- Given 新候选部署完成，when 通过回环隧道访问登录页并提交无效测试凭据，then 显示正确错误且浏览器无未处理异常。

## Spec Change Log

- 2026-09-03 review hardening: 保留活动指针、认证分类、LF 规范化和无轮询边界；将 Windows PowerShell 5.1 stdin 改为无 BOM UTF-8 基础流写入，收紧受保护站点发现、指针漂移和可识别 ACL 403，增加真实子进程、HTTP 分支、浏览器异常及 `test:unit` 门禁回归。
- 2026-09-03 runtime acceptance: 固定 Compose 项目目录，兼容 PowerShell 7.6 JSON 日期行为，以 Base64 探针跨 PowerShell 5.1 保留 Python 源码，并用显式认证请求标记及 `msg/transid` 兼容修复现场登录错误；部署、串口别名与无轮询边界均已实机复验。

## Verification

**Commands:**
- `pytest -q tests/tools/test_remote_operations.py tests/tools/test_serial_hardware.py` -- expected: 全部通过。
- `pnpm --dir ruisheng-web test:unit && pnpm --dir ruisheng-web lint && pnpm --dir ruisheng-web typecheck && pnpm --dir ruisheng-web build` -- expected: 全部通过。
- `pre-commit run --files <changed files>`、PowerShell 5.1/7 AST -- expected: 全部通过。
- 目标机受控 Status/Restart、远程 Health、浏览器登录失败和只读串口身份检查 -- expected: 满足上述 AC。

## Runtime Acceptance Evidence

- 本地门禁：工具测试 `114 passed`，Web 单测 `90 passed`，登录 E2E `4 passed`；lint、typecheck、生产构建、pre-commit 及 PowerShell 5.1/7 解析均通过。生产构建仅保留既有 ECharts 大分块警告。
- 受控重启：操作 `70842655-5a9c-4bfe-b271-51e9763414da` 一次成功，五个服务按停止/启动顺序恢复并全部 ready。
- 不可变部署：`deploy-20260903.2`，源码提交 `1093e21a5172c7cb2be3bdb37fc157a70792b6aa`，逻辑身份 `sha256:0f8aa61b0a19b468094146bc5d46b596b9bc47521e03cf132292630034ce2992`；Apply 操作 `67a9e154-d3f4-49f2-b805-0c2c8532b930` 状态为 `committed`，活动指针、数据库/角色/环境备份及五镜像身份通过。
- 远程语义健康：API ready；GW 返回受控站点 ACL `403` 并被正确识别；Web 返回 `200`。
- 浏览器验收：关闭部署前受旧 Service Worker 控制的标签后，以全新标签加载当前资产；虚假凭据显示“用户名或密码错误”，停留登录页，页面诊断日志为空。
- 串口验收：认证安装收据绑定 `.2` 候选；`0403:6001 / AI06JYFW` 在总线 `1-9` 映射为 `/dev/ttyUSB0`，稳定别名 `/dev/ruisheng-rs485` 状态为 ready。
- 安全边界：`polling_approved=false`；GW 的设备映射、设备请求、串口挂载及 `GW_SERIAL_PORTS` 均为零，容器内无串口节点，因此未启用轮询且不具备 Modbus TX 路径。B-04 继续保持 BLOCKED。

## Suggested Review Order

1. `tools/remote_maintenance.ps1` 与 `tests/tools/test_remote_operations.py`：审计日期、Compose 项目目录和 PowerShell 5.1 探针回归。
2. `tools/remote_debug.ps1`：GW ACL 健康探针的 Base64 传输。
3. `ruisheng-web/src/api/client.ts`、`ruisheng-web/src/api/auth.ts` 与 API 单测：登录认证标记及真实后端错误字段兼容。
4. 本节实机证据：重启、不可变部署、浏览器登录、串口身份和无 Modbus TX 边界。
