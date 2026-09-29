# 江苏润盛 IoT SCADA 平台

## 2026-09-24 CH340 自动 COM 适配已部署

目标机 `WIN-OAUCM8UQUGH` 已部署并验证 CH340 自动 COM 适配候选。当前在线转换器为 `COM5 / BusId 4-1`，旧 `COM6` 记录因 `BusId=null` 被安全忽略；`/dev/ruisheng-rs485` 已恢复到 `/dev/ttyUSB0`。网关和 Web 使用原有容器与镜像恢复启动，应用仍为 `deploy-20260922.1` / 源码 `68fd3a90df64185ac38c23953b81201040919d43`。

短时复测最近24轮全部为完整38点、0空值、估算缺轮0，五项服务均正常。该修复解决设备拔插、更换 USB 口或 COM 号变化后串口别名无法重建、网关停止轮询的平台缺陷；不代表板端/RS485链路的普通响应超时已经消除。部署后仍有少量首次超时、补读恢复事件，历史缺轮和24小时验收未通过结论继续保留。

详见[自动适配部署记录](docs/reports/2026-09-24-ch340-auto-com-deployment.md)。后续若再次出现普通超时，需要补充板端 RX/TX/DE、CRC、发送完成和 RS485 接线证据，区分固件、转换器与接线原因。

9月24日12:22跟进快照显示网关恢复后最近窗口367轮完整38点、空值0、估算缺轮7；58次首次超时中42次补读恢复、8次补读耗尽。平台仍能把完整帧入库，未出现合法帧到达但平台漏入库的新证据。已保存[串口边界取证跟进](docs/reports/2026-09-24-serial-boundary-followup.md)，下一步需要板端 RX/TX/DE、CRC、UART错误恢复和发送完成记录。

## 2026-09-22 DO零起点回读修复已部署

已部署deploy-20260922.1 / 68fd3a9，控制回读改用设备已配置的0起点38寄存器读取，解决其与普通轮询结果不一致的问题。301项网关回归及8次隔离控制验收通过；17:24恢复采样，最新134轮完整38点、0空值，估算仍缺3轮，维护边界与普通超时保留。CH340挂载、五项服务和实际运行源码已复核；新版DO1/DO2闭合命令及回读0→1→3已确认，断开与物理回执待补。原24小时验收失败和heartbeat暂停状态不变。见[发布记录](docs/reports/2026-09-22-do-zero-origin-readback.md)。


## 2026-09-22 CH340恢复与DO回读差异

新CH340（COM6）已按精确设备身份适配并签名安装，16:25恢复采样，五服务未重启。16:46已250轮完整38点、零空值，估算缺2轮。DO1吸合/释放及普通轮询0→1→0已实测；控制单独回读仍3，断开审计为readback_mismatch，正在修复控制读取未沿用zero_origin_38的问题。CRC兼容补丁仍暂停，原24小时验收与暂停状态保留。见[恢复记录](docs/reports/2026-09-22-ch340-recovery.md)。


## 2026-09-22 下位机回显修改后复核

用户报告DO1接通已完整原样回显，实际运行网关CRC/分帧回放通过，现版.5无需此兼容补丁。15:08仍无新采样，最后数据14:12；15:11～15:14查明已配置FTDI适配器不在连接列表、WSL串口别名缺失，网关持续2秒重连。当前只见未挂载的CH340（COM6），待确认是否更换转换器。未改程序、挂载或配置，DO实际执行尚未复验。见[复核记录](docs/reports/2026-09-22-firmware-echo-recheck.md)。

## 2026-09-22 FC05回显确认与取证

用户确认完整8字节原样回显；32项分片接收回放和46项相关回归通过。90秒普通轮询取证：29次尝试，10个合法响应全部完整入库，19次失败在USB端已无完整响应，覆盖8次补读耗尽；未捕获FC05操作，控制超时仍待实际收发证据。生产版本保持deploy-20260921.5。见[报告](docs/reports/2026-09-22-do-echo-investigation.md)。

## 2026-09-21 DO调试确认简化

- 已部署deploy-20260921.5 / 2316b64：普通DO操作改为点选确认，高危验证保留；准确区分下发前读取、写应答和状态回读超时。31项前端单测、3项浏览器测试和签名隔离验收通过。现场7条超时中4条未发送写指令、3条写应答未确认，通信根因仍待板端回显协议及原始帧证据。见[报告](docs/reports/2026-09-21-relay-debug.md)。

## 2026-09-21 实时页继电器按钮

