# 江苏润盛 IoT 监控平台 — 重做设计文档

> **版本**：v1.0 / 2026-04-13
> **状态**：设计已获用户逐段 ack，等待最终 spec review → writing-plans
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
              │   realtime:{dev}     │
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
| Redis `channel:realtime:{DevNumber}` | **Pub/Sub**（允许丢） | gw | api（WS 订阅）| 实时点值 `{point_id, value, ts}`（下一秒又来，丢单条无害） |
| Redis `stream:alarm:fired` | **Streams**（at-least-once） | gw | api `api-alarm-consumer` 组 | 告警触发事件，ACK 后 XDEL |
| Redis `stream:control:cmd` | **Streams**（at-least-once） | api | gw `gw-control-consumer` 组 | 用户控制命令，ACK 后 XDEL |
| Redis `stream:dlq:*` | **Streams** | 消费者 | 运维告警 | 死信（5 次处理失败） |
| PostgreSQL `devices.update_flag` | 轮询 | api | gw（5s 扫） | 配置变更（告警阈值/轮询间隔/定时计划） |

> 详细设计见 **§5.9 消息送达保证**。核心原则：**实时数据允许丢**（覆盖写模型），**告警与控制不许丢**（业务事故）。

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
│   │   └── errors.py            # 错误码 §D.2、统一异常响应
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
│   │   ├── publisher.py         # realtime:{dev} / alarm:fired
│   │   └── subscriber.py        # control:cmd
│   │
│   └── tests/
│       ├── unit/
│       └── replay/              # 真机抓包回放对账
│
└── pyproject.toml
```

### 2.3 共享包 `ruisheng-shared`

```
ruisheng-shared/
├── models/                      # SQLAlchemy 模型（两服务共用）
├── schemas/                     # pydantic（两服务共用）
├── enums/                       # FunCode、AlarmType、AlarmAction
└── constants/                   # 协议常量、错误码
```

### 2.4 前端 `ruisheng-web`

```
ruisheng-web/
├── src/
│   ├── api/                     # axios + openapi-typescript 自动生成
│   ├── stores/                  # Pinia
│   ├── router/
│   ├── layouts/                 # 响应式（桌面+移动）
│   ├── views/
│   │   ├── auth/
│   │   ├── dashboard/
│   │   ├── devices/
│   │   ├── alarms/
│   │   ├── reports/
│   │   ├── waveforms/           # ECharts + 调 FFT/OPM
│   │   ├── plans/
│   │   ├── scenes/              # vue-konva 组态画布
│   │   ├── pay/                 # 微信 JSAPI / 扫码
│   │   └── settings/
│   ├── components/
│   └── ws/                      # WebSocket 客户端封装
├── vite.config.ts
└── package.json
```

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
浏览器 ──HTTP POST──▶ api /api/devices/{n}/control
                        │  Idempotency-Key 头防重复
                        ├─ RBAC（ControlAuthority bit0）
                        ├─ 多租户过滤
                        ├─ 事务：写 user_control_actions(result=pending) + 生成 cmd_id
                        └─ XADD stream:control:cmd (cmd_id, payload)
                                │
                                ▼
                        gw XREADGROUP gw-control-consumer
                                │  cmd_id LRU 去重 24h（§5.9）
                                ├─ 在线 → poller 队列
                                └─ 离线 → 命令队列（Redis ZSET, 10min TTL）
                                │
                                ▼
                        编码 FunCode 6/16 + CRC ──▶ DTU ──▶ RS485 ──▶ 设备
                                │
                                ├─ ACK 成功 → 更新 action.result=success + XACK
                                ├─ 超时 3 次 → XADD stream:dlq:control + 审计 failed + 告警
                                └─ 进程崩溃 → 下次启动 XAUTOCLAIM 拿回未 ACK 继续
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

---

## §4 数据模型

### 4.1 命名与通用规范

| 项 | 规范 |
|---|---|
| 表/字段 | `snake_case` |
| 主键 | `id BIGSERIAL` + 业务键 UNIQUE |
| 时间 | 统一 `timestamptz`，存 UTC |
| 多租户 | 每张业务表强制 `usr_group VARCHAR(50) NOT NULL` + INDEX |
| 软删除 | `deleted_at timestamptz NULL` |
| 审计字段 | `created_at` / `updated_at`（触发器维护）|
| 字符集 | UTF8，collation `zh-x-icu` |

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
  user_name         VARCHAR(50) UNIQUE NOT NULL,
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
  modbus_addr             SMALLINT NOT NULL,
  baud_rate               INT,
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
  point_number      INT NOT NULL,                -- ModBus 寄存器起始地址
  fun_code          SMALLINT NOT NULL,           -- 3/4 读
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
  limit_value          DOUBLE PRECISION NOT NULL,
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
  action     JSONB NOT NULL,
  result     VARCHAR(20),
  acted_at   timestamptz NOT NULL DEFAULT now(),
  usr_group  VARCHAR(50) NOT NULL
);

CREATE TABLE timing_plans (
  id          BIGSERIAL PRIMARY KEY,
  dev_number  VARCHAR(50) NOT NULL,
  action_at   timestamptz NOT NULL,
  action      INT NOT NULL,
  repetition  INT NOT NULL DEFAULT 0,
  enable      BOOLEAN NOT NULL DEFAULT true,
  update_flag SMALLINT NOT NULL DEFAULT 0,
  created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE pay_orders (
  out_trade_no VARCHAR(50) PRIMARY KEY,
  openid       VARCHAR(100) NOT NULL,
  total_fee    INT NOT NULL,
  body         VARCHAR(255),
  pay_state    VARCHAR(20) NOT NULL DEFAULT 'pending'
                CHECK (pay_state IN ('pending','paid','failed','refund')),
  created_at   timestamptz NOT NULL DEFAULT now(),
  paid_at      timestamptz
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

### 4.3 表清单总览（共 21 张）

```
租户 (1):   wx_groups
用户 (4):   users, user_wx_bindings, user_phone_numbers, user_emails
设备 (6):   devices, device_points, device_waring_cfgs,
            device_static_data, sim_cards, device_templates
