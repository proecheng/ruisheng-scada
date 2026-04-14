# 江苏润盛 IoT 监控平台 — 重做设计文档

> **版本**：v1.3.3 / 2026-04-14
> **状态**：5 角色审查合并 + 一致性轮 + 边界/数据流轮 全部完成；等待最终 ack → writing-plans
> **关联文档**：
> - 需求基线：`D:\江苏润盛\需求清单\功能全景清单.md` v2.2（1609 行）
> - 待澄清问题：`D:\江苏润盛\需求清单\待澄清问题清单.md` v1.1
> - 澄清证据：`D:\江苏润盛\需求清单\澄清证据档案.md` v1.1
>
> **本文档作用**：把 brainstorming 阶段确认的 8 段架构决策固化为单一可执行设计基线。之后进入 `superpowers:writing-plans` 生成实施计划。

---

## 目录

- §0 项目定位与锁定决策
- §1 整体架构与进程划分
- §2 服务内部模块划分
- §3 三条核心数据流
- §4 数据模型
- §5 错误处理与可靠性
- §6 测试策略
- §7 部署与运维
- §8 时间盒与里程碑
- §9 未决问题对齐
- §10 变更记录
- §14 审查反馈整合清单（v1.3+）
- §A 协议规范附录（v1.3+）

---

## §0 项目定位与锁定决策

### 0.1 软件定位

**石油/工业设备远程监控 SCADA 平台**（核心业务：采油机、保温电气、液位温湿度监控）。

功能规模覆盖 v2.2 主文档里定义的 **P0 + P1 + P2 共 14 个业务模块**，预估代码量 15k–20k 行。

### 0.2 本阶段锁定的关键决策（brainstorming 结果）

| # | 决策项 | 决策 |
|---|---|---|
| T1 | 范围 | P0 + P1 + P2 功能完备，时间盒 **12 周** |
| T2 | 部署目标 | **Windows Server** + B/S 架构 |
| T3 | 技术栈 | **Python (FastAPI) + Vue 3 + PostgreSQL + TimescaleDB** |
| T4 | 数据库 | **PostgreSQL 15 + TimescaleDB** 插件；原 SQL Server 数据通过 pgloader 迁移 |
| T5 | 外部集成 | 微信公众号 + 微信支付 + 短信 + IVR 电话 + SMTP 全上 |
| T6 | 架构形态 | **前后分离 + Redis**：`ruisheng-api`（Web API/WS/微信支付回调） + `ruisheng-gw`（采集网关）+ Redis（实时总线）+ PostgreSQL |

### 0.3 延续自 v2.2 主文档的业务决策

- **D1** 特权账号 `18264192756` 废弃 → `Authority=Administrators`
- **D2** 告警通道可配置适配器（sms/voice/email/wechat 各 2–3 家供应商 + 通用 HTTP）
- **D3** 设备身份：`DevSerNumber` 主 + `iccid` 辅 联合识别；换 SIM 视为同设备
- **D4** 历史库按月分表，保留 1 年；超期归档到对象存储
- **D5** MVP = P0 全部 + 部分 P1（§6.3）
- **D6** 物理层 RS485；每总线 ≤128 台；每终端独立轮询 [1.0s, 100.0s] 精度 0.1s
- **D7** 空函数 = 不需要做；已砍 FunCode 7/12 + 21 个空壳 .aspx + FunCode 13/26 并入 3/6

---

## §1 整体架构与进程划分

### 1.1 部署总图

```
                       浏览器 / 微信公众号 / 安卓 App
                                 │
                          HTTPS / WSS
                                 ▼
        ┌──────────────────────────────────────────────────┐
        │  Nginx (Windows 版)                              │
        │   ├─ /                  → 静态前端 (Vue dist)    │
        │   ├─ /api/*             → ruisheng-api           │
        │   ├─ /ws                → ruisheng-api WebSocket │
        │   └─ /wechat/notify     → ruisheng-api           │
        └────────────────┬─────────────────────────────────┘
                         │
                ┌────────┴────────┐
                ▼                 ▼
    ┌───────────────────┐  ┌───────────────────────┐
    │ ruisheng-api      │  │ ruisheng-gw           │
    │ (Windows Service) │  │ (Windows Service)     │
    │                   │  │                       │
    │ FastAPI + uvicorn │  │ asyncio + pymodbus    │
    │ 业务 REST + WS    │  │ TCP server / 串口     │
    │ 微信/支付/告警    │  │ 协议解析 / 告警判定   │
    │ 调度配置写库      │  │ 实时点值入库          │
    └─────────┬─────────┘  └─────────┬─────────────┘
              │                      │
              └──────────┬───────────┘
                         ▼
              ┌──────────────────────┐
              │ Redis (pub/sub+缓存) │
              │  channels:           │
              │   channel:realtime:{dev_number} (Pub/Sub)
              │   stream:alarm:fired   (Streams)
              │   stream:control:cmd   (Streams)
              │   alarm:fired        │
              │   control:cmd        │
              └──────────┬───────────┘
                         ▼
              ┌──────────────────────┐
              │ PostgreSQL 15        │
              │ + TimescaleDB        │
              │  └─ hypertable:      │
              │     point_data_history│
              │     waveform_history │
              └──────────────────────┘
```

### 1.2 四个独立运行单元

| 单元 | 形态 | 端口 | 主要职责 |
|---|---|---|---|
| **Nginx** | Windows 服务 | 80/443 | TLS 终止、静态资源、反向代理、WSS 升级 |
| **ruisheng-api** | Windows 服务 | 内网 8000 | 业务 REST、WS 推送、微信/支付回调、定时 Job |
| **ruisheng-gw** | Windows 服务 | TCP 6000/6020（设备上行），内网 8001（admin）| RS485/DTU 接入、协议解析、告警引擎、点值落库 |
| **PostgreSQL+Redis** | Windows 服务 | 5432 / 6379 | 数据 + 实时总线 |

### 1.3 通讯总线契约

| 通道 | 形态 | 生产者 | 消费者 | 内容 |
|---|---|---|---|---|
| Redis `channel:realtime:{dev_number}` | **Pub/Sub**（允许丢） | gw | api（WS 订阅）| 实时点值 `{point_id, value, ts}`（下一秒又来，丢单条无害） |
| Redis `stream:alarm:fired` | **Streams**（at-least-once） | gw | api `api-alarm-consumer` 组 | 告警触发事件，ACK 后 XDEL |
| Redis `stream:control:cmd` | **Streams**（at-least-once） | api | gw `gw-control-consumer` 组 | 用户控制命令，ACK 后 XDEL |
| Redis `stream:dlq:*` | **Streams** | 消费者 | 运维告警 | 死信（5 次处理失败） |
| PostgreSQL `devices.update_flag` | 轮询 | api | gw（5s 扫） | 配置变更（告警阈值/轮询间隔/定时计划） |

> 详细设计见 **§5.9 消息送达保证**。核心原则：**实时数据允许丢**（覆盖写模型），**告警与控制不许丢**（业务事故）。

### 1.3.1 Config 同步（v1.3 修订：B-A4）

原 v1.2 方案是 gw 每 5s 轮询 `devices.update_flag`。审查发现延迟过大（改阈值 5s 才生效）且多 gw 实例时有竞态。

**新方案**：Redis Pub/Sub `channel:config:changed` + 主动 pull：

```
api 改配置 → 事务内：UPDATE devices.update_flag + PUBLISH channel:config:changed {dev_number}
                                                        │
                                                        ▼
gw 订阅 channel:config:changed → SELECT 最新 config → 更新内存缓存
```

- 延迟从 5s 降到 < 100ms
- 多 gw 实例都会收到（广播），各自 pull 本地责任分片的设备
- 补偿：gw 启动时仍主动扫一遍 `update_flag > 0` 的设备，兜底漏订阅

### 1.4 关键设计选择

1. **Nginx 必装**：Windows Server 上比 IIS 更轻量，更接近 Linux 经验
2. **gw 与 api 不直接通讯**：解耦关键；gw 只面对设备和 Redis/DB，api 只面对用户和 Redis/DB
3. **gw 进程内 asyncio 单线程**：每条 RS485 一个 asyncio task
4. **配置变更走 DB 标志位轮询**：gw 每 5s 扫 `update_flag`，避免 api→gw 反向 RPC
5. **微信/支付回调只走 api**：所有外网入口归 api，gw 不暴露公网

---

## §2 服务内部模块划分

### 2.1 `ruisheng-api`

```
ruisheng-api/
├── app/
│   ├── main.py                  # FastAPI 入口 + 路由挂载 + 启动钩子
│   ├── config.py                # pydantic-settings
│   ├── deps.py                  # 依赖注入：DB session / current_user / RBAC
│   │
│   ├── core/
│   │   ├── security.py          # JWT 签发/校验、密码 Bcrypt
│   │   ├── rbac.py              # 4 级权限矩阵（D1/§3.6）
│   │   ├── tenant.py            # UsrGroup 强制过滤（§3.7）
│   │   └── errors.py            # 错误码（§5.1 ErrCode）、统一异常响应
│   │
│   ├── db/
│   │   ├── base.py              # SQLAlchemy 2.0 async engine + Session
│   │   ├── models/              # ORM
│   │   └── repositories/        # 仓储模式
│   │
│   ├── api/                     # REST 路由（按业务模块拆）
│   │   ├── auth.py              # 登录/注册/短信验证码/微信 OAuth/绑定
│   │   ├── devices.py           # 设备 CRUD + 实时数据 + 历史
│   │   ├── points.py            # 点位配置 + 阈值
│   │   ├── alarms.py            # 告警查询 + 复位 + 通讯录
│   │   ├── reports.py           # 日报 / 月报 / Excel 导出
│   │   ├── waveforms.py         # 波形查询 + FFT/OPM
│   │   ├── plans.py             # 定时计划 + 保养
│   │   ├── scenes.py            # 组态 ZT*
│   │   ├── pay.py               # 微信支付 JSAPI/扫码 + 回调
│   │   ├── orgs.py              # 集团/公司/部门/用户
│   │   └── ws.py                # WebSocket 实时推送
│   │
│   ├── services/
│   │   ├── notification/        # 通知适配器（D2 可插拔）
│   │   │   ├── base.py          # INotifier 抽象
│   │   │   ├── wechat.py        # 微信模板消息 + Token 刷新
│   │   │   ├── sms.py           # 短信 aliyun/tencent/yunpian/custom
│   │   │   ├── voice.py         # IVR 电话 tencent/custom
│   │   │   └── email.py         # SMTP
│   │   ├── wechat_oauth.py      # 公众号网页授权 + openid 绑定
│   │   ├── wechat_pay.py        # JSAPI / Native 下单 + 回调验签
│   │   ├── analytics/
│   │   │   ├── fft.py           # 频谱分析
│   │   │   └── opm.py           # 抽油机平衡系数
│   │   ├── reports/
│   │   │   ├── aggregator.py
│   │   │   └── excel_export.py
│   │   └── archiver.py          # §G 月归档 Job
│   │
│   ├── tasks/                   # APScheduler 调度
│   │   ├── token_refresh.py     # 微信 token 60min
│   │   ├── sim_quota.py         # 中移 OpenAPI 流量
│   │   └── monthly_archive.py   # 每月 1 号 02:00
│   │
│   ├── pubsub/
│   │   ├── publisher.py         # 写 Redis：control:cmd
│   │   └── subscriber.py        # 订阅 Redis：alarm:fired / realtime:* → WS
│   │
│   └── tests/                   # pytest
│
├── alembic/                     # DB 迁移
└── pyproject.toml
```

### 2.2 `ruisheng-gw`

```
ruisheng-gw/
├── app/
│   ├── main.py                  # asyncio 入口
│   ├── config.py
│   │
│   ├── transport/
│   │   ├── tcp_server.py        # asyncio.start_server
│   │   ├── serial_bus.py        # pyserial-asyncio（可选）
│   │   └── connection.py        # 粘包/超时/CRC 状态机
│   │
│   ├── protocol/                # 纯字节↔结构，无 IO
│   │   ├── modbus_rtu.py        # 帧编解码 + CRC16(0xA001)
│   │   ├── frames.py            # FunCode 3/5/6/16/20/21/22/100 dataclass
│   │   ├── waveform_codec.py    # §4.2.C 波形 BLOB 编解码
│   │   └── private_codes.py     # 13/26 当 3/6 变种统一处理
│   │
│   ├── domain/
│   │   ├── device.py            # Device 状态机
│   │   ├── point.py             # 点位 + 标度换算
│   │   ├── alarm_engine.py      # WaringFlag 状态机（§F）
│   │   └── command_queue.py     # 离线命令队列（§2.2.5）
│   │
│   ├── scheduler/
│   │   ├── poller.py            # 每 RS485 一个协程，每终端独立 1.0–100.0s
│   │   └── timing_plan.py
│   │
│   ├── persistence/
│   │   ├── repository.py        # 复用 ruisheng-shared 模型
│   │   ├── batch_writer.py      # 真批量（修复 MinTransactionCnt=1）
│   │   └── config_watcher.py    # 5s 轮询 update_flag
│   │
│   ├── pubsub/
│   │   ├── publisher.py         # channel:realtime:{dev_number} / stream:alarm:fired
│   │   └── subscriber.py        # control:cmd
│   │
│   └── tests/
│       ├── unit/
│       └── replay/              # 真机抓包回放对账
│
└── pyproject.toml
```

### 2.3 共享包 `ruisheng-shared`（B-A1：schema 版本化）

```
ruisheng-shared/
├── __init__.py                  # SHARED_SCHEMA_VERSION = 20260414  ← 每次 breaking 改动 +1
├── CHANGELOG.md                 # 每次改动必登：breaking / deprecation / feature / fix
├── models/                      # SQLAlchemy 模型（两服务共用）
├── schemas/                     # pydantic（两服务共用）
├── enums/                       # FunCode、AlarmType、AlarmAction
└── constants/                   # 协议常量、错误码
```

**版本互检**（启动时硬阻断）：

```python
# ruisheng-api/app/main.py  &&  ruisheng-gw/app/main.py  都加
from ruisheng_shared import SHARED_SCHEMA_VERSION

REQUIRED = 20260414
if SHARED_SCHEMA_VERSION != REQUIRED:
    raise RuntimeError(f"shared mismatch: expect {REQUIRED}, got {SHARED_SCHEMA_VERSION}")
```

**CI breaking 检测**：
- PR 修改 `ruisheng-shared/models/` 或 `schemas/` 触发自动检测
- 对比 main 分支的 pydantic schema JSON（`python -m ruisheng_shared.dump_schemas`）
- 如检测出字段删除 / 类型变更 / 必填项新增 → 要求 PR 同时修改 api + gw 的 `REQUIRED` 版本号，否则红灯
- `CHANGELOG.md` 条目必须有 `breaking:` 前缀才允许合入

### 2.4 前端 `ruisheng-web`

```
ruisheng-web/
├── src/
│   ├── api/                     # axios + openapi-typescript 自动生成
│   │                            #   拦截器：X-Trace-Id 注入、错误码→中文映射
│   ├── stores/                  # Pinia（含 diag store：最近 API/WS/错误）
│   ├── router/
│   ├── layouts/                 # 响应式（桌面+移动断点 768/1024）
│   ├── views/
│   │   ├── auth/
│   │   ├── dashboard/
│   │   ├── devices/
│   │   ├── alarms/
│   │   ├── reports/
│   │   ├── waveforms/           # ECharts + 调 FFT/OPM；图表数据一键导 CSV
│   │   ├── plans/
│   │   ├── scenes/              # vue-konva 组态画布
│   │   ├── pay/                 # 微信 JSAPI / 扫码
│   │   ├── settings/
│   │   └── __diag.vue           # 诊断页：当前会话/连接/最近调用（§2.5.1）
│   ├── components/
│   │   ├── ErrorBoundary.vue    # 组件崩溃兜底（§2.5.1）
│   │   ├── EmptyState.vue       # 列表空态引导（§2.5.2）
│   │   ├── ConfirmDialog.vue    # 二次确认 + Undo 5s（§2.5.2）
│   │   ├── LoadingSkeleton.vue  # 骨架屏
│   │   ├── CommandPalette.vue   # Ctrl+K 全局搜索（§2.5.3）
│   │   └── Toast.vue            # 统一反馈
│   ├── composables/
│   │   ├── useShortcuts.ts      # 键盘快捷键注册
│   │   ├── useRecent.ts         # 最近访问记忆
│   │   └── useAsync.ts          # pending/success/error 三态
│   ├── directives/
│   │   ├── v-debounce.ts
│   │   └── v-permission.ts      # 前端 RBAC 显隐（后端仍为权威）
│   ├── i18n/                    # vue-i18n 骨架，zh-CN 必备，en 占位
│   ├── utils/
│   │   ├── errors.ts            # 错误码→友好消息 + 建议下一步
│   │   ├── format.ts            # 人性化时间"3 分钟前"、数值格式化
│   │   └── sentry.ts            # 前端异常上报（脱敏）
│   ├── ws/                      # WebSocket 客户端封装（自动重连、状态订阅）
│   └── debug/                   # 仅 ?debug=1 加载
│       ├── RequestLogPanel.vue
│       └── WsStatePanel.vue
├── public/
│   └── build-info.json          # 构建 hash + 时间，页脚显示
├── vite.config.ts               # Source maps 生产开启（上传 Sentry，不公开）
└── package.json
```

### 2.5 前端体验原则（可调试性 / 用户友好性 / 易操作性）

#### 2.5.1 可调试性

| 机制 | 实现 | 价值 |
|---|---|---|
| **错误边界** | 根级 `<ErrorBoundary>` + Vue `errorCaptured`；组件崩溃展示"某模块异常"+ "刷新 / 联系管理员"双按钮，不白屏 | 单组件 bug 不拖垮整页 |
| **诊断页 `/__diag`** | 仅登录用户可见：当前用户/租户/Authority；最近 50 次 API（路径+耗时+traceId）；WS 连接状态；客户端时间 vs 服务器时间偏差 | 运维/一线排障零成本 |
| **Trace ID 贯穿** | axios request 注入 `X-Trace-Id`；响应 header 回写；Toast 错误提示右侧显示 `[点击复制 Trace ID]` | 一个 ID 定位前后端 + gw 完整链路 |
| **前端异常上报** | `window.onerror` + `vue:errorHandler` + axios interceptor → Sentry（或自建 `POST /api/client_errors`）；带上 user / trace / UA / URL / build hash | 线上 bug 可复现 |
| **构建版本显示** | 页脚 `v1.2.3 · build 2026-04-13 a3f9` 点击复制；后端 `/api/meta/version` 双向对照 | 问题时马上知道"是哪版" |
| **Source Maps** | Vite 生产开启；`.map` 上传 Sentry，不发布到 CDN | 堆栈还原原文件行号 |
| **Debug Mode 开关** | URL `?debug=1` 或键盘 `Ctrl+Alt+D` 切换；开启后显示 RequestLogPanel + WsStatePanel + 所有 Tooltip 展开 | 一线排障不用装 DevTools |
| **图表数据下载** | 每个 ECharts 右上角有下载按钮 → CSV/JSON 原始数据 | 对账 + 第三方分析 |

#### 2.5.2 用户友好性

| 场景 | 规则 |
|---|---|
| **加载态** | 每个异步界面必须有 **骨架屏**，不许白屏超过 300ms；超过 3s 加"还在加载…"文案 |
| **空态** | 列表为空时渲染 `<EmptyState icon slot action>`：告诉用户"空的原因 + 下一步能做什么"（例："该部门暂无设备，点击'添加设备'") |
| **错误消息** | 禁用 `"error"` / `"failed"` / HTTP 码直显；后端 ErrCode → `utils/errors.ts` 查表映射为：**一句话现象 + 一句话建议**（如 `-200 设备离线` → "设备 #60270012 目前离线，可能 DTU 断网；稍后再试或查看信号") |
| **Toast 反馈** | 所有写操作都 toast；成功 2s 自动消、失败常驻需手动关；Ctrl+Z 5s 内可撤销（适用于删除/复位）|
| **确认对话框** | 危险操作（删除、远程控制、批量改配）二次确认；涉及真实设备动作的额外要求**输入设备号**才能确认 |
| **表单即时校验** | 用 `vee-validate` + zod schema；输入失焦即校验；提交按钮永远可点击但校验失败时抖动 + 聚焦首个错误字段 |
| **时间格式** | 列表用"3 分钟前"相对时间；详情 tooltip hover 出绝对时间 + 时区；关键事件（告警/控制）同时显示两种 |
| **国际化** | `vue-i18n`，zh-CN 全覆盖，en-US 占位（Q-S01 等保要求可能需要）；日期 / 数字 / 货币走 Intl API |
| **无障碍 a11y** | WCAG AA；所有按钮有 aria-label；表单 label 关联；焦点样式可见；色盲友好调色盘（告警色 + 辅以图形符号） |
| **移动端** | 主要操作（控制、告警复位）在屏幕下半部分或悬浮按钮；Lighthouse Mobile ≥ 85 |

