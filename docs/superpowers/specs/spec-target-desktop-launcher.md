---
title: '目标机桌面一键启动器'
type: 'feature'
created: '2026-09-03T00:00:00+08:00'
status: 'done'
baseline_commit: 'e48bf93b54277bc71b7ff541288e0e136ddb31bd'
context:
  - 'docs/REMOTE_DEBUG.md'
  - 'docs/superpowers/specs/spec-remote-runtime-acceptance-fixes.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** 目标机没有现场用户一键入口；现有 Docker 开机任务不能恢复被显式停止的容器，也不会打开 Web 页面。

**Approach:** 安装受保护的本地启动执行器和带品牌图标的桌面快捷方式。双击后自动启动或复用 Docker Desktop，从已签名发布流程提交的活动指针启动五个服务，健康后打开本机 Web。

## Boundaries & Constraints

**Always:** 从唯一受保护站点动态读取 `active-release.json`；核对指针、Manifest、Compose、已加载及运行容器镜像；只接受闭集服务、`pull_policy: never` 和回环绑定；按 `postgres/redis -> migrate -> gw/api/web -> health` 启动；使用兼容的 `shared-maintenance -> legacy-hotfix` 租约锁；配置漂移即失败；重复双击保持幂等；等待有超时；错误提示和日志不含密钥；安装目录仅 Administrators/SYSTEM 可写，`lenovo` 仅可读取执行。

**Ask First:** 修改开机任务、Docker 全局设置或候选白名单；重启 Windows；串口、轮询、Modbus 或业务数据变更。

**Never:** 保存 SSH 私钥或自连 SSH；猜测“最新目录”；执行 Compose `down` 或删除卷、候选、审计、锁；输出环境密钥；开放非回环端口；把启动成功称为生产放行。

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| 首次双击 | Docker 未就绪、活动版本有效 | 启动 Docker 和服务，健康后打开 Web | 显示阶段；超时保留现场并报错 |
| 已在运行 | Docker 与五服务健康 | 核验后打开 Web，不重建卷 | 保持幂等 |
| 容器已停止 | 部分或全部容器停止 | 按受控顺序恢复并验证 | 失败时不删除数据 |
| 发布或锁异常 | 指针、ACL、配置、镜像或锁异常 | 不执行 Compose | 失败关闭并提示管理员 |

</frozen-after-approval>

## Code Map

- `tools/start_ruisheng_local.ps1` -- 本地生命周期和浏览器入口。
- `tools/install_ruisheng_desktop_launcher.ps1` -- 受保护安装、ICO 和桌面快捷方式。
- `tests/tools/test_desktop_launcher.py` -- 启动与安装安全契约。
- `docs/REMOTE_DEBUG.md` -- 现场启动说明。

## Tasks & Acceptance

**Execution:**
- [x] `tools/start_ruisheng_local.ps1` -- 实现幂等、安全的本地启动入口。
- [x] `tools/install_ruisheng_desktop_launcher.ps1` -- 生成图标、限制 ACL 并创建快捷方式。
- [x] `tests/tools/test_desktop_launcher.py` -- 覆盖指针、服务、锁、镜像、回环、超时和禁用命令。
- [x] `docs/REMOTE_DEBUG.md` -- 说明一键启动及 Docker 行为。
- [x] 目标机桌面快捷方式 -- 经公钥 SSH 校验安装，执行无浏览器验收并核对属性。

**Acceptance Criteria:**
- Given 活动发布有效, when 双击图标, then Docker 和前后端就绪并打开 `http://127.0.0.1/`。
- Given 服务已运行, when 再次双击, then 不删除数据或开放端口。
- Given 指针、配置、镜像或锁不可信, when 启动器运行, then Compose 变更前失败。
- Given 目标机已登录, when 开机任务或启动器运行, then 无需人工先开 Docker Desktop。

## Spec Change Log

- 2026-09-04 review hardening: 拒绝 Compose `network_mode`，在首次变更前核对现有容器镜像与回环端口，固定 Docker Desktop Linux named pipe，增加配置/指针临近复核、额外容器拒绝、受保护安装源、并发安装和 PowerShell 5.1 回归；保留活动指针、闭集服务、双锁、幂等快速路径和无破坏性命令。
- 2026-09-04 field acceptance: 目标机实测发现父目录安全继承、PowerShell 自动变量 `$Matches`、空 JSON 对象属性计数和 PowerShell 7 日期自动转换差异；分别改为有效写入者校验、独立站点列表变量、真实属性枚举和日期字符串保真，并把非提升启动日志隔离到专用 ACL 目录。