- 已部署 `deploy-20260921.4` / `f832c56`：设备实时页新增 DI1/DI2 状态、开关1/开关2回读和每路闭合/断开按钮；只有设备回读确认才显示执行成功。27项前端单测、3项浏览器测试和签名候选隔离控制链路验收通过，生产实际触点与DI切换待现场验证。原通信超时/缺轮和24小时验收失败结论保留。

详见[发布与验收记录](docs/reports/2026-09-21-digital-relay-panel.md)。

> 工业 RS485/Modbus 设备远程监控平台。基于 Python FastAPI + Vue 3 + PostgreSQL/TimescaleDB + Redis，支持 TCP/DTU 和 RS485 串口双模式设备接入。

## 当前交付状态（2026-09-21）

9月21日已受控部署 `deploy-20260921.3`（源码 `c6d1198`），DEV001配置27，增加DO1/DO2逐路高/低/保持控制、数据库审计、串口执行和回读确认。修复旧页面提交通用寄存器命令但网关未消费执行的问题。320项网关/API回归、22项前端、1项浏览器测试和目标签名隔离链路验收通过；7个实际运行源码哈希一致，五服务运行、API/GW就绪、38点采样持续。多路依次执行，失败不自动重发；5条历史命令按不支持类型拒绝。实际DO引脚、断电/USB拔插/DI切换仍待现场复测，普通通信超时未根治，原24小时验收失败与heartbeat暂停状态保留。详见[DO控制修复报告](docs/reports/2026-09-21-do-channel-control.md)。

以下为同日较早发布记录。

9月21日15:23已受控部署 `deploy-20260921.1`（源码 `f82e527`），修复串口EOF/读写异常后任务永久退出；目标日志已直接确认10:56发生该故障。15:23:00恢复完整38点采样，4小时27分采样缺口及维护均保留。15:24启用DO两路电平，DEV001配置26；DI/DO接口均返回两路显示元数据，实时卡片增加旧样本置灰。123项网关、19项前端、4项浏览器回归以及目标真实PTY、签名隔离验收通过。SSH经现场运行工具后已恢复。真实断电/USB拔插/DI切换仍待现场回执，普通响应超时仍存在，不宣称全部根因消除或稳定性通过。原24小时验收结论及历史保留，heartbeat继续暂停。详见[发布及复测报告](docs/reports/2026-09-21-serial-reconnect-digital-state.md)。

## 历史交付状态（2026-09-18）

DEV001原定24小时验收已形成期末结论：补读有实测改善，持续稳定性未通过。已取证23小时2分15.703秒内16,303轮完整38点、0空值，估算缺196轮，最长间隔45.076秒；1,511次补读恢复1,315轮。SSH公钥被拒导致最后57分44.297秒证据不足，当前采集状态未知。原定时验收已暂停（PAUSED，回执已核验），不自动延长为48小时；需现场恢复访问并提供板端RX/TX/DE与CRC证据。见[期末报告](docs/reports/2026-09-18-dev001-24h-acceptance.md)。

以下为历史巡检和发布记录；其中旧24/48小时安排已由本次期末结论取代。

9月18日11:25巡检SSH认证受阻：目标在线且主机密钥匹配，但lenovo账户拒绝当前公钥。最后成功数据截止10:55:19；此后采集状态未知，不能由认证失败推断停采。需现场核对公钥授权、ACL及OpenSSH日志，原11:53:04报告节点保持。详见[取证访问记录](docs/reports/2026-09-18-dev001-observation-access.md)。

9月18日09:25巡检发现09:12:51–09:13:36出现45.076404秒采样缺口，连续8轮首次与补读均超时，随后自行恢复；五项服务身份、运行版本和配置未变。累计15,241轮均完整38点、0空值，估算缺186轮；这次没有同期USB/板端证据，具体原因仍未隔离。原11:53:04报告节点保持，不能宣称完整通过。详见[连续超时记录](docs/reports/2026-09-18-dev001-timeout-burst.md)。

9月17日16:02超时归因复核：被动USB抓取111次尝试，108个合法响应全部对应完整38点入库，3次失败在USB接收端已表现为UART帧错误、残帧或无载荷。本次没有捕获补读再次超时，不能据此解释此前所有缺轮；未发现新的平台漏收/漏入库缺陷。优先请固件工程师按实际RX/TX/DE与CRC证据核对板端和RS485链路，保留当前24小时验收。详见[归因证据与固件建议](docs/reports/2026-09-17-dev001-timeout-attribution.md)。

当前按用户最新授权先做DEV001的24小时验收：每30分钟巡检，发现有证据的软件问题后自主修复并复测，9月18日11:53形成结论。修复如影响采集，另计修复后的连续观察段并保留全部故障；本轮不自动转为48小时。详见[验收与修复安排](docs/reports/2026-09-17-serial-read-retry.md)。