#### 2.5.3 易操作性

| 功能 | 键位 / 入口 | 描述 |
|---|---|---|
| **全局搜索** | `Ctrl+K` / `Cmd+K` | Command Palette：模糊搜设备号/设备名/用户名/告警名；回车跳转 |
| **设备树快速定位** | 树侧搜索框，输入即过滤 | fuzzy（支持拼音首字母） |
| **批量操作** | 列表勾选 + 工具栏 | 批量复位告警、批量下发控制、批量导出；≥5 台时要求输入 `CONFIRM` |
| **最近访问** | 首页"最近设备"卡片 + `Ctrl+E` 打开 | 最近 5 个设备 / 最近 3 张报表 |
| **Undo** | `Ctrl+Z` / Toast 上的"撤销"按钮 | 删除、复位 5s 内可撤销 |
| **键盘导航** | `j/k` 列表上下、`Enter` 详情、`Esc` 关弹窗、`/` 聚焦搜索 | 参考 GitHub/Linear 惯例 |
| **右键菜单** | 列表行右键 | 快捷操作（复位、控制、打开组态）|
| **收藏 / 固定** | 设备详情页星标 | 收藏的设备置顶；用户级，不跨租户 |
| **响应式断点** | <768 手机、768–1024 平板、>1024 桌面 | 同一代码三端跑 |
| **PWA 基础离线** | Service Worker 缓存静态资源 + 最近 1 次接口响应 | 弱网或短断线时能看到上次数据 + 明显"离线" Badge |
| **浏览器兼容** | Chrome / Edge 最新 2 版；Safari 16+；不支持 IE | 早期告知 |

**前端性能门槛**（verified by Lighthouse CI）：
- Desktop Performance ≥ 90 / Accessibility ≥ 90 / Best Practices ≥ 90
- Mobile  Performance ≥ 85 / Accessibility ≥ 90
- 首屏 LCP ≤ 2.0s（桌面 3G）；CLS ≤ 0.1；FID ≤ 100ms
- 主 bundle ≤ 500 KB gzipped；路由级代码分割 + ECharts/konva 懒加载

### 2.5 关键设计原则

1. **协议层零 IO**：`protocol/` 纯函数，方便 fuzz + 单元测试
2. **业务模型独立**：`domain/` 不依赖 SQLAlchemy / asyncio
3. **共享包避免重复**：schema 只写一次
4. **api/router 按业务拆**：不重复旧 38 路 DataType if/else
5. **前端 OpenAPI 类型自动生成**

---

## §3 三条核心数据流

### 3.1 设备上报路径

```
设备 ──RS485 RTU──▶ DTU ──TCP 6000/6020──▶ ruisheng-gw
                                            │
                                 ┌──────────┼──────────────┐
                                 ▼          ▼              ▼
                            channel:     stream:       PostgreSQL
                            realtime:    alarm:fired   TimescaleDB
                            {DevNum}    (Streams,      point_data_*
                           (Pub/Sub,    at-least-once) + 本地WAL兜底
                           可丢)              │            (§5.11)
                                 │            ▼
                                 │      api 消费组
                                 │      XREADGROUP + XACK
                                 │            │
                                 │            ├─ 通知适配器
                                 │            └─ WS 转发给浏览器
                                 ▼
                            ruisheng-api WS ─▶ 浏览器
```

**步骤**

1. 设备帧到达 → `transport/connection.py` 缓冲 → 累够一帧 + CRC OK
2. `protocol/modbus_rtu.py` 解码 → `Frame` dataclass
3. `domain/point.py` 应用 `PointRatio/Offset` → 物理量
4. 并发三件事：入库（asyncpg COPY，每 100ms flush 或满 500 行）、publish Redis realtime、alarm_engine 比对
5. api subscriber 转 WS 给浏览器
6. ECharts 增量重绘

**性能目标**：端到端 ≤ 1s；吞吐 ≥ 3000 pps

### 3.2 用户控制路径

```
浏览器 ──HTTP POST──▶ api /api/devices/{dev_number}/control
                        │  Idempotency-Key 头防重复
                        ├─ RBAC（ControlAuthority bit0，§3.6）
                        ├─ 多租户过滤（§3.7 自动注入 WHERE usr_group）
                        ├─ 是否高危（ControlAuthority bit2 标记）
                        │     ├─ 是 → 要求 OTP 短信确认码（5min 窗口有效）
                        │     └─ 批量 ≥ 5 台 → 要求输入设备编号或 CONFIRM
                        ├─ 查 `devices.last_state` → 与目标状态冲突则 -1 拒绝（§M-S-07）
                        ├─ 事务：写 user_control_actions(result=pending, cmd_id) + 生成 cmd_id
                        └─ XADD stream:control:cmd (cmd_id, payload)
                                │
                                ▼
                        gw XREADGROUP gw-control-consumer
                                │  cmd_id LRU 去重 24h（§5.9）
                                ├─ 在线 → poller 队列
                                └─ 离线 → 命令队列（Redis ZSET, TTL 10min，超时标 cancelled）
                                │
                                ▼
                        编码 FunCode 6/16 + CRC ──▶ DTU ──▶ RS485 ──▶ 设备
                                │
                                ├─ ACK 成功 → 更新 action.result=success + XACK + WS 推用户
                                ├─ 超时 3 次 → XADD stream:dlq:control + 审计 failed + 告警 + WS 推用户
                                ├─ 进程崩溃 → 下次启动 XAUTOCLAIM 拿回未 ACK 继续
                                └─ 取消 → 用户 24h 内可 `DELETE /api/control/commands/{cmd_id}`（仅 pending 状态）

**控制命令状态机**（cmd_id 生命周期）：

  pending ──XADD──▶ dispatching ──ACK──▶ success
            │                  │
            │                  ├─timeout──▶ failed
            │                  └─cancel──▶ cancelled
            │
            └─dlq(15min)──▶ dead（需人工介入）

**响应契约**：
- HTTP 201 立即返回 `{cmd_id, status: "pending"}`（不等设备 ACK）
- WS 在 cmd_id 状态变化时推送 `{type: "control_result", cmd_id, status, at, reason?}`
- 前端 Toast 显示执行进度，5s 内无状态变化降级为"请稍后查看控制记录"

### 3.4.1 WebSocket 消息合同（前后端一致性契约）

所有 WS 消息统一信封，`type` 字段决定 payload 形状：

```typescript
// 前端 ws/types.ts 应由后端 OpenAPI/JSONSchema 自动生成
type WSMessage =
  | { type: "realtime"; dev_number: string; point_id: number; value: number; ts: string }
  | { type: "alarm"; event_id: number; dev_number: string; alarm_name: string;
      value: number; limit: number; ts: string }
  | { type: "alarm_reset"; event_id: number; dev_number: string; ts: string }
  | { type: "control_result"; cmd_id: string; status: "success"|"failed"|"timeout"|"cancelled";
      at: string; reason?: string }
  | { type: "device_state"; dev_number: string; state: "online"|"offline"|"warning"; at: string }
  | { type: "ping"; ts: string }                                      // 心跳
```

**保证**：
- 所有 `ts` / `at` 均为 ISO8601 UTC，前端自行转 +08
- 所有 `dev_number` 与 HTTP 路径 `{dev_number}`、Redis key `channel:realtime:{dev_number}` 保持同一命名
- `status` 枚举与 `user_control_actions.result` 字段取值（`pending/success/failed/timeout/cancelled`）完全一致
- 新增 `type` 必须同时更新后端 Pydantic 模型和前端 TS 类型，CI 通过 `openapi-typescript` 自动生成强制一致

### 3.4.2 HTTP 标准头统一约定（前后端契约）

| 头 | 方向 | 必需 | 用途 |
|---|---|---|---|
| `Authorization: Bearer <JWT>` | 请求 | 登录后必须 | JWT access token，验证见 §5.13 |
| `X-Trace-Id` | 请求 + 响应 | 可选（缺则后端生成 ULID） | 跨服务追踪；响应回写让前端 Toast 可复制 |
| `Idempotency-Key` | 请求 | 写操作推荐 | 防重复；服务端缓存响应 24h |
| `X-OTP-Code` | 请求 | 高危操作必需 | 短信/邮件 OTP 二次验证（§5.13）|
| `Cache-Control` | 响应 | 写操作必加 `no-store` | 防浏览器/中间代理缓存（M-B-06）|

**ID 格式统一**：`trace_id` / `cmd_id` / `jti` / `alarm_event_id` 全部使用 **ULID**（26 字符 Crockford Base32，时间可排序）。
```

### 3.3 告警触发与通知路径

```
gw 解析帧 → domain/alarm_engine
             │（查 update_flag 5s 前加载的最新阈值）
             ├─ AlarmType 比对 > < = != LX
             ├─ LX 计数器：Redis Hash hincrby（崩溃不丢）§5.9
             ├─ 检查 RelationPointID
             ├─ 事务：UPDATE device_waring_cfgs.waring_flag + INSERT alarm_records
             └─ XADD stream:alarm:fired (event_id=alarm_record_id, ...)
   │
   ▼
api XREADGROUP api-alarm-consumer
   │  event_id 用 SET NX EX 86400 去重（§5.9）
   ├─ 查 user_alarm_list（按 usr_group 隔离，§3.7）
   └─ 并发 fan-out 通知适配器：
       ├─ wechat.send_template (msg_id 幂等)
       ├─ sms.send
       ├─ voice.call
       ├─ email.send
       │
       ├─ 全部成功 → XACK
       └─ 任一失败 → 进 retry 队列指数退避 5 次；最终失败 → XADD stream:dlq:alarm + meta 告警
```

**通知适配器接口**：`INotifier` Protocol，注册表按 `config.yaml` provider 字段加载，换供应商只改 yaml。

### 3.4 失败/降级矩阵

| 失败点 | 处置 |
|---|---|
| 设备 CRC 错 | 单帧丢、gw 计数，连续 5 次告警 |
| TCP 连接断 | 该 DTU 下所有设备标"通信异常"，gw 重连 |
| Redis 不可达 | gw 继续入库；api WS 中断、前端显示"数据延迟" |
| Postgres 不可达 | gw 内存缓冲 5min，超时丢 + 告警 |
| 通知供应商挂 | 适配器内重试 5 次；其他渠道不阻塞 |
| 网关重启 | 设备自动重连；离线命令队列 Redis 持久化不丢 |
| api 重启 | WS 自动重连；gw 侧控制命令继续执行 |

### 3.5 微信支付回调验签

**每次收到 `/wechat/pay/notify` 必须按如下顺序验证**（B-S5 硬规范）：

```python
async def on_wx_pay_notify(request):
    # 1. 先读原始 form，但**不信任**任何字段直到签名通过
    raw = await request.form()
    received_sign = raw.get("sign")
    if not received_sign:
        return xml_fail("missing sign")

    # 2. 签名验证（基于原始字段 dict，排除 sign 本身）
    params = {k: v for k, v in raw.items() if k != "sign"}
    expected = wechat_pay_sign(params, api_v3_key)
    if not hmac.compare_digest(received_sign, expected):
        soft_log.warn("wxpay signature mismatch", remote=request.client.host)
        return xml_fail("bad signature")

    # 3. **签名通过后才**提取业务字段
    out_trade_no = params["out_trade_no"]
    total_fee    = int(params["total_fee"])
    time_end     = parse_wechat_time(params["time_end"])   # 微信格式 yyyyMMddHHmmss

    # 4. 时间戳窗口（5 min，防重放）
    if abs((datetime.utcnow() - time_end).total_seconds()) > 300:
        return xml_fail("timestamp out of window")

    # 5. 金额非负 + 业务匹配
    if total_fee < 0:
        return xml_fail("invalid fee")

    # 6. 幂等（INSERT ON CONFLICT，事务内）+ 业务处理
    async with db.transaction():
        ok = await db.execute(
            "INSERT INTO pay_orders_seen(out_trade_no, notified_at) VALUES ($1, NOW()) "
            "ON CONFLICT (out_trade_no) DO NOTHING", out_trade_no
        )
        if not ok.rows_affected:
            return xml_ok()  # 已处理，幂等返回成功

        await mark_paid(out_trade_no, total_fee, time_end)

    return xml_ok()
```

新增辅助表：
```sql
CREATE TABLE pay_orders_seen (
  out_trade_no VARCHAR(50) PRIMARY KEY,
  notified_at  timestamptz NOT NULL DEFAULT now()
);
```
用于微信回调幂等防护；每日凌晨清理 30 天以前记录。

### 3.6 权限矩阵（4 角色 × 14 模块）

**四级 RBAC**（源自 v2.2 功能全景 §3.6）：

| 角色 | 标识 | 数据边界 | 典型职能 |
|---|---|---|---|
| L4 平台超管 | `Administrators` | 跨租户（全 WXGroup）可见 | 平台运营、WXGroup 配置、跨租户审计；所有跨租户操作**强制 OTP** |
| L3 集团管理 | `GroupCompany` | 本集团 GroupCompany 所有 Company / Department | 集团级资产与权限配置 |
| L2 公司管理 | `Company` | 本 Company / Department | 部门设备配置、操作员管理 |
| L1 普通用户 | `User` | 本人绑定设备 | 日常监控、告警复位 |

**控制权限**（`control_authority` 位掩码，§4.2 DDL 已支持）：
- bit 0 (0x01)：设备控制权（启停 / 下寄存器写）
- bit 1 (0x02)：配置管理权（改阈值、改通道名、加删设备）
- bit 2 (0x04)：**高危控制标识**（触发 OTP 二次确认，见 §3.2）
- bit 3–7：预留

**权限矩阵**（14 业务模块 × 4 角色，符号：✓=可操作，R=只读，▲=有限制，×=禁止）：

| 模块 | 子项 | L4 | L3 | L2 | L1 | 备注 |
|---|---|---|---|---|---|---|
| 1 认证 | 所有 | ✓ | ✓ | ✓ | ✓ | — |
| 2 门户 | 设备树 | ✓ | ▲集团 | ▲公司 | ▲自有 | ▲ = 仅本租户/本层级 |
| 3 实时监控 | 查看全部 | ✓ | R(本集团) | R(本公司) | R(自有) | — |
| 4 远程控制 | 广播/单点 | ✓ | ✓ | ▲(CA bit0) | × | L2/L1 需 `control_authority & 0x01` |
| 4 远程控制 | 高危操作 | ✓(OTP) | ✓(OTP) | × | × | 需 CA bit2 + OTP 短信码 |
| 5 历史/报表 | 查询 | ✓ | ✓ | ✓ | ▲自有 | — |
| 5 报表 | Excel 导出 | ✓ | ✓ | ✓ | R | — |
| 6 波形 | 查看/对比 | ✓ | ✓ | ✓ | ▲ | — |
| 7 告警 | 查询/复位 | ✓ | ✓ | ✓ | ▲自有 | — |
| 7 告警 | 阈值配置 | ✓ | ✓ | ▲(CA bit1) | × | — |
| 7 告警 | 通讯录 | ✓ | ✓ | ✓ | R | — |
| 8 设备配置 | 比例/字段 | ✓ | ✓ | ▲(CA bit1) | × | 原硬编码特权号 18264192756 已废弃（D1），走 L4 |
| 8 设备配置 | 添加/删除 | ✓ | ✓ | ✓ | × | — |
| 9 保养 | 查询/增删 | ✓ | ✓ | ✓ | ▲自有 | L1 只能对"本人绑定设备"的保养计划增删 |
| 10 定时计划 | 增删启停 | ✓ | ✓ | ✓ | × | — |
| 11 组态 | 查看 | ✓ | ✓ | ✓ | ▲ | — |
| 11 组态 | 编辑 | ✓ | ✓ | ✓ | × | — |
| 12 用户/组织 | 本租户 | ✓ | ✓ | ▲本公司 | × | — |
| 12 用户/组织 | 跨租户 | ✓(OTP) | × | × | × | — |
| 12 用户/组织 | 创建集团/平台角色 | ✓(OTP) | × | × | × | — |
| 13 支付充值 | 充值 | ✓ | ✓ | ✓ | ✓ | 充值对象为自有设备 |
| 13 支付充值 | 订单查询 | ✓ | ✓ | ▲本公司 | ▲本人 | — |
| 14 运维/审计 | SoftLog/ControlAction | ✓ | R(本集团) | R(本公司) | × | 审计员只读 |
| 14 运维 | WXGroup 配置 | ✓(OTP) | × | × | × | — |
| 14 运维 | `/admin/log/*` 动态调级 | ✓(OTP) | × | × | × | — |

**L4 操作 OTP 规则**：所有带 `(OTP)` 标记的操作须在执行前调用 `POST /api/auth/otp/send`（短信/邮件），用户提交 OTP 后 5 min 内有效。

### 3.7 多租户强制隔离（B-B1 + B-S7 合并）

**三层防护**（缺一不可）：

**1. PostgreSQL RLS（数据库级兜底）**
```sql
-- 对所有业务表（带 usr_group 字段的）启用 RLS
ALTER TABLE devices ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON devices
  USING (usr_group = current_setting('app.tenant_id', true)
         OR current_setting('app.role', true) = 'Administrators');
-- 类似策略应用于 users, device_waring_cfgs, alarm_records, user_control_actions...
```

应用侧在每次连接/事务开始时：
```sql
SET LOCAL app.tenant_id = :current_user_usr_group;
SET LOCAL app.role = :current_user_authority;
```

**2. ORM 层自动过滤（应用级强制）**
```python
# core/tenant.py
from sqlalchemy import event

@event.listens_for(Query, "before_compile", retval=True)
def inject_tenant_filter(query):
    if query.column_descriptions[0]["type"] in TENANT_TABLES:
        usr_group = request_context.get("usr_group")
        if not usr_group and request_context.get("role") != "Administrators":
            raise TenantIsolationError("query without tenant context")
        if usr_group:
            query = query.filter(model.usr_group == usr_group)
    return query
```

**3. CI lint + 负向测试（代码审查级兜底）**
- CI 脚本扫描：每个业务表查询是否带 `WHERE usr_group` 或走 RLS（检查 pg_stat_statements）
- 每个资源 endpoint 必须有对应负向测试：UserA(tenantA) 访问 UserB(tenantB) 资源应返回 403

**违规处置**：生产检测到"无 usr_group 过滤的查询" → 立刻 P0 告警 + 自动封禁该进程。

**软删 + FK 语义**（v1.3.3 新增）：
- FK 仅检查行存在性，**不检查 deleted_at**；业务查询必须 JOIN WHERE deleted_at IS NULL
- 禁止依赖 `ON DELETE RESTRICT` 做业务级联（所有表用软删，级联永不触发）
- UI 列表遇到引用已软删行时，降级显示 `LEFT JOIN + COALESCE(target.name, '已删除') AS display_name`

**gw 服务账号**（v1.3.3 新增）：
- `CREATE ROLE ruisheng_gw BYPASSRLS LOGIN PASSWORD '...'`（gw 是后台服务，无 HTTP 上下文）
- 所有 gw SQL 由 CI lint 强制带 `WHERE usr_group = ?` 过滤（负向 test：构造跨租户 join，断言拒绝合并）
- api 继续用普通角色 + `SET LOCAL app.tenant_id` 走 RLS

### 3.8 边界情况与数据流细则（v1.3.2 新增）

> 本节汇总审查发现的边界/竞态/降级规则。每条明确"触发条件 / 系统行为 / 用户感知"。

#### 3.8.1 控制命令并发与冲突（E1）

**场景**：两用户同时点同一设备同一操作（或冲突操作）。

