---
title: 'B-05 Windows RS485 硬件接入'
type: 'feature'
created: '2026-08-24'
status: 'done'
baseline_commit: '0c1cfebb836e3fab10489a4cabc1fa334deaafc3'
context:
  - 'D:/江苏润盛/docs/superpowers/specs/spec-plan-5-customer-deployment-acceptance/deployment-contract.md'
  - 'D:/江苏润盛/docs/superpowers/specs/spec-plan-5-customer-deployment-acceptance/site-acceptance-profile.md'
---

<frozen-after-approval reason="human-owned intent - do not modify unless human renegotiates">

## Intent

**Problem:** 目标 Windows 笔记本已通过 FTDI USB-RS485 连接真实设备，但 Docker Desktop 的 WSL 环境不会自动加载 FTDI 驱动，`usbipd` 附加在主机重启、WSL 重启或 USB 重新插拔后也会失效。当前候选 GW 没有设备映射，且现场串口参数、Modbus 地址和点表尚未确认，不能安全启动轮询。

**Approach:** 在候选包外提供按 VID/PID 和唯一 USB 序列号定位的主机级附加脚本，自动完成 usbipd bind/attach、WSL 驱动加载和稳定设备别名；提供严格的站点硬件配置与 Compose override validator，把“硬件可见”与“允许采集”分开验收。先在目标机完成只读枚举和容器可见性验证，参数获批后才生成并启用 GW 串口 override。

## Boundaries & Constraints

**Always:** 保持 `deploy-20260822.7` 的签名候选基础 Compose 不变；用 FTDI `0403:6001` 和序列号 `AI06JYFW` 识别设备，不依赖会随 USB 口变化的 BUSID；站点差异只进入 `C:\Ruisheng\site` 和主机工具目录；审计记录不得包含凭据；GW 在采集参数未批准时保持无 `GW_SERIAL_PORTS`、无设备映射。

**Ask First:** 启用真实轮询前必须取得设备品牌/型号、协议和点表版本、波特率、数据位、校验位、停止位、Modbus 从站地址及采集寄存器；发送控制帧、告警越限、真实通知或重启目标机仍需单独批准。

**Never:** 不向未知设备发送探测帧或控制帧；不把 COM3 机械映射为 `/dev/ttyS2`；不写死 BUSID；不编辑签名候选文件；不以 `/dev/ttyUSB0` 一次出现或容器能 `stat` 设备作为 B-05 真机采集完成证据。

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| ATTACH_READY | FTDI 已插入，身份唯一，Docker WSL 可用 | 自动绑定/附加、加载 `usbserial`/`ftdi_sio`，建立稳定别名并记录 ready | 不打开串口，不启动 GW 轮询 |
| REPLUG_NEW_BUSID | 同一序列号设备换 USB 口或重新插拔 | 重新发现新 BUSID 并恢复别名 | 旧 BUSID 不作为持久配置 |
| DEVICE_ABSENT | 设备未插入或供电断开 | 任务保持等待并周期重试 | 记录单次 unavailable 状态，不影响现有应用容器 |
| AMBIGUOUS_IDENTITY | VID/PID 匹配多个且序列号缺失/重复 | 拒绝选择设备 | 审计为 failed，不附加任意设备 |
| POLLING_BLOCKED | 硬件可见但线路/协议参数未批准 | validator 返回 BLOCKED | 不渲染或启动串口 override |

</frozen-after-approval>

## Code Map

- `tools/serial_hardware_attach.ps1` -- Windows/usbipd/WSL 自动恢复入口，只管理设备可见性。
- `tools/validate_serial_hardware.py` -- 校验站点硬件身份、串口参数和实际 Compose 渲染结果。
- `deploy/site-serial-hardware.json.example` -- 不含站点值的硬件配置模板。
- `deploy/site-serial.override.yml` -- 候选外 GW 设备映射模板，仅在 validator PASS 后使用。
- `tests/tools/test_serial_hardware.py` -- 配置、override 和阻断条件回归测试。
- `deploy/setup-customer.md` -- Windows USB-RS485 安装、恢复、验证和启用顺序。

## Tasks & Acceptance

**Execution:**
- [x] 实现按 VID/PID/序列号发现设备、自动 bind/attach、驱动加载、稳定别名和状态审计的 PowerShell 工具。
- [x] 实现站点配置/Compose validator，拒绝未决参数、身份不唯一、路径漂移、非 8N1 和候选内硬件修改。
- [x] 提供候选外硬件配置与 override 模板，保持基础 Compose 和默认 GW 运行状态不变。
- [x] 补单元测试和部署说明，并在目标机完成 usbipd、WSL、设备节点和一次性容器的只读可见性验证。

**Acceptance Criteria:**
- Given 同一 FTDI 转换器更换 BUSID，when 自动附加任务运行，then 仍按 `0403:6001/AI06JYFW` 恢复 `/dev/ruisheng-rs485`。
- Given 设备缺席、WSL 未就绪或驱动加载失败，when 任务运行，then 现有五个应用容器继续运行且任务可重试。
- Given 线路参数、Modbus 地址或点表未批准，when 运行 validator，then 返回 BLOCKED 且运行中 GW 无设备映射、无 `GW_SERIAL_PORTS`。
- Given 全部批准参数和稳定设备路径，when 渲染基础、网络和串口 override，then 只有 GW 获得唯一设备映射和与数据库路径一致的串口配置。

## Spec Change Log

- 2026-08-24 review hardening: serial enablement now requires a fresh protected hardware-state attestation and a read-only database device-record check before `PASS`; the serial environment is limited to `GW_SERIAL_DEVICE_PATH` and `GW_SERIAL_BAUD_RATE`; base/final Compose renders are compared with an exact delta allowlist; unapproved configurations reject hidden serial mappings; the device mapping uses `rw`; and Windows attach/task scripts reject placeholders, refresh BUSID before every usbipd operation, clear stale aliases on failure, enforce protected ACLs/reparse-point checks, and install only from the authenticated publisher snapshot. These changes preserve the frozen boundary that real polling remains disabled until explicit device/protocol approval.

## Verification

**Commands:**
- `uv run pytest tests/tools/test_serial_hardware.py tests/tools/test_production_compose.py` -- expected: all pass.
- `uv run ruff check tools/validate_serial_hardware.py tests/tools/test_serial_hardware.py` -- expected: clean.
- Target host: run the attach tool once, then verify usbipd state, WSL sysfs identity, `/dev/ruisheng-rs485`, and an ephemeral container `stat`; expected: identity matches and no serial open/read/write occurs.

## Suggested Review Order

1. Re-run the serial hardware boundary tests and inspect the validator's exact Compose delta and fresh evidence requirements.
2. Review the PowerShell attach/task scripts for protected-path, BUSID refresh, alias cleanup, timeout, and Docker Desktop user-context behavior.
3. Re-run the production Compose, network boundary, release artifact, Ruff, mypy, and PowerShell parser checks.
4. On the target Windows host, verify only hardware visibility and recovery; do not enable GW polling until the device protocol and point map are approved.