时序 (3):   point_data_realtime, point_data_history*, waveform_history*
告警 (1):   alarm_records
控制 (1):   user_control_actions
计划 (3):   timing_plans, maintain_plans, maintain_actions
组态 (2):   scene_pages, scene_views
支付 (1):   pay_orders
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
| `TimingPlan` | `timing_plans` | 直通 |
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
| `channel:realtime:{dev}` | Pub/Sub | gw | api WS 订阅 | **允许丢**（下一秒还会来新值） |

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

| 模块 | 最低覆盖率 |
|---|---|
| `protocol/` | 95% |
| `domain/` | 90% |
| `services/` | 80% |
| `api/` | 70%（薄路由，靠集成测覆盖） |
| `transport/` / `pubsub/` | 集成测覆盖 |

CI 卡线：低于阈值 PR 不能合。

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

### 6.5 集成测试（testcontainers）

```python
@pytest.fixture(scope="session")
def postgres():
    with PostgresContainer("timescale/timescaledb:latest-pg15") as pg:
        run_migrations(pg.get_connection_url())
        yield pg
```

端到端用例：gw publish alarm → api subscribe → 通知适配器 mock 被调 → `alarm_records` 表写入。

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

### 6.7 E2E

按 §9 用户旅程（v2.2 主文档）：
- 微信扫码 → 绑定 → 首次看数据
- 运维收告警 → 复位
- 月度报表生成与审计
- 离线设备排障

### 6.8 性能压测目标

| 指标 | 目标 |
|---|---|
| 采集吞吐 | ≥ 3000 pps |
| API QPS | ≥ 200 / 实例，P95 < 300ms |
| 告警下发 | ≤ 5s（100 并发） |
| WS 同时连接 | ≥ 1000 |
| 在线设备数 | ≥ 5000 |

### 6.9 CI 流水线

```yaml
unit          → pytest --cov-fail-under=85 + mypy --strict + ruff
integration   → testcontainers PG+Redis
replay        → corpus/*.pcap 回放对账
e2e           → Playwright
perf-smoke    → 每 PR 短压测
perf-full     → release 分支 locust 5000/30min
```

### 6.10 双跑对账（切流前 7-30 天）

旧系统写 legacy.*，新系统写 production.*；每天 03:00 对账 SQL；差异 > 0.1% 暂停切流。

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

### 7.8 安全基线

- HTTPS Let's Encrypt 自动续期
- 3 个 PG 账号：`ruisheng_api`（业务读写）、`ruisheng_gw`（时序读写）、`ruisheng_backup`（只读+REPLICATION）
- Redis 绑定 127.0.0.1 + requirepass + ACL
- Windows 防火墙只开 80/443 + TCP 6000/6020；8000/8001/5432/6379 内部封
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

**Gate**：1 台设备 2 小时无数据丢失。

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

---

## §10 变更记录

- **v1.0 / 2026-04-13**：基于 v2.2 功能全景清单 + 6 个 brainstorming 决策（规模/部署/技术栈/数据库/外部/架构）撰写；由用户在 brainstorming 阶段逐段 ack 通过。

---

**文档结束。**

下一步（superpowers 流程）：
1. 本 spec 自检（placeholder / 一致性 / 范围 / 歧义）
2. 用户最终 review spec 文件
3. 进入 `superpowers:writing-plans` 生成实施计划