**规则**：
- 单设备控制命令串行执行：gw 的离线命令队列和 poller 队列都按 **cmd_id ULID 时间序**（ULID 内含毫秒时间戳）FIFO
- 相同语义重复命令（同 user、同 action 5s 内）→ api 层直接拒绝 409 "操作进行中，请等待"
- 冲突命令（如"启动"与"停止"同时到达）→ 全部入队顺序执行；前端 UI 基于 `last_state` 灰化不合理按钮但不强制（规则归属业务判断）
- **控制期间点位值变化锁**：gw 下发 cmd_X 到收到 ACK 之前，对同设备的**新控制命令**延后处理（读命令不受限）

#### 3.8.2 同帧触发多告警（E2 + E8）

**场景**：一次 FunCode 3 响应包含 20 个点位，其中 5 个越限。

**规则**：
- **每个 (point_id, alarm_cfg_id) 独立产生 AlarmEvent**（不合并），分别 XADD 到 `stream:alarm:fired`
- 同点位多条规则同时命中（`>80` 和 `LX`）→ 分别产生两个 event（规则 id 不同）
- api 侧通知通道做"合并窗口"：同 dev_number 同 user 在 3s 内的多条告警聚合为 1 条微信/短信（聚合文案："XX 设备 3 条告警：...")
- 邮件/IVR 电话保持不聚合（各自独立）

#### 3.8.3 Redis Streams 容量满（E3 + F9）

**场景**：`stream:alarm:fired MAXLEN ~100000` 达到上限。

**规则**：
- `XADD ... MAXLEN ~` 是**近似裁剪**，不会阻塞 XADD（只丢最老消息）
- **PEL 监控分级**：
  - 未消费堆积 > 1,000 → Grafana 告警（消费者跟不上）
  - > 10,000 → P0 告警 + 自动触发 DLQ 扩容或加速消费（`XREADGROUP COUNT` 扩到 200）
  - > 100,000 → 主动扩机（发信号让 api 副本扩展）
- 消费者失败 5 次后直接进 DLQ（§5.9 已规范），不阻塞主流
- `stream:control:cmd` 容量 50,000，同策略

#### 3.8.4 WS 慢消费者（E4）

**场景**：浏览器 tab 后台化 / 网络抖动 / client buffer 满。

**规则**：
- api 侧每个 WS 连接有**单独的发送队列**（`asyncio.Queue(maxsize=500)`）
- 队列满 → **丢弃最老实时数据**（允许丢，§1.3 已决策），**不丢告警和 control_result**（分队列处理）
- 连续 3 次发送失败（5s 内） → 主动关闭 WS，让客户端重连
- 客户端重连后 → 订阅 `channel:realtime:{dev_number}` + 补拉最近 N 条 `alarm_records`（近 5 min）
- 移动端 tab 后台化 → 心跳 `type:"ping"` 每 30s，长时间（>5min）后台 → 服务端主动断连省资源

#### 3.8.5 离线命令队列过期通知（E5）

**场景**：控制命令入离线队列 10min TTL 到期，设备仍未上线。

**规则**：
- gw 定期（每 30s）扫离线队列 → 将超时命令标 `cancelled`，更新 `user_control_actions.result='cancelled'`, `completed_at=now()`
- XADD `stream:alarm:fired` event_type="command_cancelled"（让 api 通过 WS 和告警通道告知用户）
- WS 推 `{type: "control_result", cmd_id, status: "cancelled", at, reason: "设备离线超过 10 分钟"}`
- 前端 Toast 显示并变红色，提醒用户

#### 3.8.6 DevSerNumber 冲突与身份退化（E6）

**场景**：两台设备产线失误用同一序列号。

**规则**：
- 主键 UNIQUE `(dev_ser_number, iccid)` 起保护作用：序列号相同但 iccid 不同 → 视为两台设备
- 同序列号同 iccid → 视为同一设备（产线事故需人工）
- 注册时 DB 发现冲突 → gw 返回 FunCode 21 错误响应，并写 `soft_logs` ERROR + 告警
- 运维处置：手工改其中一台的 iccid 或 dev_number 后重新出厂烧录

#### 3.8.7 JWT 长操作中过期（E7）

**场景**：用户填长表单 20 分钟后提交，access token 15min 已过期。

**规则**：
- 前端 axios 拦截器检测到 401 → 自动用 `refresh_token` 请求 `/api/auth/refresh`
- 成功 → 重试原请求
- refresh 失败（refresh 也过期）→ 暂存当前未提交表单到 `localStorage`，跳登录页，登录成功后恢复表单
- 所有 POST 类关键表单支持 `draft_id` 自动保存（每 30s 草稿写后端），防止提交失败彻底丢失

#### 3.8.8 历史查询大数据集（E9）

**场景**：前端请求 `/api/devices/X/history?from=2026-01-01&to=2026-04-13`。

**规则**：
- **强制分页**：单次最多返回 5000 行（带 `next_cursor`）
- 超过 1 天范围的查询**自动降采样**：查询参数 `sample_interval_s` 默认按时长计算（1 天=30s，7 天=5min，30 天=1h）
- 响应头 `X-Downsampled: true` + `X-Sample-Interval-S: 300` 告知前端
- 原始数据导出走异步任务：`POST /api/exports` → 生成 Parquet 上对象存储 → 邮件/WS 通知完成
- 长查询超时 30s → 返回 `-200 timeout, 请缩小范围或用导出功能`

#### 3.8.9 归档 Job 与查询并发（E10）

**场景**：归档 Job 正在对某月 chunk 启用压缩，同时查询命中该 chunk。

**规则**（TimescaleDB 原生支持）：
- `compress_chunk` 操作持有 `AccessExclusiveLock` → 查询需等待或超时
- 我们的约束：归档 Job 只动 ≥ 7 天前的 chunk（§5.10 已定），实际业务查询以近 1–3 天为主，冲突概率低
- 查询侧设置 `SET LOCAL lock_timeout = '5s'` → 超时返回 `-301 DB 忙，请稍后重试`
- 归档 Job 窗口固定在 02:00–04:00，与业务低峰对齐

#### 3.8.10 配置变更 Pub/Sub 丢失的兜底（F5）

**场景**：gw 刚好断 Redis 错过了 `channel:config:changed` 广播。

**规则**：
- gw 重连 Redis 后，**立即全量扫 `devices WHERE update_flag > 0`** 拉取所有待更新配置（config_watcher 兜底）
- 每 5 min 定期也扫一次（防止 Redis 消息真丢）
- `update_flag` 在 gw 应用完配置后由 gw 自己 clear → api 侧读取到 0 代表"已同步"

#### 3.8.11 告警事务与 Stream 发布原子性（F4）

**场景**：事务 INSERT alarm_records 成功，紧接的 XADD stream 失败。

**规则**（双写一致性 → 改为 Outbox 模式）：
```sql
-- 新增 outbox 表（第 22 张）
CREATE TABLE alarm_outbox (
  id          BIGSERIAL PRIMARY KEY,
  alarm_id    BIGINT NOT NULL REFERENCES alarm_records(id),
  payload     JSONB NOT NULL,
  published   BOOLEAN NOT NULL DEFAULT false,
  created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON alarm_outbox (published, created_at) WHERE published = false;
```
gw 侧：
```python
async with db.transaction():
    alarm_id = await db.insert_alarm(...)
    await db.insert_outbox(alarm_id, payload)        # 与 alarm_records 同事务
# 事务外：独立 relay 协程扫 outbox published=false → XADD → 成功后 UPDATE published=true
```
保证："落库成功但告警未发"这种半状态会被 relay 自动补偿。

#### 3.8.12 cmd_id 跨重启去重（F3）

gw 重启后内存 LRU 丢失 → 改为 **Redis `SET NX EX 86400`**：
```python
ok = await redis.set(f"cmd_seen:{cmd_id}", "1", nx=True, ex=86400)
if not ok:
    await xack_and_skip(cmd_id)   # 已处理过，幂等
```

#### 3.8.13 广播帧地址 0（F7）

**规则**：gw **拒收地址 0 的 ModBus 帧**（log WARN）。理由：新系统不使用 ModBus 广播语义，所有下行都针对具体 RTU 地址。

#### 3.8.14 同 DTU 多设备帧交错（F8）

**规则**：
- 现状约束 `SILENCE_MS=200` 保证 RS485 半双工下每个设备回复完才下一个
- 但 TCP 透传层如果 DTU 内部并发（少见），gw 侧依赖**帧 CRC + 地址字段**识别归属
- 解析出的帧按 `dev_addr` 路由到对应设备的 `Device` 对象（而非按 TCP 连接）
- CRC 错帧丢弃 + 累计计数（§5.2）

#### 3.8.15 波形 BLOB 容错解码（F6）

**规则**：
```python
def decode_waveform(blob: bytes) -> tuple[list[float], int]:
    if len(blob) < 2:
        raise ProtocolError("waveform blob truncated header")
    sample_time = blob[0]
    packet_count = blob[1]
    expected_len = 2 + packet_count * sizeof(value_type)
    if len(blob) < expected_len:
        raise ProtocolError(f"waveform truncated: expect {expected_len}, got {len(blob)}")
    # 严格解析；解析失败 → 丢弃单条，不写 DB，log WARN（§5.2 CRC 错同级别处置）
```

#### 3.8.16 用户注销的级联处理（F10）

**场景**：用户主动注销。

**规则**：
- 软删除 `users.deleted_at = now()`
- 吊销所有 JWT（jti 黑名单 7 天）
- 用户进行中的控制命令：保持原状态继续执行（审计留档）
- 用户订阅的告警通道：从 `user_phone_numbers` / `user_emails` / `user_wx_bindings` 级联删除
- 该用户归属的设备 → 归还给上级管理员（L2 → L3 → L4）
- 保留 `user_control_actions` 审计记录 3 年（合规），显示为"已注销用户 XXX（脱敏）"
- `maintain_actions` 审计记录 **同款规则**：保留 3 年 + 脱敏显示（v1.3.3 追加）

#### 3.8.17 保养计划推进状态机（v1.3.3 新增）

**场景**：师傅 App 点"完成保养"。

**推进公式（并发安全）**：
```
next_due_at_new = max(now(), next_due_at_old) + interval_days * INTERVAL '1 day'
```
- **禁止累加式** `next_due_at + interval`（漏保养一周后做只推进一个周期，导致漏检永远推迟）
- `max(now(), old)` 保证即使超期后补做，下次也从 now() 起算

**事务序**：
```
BEGIN;
  SELECT * FROM maintain_plans WHERE id = :plan_id FOR UPDATE;  -- 行锁防并发
  INSERT INTO maintain_actions (action_uuid, plan_id, dev_number, user_name, usr_group)
    VALUES (...) ON CONFLICT (action_uuid) DO NOTHING;          -- 幂等（弱网双击）
  UPDATE maintain_plans
    SET next_due_at = GREATEST(now(), next_due_at) + make_interval(days => interval_days),
        updated_at = now()
    WHERE id = :plan_id;
COMMIT;
```

**action_uuid 约定**：前端生成 ULID（26 字符），网络失败重试时复用同一 uuid → `ON CONFLICT DO NOTHING` 幂等。

**时间容差**（v1.3.3 通用约定）：所有表单时间字段允许 ≤ 60s 早于服务端 now()（应对浏览器时钟偏差）；CHECK 约束写作 `next_due_at >= created_at - INTERVAL '1 minute'`。

---

## §4 数据模型

### 4.1 命名与通用规范

| 项 | 规范 |
|---|---|
| 表/字段 | `snake_case` |
| 主键 | `id BIGSERIAL` + 业务键 UNIQUE |
| 时间 | 统一 `timestamptz`，存 UTC；系统假定 `TZ='Asia/Shanghai'`，无 DST |
| 多租户 | 每张业务表强制 `usr_group VARCHAR(50) NOT NULL REFERENCES wx_groups(usr_group)` + INDEX |
| 软删除 | `deleted_at timestamptz NULL`（审计类表除外，详见 §4.1.1） |
| 审计字段 | `created_at` / `updated_at`（触发器 `trg_<table>_updated` 维护；见 §4.1.1） |
| 字符串归一 | VARCHAR 入库前在 API 层 `strip()` + NFC normalize；DB 不做 trim |
| 字符集 | UTF8，collation `zh-x-icu` |
| FK 命名 | 不在 DDL 写 `CONSTRAINT fk_...` 名，交 Alembic `naming_convention` 生成（`fk_<table>_<col>_<referred>`） |
| CHECK 命名 | 同上，交 naming_convention（`ck_<table>_<constraint_name>`） |
| Index 命名 | 显式 `ix_<table>_<purpose>` / `ux_<table>_<purpose>`（partial unique） |
| RLS policy | 所有表同名 `tenant_isolation`（模板见 §3.7）|
| Trigger 命名 | `trg_<table>_updated`（updated_at 自动维护） |
| 时间容差 | 所有表单时间字段：CHECK `target_time >= created_at - INTERVAL '1 minute'` |

### 4.1.1 通用设施（v1.3.3 新增）

```sql
-- (1) 通用 updated_at 触发器函数（被每张含 updated_at 的表的触发器复用）
CREATE OR REPLACE FUNCTION set_updated_at() RETURNS TRIGGER AS $$
BEGIN NEW.updated_at = now(); RETURN NEW; END $$ LANGUAGE plpgsql;

-- (2) gw 服务账号（BYPASSRLS，SQL 由 CI lint 强制带 WHERE usr_group）
CREATE ROLE ruisheng_gw BYPASSRLS LOGIN PASSWORD '<prod-secret>';
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO ruisheng_gw;

-- (3) api 服务账号（走 RLS，每事务 SET LOCAL 两个变量）
CREATE ROLE ruisheng_api LOGIN PASSWORD '<prod-secret>';
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO ruisheng_api;
-- api 每个请求 begin tx 后：
--   SET LOCAL app.tenant_id = '<user.usr_group>';
--   SET LOCAL app.role      = '<user.authority>';  -- Administrators/GroupCompany/Company/User
```

### 4.2 核心表 DDL 节选

完整 DDL 将在 implementation plan 阶段生成为 `alembic/versions/001_initial.py`。此处保留 8 张关键表示例（其他在 §4.5 列清单）。