## Design Notes

桌面双击就是本机启动批准；本地执行器保留双锁、漂移校验和审计。安装器经管理员 SSH 执行一次，日常启动不提权，故不应出现 UAC。

## Verification

**Commands:**
- `pytest -q tests/tools/test_desktop_launcher.py` -- 安全契约通过。
- PowerShell 7 和 5.1 AST 解析两份脚本 -- 零语法错误。
- 目标机以 `-NoBrowser -NoUi` 运行 -- 五服务 ready、活动候选不变、无额外监听或串口变化。
- 读取快捷方式并请求 `http://127.0.0.1/` -- 目标、参数、图标及 Web 正常。

## Runtime Acceptance Evidence

- 本地门禁：启动器专项测试 `11 passed`，远程维护兼容回归 `80 passed`；Ruff、pre-commit、`git diff --check`、PowerShell 7 与 Windows PowerShell 5.1 AST 均通过。
- 受保护安装：本机与目标暂存文件 SHA-256 一致；安装路径为 `C:\Program Files\Ruisheng\Launcher`，仅 SYSTEM/Administrators 可写，`lenovo` 仅可读取执行；独立审计目录仅三者可访问。
- 桌面入口：`C:\Users\lenovo\Desktop\润盛监控系统.lnk` 指向 Windows PowerShell 5.1，参数、工作目录和 64×64 ICO 均核验通过，运行不要求提升且未修改现有开机任务。
- 实机启动：安装后的 PowerShell 5.1 入口输出 `READY candidate=deploy-20260903.2`；活动源码仍为 `1093e21a5172c7cb2be3bdb37fc157a70792b6aa`，五服务运行且实际镜像 ID 与 Manifest 一致，Web 返回 200，两把维护锁已释放。
- 安全边界：Web/GW 宿主端口仍仅绑定 `127.0.0.1`；GW 无设备映射、DeviceRequests 或 `GW_SERIAL_*` 环境变量；未重启 Windows、未重建容器、未发送 Modbus 报文，也未改变生产放行状态。

## Suggested Review Order

**启动与信任边界**

- 从 Docker、活动指针到幂等启动的主入口。
  [`start_ruisheng_local.ps1:1095`](../../../tools/start_ruisheng_local.ps1#L1095)

- 唯一站点发现同时兼容安全继承的父目录。
  [`start_ruisheng_local.ps1:179`](../../../tools/start_ruisheng_local.ps1#L179)

- Compose 闭集、不可拉取镜像和回环端口门禁。
  [`start_ruisheng_local.ps1:525`](../../../tools/start_ruisheng_local.ps1#L525)

**并发与运行态**

- 双租约锁与远程维护保持相同获取顺序。
  [`start_ruisheng_local.ps1:839`](../../../tools/start_ruisheng_local.ps1#L839)

- 现有容器也必须通过实际镜像及端口核验。
  [`start_ruisheng_local.ps1:675`](../../../tools/start_ruisheng_local.ps1#L675)

**安装与交互**

- 安装目录和普通用户权限分离。
  [`install_ruisheng_desktop_launcher.ps1:82`](../../../tools/install_ruisheng_desktop_launcher.ps1#L82)

- 独立审计 ACL 使普通双击无需 UAC。
  [`install_ruisheng_desktop_launcher.ps1:138`](../../../tools/install_ruisheng_desktop_launcher.ps1#L138)

- 原子创建带品牌 ICO 的桌面快捷方式。
  [`install_ruisheng_desktop_launcher.ps1:388`](../../../tools/install_ruisheng_desktop_launcher.ps1#L388)

**验证与运维说明**

- 安全契约、顺序和 PowerShell 5.1 回归入口。
  [`test_desktop_launcher.py:47`](../../../tests/tools/test_desktop_launcher.py#L47)

- 现场用户启动方式和 Docker Desktop 行为。
  [`REMOTE_DEBUG.md:33`](../../REMOTE_DEBUG.md#L33)