9月17日11:53已发布 `deploy-20260917.1`，源提交 `3a3ad8d`：单设备串口超时后最多补读一次，保留原始超时并分别统计补读结果。290项回归、签名校验及目标机隔离验收通过；数据库0013和DEV001配置23不变。实机已确认补读生效，长期稳定性继续观察，24/48小时节点为9月18日/19日11:53。详见[有限补读与现场对比](docs/reports/2026-09-17-serial-read-retry.md)。以下较早条目为历史记录。

9月17日10:01已安装串口缺失启动失败的自动恢复任务，10:03实际运行结果0，五项服务启动时间保持不变。51项相关回归通过；现场故障注入未执行。10:04最近一小时641轮完整38点、72次超时，稳定性仍未通过。详见[自动恢复记录](docs/reports/2026-09-17-serial-gateway-recovery.md)。

9月17日01:44复核：网关00:47已恢复，628轮完整38点，但仍有55次超时，单台稳定性未通过。调查入口已补充远端进程树超时/内存/输出限制，32项回归及真实目标取证通过。实际停采7小时9分计入不可用时间，恢复后24/48小时节点为9月18日/19日00:47。下方9月16日数据为恢复前记录。

最后已验收的现场版本为 `deploy-20260915.4`（9月15日），数据库 `0013_serial_polling_profile`。1号泵站 DEV001 已运行38点采集；实时推送、历史查询、登录自动续期和DI两路显示通过现场验证。温湿度地址35/36已确认，现场倍率0.1；DO回读布局和部分工程倍率仍待核对。

9月16日16:42（北京时间）现场已恢复连接并取得完整只读快照：五项服务正常、零重启，API/GW就绪、网页200。远程离线期间每小时仍有入库；最近24小时14,808轮均为完整38点，但有2,387次串口应答超时，与缺轮估算一致，最长间隔20.15秒。持续观察已开始，稳定性验收仍待完成。

当前工作为 Plan 5 持续采集与数据准确性验收：已有证据统计和只读现场统计工具已完成；从9月16日16:42起进行24～48小时观察，优先定位串口超时，完成DEV001单台设备稳定性、正式倍率及DO状态映射验收。用户已确认暂无第二台设备，第二台实物验收延期，不作为本轮阻塞项。发布基线已在独立分支 `codex/field-acceptance-20260916` 整理，尚未向现场发布。桌面入口已在9月14～15日按PowerShell7.6.5验收。

9月16日进一步完成单台串口被动取证：72次请求中62次完整响应与62轮完整38点入库对应，10次异常与超时日志逐项对应；其中3次有FTDI UART帧错误、6次无载荷、1次仅2字节。具体接线/转换器/固件故障仍待现场对照，稳定性尚未通过。详见[单台串口调查报告](docs/reports/2026-09-16-single-device-serial-diagnosis.md)。

9月17日发现网关曾因串口别名短暂不存在而退出，已用既有维护入口恢复；恢复后采集已重新推进，停采窗口与串口整改分开统计。详见[网关停采与恢复记录](docs/reports/2026-09-16-field-acceptance.md)。

详见[9月16日验收与整理报告](docs/reports/2026-09-16-field-acceptance.md)、[单台点位校准清单](docs/point-tables/calibration-checklist-20260916.md)。下方 Releases 为历史公开版本。

## Releases