```sql
CREATE TABLE wx_groups (
  usr_group        VARCHAR(50) PRIMARY KEY,
  appid            VARCHAR(50),
  appsecret        VARCHAR(100),
  token            VARCHAR(200),
  token_expires_at timestamptz,
  template_id      VARCHAR(50),
  company_name     VARCHAR(100),
  sys_title        VARCHAR(100),
  remark           VARCHAR(255),
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE users (
  id                BIGSERIAL PRIMARY KEY,
  user_name         VARCHAR(50) UNIQUE NOT NULL
                    CHECK (user_name ~ '^1[3-9][0-9]{9}$'   -- 中国大陆手机号
                           OR user_name ~ '^[a-zA-Z][a-zA-Z0-9_]{3,29}$'),  -- 或预置账号
  password_hash     VARCHAR(100) NOT NULL,     -- bcrypt
  login_name        VARCHAR(50),
  group_company     VARCHAR(100),
  company           VARCHAR(100),
  department        VARCHAR(100),
  authority         VARCHAR(20) NOT NULL CHECK
                     (authority IN ('Administrators','GroupCompany','Company','User')),
  control_authority SMALLINT NOT NULL DEFAULT 0,   -- 位掩码
  sys_name          VARCHAR(50),
  usr_group         VARCHAR(50) NOT NULL REFERENCES wx_groups(usr_group),
  deleted_at        timestamptz,
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_users_tenant ON users(usr_group);

CREATE TABLE devices (
  id                      BIGSERIAL PRIMARY KEY,
  dev_number              VARCHAR(50) UNIQUE NOT NULL,
  dev_ser_number          VARCHAR(50) NOT NULL,
  iccid                   VARCHAR(50),
  dev_name                VARCHAR(100),
  dev_type                VARCHAR(50),
  modbus_addr             SMALLINT NOT NULL CHECK (modbus_addr BETWEEN 1 AND 247),
  baud_rate               INT CHECK (baud_rate IN (9600, 19200, 38400, 57600, 115200)),
  group_company           VARCHAR(100),
  company                 VARCHAR(100),
  department              VARCHAR(100),
  administrators          VARCHAR(50) REFERENCES users(user_name),
  dev_ip                  INET,
  code_file               VARCHAR(255),
  code_updated_at         timestamptz,
  update_interval_decisec INT NOT NULL DEFAULT 100,   -- D6: 终端级轮询 [10..1000] = 1.0..100.0s
  last_call_at            timestamptz,
  last_back_at            timestamptz,
  loss_count              INT NOT NULL DEFAULT 0,
  is_online               BOOLEAN NOT NULL DEFAULT false,
  last_state              JSONB,                      -- 关键点位快照 {"<point_id>": <value>, "_at": "<ISO ts>"}；控制前冲突检测用（M-S-07）
  update_flag             INT NOT NULL DEFAULT 0,
  usr_group               VARCHAR(50) NOT NULL REFERENCES wx_groups(usr_group),
  deleted_at              timestamptz,
  created_at              timestamptz NOT NULL DEFAULT now(),
  updated_at              timestamptz NOT NULL DEFAULT now(),
  UNIQUE (dev_ser_number, iccid),
  CHECK (update_interval_decisec BETWEEN 10 AND 1000)
) WITH (
  fillfactor = 80,                            -- last_call_at / loss_count 高频 UPDATE
  autovacuum_vacuum_scale_factor = 0.05
);

CREATE TABLE device_points (
  id                BIGSERIAL PRIMARY KEY,
  dev_number        VARCHAR(50) NOT NULL REFERENCES devices(dev_number) ON DELETE CASCADE,
  point_name        VARCHAR(100) NOT NULL,
  user_point_name   VARCHAR(100),
  point_number      INT NOT NULL CHECK (point_number BETWEEN 0 AND 65535),  -- ModBus 寄存器地址
  fun_code          SMALLINT NOT NULL CHECK (fun_code IN (1, 2, 3, 4)),     -- 仅"读"类
  dev_addr          SMALLINT NOT NULL,
  r_bit             SMALLINT,                    -- 位寻址
  value_type        VARCHAR(20) NOT NULL,        -- '字' / '双字' / 'bit'
  point_unit        VARCHAR(20),
  point_ratio       DOUBLE PRECISION DEFAULT 1,
  point_offset      DOUBLE PRECISION DEFAULT 0,
  user_ratio        DOUBLE PRECISION DEFAULT 1,
  user_point_offset DOUBLE PRECISION DEFAULT 0,
  min_value         DOUBLE PRECISION,
  max_value         DOUBLE PRECISION,
  show              SMALLINT NOT NULL DEFAULT 1,
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now()
  -- 注：轮询间隔在 devices.update_interval_decisec（终端级，D6）
);

CREATE TABLE device_waring_cfgs (
  id                   BIGSERIAL PRIMARY KEY,
  dev_number           VARCHAR(50) NOT NULL REFERENCES devices(dev_number) ON DELETE CASCADE,
  point_id             BIGINT NOT NULL REFERENCES device_points(id) ON DELETE CASCADE,
  reg_bit              SMALLINT,
  alarm_name           VARCHAR(100) NOT NULL,
  alarm_type           VARCHAR(4) NOT NULL CHECK (alarm_type IN ('>','<','=','!=','LX')),
  limit_value          DOUBLE PRECISION NOT NULL CHECK ('NaN' != limit_value::text AND 'Infinity' != abs(limit_value)::text),
  relation_point_id    BIGINT REFERENCES device_points(id),
  relation_reg_bit     SMALLINT,
  relation_alarm_type  VARCHAR(4),
  relation_limit_value DOUBLE PRECISION,
  enable               BOOLEAN NOT NULL DEFAULT true,
  phone_alarm          INT NOT NULL DEFAULT 0,
  reset_remind         BOOLEAN NOT NULL DEFAULT false,
  dev_sync_flag        SMALLINT NOT NULL DEFAULT 0,
  waring_flag          BOOLEAN NOT NULL DEFAULT false,
  alarm_msg            VARCHAR(255),
  created_at           timestamptz NOT NULL DEFAULT now(),
  updated_at           timestamptz NOT NULL DEFAULT now()
);

-- 实时覆盖写（UPDATE-heavy，必须强化 autovacuum 防膨胀）
CREATE TABLE point_data_realtime (
  dev_number   VARCHAR(50) NOT NULL,
  point_id     BIGINT NOT NULL,
  org_value    DOUBLE PRECISION,
  rt_value     DOUBLE PRECISION,
  recorded_at  timestamptz NOT NULL,
  PRIMARY KEY (dev_number, point_id)
) WITH (
  fillfactor = 70,                            -- 留 30% 空间给 HOT 更新
  autovacuum_vacuum_scale_factor = 0.05,      -- 5% 死行就 vacuum（默认 20% 太晚）
  autovacuum_analyze_scale_factor = 0.02,
  autovacuum_vacuum_cost_limit = 1000,        -- 提高 vacuum 速度
  autovacuum_vacuum_insert_scale_factor = 0.1
);

-- 历史（TimescaleDB hypertable）
CREATE TABLE point_data_history (
  dev_number    VARCHAR(50)    NOT NULL,
  point_id      BIGINT         NOT NULL,
  org_value     DOUBLE PRECISION,
  rt_value      DOUBLE PRECISION,
  recorded_at   timestamptz    NOT NULL
);
SELECT create_hypertable('point_data_history', 'recorded_at',
  chunk_time_interval => INTERVAL '1 month');
CREATE INDEX ON point_data_history (dev_number, point_id, recorded_at DESC);
SELECT add_retention_policy('point_data_history', INTERVAL '1 year');

-- 波形 BLOB（TimescaleDB hypertable）
CREATE TABLE waveform_history (
  dev_number          VARCHAR(50)    NOT NULL,
  point_id            BIGINT         NOT NULL,
  data_array          BYTEA          NOT NULL,
  tz_data_array       BYTEA,
  sample_time_decisec SMALLINT       NOT NULL,
  packet_count        SMALLINT       NOT NULL,
  recorded_at         timestamptz    NOT NULL
);
SELECT create_hypertable('waveform_history', 'recorded_at',
  chunk_time_interval => INTERVAL '1 month');
SELECT add_retention_policy('waveform_history', INTERVAL '1 year');

-- 告警合表
CREATE TABLE alarm_records (
  id            BIGSERIAL PRIMARY KEY,
  dev_number    VARCHAR(50) NOT NULL,
  point_id      BIGINT,
  alarm_name    VARCHAR(100),
  alarm_msg     VARCHAR(255),
  alarm_value   DOUBLE PRECISION,
  channels_sent JSONB NOT NULL DEFAULT '{}'::jsonb,
  triggered_at  timestamptz NOT NULL,
  reset_at      timestamptz,
  usr_group     VARCHAR(50) NOT NULL
);
CREATE INDEX ON alarm_records (dev_number, triggered_at DESC);

CREATE TABLE user_control_actions (
  id         BIGSERIAL PRIMARY KEY,
  dev_number VARCHAR(50) NOT NULL,
  user_name  VARCHAR(50) NOT NULL,
  action     JSONB NOT NULL,                     -- {fun_code, reg, value, ...}
  cmd_id     VARCHAR(32) UNIQUE,                 -- ULID；关联 stream:control:cmd & WS control_result
  result     VARCHAR(20) CHECK
               (result IN ('pending','success','failed','timeout','cancelled')),
  acted_at   timestamptz NOT NULL DEFAULT now(),
  completed_at timestamptz,                      -- 设备 ACK 或失败终态时间
  usr_group  VARCHAR(50) NOT NULL
);
CREATE INDEX ON user_control_actions (dev_number, acted_at DESC);
CREATE INDEX ON user_control_actions (user_name, acted_at DESC);

-- timing_plans（v1.3.3 扩展：补 usr_group / deleted_at / updated_at / RLS / 索引）
CREATE TABLE timing_plans (
  id          BIGSERIAL PRIMARY KEY,
  dev_number  VARCHAR(50) NOT NULL REFERENCES devices(dev_number) ON DELETE RESTRICT,
  action_at   timestamptz NOT NULL,
  action      INT NOT NULL,
  repetition  INT NOT NULL DEFAULT 0,
  enable      BOOLEAN NOT NULL DEFAULT true,
  update_flag SMALLINT NOT NULL DEFAULT 0,                        -- gw 感知配置变更
  usr_group   VARCHAR(50) NOT NULL REFERENCES wx_groups(usr_group) ON DELETE RESTRICT,
  deleted_at  timestamptz,
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_timing_plans_dev_action ON timing_plans (dev_number, action_at);
CREATE INDEX ix_timing_plans_due        ON timing_plans (action_at)
  WHERE enable = true AND deleted_at IS NULL;
CREATE INDEX ix_timing_plans_usr_group  ON timing_plans (usr_group);

CREATE TRIGGER trg_timing_plans_updated BEFORE UPDATE ON timing_plans
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

ALTER TABLE timing_plans ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON timing_plans USING (
  usr_group = current_setting('app.tenant_id', true)
  OR current_setting('app.role', true) = 'Administrators'
);

-- maintain_plans（v1.3.3 新增：保养计划模板；不下发 gw，故无 update_flag）
CREATE TABLE maintain_plans (
  id             BIGSERIAL PRIMARY KEY,
  dev_number     VARCHAR(50) NOT NULL REFERENCES devices(dev_number) ON DELETE RESTRICT,
  plan_name      VARCHAR(100) COLLATE "zh-x-icu" NOT NULL,
  description    VARCHAR(255),
  interval_days  INT NOT NULL CHECK (interval_days BETWEEN 1 AND 3650),
  next_due_at    timestamptz NOT NULL,
  enable         BOOLEAN NOT NULL DEFAULT true,
  usr_group      VARCHAR(50) NOT NULL REFERENCES wx_groups(usr_group) ON DELETE RESTRICT,
  deleted_at     timestamptz,
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now(),
  CHECK (next_due_at >= created_at - INTERVAL '1 minute')    -- 60s 容差，见 §4.1
);
CREATE INDEX ix_maintain_plans_dev_number       ON maintain_plans (dev_number);
CREATE INDEX ix_maintain_plans_usr_group        ON maintain_plans (usr_group);
CREATE INDEX ix_maintain_plans_next_due_active  ON maintain_plans (next_due_at)
  WHERE enable = true AND deleted_at IS NULL;
CREATE UNIQUE INDEX ux_maintain_plans_dev_plan_name
  ON maintain_plans (dev_number, plan_name) WHERE deleted_at IS NULL;

CREATE TRIGGER trg_maintain_plans_updated BEFORE UPDATE ON maintain_plans
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

ALTER TABLE maintain_plans ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON maintain_plans USING (
  usr_group = current_setting('app.tenant_id', true)
  OR current_setting('app.role', true) = 'Administrators'
);

-- maintain_actions（v1.3.3 新增：保养执行审计；一次写入 + action_uuid 幂等）
CREATE TABLE maintain_actions (
  id           BIGSERIAL PRIMARY KEY,
  action_uuid  VARCHAR(26) NOT NULL UNIQUE,               -- ULID，前端生成；弱网幂等
  plan_id      BIGINT NOT NULL,                           -- 弱引用：审计永久留痕，不设 FK
  dev_number   VARCHAR(50) NOT NULL,                      -- 冗余：设备删后仍可审计
  acted_at     timestamptz NOT NULL DEFAULT now(),
  user_name    VARCHAR(50) NOT NULL REFERENCES users(user_name) ON DELETE RESTRICT,
  note         VARCHAR(1000),                             -- 保养备注（师傅文字记录）
  usr_group    VARCHAR(50) NOT NULL REFERENCES wx_groups(usr_group) ON DELETE RESTRICT
);
CREATE INDEX ix_maintain_actions_plan_acted ON maintain_actions (plan_id,    acted_at DESC);
CREATE INDEX ix_maintain_actions_dev_acted  ON maintain_actions (dev_number, acted_at DESC);
CREATE INDEX ix_maintain_actions_user_acted ON maintain_actions (user_name,  acted_at DESC);
CREATE INDEX ix_maintain_actions_usr_group  ON maintain_actions (usr_group);

ALTER TABLE maintain_actions ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON maintain_actions USING (
  usr_group = current_setting('app.tenant_id', true)
  OR current_setting('app.role', true) = 'Administrators'
);

CREATE TABLE pay_orders (
  out_trade_no VARCHAR(50) PRIMARY KEY,
  openid       VARCHAR(100) NOT NULL,
  total_fee    INT NOT NULL CHECK (total_fee >= 0),
  body         VARCHAR(255),
  pay_state    VARCHAR(20) NOT NULL DEFAULT 'pending'
                CHECK (pay_state IN ('pending','paid','failed','refund')),
  created_at   timestamptz NOT NULL DEFAULT now(),
  paid_at      timestamptz,
  CHECK ((pay_state = 'paid') = (paid_at IS NOT NULL))
);

CREATE TABLE soft_logs (
  id          BIGSERIAL PRIMARY KEY,
  level       VARCHAR(10) NOT NULL,
  msg         VARCHAR(500) NOT NULL,
  context     JSONB,
  recorded_at timestamptz NOT NULL DEFAULT now()
);
SELECT create_hypertable('soft_logs', 'recorded_at',
  chunk_time_interval => INTERVAL '1 month');
SELECT add_retention_policy('soft_logs', INTERVAL '1 year');
```

### 4.3 表清单总览（共 26 张，v1.3.3 修正计数）

```
租户 (1):   wx_groups
用户 (4):   users, user_wx_bindings, user_phone_numbers, user_emails
设备 (6):   devices, device_points, device_waring_cfgs,
            device_static_data, sim_cards, device_templates
时序 (3):   point_data_realtime, point_data_history*, waveform_history*
告警 (2):   alarm_records, alarm_outbox（§3.8.11 事务 outbox）
控制 (1):   user_control_actions
计划 (3):   timing_plans, maintain_plans, maintain_actions
组态 (2):   scene_pages, scene_views
支付 (2):   pay_orders, pay_orders_seen
日志 (2):   soft_logs*, user_login_records*

* = TimescaleDB hypertable，按月分块 + 1 年自动滚动
```

### 4.4 不继承的旧遗产（显式声明）

| 旧遗产 | 新系统态度 | 原因 |
|---|---|---|
| `GroupID` 端口偏移机制（`6000 + GroupID`）| ❌ 不继承 | 旧系统用它在同机跑多实例；新系统用 NSSM 多服务/多配置解决，不需要偏移 |
| `MinTransactionCnt=1` 伪批量 | ❌ 丢弃 | 真批量 batch_writer，每 100ms flush 或满 500 行 |
| `charts_0..19` 服务端 PNG 目录 | ❌ 丢弃 | 前端 ECharts 渲染 |
| `C:\APP\` APK 路径、`C:\IOF\Execl\` 导出路径 | ❌ 丢弃 | 对象存储替代 |
| InProc Session | ❌ 丢弃 | JWT 替代 |
| 硬编码特权号 `18264192756` | ❌ 丢弃（D1）| RBAC 替代 |
| `FunCode 7 / 12` | ❌ 不做（D7）| 旧代码无 case 分支 |
| 21 个空壳 `.aspx` 页面 | ❌ 不做（D7）| 前端 SPA 自然替代 |
| `DEL_<num>_<ts>` 软删除前缀 hack | ❌ 不用 | `deleted_at` 字段替代 |
| 点位级 `UpdateInterval`（每个点独立间隔）| ❌ 不继承 | D6 改为**终端级**轮询；同一 ModBus 从站一次读多个寄存器是协议本来的样子，点位级间隔既违反物理又无意义 |
| 告警状态仅在内存（重启丢）| ❌ 不继承 | 告警 `waring_flag` 状态机持久化到 DB；LX 消抖计数器存 Redis（见 §5.9）|
| 控制命令走内存重试（重启丢）| ❌ 不继承 | 控制命令走 **Redis Streams**（at-least-once），崩溃可恢复（见 §5.9）|
| 告警事件走 Redis pub/sub（at-most-once）| ❌ 不继承 | `alarm:fired` 改为 **Redis Streams**，api 消费 ACK 后才删（见 §5.9）|
| DB 宕机后内存缓冲 5min 就丢 | ❌ 改进 | gw 侧写 **本地磁盘 WAL**，DB 恢复后重放（见 §5.11）|
| 被动 TCP 60s 超时（不发 keepalive）| ❌ 改进 | 应用层 **双向心跳**（见 §5.11）|

### 4.5 遗产数据迁移策略

**迁移对象**：旧 `ModBus.mdf`（1.27 GB 主库）中的业务数据 → 新 PostgreSQL。历史库 `ModBusHis.mdf` 基本为空（Q-D01 需 DBA 确认）。

**阶段**

| 阶段 | 动作 | 时点 |
|---|---|---|
| P-0 准备 | DBA attach 旧 mdf → 在 Windows SQL Server 中可连接状态 | Sprint 1 首周 |
| P-1 Schema 对账 | 导出旧 schema → 映射到新 snake_case 表（见下表） | Sprint 1 W2 |
| P-2 试迁移 | 用 **pgloader** 跑 dry-run；小样本对比行数 | Sprint 1 末 |
| P-3 增量机制 | 双跑期启用；旧库写 legacy.\*，CDC/触发器投 Redis 同步到新库 | Sprint 3 初 |
| P-4 一次性灌入 | Sprint 3 末冷切：停写旧库 → 最后一次 pgloader → 对账 | Sprint 3 末 |
| P-5 验收 | 对账 SQL 差异 < 0.1% → 切流开始 | Sprint 4 初 |

**字段映射核心表（Sprint 1 W2 产出完整 map.yaml）**

| 旧 PascalCase | 新 snake_case | 转换规则 |
|---|---|---|
| `Usr.UserName` | `users.user_name` | 直通 |
| `Usr.PassWord` MD5 | `users.password_hash` | 首次登录强制重置（D7 / H.3） |
| `Usr.Authority` | `users.authority` | 直通，枚举严格校验 |
| `Usr.ControlAuthority` | `users.control_authority` | 直通（SMALLINT 位掩码） |
| `DevInf.DevNumber` | `devices.dev_number` | 直通 |
| `DevInf.iccid` | `devices.iccid` | 直通 |
| `DevPointInf.UpdateInterval` 秒（点位级）| **聚合**到 `devices.update_interval_decisec` | 同一设备取旧所有点位 UpdateInterval 的**最小值**，**× 10** 转 0.1s 单位；旧为 NULL 时默认 100（10s）；D6 改为终端级（参见 §4.4） |
| `DevPointData_His_*` 分表 | `point_data_history` hypertable | 按月旧表 concat + INSERT ... SELECT |
| `His<YYYYMM>` 历史 | `point_data_history` + 波形入 `waveform_history` | 按 `data_array` 是否非空分流 |
| `PhoneAlarmRecord` + `WXAlarmRecord` | `alarm_records` 合表 | `channels_sent` JSONB 按来源填 `{sms:"ok"}` / `{wechat:"ok"}` |
| `PayOrder` | `pay_orders` | 直通 |
| `SoftLog` | `soft_logs` | 直通 |
| `TimingPlan` | `timing_plans` | **非直通**（v1.3.3）：usr_group 从 `devices.usr_group` 按 `dev_number` 反查填入；`deleted_at = null`；`updated_at = created_at`；`DEL_xxx_ts` 前缀记录转 `deleted_at` |
| `ZTPageInf` / `ZTViewInf` | `scene_pages` / `scene_views` | 直通 |
| `DEL_xxx_ts` 软删前缀 | `deleted_at` 字段 | 正则提取原 DevNumber + 时间戳 |

**工具链**

```bash
# pgloader 脚本（示意，Sprint 1 末完成）
LOAD DATABASE
  FROM mssql://ruisheng@legacy-win:1433/ModBus
  INTO postgresql://ruisheng_api:xxx@localhost/ruisheng
  WITH
    include no drop, create no tables,   -- 表先由 alembic 建
    reset sequences, data only,
    preserve index names
  CAST
    type datetime to timestamptz drop default drop not null
  BEFORE LOAD DO
    $$ ... 启用 timescaledb, 禁用触发器 $$
  AFTER LOAD DO
    $$ ... 启用触发器, ANALYZE, REFRESH 物化视图 $$;
```

**对账脚本（每日 03:00 跑）**

- `SELECT COUNT(*), MIN(ts), MAX(ts), SUM(value)` 各表分别对比
- 差异 > 0.1% 邮件告警 + 暂停切流

### 4.6 关键设计点

| 点 | 选择 | 原因 |
|---|---|---|
| 业务键 vs 自增 ID | 双键：`id` PK + `dev_number` UNIQUE | FK 用 `dev_number` 易读，性能用 `id` |
| 时区 | 全 UTC，前端 +08 | 跨时区无歧义 |
| 软删除 | `deleted_at` 字段 + 视图过滤 | 替代旧 `DEL_xxx_ts` 脏 hack |
| `update_interval` | INT 单位 0.1s，[10..1000] | D6：1.0–100.0s，避 decimal 精度坑 |
| 历史分表 | TimescaleDB hypertable | D4：按月自动分块 + 自动滚动 |
| 告警合表 | `alarm_records` JSONB `channels_sent` | 替代旧双表 |
| 多租户 | `usr_group NOT NULL` + CI lint | §3.7 双保险 |
| 审计 | `user_control_actions` JSONB | 灵活记录任意控制语义 |

---

## §5 错误处理与可靠性

### 5.1 错误码规范

```python
class ErrCode(IntEnum):
    OK              = 0
    BIZ_FAIL        = -1     # HTTP 200
    BAD_PARAM       = -100   # HTTP 400
    UNAUTHED        = -101   # HTTP 401
    FORBIDDEN       = -102   # HTTP 403
    DEV_OFFLINE     = -200
    DEV_NO_REPLY    = -201
    DEV_CRC_FAIL    = -202
    INTERNAL        = -300   # HTTP 500
    DB_UNAVAILABLE  = -301   # HTTP 503

class ApiResponse(BaseModel):
    code: int
    msg:  str = "ok"
    data: Any = None
    transid: str | None = None
```

**PG 错误 → 业务错误码映射（TODO Plan 2，v1.3.3 追加）**：

| PG SQLSTATE | 典型触发 | 建议业务码 | 用户提示（中文）|
|---|---|---|---|
| `23505` unique_violation | `ux_maintain_plans_dev_plan_name` 重名 | `BAD_PARAM` | "同设备下已存在同名计划" |
| `23503` foreign_key_violation | dev_number/user_name 引用不存在行 | `BAD_PARAM` | "关联的设备/用户不存在" |
| `23514` check_violation | `interval_days BETWEEN 1 AND 3650` 越界 | `BAD_PARAM` | 按约束名映射（见 §4.1） |
| `40001` serialization_failure | 并发写 `maintain_plans` 冲突 | `BIZ_FAIL` | "操作冲突，请重试" |
| `42501` insufficient_privilege | RLS 拦下跨租户查询 | `FORBIDDEN` | "无权访问" |

具体映射实现见 Plan 2 (api) `app/core/exception_handler.py`；本 plan (Plan 0) 只约定约束名规范，不实现映射。

### 5.2 协议层容错矩阵

| 异常 | 处置 | 告警 |
|---|---|---|
| 单帧 CRC 错 | 丢帧、计数 | 否 |
| 连续 5 次 CRC 错 | 主动断链 + soft_logs + 告警 | ✅ |
| 帧长 > 64KB | 拒收、断链 | ✅ |
| TCP 心跳超时 (60s) | 关连接、设备标"离线告警态" | ✅ |
| RS485 轮询超时 | LossCnt++，≥ 3 次 → 离线 | 离线时 ✅ |
| 设备未注册却发数据 | 缓存 IP + 召唤（10s × 5 次后丢弃） | 否 |
| 控制命令无 ACK (5s) | 重试 3 次（指数退避 1/2/4 s），失败进 Redis Streams 重试队列（非进程内） | 全失败时 ✅ |
| gw 进程崩溃（未捕异常 / OOM） | NSSM 自动重启 + 写 Windows Event Log + 生成 core dump → logs\crash\ | ✅ |
| 设备洪泛上报（恶意 / 故障） | 单连接令牌桶限流 10 帧/s；超限断链 + 黑名单 5min | ✅ |
| Windows 时钟漂移 > 1s | gw 启动时校 NTP，运行时每 30min 比 DB `now()`；漂移 >1s 告警 | ✅ |
| HTTPS / 微信 access_token / appsecret 到期 | Prometheus `cert_expire_days` 和 `wx_token_expire_s` 阈值告警（≤ 15 天 / ≤ 10 min） | ✅ |
| 磁盘剩余 < 10% | 停止写入 `soft_logs`（只留 ERROR）；≤ 5% 时 gw 拒绝新采集并告警 | ✅ |

### 5.3 告警引擎关键伪码

```python
class AlarmEngine:
    def evaluate(self, value: float, cfg: WarningCfg) -> AlarmEvent | None:
        triggered = self._compare(value, cfg)

        if cfg.alarm_type == 'LX':
            if triggered:
                self._lx_counter[cfg.id] += 1
                triggered = self._lx_counter[cfg.id] >= int(cfg.limit_value)
            else:
                self._lx_counter[cfg.id] = 0

        if triggered and not cfg.waring_flag:     # 0→1
            cfg.waring_flag = True
            return AlarmEvent.fired(cfg, value)
        if not triggered and cfg.waring_flag:     # 1→0
            cfg.waring_flag = False
            if cfg.reset_remind:
                return AlarmEvent.reset(cfg, value)
        return None
```

### 5.4 离线判定与命令队列

- 阈值（可配）：离线 15min，清除 2min，召唤 30s，轮询周期每终端独立 1.0–100.0s
- 离线命令队列 TTL 10min，Redis 持久化防重启丢

### 5.5 通知通道重试

```python
RETRY_DELAYS = [5, 15, 60, 300, 1800]   # 最多 5 次，指数退避
```

终态失败 → meta 告警（告警自身失败）

### 5.6 DB/Redis 故障降级

| 故障 | gw | api | 用户感知 |
|---|---|---|---|
| Redis 宕机 | 继续入库；publish 进内存队列 | WS 断，转 HTTP 轮询 | 实时数据延迟 5-10s |
| PG 短时宕机 | 内存缓冲 5min，超时丢弃+告警 | 503 | 写暂停，WS 仍推 |
| PG 长时宕机 | 持续重连 | 503 | 整站不可用 |

### 5.7 启动 / 停机健壮性

- **启动顺序**：PG → Redis → gw → api → nginx
- **gw 停机**：停新连接 → 当前轮询完成 (≤2s) → flush batch_writer → 断 Redis/DB → 退出
- **api 失败**：写 SoftLog 退出，NSSM 自动重启 (10s 后)

### 5.8 健康检查 + 可观测

- `/api/health`：api/db/redis/gw_last_seen 四维
- Prometheus metrics 覆盖 4 类：
  - **业务**：`devices_online` / `frames_received_total` / `crc_failures_total` / `alarms_fired_total`
  - **性能**：`db_write_latency_ms` / `redis_publish_lag` / `stream_pending_total` / `api_request_duration_ms`
  - **长期健康**（§5.10）：`pg_table_bloat_ratio` / `pg_dead_tuples` / `redis_memory_used_bytes` / `disk_free_percent`
  - **过期类**（安全）：`ssl_cert_expire_days` / `wechat_token_expire_seconds` / `appsecret_last_rotated_days`
- 日志：loguru → stdout → NSSM 滚动（10MB × 10 份）；异常 → Sentry
- 崩溃：`faulthandler` + Windows Error Reporting → `D:\ruisheng\logs\crash\`

### 5.9 消息送达保证（Redis Streams 替代 pub/sub）

**问题**：Redis pub/sub 是 **fire-and-forget**，消费者重启期间的消息会**直接丢**。告警漏发 = P0 事故。

**方案**：三条业务关键通道全部改 **Redis Streams**（at-least-once + 消费组 + pending entries list）。

| 通道 | 类型 | 生产者 | 消费组 | 保留策略 |
|---|---|---|---|---|
| `stream:alarm:fired` | Streams | gw | `api-alarm-consumer` | `XTRIM MAXLEN 100000` + 消费 ACK 后自然淘汰 |
| `stream:control:cmd` | Streams | api | `gw-control-consumer` | 同上，ACK 后 XDEL |
| `channel:realtime:{dev_number}` | Pub/Sub | gw | api WS 订阅 | **允许丢**（下一秒还会来新值） |

**补偿机制**：

- 消费者启动先 `XAUTOCLAIM` 拿回崩溃时未 ACK 的消息
- 处理成功才 `XACK`；处理 5 次失败 → 进"死信流" `stream:dlq:*` + 告警
- PEL（pending entries list）堆积 > 1000 → 告警（消费者跟不上）

**幂等保证**（防重复）：

| 场景 | 幂等键 | 机制 |
|---|---|---|
| 微信支付回调 | `out_trade_no` | `INSERT ... ON CONFLICT DO NOTHING` + 行锁；已 `paid` 直接返回 success |
| 微信消息推送 | `msg_id + openid` | 同上 |
| 告警事件消费 | `alarm_event_id`（由 gw 生成 UUID） | Redis `SET ... NX EX 86400` 去重 |
| 控制命令执行 | `cmd_id`（由 api 生成） | gw 内存 LRU 去重 24h + DB 写唯一约束 |
| HTTP POST 关键接口 | 请求头 `Idempotency-Key` | 响应缓存 24h |

**告警状态持久化**（修复 R3 / R6）：

```python
# 不再只靠 cfg.waring_flag 内存字段
class AlarmEngine:
    async def evaluate(self, value, cfg) -> AlarmEvent | None:
        # LX 计数器：Redis Hash，崩溃不丢
        if cfg.alarm_type == 'LX':
            count = await redis.hincrby(
                f"lx_counter:{cfg.dev_number}",
                str(cfg.id),
                1 if triggered else -count  # 不越限清零
            )
            triggered = count >= int(cfg.limit_value)

        # 状态字段落库 + UPDATE device_waring_cfgs.waring_flag
        # 事务内：UPDATE cfg 和 INSERT alarm_record 同提交
        async with db.transaction():
            if triggered and not cfg.waring_flag:
                await db.execute("UPDATE device_waring_cfgs SET waring_flag=true WHERE id=$1", cfg.id)
                event_id = await db.execute("INSERT INTO alarm_records(...) RETURNING id", ...)
                await redis.xadd("stream:alarm:fired", {"event_id": event_id, ...})
```

### 5.10 长期性能维护（运行一年不变慢）

**表膨胀防护（修复 R2 / M1 / M2）**：

| 表 | 写模式 | 防护 |
|---|---|---|
| `point_data_realtime` | UPDATE 覆盖写 | fillfactor=70 + 强化 autovacuum（见 §4.2）|
| `devices` | UPDATE `last_call_at` 等 | fillfactor=80 + autovacuum |
| `device_waring_cfgs` | UPDATE `waring_flag` | fillfactor=80 |
| `point_data_history` | INSERT only | TimescaleDB 压缩（见下）|
| `waveform_history` | INSERT only | 同上 |
| `soft_logs` / `user_login_records` | INSERT only | hypertable + retention |
| `alarm_records` / `user_control_actions` | INSERT only | **补入 hypertable（此版新增）** |

**TimescaleDB 自动压缩**：老 chunk 超过 7 天自动压缩到 ~10% 体积。

```sql
ALTER TABLE point_data_history SET (
  timescaledb.compress,
  timescaledb.compress_segmentby = 'dev_number, point_id'
);
SELECT add_compression_policy('point_data_history', INTERVAL '7 days');

ALTER TABLE waveform_history  SET (timescaledb.compress, timescaledb.compress_segmentby = 'dev_number');
SELECT add_compression_policy('waveform_history', INTERVAL '7 days');
```

**归档范围扩展（补齐 M1）**：原 D4 只覆盖 3 张时序表，本版追加两张：

```sql
-- 告警记录也转 hypertable
SELECT create_hypertable('alarm_records', 'triggered_at', chunk_time_interval => INTERVAL '1 month');
SELECT add_retention_policy('alarm_records', INTERVAL '2 year');   -- 审计合规保留 2 年
SELECT add_compression_policy('alarm_records', INTERVAL '30 days');

-- 控制审计（合规要求可能更长）
SELECT create_hypertable('user_control_actions', 'acted_at', chunk_time_interval => INTERVAL '1 month');
SELECT add_retention_policy('user_control_actions', INTERVAL '3 year');
SELECT add_compression_policy('user_control_actions', INTERVAL '30 days');
```

**定期维护 Job**（APScheduler）：

| Job | 频率 | 动作 |
|---|---|---|
| `vacuum_hot_tables` | 每天 03:00 | `VACUUM (ANALYZE) point_data_realtime, devices, device_waring_cfgs` |
| `reindex_concurrently` | 每周日 02:00 | `REINDEX TABLE CONCURRENTLY` 高频 UPDATE 表 |
| `bloat_check` | 每天 | 查 `pg_stat_user_tables`，bloat ratio > 3 → 告警 |
| `archive_to_oss` | 每月 1 号 02:00 | retention policy 剔除前，先 COPY TO Parquet 上传对象存储 |
| `redis_memory_cap` | 每 5 min | `INFO memory` > 80% → 告警，> 95% → 限流保护 |

**连接池扩缩规则（修复 M5）**：

| 设备规模 | api pool | gw DB pool | gw Redis pool | pgbouncer |
|---|---|---|---|---|
| ≤ 500 设备 | 20 | 10 | 20 | 可不加 |
| 500–2000 | 40 | 20 | 50 | **引入 pgbouncer** transaction 模式 |
| 2000–5000 | 80 | 40 | 100 | 必须 |
| > 5000 | 拆读写副本 | 40 主 + 40 读 | 200 | 必须 |

**前端缓存**：静态资源 Nginx `expires 30d`，Vue dist 文件名含 hash；API 层关键 GET 接口带 `Cache-Control: private, max-age=5`（实时数据不缓存，配置类数据缓存）。

### 5.11 通讯持续性（设备稳连 + 重启不丢）

**TCP 应用层双向心跳（修复 C1）**：

| 方向 | 周期 | 超时判定 |
|---|---|---|
| gw → 设备：读空寄存器 (FunCode 3, count=0) 或约定心跳帧 | 30s | 连续 3 次无响应 = LossCnt++ |
| 设备 → gw：应当 60s 内至少回一帧 | — | 无则主动断链并告警 |
| TCP 层 `SO_KEEPALIVE` | 60/30/3 | 兜底防 NAT 超时 |

**本地磁盘 WAL 缓冲（修复 C2）**：

```
gw 入库路径：
  解析帧 → batch_writer.queue.put(row)
            │
            ├─ PG 可达：100ms / 500 行 flush
            └─ PG 不可达：
                 ├─ 内存缓冲 满（≥ 1 万行）→ 溢出到 D:\ruisheng\gw\wal\YYYYMMDD-HHMM.ndjson
                 ├─ PG 恢复：回放 wal 目录 + 清文件
                 └─ wal 占磁盘 > 10GB → 老文件丢弃 + P0 告警
```

**设备重连保证（修复 C4）**：

- gw 重启：不主动断现有连接（NSSM stop 会发 SIGTERM，我们在 shutdown 钩子中"优雅关闭"，不直接 RST）
- 完全崩溃：DTU 侧按约定"3 次心跳失败自动重连"；本文档**明确要求** DTU 厂商支持并在 Q-P 系列询问中加 Q-P10 确认
- 重连后：gw 先接受注册 → 查 `devices.last_back_at`：
  - 若间隔 < 2 min：认为是短抖动，LossCnt 不清零，直接续跑
  - 若间隔 ≥ 2 min：认为是长断，发送一次"追补召唤"把关键点位全读一遍

**限流 / 背压（修复 C5）**：

- 入口级：单 TCP 连接令牌桶 10 帧/s（瞬时爆 20），超限丢帧 + 计数
- 处理级：`asyncio.Queue(maxsize=10000)` 满了 → 丢老帧（drop-tail）+ 告警；实时数据比历史数据更重要
- 下游级：Redis Streams `MAXLEN ~` 近似长度上限（publisher 非阻塞）
- API 级：FastAPI `slowapi` 每 IP 100 req/min；登录 5 次/min

**时钟一致性（修复 C6）**：

- Windows Server 启用 `w32time` + 指向国内权威 NTP（阿里/腾讯/国授时中心）
- gw 启动时强制同步一次；运行中每 30 min 比对 `SELECT now()` 与本地时间，差 > 1s 告警并用 DB 时间作为入库 `recorded_at`（不信任本地时间）

**微信 Token 刷新健壮化（修复 M6）**：

```python
async def refresh_token(wx_group):
    try:
        new = await wx_api.get_token()
        await db.update(wx_group, token=new, token_expires_at=now+7200)
        metric.wx_token_refresh_success.inc()
    except Exception:
        metric.wx_token_refresh_failure.inc()
        remaining = wx_group.token_expires_at - now
        if remaining < 600:                                       # 10 min
            await send_meta_alarm("微信 Token 即将过期且刷新失败")
        # 保留旧 token 继续用，直到 API 真失败
```

### 5.12 日志策略（完备记录 + 低影响）

**核心原则**：**多记级别、少记体积**。所有日志**结构化 JSON**，异步写入，批量 flush，按级别和事件分治。

#### 5.12.1 级别语义与默认配置

| 级别 | 用途 | 生产默认 | 去向 | 备注 |
|---|---|---|---|---|
| **DEBUG** | 协议帧字节、SQL 语句、WS 帧、第三方请求/响应 | **关闭** | 仅文件，不入 DB | 动态开启（按 dev/user 粒度），30min 自动恢复 |
| **INFO** | 设备上线/下线、用户登录、告警触发、控制下发、配置变更 | 开 | 文件 + Grafana/Loki（可选） | 重要业务足迹，不入 `soft_logs` 表（数据库已有 `alarm_records` / `user_control_actions`） |
| **WARN** | 单帧 CRC 错、重试中、外部 API 超时一次、降级触发 | 开 | 文件 + `soft_logs` 表 | **衰减采样**：同 ratelimit_key 超过 10 次后每 10×/100×/1000× 才记一次 |
| **ERROR** | 未捕获异常栈、外部 API 终态失败、幂等冲突、DB 写失败 | 开 | 文件 + `soft_logs` + Sentry | 必须带 traceback；触发 Prometheus 计数 |
| **CRITICAL** | 进程即将退出、严重数据不一致 | 开 | 文件 + `soft_logs` + 告警 | 必出通知 |

#### 5.12.2 关联 ID 贯穿（跨服务追踪）

每条日志必带下列字段（可为空但字段必存在）：

```json
{
  "ts": "2026-04-13T10:35:12.234+08:00",
  "level": "WARN",
  "logger": "ruisheng-gw.protocol.modbus_rtu",
  "msg": "CRC mismatch, dropping frame",
  "trace_id": "01HXXXXY2Z...",     // 一次 HTTP 请求或一次设备帧处理的 root ID
  "span_id": "a9f3b2c1",           // 子操作 ID
  "dev_number": "60270012",        // 设备相关操作
  "user_name": "138****8000",      // 用户操作（脱敏）
  "usr_group": "ruisheng_default", // 租户
  "build": "v1.2.3-a3f9",
  "pid": 4321,
  "context": { ... }                // 业务上下文（可变）
}
```

**注入规则**：
- api 侧 FastAPI middleware 从 `X-Trace-Id` 取，无则生成 ULID
- gw 侧每次收帧生成新的 trace_id；流到 Redis Streams 时透传
- loguru 通过 `bind()` 在上下文内自动附加

#### 5.12.3 敏感字段自动脱敏（防合规事故）

loguru filter 在写盘前强制替换：

| 字段名模式 | 处理 |
|---|---|
| `password` / `password_hash` / `secret` / `appsecret` / `api_key` / `jwt` / `token` | 全替换为 `***` |
| `phone_number` / `user_name`（当为手机号时）| `138****8000` 保留首 3 + 尾 4 |
| `openid` | 保留首 6 + `***` |
| `id_card` | `420***********1234` |
| `dev_number` / `dev_ser_number` | **部分脱敏**：首 3 + 尾 2，中间 `****`（v1.3 新增，M-S-04）|
| `iccid` | 首 4 + `****` + 末 4 |
| `limit_value` / `min_value` / `max_value` | `<redacted:numeric>`（v1.3 新增）|
| `email` | `u***@example.com` |
| `total_fee` / `amount` | **按配置**：默认记录（便于排障），合规要求高时改 `***` |
| 请求体含 `PAYLOAD_MAX_BYTES=4096` 超长截断 | 以防日志爆磁盘 |

```python
# core/logging.py
SENSITIVE_KEYS = {"password", "password_hash", "secret", "appsecret",
                  "api_key", "jwt", "token", "cookie", "authorization"}

def redact(record):
    for key in list(record["extra"].keys()):
        if key.lower() in SENSITIVE_KEYS:
            record["extra"][key] = "***"
        elif key.lower() in {"phone_number", "msisdn"} and record["extra"][key]:
            v = str(record["extra"][key])
            record["extra"][key] = f"{v[:3]}****{v[-4:]}" if len(v) >= 7 else "***"
    return record

logger.configure(patcher=redact)
```

#### 5.12.4 低影响写入（不拖慢主路径）

| 机制 | 参数 | 说明 |
|---|---|---|
| **异步写** | `loguru.add(sink, enqueue=True)` | 主协程只入队；专用线程写盘 |
| **批量 flush** | 每 100ms 或满 1000 条 | 减少 fsync |
| **采样衰减** | WARN 级 10× / 100× / 1000× 阶梯 | 防"日志风暴"（设备大面积掉线时） |
| **soft_logs 表写入** | 只收 WARN / ERROR / CRITICAL | DEBUG/INFO 不入库，只进文件 |
| **字符串延迟格式化** | `logger.debug("value={}", v)` 而非 f-string | level 被禁时完全跳过 |
| **大对象截断** | request body / BLOB 超 4KB 截断 | 防单条巨型日志 |
| **磁盘占用硬上限** | 每个服务 20 GB | 超过触发压缩 + 轮转 |
| **轮转策略** | 按日 + 按大小 100MB；压缩 gzip；保留 30 天本地 + 1 年冷存储 | 对象存储 S3/OSS |
| **磁盘降级** | 剩余 < 10% → 只写 ERROR+；< 5% → 停止文件日志，改内存环形缓冲 | 断臂求生 |

**性能预算**：日志开销应 ≤ 单请求 1%；协议层热路径（每帧解析）绝对禁用 DEBUG 级 string 构造。

#### 5.12.5 动态调级（零停机排障）

管理接口（需 L4 权限）：

```
POST /api/admin/log/level                 {logger:"ruisheng-gw.poller", level:"DEBUG", ttl:1800}
POST /api/admin/log/device/{dev_number}   {level:"DEBUG", ttl:1800}   # 单设备开 DEBUG 30 min
POST /api/admin/log/user/{user_name}      {level:"DEBUG", ttl:1800}
GET  /api/admin/log/current               → 返回所有活跃的临时调级
```

- 所有 `ttl` 过期后自动恢复默认；最长 6 小时
- 操作记 `user_control_actions` 审计
- gw 侧通过 Redis Pub/Sub 接收指令，不需重启

#### 5.12.6 集中查询

**MVP（v1.0）**：文件 + ripgrep + jq，运维手工查：
```bash
rg '"trace_id":"01HXXX"' D:\ruisheng\logs\**\*.log | jq .
```

**v1.1 升级**：引入 **Grafana + Loki**（文件挂载，无需数据库），按 trace_id 跨 api/gw 聚合；成本极低，部署 2 个 exe。

**关键查询预设**（Grafana 仪表盘模板）：
- 某 trace_id 的完整时序
- 某设备近 1 小时所有 WARN+
- 告警触发链路（gw 解析 → XADD → api 消费 → 通知发送）
- 登录失败/权限拒绝热点

### 5.13 JWT 加固（B-S2：失窃回收 + 客户端绑定）

**Token 生命周期**：
- `access_token` 有效期 **15 min**（旧设计未限定）
- `refresh_token` 有效期 **7 天**，仅走 `POST /api/auth/refresh`
- Refresh 流程要求 **old refresh + new nonce**，旋转单次使用（旧 refresh 立刻失效）

**客户端特征绑定**：
```python
# core/security.py
def issue_token(user, request):
    client_fingerprint = hashlib.sha256(
        f"{request.client.host}|{request.headers['user-agent']}".encode()
    ).hexdigest()[:16]
    payload = {
        "sub": user.user_name,
        "usr_group": user.usr_group,
        "role": user.authority,
        "ca": user.control_authority,
        "fp": client_fingerprint,
        "jti": str(ulid.new()),
        "exp": now + 900,
    }
    return jwt.encode(payload, ...)