| 组件 | 版本 | 说明 |
|------|------|------|
| 生产部署包 | [deploy-v0.1.0](https://github.com/proecheng/ruisheng-scada/releases/tag/deploy-v0.1.0) | Docker Compose 全栈部署 |
| 前端 | [web-v0.1.0](https://github.com/proecheng/ruisheng-scada/releases/tag/web-v0.1.0) | Vue 3 SPA |
| API | [api-v0.1.0](https://github.com/proecheng/ruisheng-scada/releases/tag/api-v0.1.0) | FastAPI REST + WebSocket |
| 网关 | [gw-v0.1.0](https://github.com/proecheng/ruisheng-scada/releases/tag/gw-v0.1.0) | Modbus 采集网关 |

## 快速部署（Docker）

**前提：** 已安装 [Docker Desktop](https://www.docker.com/products/docker-desktop/)

```bash
# 1. 配置环境变量
cp .env.prod.example .env.prod
# 编辑 .env.prod，填写所有 CHANGE_ME_* 密码

# 2. 初始化数据库（不启动 API、GW 或 Web 对外服务）
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d postgres redis migrate

# 3. 检查迁移任务成功退出
docker compose -f docker-compose.prod.yml --env-file .env.prod ps --all migrate
```

生产 bootstrap 只迁移数据库表结构，不创建演示数据或账号。管理员引导和凭据交接尚未交付，B-02 不解除 G0-05/CAP-2；在独立流程获批并完成前，不得将系统开放给用户。

详细说明见 [`deploy/setup-customer.md`](deploy/setup-customer.md)。

## 本地开发

```bash
# 安装 uv
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"

# 同步依赖
uv sync --all-packages

# 启动 PostgreSQL + Redis
uv run task up

# 数据库迁移
uv run task migrate

# 显式加载演示数据（仅限本地开发/测试，生产环境不得运行）
uv run task seed

# 运行测试
uv run task test

# 启动前端开发服务器
cd ruisheng-web && pnpm install && pnpm dev
```

## 技术栈

| 层 | 技术 |
|----|------|
| 前端 | Vue 3 + Vite + Pinia + ECharts + vue-konva |
| API | Python FastAPI + uvicorn + SQLAlchemy async |
| 网关 | Python asyncio（Modbus TCP/RTU + RS485 串口） |
| 数据库 | PostgreSQL 15 + TimescaleDB 2.16（时序数据） |
| 缓存/消息 | Redis 7（pub/sub + 限流 + JWT 黑名单） |
| 部署 | Docker Compose（6 services） |

## 仓库结构

```
ruisheng-scada/
├── ruisheng-shared/        # 共享包：ORM 模型 + 错误码 + 常量
├── ruisheng-api/           # FastAPI REST + WebSocket API
│   └── Dockerfile
├── ruisheng-gw/            # Modbus 采集网关（TCP + RS485）
│   └── Dockerfile
├── ruisheng-web/           # Vue 3 前端 SPA
│   ├── Dockerfile
│   └── nginx.conf
├── alembic/                # 数据库迁移
├── seeds/                  # 本地开发/测试演示数据（显式加载）
├── scripts/
│   └── entrypoint-migrate.sh
├── deploy/                 # 客户机部署包
│   ├── export-images.sh
│   └── setup-customer.md
├── docker-compose.prod.yml # 生产部署
├── docker-compose.dev.yml  # 本地开发（仅 DB）
└── .env.prod.example       # 环境变量模板
```

## 开发进度

- [x] **Plan 0**：基础设施（共享包 + alembic + docker + 工具链）
- [x] **Plan 1**：采集网关 `ruisheng-gw`（Modbus TCP/RTU + WAL + Redis pub/sub）
- [x] **Plan 2**：Web API `ruisheng-api`（250+ 端点 + JWT + RLS + WebSocket）
- [x] **Plan 3**：前端 `ruisheng-web`（Vue 3 SPA + 组态画面 + ECharts + PWA）
- [x] **Serial Port**：RS485 串口设备接入（双模式）
- [x] **Plan 4**：Docker Compose 生产部署（本机冒烟测试通过）
- [ ] **Plan 5**：单台38点采集及界面验收完成；持续稳定性、正式倍率/DO回读和多台实物验收待完成

## 贡献

见 [`CONTRIBUTING.md`](CONTRIBUTING.md)。

## 2026-09-25 点位完整16位有符号/无符号类型已部署

- 用户确认按完整16位寄存器解释：`0xFFFF` 为有符号 `-1`、无符号 `65535`；网关、API类型校验和点位编辑提示已更新。
- 目标机 `WIN-OAUCM8UQUGH` 已完成 GW/Web 服务级受保护发布；现有 DEV001 点位配置未修改。
- 首次网关热更新遗漏现场串口 override，造成约26分钟采集缺口；已修复部署入口并重新部署，`/dev/ruisheng-rs485` 已恢复挂载，DEV001 已恢复在线、38点完整采集。
- 详见 [`docs/reports/2026-09-24-point-byte-types.md`](docs/reports/2026-09-24-point-byte-types.md) 和 `tmp-test-logs/field-acceptance-20260916/observation-state.json`。

## 2026-09-28 桌面启动器误报已修复

现场截图中的 `compose_manifest_image_mismatch` 已通过启动器校验修复并在目标机实测恢复。当前五项服务运行、Web HTTP 200、DEV001 38 点完整无空值；最近24小时历史缺口仍保留，不能据此宣称连续24小时验收通过。详情见 [`docs/reports/2026-09-28-desktop-launcher-startup-repair.md`](docs/reports/2026-09-28-desktop-launcher-startup-repair.md)。
- 截图对应的旧启动器进程已清理，未重启 Docker；复核后五项服务仍运行、Web HTTP 200、DEV001 38 点完整无空值。现场可重新双击快捷方式。- 16:22 的一次 `desktop_launcher_failed` 卡住实例已清理；直接启动目标机快捷方式复测返回 `already_ready`，服务和采集保持健康。