def verify_token(token, request):
    payload = jwt.decode(token, ...)
    if payload["fp"] != current_fingerprint(request):
        raise InvalidToken("fingerprint mismatch")
    if redis.sismember("jwt_blacklist", payload["jti"]):
        raise InvalidToken("revoked")
```

**吊销机制**（jti 黑名单）：
- 用户登出：`SADD jwt_blacklist {jti} EX {remaining_ttl}`
- 用户改密：吊销当前所有 jti（用 `GET user:{id}:tokens` 扫）
- 异常登录检测：同 user_name 连续 5 次不同 fingerprint → 吊销该用户全部 token + 告警

**分布式登录锁（替代单机内存锁，B-S8）**：
```python
# core/login_limit.py
async def check_login(username, ip):
    # 用户维度：5 次/5min → 锁 30min
    key_user = f"login_fail:{username}"
    n = await redis.incr(key_user)
    if n == 1: await redis.expire(key_user, 300)
    if n >= 5:
        await redis.setex(f"login_lock:{username}", 1800, "1")
        await notify_user("账号遭 5 次失败登录尝试")

    # IP 维度：20 次/5min → 黑名单 1h
    key_ip = f"login_fail_ip:{ip}"
    n = await redis.incr(key_ip)
    if n == 1: await redis.expire(key_ip, 300)
    if n >= 20:
        await redis.setex(f"ip_block:{ip}", 3600, "1")
```

**OTP 二次验证**（权限矩阵中 L4 / 高危控制操作需要）：
- 发送：`POST /api/auth/otp/send` → 短信/邮件/微信模板 → 写 Redis `otp:{uid}:{action}` TTL 5min
- 验证：操作请求头 `X-OTP-Code`，后端 `GETDEL otp:{uid}:{action}` 比对

### 5.14 Windows 与设备安全加固（B-S1 + B-S4）

**设备认证加固（B-S1，对应 Q-P10 新问）**：

仅靠 `DevSerNumber` 注册可被伪造 → 加 HMAC-SHA256 预共享密钥挑战：

```
注册协议（新 FunCode 21 流程）：
  1. 设备连 TCP 6000 发 [Addr][21][DevSerNumber(24)][CRC]
  2. gw 生成 32B Nonce，返回 [Addr][21][Nonce(32)][CRC]
  3. 设备计算 HMAC-SHA256(PSK, Nonce || DevSerNumber) 截取 16B
     发 [Addr][21][MAC(16)][CRC]
  4. gw 侧 devices.psk 同样计算并比对，匹配则注册成功
  5. 失败 → 断链 + soft_logs ERROR + 连续 3 次失败入 IP 临时黑名单

PSK 管理：
  - 首次出厂烧录到设备（DTU 生产时）
  - 平台侧存 devices.psk_encrypted（用 DPAPI 加密，见下）
  - 轮换：平台 API POST /api/admin/devices/{dev_number}/rotate_psk
          → 下发新 PSK（用旧 PSK 加密） → 设备确认 → 切换
  - 丢失：人工重置（需运维到现场）
```

> 需 **Q-P10** 确认 DTU / 设备固件支持此握手；不支持的设备走降级策略（白名单 IP + 告警标记）。

**Windows 密钥存储（B-S4：替代 `.secrets.env` 700 权限无效）**：

```python
# core/secrets.py (Windows)
import win32crypt

def encrypt_secret(plaintext: str, entropy: bytes = b"ruisheng-iot-v1") -> bytes:
    return win32crypt.CryptProtectData(
        plaintext.encode(), "", entropy,
        None, None, 0
    )

def decrypt_secret(blob: bytes, entropy: bytes = b"ruisheng-iot-v1") -> str:
    return win32crypt.CryptUnprotectData(blob, None, entropy, None, 0)[1].decode()

# 启动时从加密 blob 文件加载；管理 API /admin/secrets/rotate 更新
```

**Windows Server 加固基线**（清单，M-S-01 / M-S-02 / M-S-03）：

| 项 | 配置 |
|---|---|
| RDP 端口 | 改非标（如 12389），启用 NLA；默认账号 Administrator 禁用，创建独立高权账号 + Fail2Ban 式封禁 |
| PostgreSQL | `ssl=on`，`hostssl ... cert`，启用 pgaudit，3 账号（`ruisheng_api` / `ruisheng_gw` / `ruisheng_backup`）各限权 |
| Redis | 启用 ACL（Redis 6+），为 `alarm_consumer` / `control_consumer` / `config_publisher` 分别签发；SSL 6380 |
| Windows 防火墙 | 仅开 80/443 + TCP 6000/6020；8000/8001/5432/6379/6380 仅 localhost |
| 依赖扫描 | CI 每周 `pip-audit` + `safety` + npm `audit`；Dependabot 自动 PR |
| 备份加密 | pg_basebackup 输出 → 7z 密码压缩 → 上传对象存储 (SSE-S3)；密钥由 DPAPI 加密 |
| Sentry 脱敏 | `before_send=sanitize_event`（删 Authorization/token/password/device_id）；默认自建部署 |

---

## §6 测试策略

### 6.1 测试金字塔

```
E2E (Playwright)       ~30 用例    ← P0 用户旅程
Integration (testcon)  ~150 用例   ← API↔DB↔Redis
Unit                   ~600 用例   ← protocol/domain/services
报文回放              10000+ 帧   ← 真机抓包对账
```

### 6.2 覆盖率门槛

| 模块 | 最低**分支覆盖率** | 备注 |
|---|---|---|
| `protocol/` | **95%** | Hypothesis 模糊 + 真机/伪 corpus 对账；v1.3 明确**分支覆盖**（非行覆盖）|
| `domain/` | **90%** | 状态机全覆盖 |
| `services/` | 80% | — |
| `api/` | 70% | 薄路由，靠集成测覆盖 |
| `transport/` / `pubsub/` | 集成测覆盖 | — |

**排除目录**（不计入分子/分母）：pydantic 模型自动生成代码、alembic 迁移、`__init__.py`、dev 工具

**变异测试**（M-T-01，S2 引入）：`mutmut` 每周对 `protocol/` + `domain/` 运行一次，变异存活率 < 10% 才算真有效。CI 卡线：覆盖率 + 变异存活率双通过才合 PR。

### 6.3 协议层测试要点

- 每个 FunCode 至少 3 个真机样本
- CRC16 多项式 0xA001
- Hypothesis 模糊测试：随机 4–2048 字节输入只能抛 ProtocolError
- 坏帧 / 超长 / CRC 错 / 截断各一组负向用例

### 6.4 告警状态机测试要点

- `WaringFlag` 0→1 触发、1→1 静默、1→0 复位
- LX 消抖连续 N 次
- PhoneAlarm 位掩码解析
- 复合 Relation 条件

#### 6.4.1 `alarm_engine` 单元测试 fixture（B-T2 硬规范）

由于 `alarm_engine.evaluate()` 含 `redis.hincrby` + `db.transaction` 两处 I/O，必须 mock：

```python
# tests/unit/domain/conftest.py
import pytest, fakeredis
from unittest.mock import AsyncMock

@pytest.fixture
async def alarm_engine_under_test():
    redis_fake = fakeredis.aioredis.FakeRedis()       # 真 Redis 行为（hincrby/hget/expire）
    db_mock = AsyncMock()
    db_mock.transaction.return_value.__aenter__ = AsyncMock()
    db_mock.transaction.return_value.__aexit__ = AsyncMock()
    return AlarmEngine(redis=redis_fake, db=db_mock)

# tests/unit/domain/test_alarm_engine.py
@pytest.mark.parametrize("alarm_type,value,limit,expected", [
    (">",  85, 80, "fired"),
    (">",  75, 80, None),
    ("<",  75, 80, "fired"),
    ("=",  80, 80, "fired"),
    ("!=", 79, 80, "fired"),
    ("LX", 85, 3,  None),   # 1 次，未到 N
])
async def test_evaluate_all_types(alarm_engine_under_test, alarm_type, value, limit, expected):
    cfg = make_cfg(alarm_type=alarm_type, limit_value=limit)
    ev = await alarm_engine_under_test.evaluate(value, cfg)
    assert (ev and ev.kind) == expected

async def test_lx_debounce_persists_across_restart(alarm_engine_under_test):
    """LX 计数器 Redis 持久化：模拟 evaluate 3 次连续越限后 engine 重建，第 4 次仍能触发"""
    ...

async def test_db_failure_no_alarm_leak(alarm_engine_under_test):
    """DB 事务失败时告警不得落 Redis Streams（避免幻告警）"""
    alarm_engine_under_test.db.transaction.side_effect = RuntimeError
    with pytest.raises(RuntimeError):
        await alarm_engine_under_test.evaluate(100, cfg)
    assert not await alarm_engine_under_test.redis.xlen("stream:alarm:fired")
```

必达测试矩阵：5 AlarmType × 2 状态 × 2 关联条件 × 2 消抖 × 2 复位提醒 = **40+ 用例**。

### 6.5 集成测试（testcontainers）

```python
@pytest.fixture(scope="session")
def postgres():
    with PostgresContainer("timescale/timescaledb:latest-pg15") as pg:
        run_migrations(pg.get_connection_url())
        with psycopg.connect(pg.get_connection_url()) as conn:
            conn.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")
            # 逐张 hypertable + retention + compression 用 conftest 幂等初化
            bootstrap_hypertables(conn)
        yield pg
```

端到端用例：gw publish alarm → api subscribe → 通知适配器 mock 被调 → `alarm_records` 表写入。

#### 6.5.1 Windows testcontainers Fallback（B-T3）

许多客户 Windows Server 禁用 Docker Desktop。开发/CI 必须双轨支持：

```python
# tests/conftest.py
@pytest.fixture(scope="session")
def postgres():
    if os.environ.get("USE_EMBEDDED_PG") == "1" or is_windows_no_docker():
        # Fallback: Windows 上用 PostgreSQL Portable（embedded_pg 包装）
        with EmbeddedPostgres(port=random_port(), version="15") as pg:
            install_timescale_ext(pg)
            run_migrations(pg.url)
            yield pg
    else:
        with PostgresContainer("timescale/timescaledb:latest-pg15") as pg:
            run_migrations(pg.get_connection_url())
            yield pg
```

- 本地开发机：默认 Docker Desktop
- Windows CI / 运维机：`USE_EMBEDDED_PG=1` 走嵌入模式
- Redis 同策略：`fakeredis` 单元 / 真 Redis Windows build 集成

两条路径都必须跑通：CI 矩阵同时跑 `container` 和 `embedded` 两个 job。

### 6.6 报文回放对账（最核心）

```
ruisheng-gw/tests/replay/
├── corpus/
│   ├── 2026-04-13_pump_normal.pcap       # 抽油机正常 24h
│   ├── 2026-04-13_pump_alarm.pcap        # 含告警
│   ├── 2026-04-13_register_burst.pcap    # 大批量注册
│   └── 2026-04-13_offline_recover.pcap   # 断网+恢复
├── expected/                             # 旧系统金标准 JSON
└── test_replay.py
```

**对账等级**

| 等级 | 容差 | 用途 |
|---|---|---|
| 严格 | 0% | 注册 / 控制 / 告警事件 |
| 紧 | ±0.001 | 数据采集点值 |
| 宽 | 时间戳 ±1s | 异步批量入库 |

#### 6.6.1 伪设备 PCAP 生成器（B-T1，不等真机抓包）

真机 corpus（Q-V02）延期风险高 → S1 立即启动伪设备生成，保证协议层可开发测试：

```python
# tests/replay/generator.py  —— Scapy 构造 ModBus RTU 帧 → pcap
from scapy.all import wrpcap, IP, TCP, Raw
from app.protocol.modbus_rtu import encode_frame, crc16

def gen_register_frame(dev_ser: str) -> bytes:
    payload = dev_ser.encode("ascii").ljust(24, b"\x00") + b"1.0.0" + b"1.0"
    body = bytes([0xFE, 0x15]) + payload
    return body + crc16(body).to_bytes(2, "little")

def gen_read_response(addr: int, values: list[int]) -> bytes:
    data = b"".join(v.to_bytes(2, "big") for v in values)
    body = bytes([addr, 0x03, len(data)]) + data
    return body + crc16(body).to_bytes(2, "little")

def gen_scenario(name: str, seed: int) -> Path:
    """生成一条完整会话的 pcap：注册 → 连续 10 帧采集 → 模拟一次告警 → 控制响应"""
    ...
```

**S1 必达**：5 种设备类型 × 3 种工况（正常 / 告警 / 异常） = 15 个 pcap，合计 ≥ 1 万帧；`expected/*.json` 由生成器同时输出（确定性）。

**真机 corpus 到位后**：并列入 `corpus/real/`，对账用例同时跑两组；伪生成器主要用于**异常场景补全**（真机很难抓到"连续 100 次 CRC 错"这种边界）。

**Corpus 管理**：
- 路径：`D:\ruisheng\corpus\{real,generated}\YYYY-MM-DD\*.pcap`
- 对象存储同步：S3 `s3://ruisheng-corpus/...`，CI 拉取用 `aws s3 sync`
- 脱敏：生成器内置（真 DevSerNumber 映射为 `TEST-XXXX`；真手机号不入 corpus）
- 版本：每个 corpus 目录有 `manifest.yaml` 记录来源 / 生成参数 / 预期结果哈希

### 6.7 E2E

按 §9 用户旅程（v2.2 主文档）：
- 微信扫码 → 绑定 → 首次看数据
- 运维收告警 → 复位
- 月度报表生成与审计
- 离线设备排障

### 6.8 性能压测目标与场景（B-T4）

**场景化量化表**（locust / k6 脚本直接对应）：

| 场景 | 设备数 | 每设备点位 | 轮询周期 | 期望 pps | 持续时间 | 通过标准 |
|---|---|---|---|---|---|---|
| MVP-正常 | 500 | 50 | 5s | 5000 pps | 30 min | 0 丢帧，P95 DB 写 < 100ms |
| MVP-高频 | 200 | 50 | 1s | 10000 pps | 30 min | 允许 < 0.1% 丢帧（背压保护）|
| 完整规模 | 5000 | 100 | 10s | 50000 pps | **4 h soak** | 0 丢帧，Redis Streams PEL < 1000 |
| 极限 | 5000 | 100 | 5s | 100000 pps | 15 min | 观察背压是否激活，评估硬件升级需求 |

**API 压测矩阵**（k6，混合场景）：

| 端点 | 流量占比 | QPS 目标 | P95 |
|---|---|---|---|
| `GET /api/devices/{dev_number}/realtime` | 50% | ≥ 200 | < 100ms |
| `GET /api/devices/{dev_number}/history?from=...` | 20% | ≥ 50 | < 500ms |
| `GET /api/alarms?status=active` | 15% | ≥ 30 | < 200ms |
| `POST /api/devices/{dev_number}/control` | 10% | ≥ 20 | < 300ms |
| `POST /api/waveforms/analyze (FFT)` | 5% | ≥ 5 | < 2s |

**WS 压测**：
- 连接数 ≥ 1000 同时在线
- 连接建立速率 ≥ 50/s
- 每连接收发 3–30 Hz 消息（模拟真实浏览量）
- 30 min 0 断开（自动重连不算）

**容量测试（soak）**：
- 4 h + 1 周 两档
- 关键指标：内存无泄漏（RSS 稳定）、磁盘无持续增长（归档生效）、告警可重复触发

#### 6.8.1 混沌测试矩阵（B-T5）

**工具**：`toxiproxy`（Windows 原生支持）+ pytest 驱动

**场景**（10 项必测）：

| # | 场景 | 注入方式 | 预期恢复 |
|---|---|---|---|
| C1 | Redis 断 30s | toxiproxy down | gw 继续入库；api WS 断；30s 后自愈，0 告警丢失（XAUTOCLAIM） |
| C2 | PG 断 60s | toxiproxy down | gw 切本地 WAL；60s 后自动 replay，0 数据丢失 |
| C3 | gw SIGKILL | `taskkill /F` | NSSM 10s 重启；设备自动重连；未 ACK 控制命令被 XAUTOCLAIM 重放 |
| C4 | api 进程满 fd | `ulimit -n 100` | 健康检查失败；NSSM 重启；无数据丢失 |
| C5 | 磁盘满 | `fsutil file createnew pad.bin 999GB` | 日志降级 WARN+；采集继续 |
| C6 | Redis 满内存 | `CONFIG SET maxmemory 10mb` | 背压激活：gw 限流发告警；XADD 拒绝进 DLQ |
| C7 | DB 写延迟注入 500ms | toxiproxy latency | batch_writer 队列堆积；`db_write_latency_ms` 告警触发 |
| C8 | TCP 丢包 5% | toxiproxy | 协议层 CRC 错重发；LossCnt 增加但不离线 |
| C9 | 时钟回跳 -1h | Windows `w32tm` | NTP 比对告警；DB now() 依然权威；无数据乱序 |
| C10 | WS 客户端 1000 同时断 | 自研脚本 | 5s 内自动重连完成；WS 服务端不泄露资源 |

**CI 集成**：每周日 02:00 跑全部 10 场景；每次 PR 跑 C1/C2/C3 三个核心场景。

### 6.9 CI 流水线

```yaml
unit          → pytest --cov-fail-under=85 + mypy --strict + ruff
integration   → testcontainers PG+Redis
replay        → corpus/*.pcap 回放对账
e2e           → Playwright
perf-smoke    → 每 PR 短压测
perf-full     → release 分支 locust 5000/30min
```

### 6.10 双跑对账（B-B6 细化）

旧系统写 legacy.*，新系统写 production.*；每天 03:00 对账 SQL。

**对账维度拆表**（不同维度不同容忍度）：

| 维度 | 口径 | 容忍度 | 处置 |
|---|---|---|---|
| **点值采集数量** | `COUNT(*) GROUP BY dev_number, day` | ±0.1% | 超标 → 告警不阻断 |
| **点值数值** | `SUM(value), MIN, MAX` 按设备+点位+小时 | ±0.001 | 超标 → 人工 review |
| **告警事件数** | `COUNT(*) GROUP BY dev_number, day` | **严格相等** | 任何偏差 → 暂停切流，人工诊断 |
| **告警触发/复位配对** | 每个 fired 必有 reset 或标"长期告警" | 严格 | 偏差 → P0 告警 |
| **控制操作数** | `COUNT(*) GROUP BY user, day` | ≤ 1 条差（容忍重试）| 超标 → 阻断切流 |
| **注册事件** | `COUNT(*) GROUP BY dev_ser_number` | 严格相等 | — |
| **支付订单** | 订单号对应 + 金额严格 | 严格 | 任何偏差 → 阻断 |

**Ground Truth 规则**：
- **旧系统是"参考值"而非"真值"**：已经通过 v2.2 文档 D7 砍掉的空实现（FunCode 7/12、21 个空壳页面）不列入对账
- **差异白名单**：对于已知旧 bug（例：`MinTransactionCnt=1` 伪批量导致入库顺序抖动），在 `reconciliation_whitelist.yaml` 登记；白名单需要 L4 + QA 双签
- **人工审核 SOP**：超标 → 运维 + 业务双人审阅 → 决定（接受偏差 / 回滚 / 修旧系统）

**对账 SQL 示例**（存 `scripts/reconcile_daily.sql`）：
```sql
-- 点值对账
WITH legacy AS (SELECT dev_number, DATE(recorded_at) d, COUNT(*) c FROM legacy.point_data GROUP BY 1,2),
     new    AS (SELECT dev_number, DATE(recorded_at) d, COUNT(*) c FROM public.point_data_history GROUP BY 1,2)
SELECT l.dev_number, l.d, l.c AS legacy_c, n.c AS new_c,
       ABS(l.c - n.c)::numeric / NULLIF(l.c, 0) AS diff_ratio
FROM legacy l FULL OUTER JOIN new n USING (dev_number, d)
WHERE ABS(l.c - COALESCE(n.c, 0))::numeric / NULLIF(l.c, 1) > 0.001
ORDER BY diff_ratio DESC;
```

输出放 Grafana 仪表盘，超阈值自动发告警到运维企业微信群。

### 6.11 数据库升级路径测试（M-T-04）

**每次 alembic revision 提交必须通过**：

```bash
# tests/migration/test_upgrade_path.sh
pg_dump -d ruisheng_prev > snapshot.sql           # 用上一个 tag 跑一遍生成
alembic downgrade base && alembic upgrade head    # full up
pytest tests/smoke                                 # 业务关键查询 OK
alembic downgrade -1 && alembic upgrade head     # 回退一步再前进，验证对称
pytest tests/data_integrity                        # 数据兼容性对比
```

**v1.0 → v1.1 灰度**：
- 新 schema 必须**向前兼容旧代码**（添加列但不删、旧列不改类型）
- 旧代码读新 schema 至少一周无报错 → 再启用新列
- Breaking 变更走"双写迁移"：新旧字段同时写 → 开关切换 → 清理旧字段（三次 release）

### 6.12 其他测试类型（M-T-05）

| 测试类型 | 工具 | 频率 | 门槛 |
|---|---|---|---|
| **可访问性 a11y** | `@axe-core/playwright` | E2E 每用例 | 0 严重违规，WCAG AA |
| **i18n 伪翻译** | vue-i18n + pseudo-locale | 每 release 前 | UI 不破相（截断/溢出）|
| **合约测试** | schemathesis + OpenAPI | 每 PR | 随机 100 请求 0 500 |
| **依赖 SCA** | `pip-audit` + `npm audit` + Dependabot | 每日 | 0 High/Critical |
| **Flaky 治理** | pytest-rerunfailures | CI 内 | 重跑 3 次；`@pytest.mark.skip_flaky` 隔离，独立报表 |

---

## §7 部署与运维

### 7.1 目标环境

| 项 | 值 |
|---|---|
| OS | Windows Server 2019/2022 |
| CPU / RAM | 8 核 / 16 GB |
| 磁盘 | SSD 500 GB |
| Python | 3.11.x Embedded |
| 外部 | PostgreSQL 15 + TimescaleDB、Redis 7、Nginx |

### 7.2 目录规划

```
D:\ruisheng\
├── api\          # ruisheng-api venv + 代码
├── gw\           # ruisheng-gw venv + 代码
├── web\          # Vue dist
├── nginx\        # Nginx + conf
├── postgres\     # (可选内嵌)
├── redis\
├── logs\         # api/ gw/ nginx/
├── backup\       # pg_basebackup 每日
├── corpus\       # 报文回放素材
└── config\
    ├── api.yaml
    ├── gw.yaml
    └── .secrets.env  # 权限 700
```

### 7.3 Windows Service 安装（NSSM）

```bat
nssm install ruisheng-api "D:\ruisheng\api\venv\Scripts\python.exe"
nssm set    ruisheng-api AppParameters "-m uvicorn app.main:app --host 127.0.0.1 --port 8000"
nssm set    ruisheng-api AppDirectory  "D:\ruisheng\api"
nssm set    ruisheng-api AppStdout     "D:\ruisheng\logs\api\stdout.log"
nssm set    ruisheng-api AppRotateFiles 1
nssm set    ruisheng-api AppStopMethodConsole 15000
nssm set    ruisheng-api Start         SERVICE_AUTO_START
```

依赖顺序：`postgres → redis → gw → api → nginx`

### 7.4 Nginx 核心配置

```nginx
server {
    listen 443 ssl http2;
    server_name iot.ruisheng.com;
    ssl_certificate     D:/ruisheng/nginx/ssl/fullchain.pem;
    ssl_certificate_key D:/ruisheng/nginx/ssl/privkey.pem;

    root D:/ruisheng/web/dist;
    location / { try_files $uri $uri/ /index.html; }

    location /api/     { proxy_pass http://127.0.0.1:8000; }
    location /ws       { proxy_pass http://127.0.0.1:8000; proxy_http_version 1.1;
                         proxy_set_header Upgrade $http_upgrade;
                         proxy_set_header Connection "upgrade";
                         proxy_read_timeout 3600s; }
    location /wechat/  { proxy_pass http://127.0.0.1:8000; }
}
server { listen 80; return 301 https://$host$request_uri; }
```

### 7.5 配置管理

- `api.yaml` / `gw.yaml`：业务配置（DB URL、Redis、通知适配器 provider 等）
- `.secrets.env`：密码/密钥/appsecret（700 权限，永不进 git）
- JWT secret 由 `openssl rand -hex 32` 生成，6 个月轮换

### 7.6 备份

```bat
:: daily_backup.bat 每天 02:00
pg_basebackup -h 127.0.0.1 -U ruisheng_backup -D D:\ruisheng\backup\%DATE% -Ft -z -P
aliyun oss sync D:\ruisheng\backup\%DATE% oss://ruisheng-backup/%DATE%/
forfiles /P D:\ruisheng\backup /D -30 /C "cmd /c rd /s /q @path"
```

WAL 归档 `archive_command`，实现 PITR；每季度 1 次恢复演练，RTO ≤ 30 min。

### 7.7 监控告警

```
Prometheus → scrape api/gw/pg/redis/windows_exporter → Grafana
Alertmanager → 钉钉 / 企微 / 邮件
```

关键告警：gw 下线 60s、CRC 失败率 > 0.1、DB 写延迟 P95 > 500ms、磁盘剩余 < 20%、通知通道全挂、设备在线率 5min 骤降 20%

**日志运维**：结构化 JSON + 关联 ID 贯穿 + 脱敏 + 动态调级 + 磁盘自适应，详见 **§5.12**。

### 7.8 安全基线

- HTTPS Let's Encrypt 自动续期
- 3 个 PG 账号：`ruisheng_api`（业务读写）、`ruisheng_gw`（时序读写）、`ruisheng_backup`（只读+REPLICATION）
- Redis 绑定 127.0.0.1 + requirepass + ACL
- Windows 防火墙只开 80/443 + TCP 6000/6020；8000/8001/5432/6379/6380 仅 localhost（与 §5.14 基线一致）
- 审计：所有写操作 → `user_control_actions`；失败登录 5 次 → 锁 30 min

### 7.9 升级与回滚

```
升级：pg_dump 快照 → 记录 alembic rev → stop → 换码 → alembic upgrade head → start → 冒烟
回滚：stop → git reset --hard <tag> → alembic downgrade <prev> → start
```

---

## §8 时间盒与里程碑

### 8.1 总节奏

**12 周 = 4 × 3 周 Sprint**，每 Sprint 末内部演示 + 修正。

```
Week 1 2 3 │ 4 5 6 │ 7 8 9 │ 10 11 12
     S1    │ S2    │ S3    │ S4
     骨架  │ P0    │ P1+双跑│ P2+切流
     α    │ β     │ γ     │ 1.0
```

### 8.2 Sprint 1（第 1–3 周）— 骨架 + 协议层

**必达**
- 工程骨架：两仓 + 共享包 + CI 通 + `docker compose up` 一键启动
- 数据库：alembic 迁移建完 21 表 + hypertable + retention + 种子
- 协议层：`modbus_rtu.py` 覆盖率 95% + Hypothesis 模糊测试
- 采集闭环：TCP listener → 注册 → 轮询 → 入库 → Redis publish
- API & WS：登录 JWT + 实时接口 + WS 推送
- 前端：登录页 + 设备详情页 + WS 客户端
- 集成测试：1 台 simulated 设备端到端 ≤ 1s

**Gate**：
- 1 台 simulated 设备 2 小时无数据丢失
- **PITR 实演完成**（B-A2）：从 pg_basebackup + WAL 恢复到任意分钟，实测 RTO，更新 §7.6 真实值
- **伪设备 PCAP corpus 就位**（B-T1）：至少 15 个 pcap ≥ 1 万帧；真机 corpus 到齐则并存
- **shared schema 版本化 CI 卡点激活**（B-A1）

### 8.3 Sprint 2（第 4–6 周）— P0 核心业务

**必达**：认证注册、实时监控、远程控制、历史/日报、告警管理、告警引擎、邮件+微信通知、RBAC+多租户、响应式前端覆盖 P0 全模块

**并行**：拿到真机抓包 → 建 `replay/corpus/`，CI 生效

**Gate**：simulated 10 × 20 × 10s 跑 24h 0 丢失 0 漏警；真机回放对账点值 ±0.001，告警严格相等；Lighthouse ≥ 80。

### 8.4 Sprint 3（第 7–9 周）— P1 全功能 + 双跑

**必达**：短信+电话 2+1 家适配器、波形分析+FFT+OPM、月报表、定时计划、保养、组织管理（废弃特权号）、SIM 管理、Prometheus+Grafana、性能达标

**关键动作**：**双跑开启**，每日对账

**Gate**：P0 + 90% P1 上 β；双跑 7 天差异 < 0.1%；监控全绿。

### 8.5 Sprint 4（第 10–12 周）— P2 + 切流

**必达**：组态 ZTView/ZTViewCfg+vue-konva、微信支付+回调、设备充值、App 下载、发版+回滚脚本、渗透测试、文档、灰度切流 10%→50%→100%、旧系统下线

**Gate**：全量用户切过去 ≥ 7 天无事故；P0/P1 全通；运维手册 + 回滚剧本验证。

### 8.6 里程碑

| M | 时间 | Gate |
|---|---|---|
| M1 骨架跑通 | 第 3 周末 | → S2 |
| M2 α 版 | 第 6 周末 | → 启 corpus+对账 |
| M3 双跑中 | 第 9 周末 | → 灰度切流 |
| M4 全量切换 | 第 12 周末 | 30 天观察期 |
| v1.1 | 第 13–14 周 | 组态增强 / P2 遗留 |

### 8.7 人力假设

| 角色 | 人数 |
|---|---|
| 后端 / 协议 | 2 |
| 前端 | 1 |
| DBA / 运维 | 1 |
| 测试 | 0.5 |
| **合计** | **4–4.5 人 × 12 周 ≈ 50 人周** |

1–2 人单兵时间盒放宽到 **18–20 周**。

---

## §9 未决问题对齐

这些 P0 问题的回答**截止时间**绑定里程碑：

| 问题 | 内容 | 截止 |
|---|---|---|
| Q-V02 | 真机抓包 | S1 末（第 3 周末） |
| Q-P07 | MBAP 确认 | S1 末 |
| Q-P08 | DevSerNumber 格式 | S1 末 |
| Q-E01/E02/E03 | 第三方账号 | S3 初（第 7 周初） |
| Q-V01 | 现网规模 | S3 末（性能目标校准） |
| Q-V04 | 第三方对接清单 | S3 末（切流前） |
| Q-B07 | 充值扣费模型 | S4 初 |
| 其他 P2 | | v1.1 前 |

其他待澄清问题参见 `D:\江苏润盛\需求清单\待澄清问题清单.md`。

**v1.3 新增升级**：
- **Q-P10（新增 P0）**：DTU 是否支持 HMAC-SHA256 预共享密钥 + FunCode 0x19 心跳 —— S1 初必须答；不支持则走设备白名单 + IP 黑名单降级。

### §9.3 关键用户旅程（B-B5）

> 3 条端到端业务流程，用于 E2E 测试设计 + UX 评审 + 验收判据。每条标注涉及章节编号，避免设计缺口。

#### 旅程 A：运维值班员告警处置闭环

```
10:30:00  T+0     设备 60270012 电流越限（value=95, limit=80）
10:30:00  T+0.5s  gw 解析帧 → alarm_engine.evaluate → WaringFlag 0→1
                  XADD stream:alarm:fired → 事务 INSERT alarm_records
10:30:01  T+1s    api subscriber 消费 → 查 user_alarm_list
                  并发 fan-out：微信模板 + SMS
10:30:03  T+3s    运维张三收到微信模板消息（配 SMS 兜底）
10:30:15  T+15s   张三点开 Vue Web → 设备详情页 → 看实时曲线
10:30:45  T+45s   判断是"机器过热"，点"远程停机"
                  前端弹二次确认，要求输入设备号 → 提交
                  (高危，ControlAuthority bit2 → 要求 OTP)
                  收到 SMS 验证码 123456，输入确认
10:30:55  T+55s   api 写 user_control_actions(pending) → XADD stream:control:cmd
10:30:56  T+56s   gw 消费 → 离线命令队列（若在线则直接 poller）→ 下发 FunCode 6
10:30:58  T+58s   设备 ACK → gw XACK + update action.result=success
                  publish channel:realtime → api WS 推张三："已停机"
10:31:30  T+90s   设备值回落 70 → WaringFlag 1→0，ResetRemind=true
                  发复位通知（微信）
10:32:00  T+2m    张三在告警列表标"已处置" → 关闭事件
```

**验收标准**：
- 端到端延迟（告警触发→微信收到）≤ 5s（§7.2）
- 控制指令往返（点击→ACK）≤ 3s（在线设备）
- 所有步骤均入 `alarm_records` / `user_control_actions` / `soft_logs`，一个 trace_id 贯穿
- 高危操作无 OTP 时后端拒绝

#### 旅程 B：新租户微信扫码绑定到首次看数据

```
T+0      新用户小李扫二维码 → 公众号推送菜单 → 点"我的设备"
T+2s     跳 /Default_WX → OAuth 拿到 openid
T+3s     查 user_wx_bindings(openid) → 未绑定 → 跳 /phone_register
T+15s    填手机号 138****8000 → 点"获取验证码" → 3s 收到 SMS
T+25s    输入验证码 → 后端 INSERT users + UPDATE user_wx_bindings
         （租户 usr_group 由公众号 appid 反查 wx_groups 自动归属）
T+26s    签发 JWT（绑 client_fingerprint）→ 写 Cookie → 跳设备列表
T+27s    列表空 → 显示 EmptyState："管理员还没为您分配设备..."
T+5min   小李联系管理员 → 管理员 Web 端把设备归属给小李
T+5min+3s 小李刷新 → 看到 3 台设备 → 点第一台看实时数据
```

**验收标准**：
- OAuth → 绑定 → 首次看到数据全路径 ≤ 8 min（含管理员手工环节）
- 空态必须有引导文案，不许白屏（§2.5.2）
- JWT 必须绑 fingerprint（§5.13）
- 租户自动归属，用户不选（防选错）

#### 旅程 C：网络断线恢复 + 离线命令下发

```
T+0      设备在线，用户点"启动电机"
T+1s     api XADD stream:control:cmd
T+2s     gw 消费 → 发 FunCode 6 → 设备 ACK → 成功通知用户
T+5s     现场 4G 信号突降，DTU 断 TCP
T+15s    gw 检测 TCP RST → DTU 下所有设备标"通信异常"
T+16s    写 alarm_records（通信类告警）+ 通知用户"设备离线"
T+2min   用户想发"停机" → 前端禁用控制按钮？**不禁用**
         （B-B3 决策：离线时接受命令入队）
         api 照常 XADD → gw 消费 → 查设备状态=离线 → 入离线队列 TTL 10min
         api 立即 WS 推送："设备离线，命令已入队待执行"
T+3min   现场信号恢复，DTU 重连
T+3min+1s gw 接到新 FunCode 21 注册 → 匹配 devices.dev_ser_number + iccid
         恢复设备状态 → 发补召唤读所有关键点位
         查离线队列 → 按入队顺序下发未执行命令
T+3min+3s 设备 ACK 停机 → 成功通知用户
```

**验收标准**：
- 离线判定延迟 ≤ 15 min（默认）或更快（若轮询周期 × 3 < 15 min）
- 离线命令队列 TTL 10 min，超时转 cancelled + 通知用户
- 重连识别要靠 `dev_ser_number + iccid`（§D3）
- 任何离线期操作都有用户可见的状态反馈（不许默默失败）

---

## §10 变更记录

- **v1.0 / 2026-04-13**：基于 v2.2 功能全景清单 + 6 个 brainstorming 决策（规模/部署/技术栈/数据库/外部/架构）撰写；由用户在 brainstorming 阶段逐段 ack 通过。
- **v1.1**：自检修订（GroupID 砍除；update_interval_decisec 从点位级挪到终端级；遗产数据迁移策略）
- **v1.2**：健壮性/长期不变慢/通讯持续三维加固（Redis Streams 替代 pub/sub；fillfactor+autovacuum；TimescaleDB 压缩；本地 WAL 兜底；TCP 双向心跳；时钟校准；微信 Token 降级）
- **v1.3**：**5 角色并行审查（SA / 架构师 / 通讯 / 安全 / 测试）合并修订**。27 项 Blocker 内联修订；新增 §3.6 权限矩阵、§3.7 多租户 RLS、§5.13 JWT 加固、§5.14 Windows 加固、§6.11 升级路径、§6.12 其他测试、§9.3 用户旅程、§14 审查整合清单、**§A 协议规范附录**（全章）。D8 决策入账（5 角色 Blocker 合并为单次交付）。
- **v1.3.1**：**数据 / 函数 / 前后台变量一致性轮**。修订点：(1) Redis channel key 统一 `channel:realtime:{dev_number}`；(2) URL 参数统一 `{dev_number}` 替代 `{n}`；(3) 所有私有 API 加 `/api/` 前缀（`/api/auth/*` / `/api/admin/*`）；(4) interval_decisec 修正为 update_interval_decisec 前后端同名；(5) user_control_actions 加 cmd_id UNIQUE + result CHECK 约束；(6) 新增 §3.4.1 WS 消息合同 + §3.4.2 HTTP 标准头契约；(7) ID 统一 ULID；(8) pay_orders_seen DDL 与表清单同步；(9) 目录增补 §14 / §A 锚点；(10) 错误码引用从 §D.2 改 §5.1。
- **v1.3.2**：**边界情况 + 数据流完整性轮**。修订 10 处 🔴 边界漏洞 + 10 处 🟡 数据流缺口 + 5 处 🟢 DDL 约束。关键新增：(1) §3.8 新章节 16 个边界规则（并发控制串行化 / 同帧多告警 / Stream 满 / WS 慢消费者 / 离线命令过期通知 / DevSerNumber 冲突退化 / JWT 长操作过期 / 历史查询大数据集降采样 / 归档与查询并发 / Outbox 事务补偿 / cmd_id 跨重启去重等）；(2) 微信支付验签顺序修正（先验签后取字段，hmac.compare_digest）；(3) DDL 加 CHECK 约束（user_name 正则 / modbus_addr 1-247 / fun_code 1-4 / pay_orders.total_fee≥0 / pay_state 与 paid_at 一致）；(4) 新增 alarm_outbox 表保证告警落库与 Stream 发布原子性。
- **v1.3.3 / 2026-04-14**：**保养模块 DDL 完整化 + spec 全局一致性加固（Plan 0 Stage C Task C7 派发前 2 轮审查产出）**。`SHARED_SCHEMA_VERSION` bump `20260413 → 20260414`（breaking）。关键修订：
  - (1) **§4.2 新增 `maintain_plans` / `maintain_actions` 两表完整 DDL**：保养计划模板 + 执行审计（原 spec 只在 §4.3 表清单提及名字，无 DDL）；
  - (2) **§4.2 `timing_plans` 原地重写**：补 `usr_group` + `deleted_at` + `updated_at` + `REFERENCES devices/wx_groups` + 3 索引 + 触发器 + RLS policy（原版为 12 年前老 DDL，缺多租户基础设施）；
  - (3) **§3.6 line 656** 保养 L1 权限 `R → ▲自有`（一线员工可对本人绑定设备的保养计划增删，符合 SCADA 现场作业语义）；
  - (4) **§3.7 扩写**：软删 + FK 语义（FK 不检查 deleted_at，业务查询必须 JOIN + COALESCE 降级显示）+ gw 服务账号 `ruisheng_gw BYPASSRLS` + api 每事务 SET LOCAL 两变量；
  - (5) **§3.8.17 新增**：保养计划推进状态机（`max(now(), old) + interval` 非累加公式 + `FOR UPDATE` 行锁 + `action_uuid ULID` 幂等），防漏做 + 防双击；
  - (6) **§3.8.16 追加**：`maintain_actions` 同款脱敏保留 3 年规则；
  - (7) **§4.1 规约表加行**：TZ='Asia/Shanghai' 无 DST / 字符串 strip+NFC 在 API 层 / FK-CHECK 命名交 naming_convention / Index 显式命名 / RLS policy 统一 `tenant_isolation` / Trigger `trg_<table>_updated` / 表单时间字段 60s 容差；
  - (8) **§4.1.1 新增通用设施**：`set_updated_at()` 触发器函数 + `ruisheng_gw` / `ruisheng_api` 两个 DB 角色定义；
  - (9) **§4.3 表清单数量** `21 → 26` 修正（原计数自 v1.0 就错）；
  - (10) **§4.5 line 1341** `TimingPlan` 迁移规则从"直通"改为"非直通"：usr_group 从 devices 反查填入 + `updated_at = created_at` + `DEL_xxx_ts → deleted_at`；
  - (11) **§5.1** 追加 PG SQLSTATE → ErrCode 中文映射表（TODO Plan 2 实现 `app/core/exception_handler.py`）；
  - (12) 本轮审查产出含 5 BLOCKER + 8 MAJOR + 9 MINOR，全部 BLOCKER+MAJOR 已落地本次 commit，MINOR 按"延后 Plan 2/3"或 spec TODO 处理。

---

## §14 审查反馈整合清单（v1.3）

> 5 角色审查合计发现 32 🔴 + 28 🟡，去重合并后得 27 项 Blocker + 35 项中黄低绿。Blocker 全部已内联修订；其他项按编号进入跟踪，绑定里程碑。

### 14.1 已内联修订（Blocker，共 27）

| ID | 来源 | 问题简述 | 修订位置 |
|---|---|---|---|
| **B-P1** | 通讯 #1 | FunCode 12 无替代方案 | §A.4、§5.11 |
| **B-P2** | 通讯 #2 | 心跳帧 FunCode 3 count=0 不兼容 | §A.5、§5.11 |
| **B-P3** | 通讯 #3 | 限流 10 vs 100 帧/s 矛盾 | §5.11 |
| **B-P4** | 通讯 #5 | 粘包分帧算法未落地 | §A.2、§5.11 |
| **B-P5** | 通讯 #9 | TCP 直跑 RTU vs MBAP 未定 | §A.2 |
| **B-P6** | 通讯 #10 | 6000/6020 端口分工模糊 | §A.7 |
| **B-P7** | 通讯 #7 | RS485 吞吐与轮询矛盾 | §A.8、§5.11 |
| **B-B1** | SA #1 + 安全 H7 | 多租户 ORM 强制 + RLS | §3.7 |
| **B-B2** | SA #2 | 5 种 AlarmType 判定表 | §5.3、§F 引用 |
| **B-B3** | SA #3 + 安全 H2 + 架构 P-004 | 控制完整生命周期 + 双人审批 | §3.2 |
| **B-B4** | SA #5 + 安全 H2 | 权限矩阵 4×14 + L4 OTP | §3.6 |
| **B-B5** | SA #6 | 3 条关键用户旅程 | §9.3 |
| **B-B6** | SA #7 + 测试 #5 | 双跑对账维度 + ground truth | §6.10 |
| **B-A1** | 架构 P-002 | shared schema 版本化 | §2.3 |
| **B-A2** | 架构 P-003 | RTO 30min 演练 | §8.2 S1 |
| **B-A3** | 架构 P-001 | 多实例 gw 扩展路径 | §1.4.x |
| **B-A4** | 架构 W-001 | 5s 轮询改 Redis Pub/Sub | §1.3 |
| **B-S1** | 安全 H1 | 设备 HMAC-SHA256 | §5.14 |
| **B-S2** | 安全 H4 | JWT 失窃回收 + 客户端绑定 | §5.13 |
| **B-S3** | 安全 H3 | 审计 append-only + 哈希链 | §5.9 |
| **B-S4** | 安全 H5 | Windows DPAPI | §5.14、§7.5 |
| **B-S5** | 安全 H9 | 微信支付签名 + 5min 窗口 | §3.5、§5.9 |
| **B-T1** | 测试 #1 | 伪设备 PCAP 生成器 | §6.6.1 |
| **B-T2** | 测试 #2 | alarm_engine fixture | §6.4.1 |
| **B-T3** | 测试 #3 | Windows testcontainers fallback | §6.5.1 |
| **B-T4** | 测试 #4 | 性能场景量化表 | §6.8 |
| **B-T5** | 测试 #8 | 混沌测试矩阵 | §6.8.1 |

### 14.2 进入跟踪（中黄 + 低绿，共 35）

> 不 block v1.0 MVP，但进跟踪表；按里程碑绑定。未列全条目参见各审查报告原文，归档在 `D:\江苏润盛\docs\superpowers\reviews\`。

| ID | 来源 | 简述 | 责任期 |
|---|---|---|---|
| M-A-01 | 架构 W-002 | Windows Nginx 并发 ≤200 阈值告警 | S2 |
| M-A-02 | 架构 W-003 | Python GIL/RSS 监控告警 + Go 迁移预案占位 | S3 |
| M-A-03 | 架构 W-004 | 异地容灾双写对象存储 | S4 |
| M-A-04 | 架构 W-005 | 动态调级跨进程实现路径 | S2 |
| M-A-05 | 架构 N-002 | corpus 自动化脱敏脚本 | S1 |
| M-S-01 | 安全 M1 | RDP 加固（非标端口+NLA+堡垒机） | S1 |
| M-S-02 | 安全 M2 | PG 启 SSL + cert 认证 + pgaudit | S2 |
| M-S-03 | 安全 M3 | Redis ACL + SSL | S2 |
| M-S-04 | 安全 M4 | 日志脱敏扩展（iccid/limit_value/email）| S1（见 §5.12.3）|
| M-S-05 | 安全 M5 | TCP 6000/6020 DMZ + seccomp 限制 | S3 |
| M-S-06 | 安全 M6 | 密钥轮换框架 + git-secrets CI | S2 |
| M-S-07 | 安全 M7 | 控制命令状态转移验证 | S2 |
| M-S-08 | 安全 M8 | Sentry 事件脱敏 before_send | S1 |
| M-S-09 | 安全 H6 | openid 绑定二次验证 + 异常登录检测 | S2 |
| M-S-10 | 安全 H8 | 分布式登录锁 Redis 集中化 | S3（多副本启用时必做）|
| M-T-01 | 测试 #6 | 分支覆盖 + mutmut 变异测试 | S2 |
| M-T-02 | 测试 #7 | E2E 扩到 14 模块矩阵 | S3 |
| M-T-03 | 测试 #9 | Flaky 治理（重跑 3 次 / 隔离）| S2 |
| M-T-04 | 测试 #10 | 升级路径测试（alembic up/down）| S3 |
| M-T-05 | 测试 L1-L4 | 可访问性/i18n/合约/SCA | S3-S4 |
| M-B-01 | SA #8 | 离线判定加速（周期 × 3 或 15min 取小）| S2 |
| M-B-02 | SA #9 | WAL 溢出改为压缩 + 归档 | S2 |
| M-B-03 | SA #10 | 压测场景规范化（已部分入 §6.8）| S3 |
| M-B-04 | SA #11 | MVP 边界明确化 P1 的 3 项 | S1（见 §8.2）|
| M-B-05 | SA #12 | 日志金额字段脱敏决策 | S1（见 §5.12.3）|
| M-B-06 | SA #13 | POST/DELETE `Cache-Control: no-store` | S1（前端约定）|
| M-B-07 | SA #14 | 网络不稳定 UI 降级提示 | S2 |
| M-B-08 | 架构 N-001 | 分布式追踪 Jaeger/Zipkin | v1.1 |
| M-B-09 | 架构 N-003 | 多租户 SQL 注入风险评估 | S2（见 §3.7 lint）|
| M-P-01 | 通讯 #4 | 从站无心跳能力说明 | §A.5 已说明 |
| M-P-02 | 通讯 #6 | 心跳帧 16 进制格式 | §A.5 已说明 |
| M-P-03 | 通讯 #8 | FunCode 21 DevSerNumber 编码 | §A.4 已草拟，待 Q-P08 最终确认 |
| M-P-04 | 通讯 #11 | 波形时间反推 UI 标注偏差 | S2（前端） |
| M-P-05 | 通讯 #12 | 单帧最大 4096B 协议规范 | §A.2 已说明 |
| M-P-06 | 通讯 #13 | CRC16 字节序明确 + 验证向量 | §A.3 已说明 |

### 14.3 新增/升级的待澄清问题

- **Q-P10（新增，🔴 P0）**：DTU 是否支持 HMAC-SHA256 预共享密钥机制（由 §5.14 设备认证加固触发）
- **Q-P07（升级，已定 Answer）**：TCP 封装改为"直跑 RTU 帧（含 CRC16）"已入 §A.2；仍需原开发者/DTU 最终确认
- **Q-P08（进一步）**：FunCode 21 Payload 精确 16 进制样本（见 M-P-03）
- **Q-B07（升级，影响 §8.5）**：充值扣费模型仍空；v1.3 spec 采取"UI 完成+扣费逻辑延后 v1.1"的保守策略（见 §8.5 修订）

---

## §A 协议规范附录

> 新增独立附录，包含字节级帧结构 / 粘包分帧 / CRC 验证向量 / DevSerNumber 编码 / 心跳帧 / 波形 BLOB 字节序 / 端口分工 / RS485 物理约束表。整章由通讯工程师审查驱动。

### A.1 ModBus RTU 帧格式

```
┌────────┬────────┬──────────────┬────────┬────────┐
│ 地址   │ 功能码 │ 数据段       │ CRC L  │ CRC H  │
│ 1 字节 │ 1 字节 │ N 字节       │ 1 字节 │ 1 字节 │
└────────┴────────┴──────────────┴────────┴────────┘

地址：1 ~ 247（0 = 广播，不使用；248-255 保留）
功能码：见 §A.4
CRC：见 §A.3，**低字节在前**（ModBus 标准小端）
```

### A.2 TCP 封装规范（决策：直跑 RTU，不用 MBAP）

**决策**：TCP 上直接跑 ModBus RTU 完整帧（含 CRC16）。理由：
- 旧代码已经有 CRC16 实现（0xA001 多项式）
- DTU 厂商常见为"透明传输"，不改 RTU 结构
- 保留 CRC16 可对抗 DTU 侧解封装后的位错

**TCP Payload 格式**：
```
1 个 TCP 报文可能包含：
  - 1 个完整 RTU 帧（短包）
  - N 个完整 RTU 帧（粘包，见 A.2.1）
  - 部分 RTU 帧（分片，见 A.2.1）
```

#### A.2.1 粘包分帧算法（硬规范）

```python
# transport/connection.py 伪码
class FrameReceiver:
    buffer: bytearray = bytearray()
    last_byte_at: datetime = None
    SILENCE_MS: int = 200          # ≥ 3.5 字符时间 @ 9600 bps
    MAX_FRAME: int = 4096

    async def on_bytes(self, data: bytes):
        now = datetime.utcnow()
        self.buffer.extend(data)
        self.last_byte_at = now

        # 缓冲超限 → 立即断链
        if len(self.buffer) > self.MAX_FRAME:
            raise ProtocolError("frame too long")
            # 上层应发告警并断 TCP

    async def tick(self):
        """由 asyncio 定时器每 50ms 调用"""
        if not self.buffer: return
        silent_ms = (datetime.utcnow() - self.last_byte_at).total_seconds() * 1000
        if silent_ms >= self.SILENCE_MS:
            # 静止时间到，尝试按完整帧边界解析
            while self.buffer:
                frame, consumed = try_parse_one_frame(bytes(self.buffer))
                if frame is None:
                    # 无法分帧（CRC 错 / 长度异常）→ 丢弃首字节续尝试
                    self.buffer.pop(0)
                    self.crc_fail_count += 1
                else:
                    del self.buffer[:consumed]
                    await self.dispatch(frame)
```

**为什么 200ms**：
- ModBus RTU 标准要求帧间静止 ≥ 3.5 字符时间
- 最低波特率 9600 → 每字节 ≈ 1.04 ms → 3.5 字符 ≈ 3.6 ms
- 经 4G 透传后存在抖动，保守取 200ms 避免误分帧

**最大帧长 4096B**：
- FunCode 16 一次最多写 123 个寄存器 = 246 + 头尾 ≈ 256B，远小于 4096B
- 波形大块响应（FunCode 23 或自定义）预留上限

### A.3 CRC16 计算规范

```
多项式：0xA001（反向多项式，ModBus 标准）
初始值：0xFFFF
最终 XOR：无（保留计算结果）
字节序（在帧中）：低字节在前，高字节在后
```

**验证向量**（CI 测试必测）：

| 帧（无 CRC） | 期望 CRC (LE) |
|---|---|
| `01 03 00 00 00 02` | `C4 0B` |
| `01 06 00 01 03 E8` | `D8 BA` |
| `01 10 00 00 00 02 04 00 0A 01 02` | `A3 05` |

**参考实现**：见 `电能终端波形分析工具` Form1.cs:116-142。

### A.4 FunCode 定义表（新系统全量）

| FunCode | 名称 | 请求字节结构 | 响应字节结构 | 备注 |
|---|---|---|---|---|
| **0x03** | 读保持寄存器 | `[Addr][03][StartH][StartL][CntH][CntL][CRC L][CRC H]` | `[Addr][03][ByteCnt][Data×2N][CRC L][CRC H]` | **采集主通道**；FunCode 13 是本码的别名，同分支处理 |
| **0x05** | 单线圈写 | `[Addr][05][CoilH][CoilL][ValH=FF/00][ValL=00][CRC L][CRC H]` | echo 请求 | — |
| **0x06** | 单寄存器写 | `[Addr][06][RegH][RegL][ValH][ValL][CRC L][CRC H]` | echo 请求 | FunCode 26 是别名，同分支处理 |
| **0x10 (16)** | 多寄存器写 | `[Addr][10][StartH][StartL][CntH][CntL][ByteCnt][Data×2N][CRC L][CRC H]` | `[Addr][10][StartH][StartL][CntH][CntL][CRC L][CRC H]` | **批量配置下发主通道**，替代砍掉的 FunCode 12 |
| **0x14 (20)** | ICCID 上报（私有）| `[Addr][14][ICCID × 10/20 字节][CRC L][CRC H]` | ACK `[Addr][14][Status][CRC L][CRC H]` | Q-P05 待最终确认 ICCID 长度与编码（ASCII/BCD） |
| **0x15 (21)** | 设备注册（私有）| `[0xFE][15][DevSerNumber × 24][FwVer × 5][HwVer × 3][CRC L][CRC H]` | `[Addr][15][Result][CRC L][CRC H]` | DevSerNumber 24 字节 ASCII（草案）；Q-P08 待最终确认 |
| **0x16 (22)** | 低功耗注册（私有）| 同 0x15 但 FwVer/HwVer 可选 | 同上 | — |
| **0x19 (25)** | 心跳帧（新增约定）| `[Addr][19][Token × 4][CRC L][CRC H]` | `[Addr][19][Token × 4][Status][CRC L][CRC H]` | Token 是 gw 生成的 4 字节随机数，设备原样回显，防伪造（替代原"FunCode 3 count=0"方案） |
| **0x64 (100)** | 通用响应（私有）| — | `[Addr][64][CtrlID × 4][Result][Data...][CRC L][CRC H]` | Result 状态码表见 Q-P06，初版：0=成功，1=参数错，2=设备忙，3=越限，4=未授权 |

#### A.4.1 砍掉的 FunCode 说明
- **FunCode 7（请求服务）**：旧系统无实现 → 不做
- **FunCode 12（寄存器批量同步）**：功能由 **FunCode 16（多寄存器写）+ 离线命令队列分批下发** 承担
  - 批量改 100 个点位阈值：分 10 批 × 每批 10 个连续寄存器 × FunCode 16
  - 预估耗时：10 × (请求 30B + 响应 8B) × 40ms/帧 ≈ 0.4s（9600 bps）
  - 若连续寄存器地址不齐（分散）→ 退化为多个 FunCode 6，在离线命令队列中排队执行

### A.5 心跳帧规范（新增 FunCode 0x19）

**为什么不用 `FunCode 3, count=0`**：
- ModBus 标准未定义 count=0 行为
- 大多数从站会抛异常响应码 3（非法数据值）

**新方案**：
- gw → 设备：`[Addr][0x19][Token(4B)][CRC]`，Token 为 `uint32` 随机数
- 设备 → gw：`[Addr][0x19][Token(4B echo)][Status(1B)][CRC]`
  - Status: 0=正常, 1=忙碌, 2=即将下线
- 周期：30 秒
- 超时：`3 × 30s` 无响应 → LossCnt++
- **从站侧实现要求**：DTU 厂商 / 设备固件必须支持 FunCode 0x19（已升级为 **Q-P10**）

**若设备不支持 FunCode 0x19 的降级**：
- 退化为"隐式心跳"：最近 30s 内有过 FunCode 3 响应即认为在线
- gw 需持续发轮询（不得超过 30s 无查询任一设备）
- 此情况下 TCP 层 `SO_KEEPALIVE(60/30/3)` 是唯一兜底

### A.6 波形 BLOB 编码（从需求 §4.2.C 固化）

```
偏移 长度 字段
0    1B   SampleTime_decisec（采样间隔，单位 0.1s，值 10 = 1 秒）
1    1B   PacketCount（本 BLOB 内采样点数）
2    N    Values[PacketCount]
         - ValueType="字"   → 每值 2B，**大端无符号**整数
         - ValueType="双字" → 每值 4B，**大端无符号**整数
```

**时间戳反推规则**（修订 v1.2 公式偏差问题）：
```
for i in range(PacketCount):
    # recorded_at 应取设备端发送时刻，不是 gw 入库时刻
    # 若设备无时间戳字段，取 (gw_receive_at - 网络延迟估计 - SampleTime_decisec × 0.1 × (PacketCount - i))
    # 网络延迟估计 = max(100ms, 最近 N 帧往返平均延迟)
    ts[i] = device_send_at - (PacketCount - i) × SampleTime_decisec × 0.1s
```

**UI 必须标注**：波形查看页显示"采样时间可能有 ±1s 网络抖动"提示文本。

### A.7 TCP 端口分工

| 端口 | 协议 | 连接模式 | 用途 |
|---|---|---|---|
| **6000** | TCP | 长连接（DTU 单连接，透传所有 RS485 设备） | 注册帧（FunCode 21/22）+ 心跳（FunCode 0x19）+ ICCID 上报（FunCode 20）+ 设备响应（FunCode 100）+ **离线命令响应** |
| **6020** | TCP | 长连接，复用 6000 同连接（推荐） 或独立连接 | 数据轮询（FunCode 3）+ 控制下发（FunCode 6/16） |
| 可选 SerialPort | RS485 直连 | — | 本地调试，协议帧同上 |

**连接模型**（硬规范）：
- **1 个 DTU 建立 1 条 TCP 连接**，透传其下所有 RS485 从站（≤128 台）
- DTU 在 TCP 连接上透传 RS485 帧（原样 bytes，不封装）
- 同一 DTU 下不同从站的帧按"地址字段"区分
- gw 侧一个连接对应内存中的一个 `DTUSession`，维护该 DTU 下 N 个 `Device` 的状态

### A.8 RS485 物理层约束表（前端强制校验）

**一次完整读请求耗时估算**（9600 bps 为例）：
```
请求帧长 = 8 字节 → 传输时间 = 8 × 10 bit / 9600 = 8.3 ms
响应帧长 = 5 + 2N 字节（N 个寄存器）→ 假设读 10 个点位 = 25 字节 = 26 ms
帧间静止 = 3.5 字符 ≈ 4 ms
单次往返 = 8.3 + 26 + 4 ≈ 38 ms ≈ 40 ms（保守）
```

**波特率 × 终端数 × 最小轮询周期（秒）约束表**：

| 波特率 | 单次往返 | 128 台一轮 | **最小轮询周期** |
|---|---|---|---|
| 9600 bps | 40 ms | 5.1s | **6s**（保留 1s 余量） |
| 19200 bps | 20 ms | 2.6s | **3s** |
| 38400 bps | 10 ms | 1.3s | **2s** |
| 115200 bps | 4 ms | 0.5s | **1s** |

**前端配置校验规则**：
- API `POST /api/devices/{dev_number}/update_interval` 接收 body `{ "update_interval_decisec": 100 }`（与 DB 字段同名）
- 后端计算：该 RS485 总线上（同 DTU 下）所有设备 `update_interval_decisec` 的**最小值**
- 若 `min_interval_s < 约束表下限` → 返回错误码 `-100` + `msg: "该波特率下最少轮询周期应 >= X.Xs"`
- 前端给即时提示 + 禁用提交按钮

---

**协议附录结束。**

---

**文档结束（v1.3）。**

下一步（superpowers 流程）：
1. 本 spec 自检（placeholder / 一致性 / 范围 / 歧义）
2. 用户最终 review spec 文件
3. 进入 `superpowers:writing-plans` 生成实施计划
