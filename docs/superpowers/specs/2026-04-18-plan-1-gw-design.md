# Plan 1 — `ruisheng-gw` 设备采集网关设计

**日期**：2026-04-18（v1）→ 2026-04-18（v2 adversarial-review-driven revision）
**状态**：Brainstorming + 5-role adversarial review → ready for writing-plans
**上游 spec**：`docs/superpowers/specs/2026-04-13-ruisheng-iot-design.md` v1.3.6 §2.2
**依赖**：Plan 0 `plan-0-complete` + `shared-v0.1.0`（ruisheng-shared 0.1.0）

---

## 0. 版本历史

| 版本 | 日期 | 要点 |
|---|---|---|
| v1 | 2026-04-18 | 初次 brainstorm 输出：7 Stage / 37 task / scope B MVP-ready core |
| **v2** | **2026-04-18** | **5-role adversarial review 回应**（系统架构师/分析师/测试员/通信工程师/维护员）：修 A1-A7 协议正确性 + B1-B7 数据完整性/契约 + C1 加简化 alarm + D1-D7 运维（日志 schema / /health / WAL / rollback / metrics / config）+ E1-E7 测试（Clock 注入 / replay 保时序 / testcontainers session-scope / Hypothesis / branch cov）+ F1-F3 不做 HA（YAGNI）；Stage 数保持 7，task 37→~48；shared 0.1.0 不 bump（所有新 schema 放 gw 内）；scope B → **MVP-ready core + 简化 alarm 分发**（不做状态机） |

---

## 1. 概览

### 1.1 系统定位

`ruisheng-gw` 是设备采集网关。作为 TCP server 接受 DTU 连接（DTU 下挂 1 条 RS485 总线，总线串接多个 ModBus 从机）。核心职责：

1. 解析设备上行帧（注册 FC100 / 周期上报 FC3 resp / 心跳 FC 0x19）
2. 主动下发轮询请求读实时点位（FC 3/5/6/16）
3. 标度换算 + 批量入库（`point_data_realtime` UPSERT + `point_data_history` INSERT）
4. 实时点位 Redis pub（`channel:realtime:v1:{dev_number}`）
5. **简化 alarm 分发**（超阈值直发 Redis `channel:alarm:v1:{dev_number}`，v2 新增）
6. DB 写失败 WAL 兜底（上游 §5.11）

### 1.2 关键决策（v2 修订后）

| 决策 | 选择 |
|---|---|
| **Scope** | B — MVP-ready core + 简化 alarm（修 C1）：transport + 10 FunCode 协议（**含 FC 0x19 心跳**）+ per-device scheduler + batch_writer（修 MinTransactionCnt=1）+ device 基本状态机 + Redis realtime pub + **threshold alarm pub**（**不含状态机**）|
| **并发 & 容错** | per-device 轮询协程 + `asyncio.create_task` + 手动 supervise（**非 TaskGroup**，v2 F2 修）+ per-bus `asyncio.Lock` |
| **测试策略** | pytest Unit + testcontainers integration（**session-scope containers**，v2 E4 修）+ Plan 0 pcap_gen 15 corpus replay（**保留 `pkt.time`**，v2 E3 修）+ **Hypothesis property test**（v2 E5 新）+ **branch coverage 75%**（v2 E6 新） |
| **Stage 结构** | 7 Stage（A scaffold+observability / B protocol / C transport / D domain / E scheduler+persistence+WAL / F pubsub+alarm+tests / G CI+release） |
| **bus_id 建模**（OQ-1 v2 修）| **session key = `dev_number`**（shared ORM 已 `UniqueConstraint` 全局唯一）；bus_id 从 registry 加载时推断；**TCP 重连只换 writer 引用不换 bus_id** |
| **真 DTU 测试**（OQ-2）| 不在 Plan 1，延 Plan 1.5 专门现场校准阶段 |
| **多租户**（OQ-3）| 单 gw + BYPASSRLS；**加 CI lint** 扫 gw SQL AST 强制 FORCE RLS 12 表含 `usr_group` 谓词（v2 B6） |
| **Shared bump**（OQ-4）| **Plan 1 不 bump shared 0.1.0**（v2）：RealtimeEvent + AlarmEvent pydantic model 放 gw 内（Plan 2 暂从 gw import，Plan 1.5 再迁 shared） |
| **HA / 多 gw**（v2 F1）| **不做，不预留** `GW_INSTANCE_ID` / Redis lease — YAGNI，Plan 1.5 重新设计 |

### 1.3 明确不包含（Plan 1.5 承接）— v2 扩展

**协议/数据**：
- 私有 FC13/26 vendor-specific 解码（v2 A5 — Plan 1 Stage B5 仅为 standard FC3/FC6 等同则 BLOCKED）
- 告警状态机（fired/reset 配对 + alarm_records 持久化 + alarm_outbox stream）
- 离线命令队列（control:cmd 订阅 + 执行）
- 波形 BLOB 编解码 §4.2.C + waveform_history 写入

**运维**：
- 5s `update_flag` 配置热更新 watcher + SIGHUP reload
- HA / 多 gw + Redis lease 分片
- Graceful blue-green / rolling upgrade
- 日志轮转 / Windows Event Log 集成
- Per-device debug verbose toggle / SIGUSR1 dump state

**测试**：
- 真 DTU 现场接入 + vendor 协议差异修补
- 1000 连接 flood / 24h soak / 内存泄漏检测

**架构演进**：
- SHARED_SCHEMA_VERSION range（N/N-1 互通）— Plan 1 exact match，升 shared 需协调重启
- Redis payload model 迁入 shared（Plan 1 暂 gw 独有）

### 1.4 产出概览

| 项 | v1 | v2 |
|---|---|---|
| Stage 数 | 7 | 7 |
| Task 估 | 37 | ~48 |
| 新增代码 | 3-4K LOC | 4-5K LOC |
| 新增测试 | 155 case | ~185 case |
| Plan bug 预估 | 15-25 | 20-30 |
| 时间估 | 6-8 周 | 7-9 周 |
| 最终 tag | `plan-1-complete` + `gw-v0.1.0` + GitHub Release | 同 v1 |

---

## 2. Architecture

### 2.1 Stage 依赖图

```
          ┌──────────────────────┐
          │ A 骨架+observability │  uv workspace + main.py + schema check
          └──────────┬───────────┘  + structlog + /health + config --print
                     │
          ┌──────────┼──────────┐
          ▼          ▼          ▼
      ┌───────┐ ┌───────┐ ┌───────┐
      │ B 协议│ │ C 传输│ │ D 域模型│  (逻辑可并行；subagent-driven 下串行派发)
      └───┬───┘ └───┬───┘ └───┬───┘
          │         │         │
          └─────┬───┴────┬────┘
                ▼        ▼
           ┌───────────────┐
           │ E 调度+持久化 │  ← scheduler + batch_writer + WAL + CI lint
           └───────┬───────┘
                   ▼
           ┌───────────────┐
           │ F Pub+Alarm+  │  ← realtime pub + simple alarm + replay tests
           │   Tests        │
           └───────┬───────┘
                   ▼
           ┌───────────────┐
           │ G CI + tag    │  ← plan-1-complete + gw-v0.1.0
           └───────────────┘
```

### 2.2 模块目录（v2 扩展）

```
ruisheng-gw/
├── pyproject.toml
├── src/ruisheng_gw/
│   ├── __init__.py
│   ├── main.py                 # asyncio 入口 + REQUIRED schema check
│   ├── config.py               # pydantic-settings, extra="forbid"
│   ├── logging_setup.py        # v2: structlog + correlation context vars
│   ├── health.py               # v2: aiohttp /health /ready /metrics (port 9090)
│   │
│   ├── protocol/               # Stage B — 纯字节↔结构，无 IO
│   │   ├── modbus_codec.py     # v2 renamed: RTU-on-TCP 编解码（非串行，无静默期）
│   │   ├── frames.py           # FC 3/5/6/16/19/20/21/22/100 dataclass + ExceptionResponse
│   │   ├── framer.py           # v2 NEW: 长度感知分帧 + idle-timeout resync + heartbeat stripper
│   │   ├── private_codes.py    # FC 13/26 BLOCKED stub（Plan 1 不 naive alias）
│   │   └── exceptions.py
│   │
│   ├── transport/              # Stage C
│   │   ├── tcp_server.py       # asyncio.start_server
│   │   ├── connection.py       # framer 驱动 + heartbeat_stripper + writer_generation
│   │   └── session.py          # dev_number → (writer, generation_id, bus_id)
│   │
│   ├── domain/                 # Stage D
│   │   ├── device.py           # Device 状态机
│   │   ├── point.py            # 标度换算（overflow/NaN 处理）
│   │   ├── registry.py         # DB load: (dev_number, device_id, usr_group, bus_id)
│   │   └── alarm_simple.py     # v2 NEW: threshold check → AlarmEvent（无状态机）
│   │
│   ├── scheduler/              # Stage E
│   │   ├── clock.py            # v2 NEW: Clock protocol (RealClock + FakeClock)
│   │   ├── poller.py           # per-device 协程 + writer re-lookup per poll
│   │   ├── supervisor.py       # create_task + 手动 supervise + restart/quarantine
│   │   └── bus_lock.py         # per-bus asyncio.Lock（keyed by registry.bus_id）
│   │
│   ├── persistence/            # Stage E
│   │   ├── repository.py       # SQLAlchemy 2.0 async，含 usr_group 过滤
│   │   ├── batch_writer.py     # 修 MinTransactionCnt=1 + Clock 注入
│   │   └── wal.py              # v2 NEW: D:\ruisheng\gw\wal\*.ndjson + replay
│   │
│   └── pubsub/                 # Stage F
│       ├── publisher.py        # channel:realtime:v1:{dev_number}
│       └── schemas.py          # v2 NEW: RealtimeEvent + AlarmEvent pydantic (schema_version)
│
├── tests/
│   ├── unit/                   # ~130 case（Clock-driven for timer logic）
│   ├── integration/            # ~25 case (testcontainers session-scope)
│   ├── replay/                 # ~15 case (pcap_gen corpus, 保 pkt.time)
│   └── property/               # v2 NEW: ~5 Hypothesis tests
│
├── ci_lint/
│   └── tenant_filter_lint.py   # v2 NEW: AST 扫 gw SQL 强制 usr_group 谓词
│
├── CHANGELOG.md                # Stage G
├── README.md                   # Stage G
└── docs/
    ├── gw-logs.md              # v2 NEW: 日志 schema
    ├── gw-metrics.md           # v2 NEW: Prometheus metrics 清单
    └── PLAN-1-ROLLBACK-RUNBOOK.md  # v2 NEW
```

---

## 3. Stage 拆分

### Stage A — 骨架 + observability（~8 task，v2 从 5 扩）

**Files**：pyproject.toml / `__init__.py` / `main.py` / `config.py` / `logging_setup.py` / `health.py`。

| # | Task |
|---|---|
| A1 | 新建子包 pyproject + workspace 注册 + `uv sync --all-packages` 能装 |
| A2 | `main.py` 骨架 + `REQUIRED = 20260415`（hardcoded literal，与 G7 #28-B 两版本字段分离一致）schema 校验 + `alembic head` 校验（分别 exit code 1/2） |
| A3 | `config.py` pydantic-settings（`extra="forbid"`）+ `.env.example` + `--print-config` 子命令（v2 D7）+ required-field 启动断言 |
| A4 | **v2 D1 日志 schema**：`logging_setup.py` structlog JSON 输出 + `context_vars.bind(conn_id, dev_number, bus_id, usr_group)` 自动注入；`docs/gw-logs.md` 定义 schema |
| A5 | **v2 D2 /health 端点**：`health.py` aiohttp :9090 暴露 `/health`（liveness: 进程存活）+ `/ready`（readiness: db/redis 连通 + last_flush_ts < 5s）+ `/metrics`（Prometheus 纯文本） |
| A6 | `test_startup.py`（schema mismatch / alembic mismatch / config invalid 三类 exit 码分别断言）+ CI job `gw-smoke`（`python -m ruisheng_gw --check-only` 启动即退） |
| A7 | `docs/gw-metrics.md` 初稿（列全量 metric 名 + label + 类型 + 用途） |
| A8 | Stage A tag `plan-1-stage-a-complete` + PROGRESS |

**验收**：`python -m ruisheng_gw --check-only` 正常退；`curl :9090/health` 返 200 JSON；结构化日志一条含全 context；ruff/mypy clean。

### Stage B — 协议层（~8 task，v2 扩）

**Files** under `protocol/`：`modbus_codec.py`（v2 改名）/ `frames.py` / `framer.py`（v2 新）/ `private_codes.py` / `exceptions.py`。

| # | Task |
|---|---|
| B1 | `modbus_codec.py` CRC16(0xA001) + **wire byte order 明确 lo 先 hi 后**（v2 A7）+ 单元测试含 Modbus Appendix B 官方向量 |
| B2 | `frames.py` FC 3（read holding）req + resp dataclass + 序列化/反序列化 |
| B3 | `frames.py` FC 5/6/16（write single coil / single holding / multi holding） |
| B4 | **v2 A2+A3+A4 合入**：`frames.py` **`ExceptionResponse`**（`(fc \| 0x80) + errcode`）+ **FC 0x19 心跳帧**（§A.5 强制）+ **FC 22 含义纠正 "低功耗注册（私有）"**（非文件记录）；任何 FC 解码入口先测 `(fc & 0x80)` 分派 Exception |
| B5 | `private_codes.py` **v2 BLOCKED**：FC 13/26 不 naive alias；在 `decode_private_fc()` 入口 raise `PrivateCodeNotImplemented(vendor=...)`；registry 预留 `device.vendor_id` 字段（内存 only，不入 DB，Plan 1.5 迁 shared）；**Plan 1 要求 Plan 0 pcap_gen 15 corpus 不含 FC13/26**（已验） |
| B6 | `framer.py` **v2 A1 核心**：**长度感知分帧**（FC 分派 bytecount）+ idle-timeout 300ms 重同步 + **v2 A6 DTU 厂商 heartbeat stripper**（regex 剥 ASCII `[\r\n][A-Z#@!][\w=:.,]+` 行）；显式禁止 CRC-boundary（docstring 警示） |
| B7 | `property/test_modbus_property.py` **v2 E5 Hypothesis**：随机字节 → framer 分帧 → frames.py 解码 → re-encode → byte-identical round-trip |
| B8 | Stage B tag + PROGRESS |

**验收**：9 FunCode × {正常编解码, CRC 失败, Exception 响应, 厂商 heartbeat 混入} = 36 case；Hypothesis 100 examples 全绿；mypy strict clean。

### Stage C — 传输层（~5 task）

**Files** under `transport/`：`tcp_server.py` / `connection.py` / `session.py`。

| # | Task |
|---|---|
| C1 | `tcp_server.py` `asyncio.start_server` 骨架 + bind + accept loop + `TCP_NODELAY`（v2 A1 协议层需）+ graceful shutdown signal handler（Windows/POSIX 分支） |
| C2 | `connection.py` 驱动 framer（非 CRC-boundary）+ heartbeat_stripper 前置过滤 + 粘包 buffer 64KB（v2 从 4KB 扩，A6 误丢修）+ parse-failures-in-a-row >= 10 才断连（非 buffer size） |
| C3 | `connection.py` FC 0x19 心跳 timeout — 3× 心跳周期（默认 30s × 3）无 FC 0x19 帧 → 断连 + device OFFLINE（v2 A3 精确定义） |
| C4 | **v2 B1+B7 session 核心**：`session.py` 用 **`dev_number` 为 key**（shared ORM UQ 全局唯一）；存 `{dev_number: (writer, generation_id, bus_id, usr_group)}`；TCP 重连**原子替换 writer + generation++**（保 bus_id 不变）；poller 每 poll `get_writer(dev_number)` re-lookup（v2 B7）；metric `writer_stale_total{dev_number}` |
| C5 | Stage C tag + PROGRESS |

**验收**：integration stub 发 "FC100 注册 + FC3 轮询 + 厂商 ASCII keepalive + FC 0x19 心跳" 混合字节；session 正确；模拟断连+重连（换源 port）后 generation++ 且 bus_id 不变。

### Stage D — 域模型（~5 task）

**Files** under `domain/`：`device.py` / `point.py` / `registry.py` / `alarm_simple.py`（v2 新）。

| # | Task |
|---|---|
| D1 | `device.py` Device 状态机（UNREGISTERED → ONLINE → OFFLINE）+ transition hook 写 `is_online`；clock 注入（`last_seen: float`） |
| D2 | `point.py` 标度换算：raw → engineering（`× point_ratio + point_offset`）→ display（`× user_ratio + user_point_offset`）；**边界处理**（v2 I3）：ratio=0 raise / overflow 截断 + metric / NaN → `None` |
| D3 | `registry.py` DB load：`SELECT dev_number, device_id, usr_group, bus_id_inferred, update_interval_decisec FROM devices`；内存 map keyed by `dev_number`；**bus_id 推断规则**（v2 B1）：若 devices 表未来加 bus_index 列则用之（触发 shared 0.2.0），Plan 1 MVP 用 `dtu_ip`（注册时从 connection.peer 推断） |
| D4 | **v2 C1 alarm_simple**：`alarm_simple.py` `check_threshold(dev_number, point_id, value) -> AlarmEvent \| None`；读 device_points.min_val/max_val/alarm_level；**无状态机**（每次超阈都 fire，去重留 Plan 1.5）；返回 AlarmEvent 由 batch_writer flush 时带走 pub |
| D5 | Stage D tag + PROGRESS |

**验收**：Device 状态机 4 条边 + Point 标度 10 case（ratio=0 / overflow / NaN / 正常）+ alarm_simple 6 case（无阈值 / 低阈 / 高阈 / 正常 / NaN / ratio=0）。

### Stage E — 调度 + 持久化 + WAL（~10 task，v2 最重）

**Files**：`scheduler/clock.py`（v2 新）/ `poller.py` / `supervisor.py` / `bus_lock.py` + `persistence/repository.py` / `batch_writer.py` / `wal.py`（v2 新）+ `ci_lint/tenant_filter_lint.py`（v2 新）。

| # | Task |
|---|---|
| E1 | **v2 E1 Clock**：`scheduler/clock.py` `Clock` 协议（`async def sleep(seconds)` + `def monotonic() -> float`）；`RealClock`（prod）+ `FakeClock`（test 支持 `advance(seconds)`）；所有 timer-based 代码接受 `clock: Clock` 构造参 |
| E2 | `bus_lock.py` per-`bus_id` `asyncio.Lock` + 15s `acquire` timeout + `get_bus_lock(bus_id)` 工厂；测试用 FakeClock 确定性 |
| E3 | `poller.py` per-device 协程 — interval 读 `device.update_interval_decisec`（`validators.rs485` 校验）+ 每 poll `session.get_writer(dev_number)` re-lookup（v2 B7） |
| E4 | `supervisor.py` **`create_task` + 手动 supervise**（v2 F2，放弃 TaskGroup）：`on_task_done` callback 捕获 exc → restart 计数（FakeClock 窗口滚动）→ 10min 3 次 quarantine；metric `poller_restart_total{dev_number,reason}` |
| E5 | `batch_writer.py` queue(maxsize=10000) + **Clock-driven** 100ms timer + 500 行双阈 flush + back-pressure drop-**tail**（v2 与 上游 §5.11 对齐，修 v1 drop-oldest 方向错）+ retry 3× 指数退避 |
| E6 | `repository.py` SQLAlchemy 2.0 async bulk insert `point_data_realtime` UPSERT + `point_data_history` INSERT（同事务）；BYPASSRLS role；**所有查询强制含 `usr_group` 谓词**（12 FORCE RLS 表，point_data_realtime 无 usr_group 除外） |
| E7 | **v2 D3 WAL**：`persistence/wal.py` 实现 `D:\ruisheng\gw\wal\YYYYMMDD-HHMM.ndjson`（Windows 路径）/ `/var/log/ruisheng/gw/wal/*.ndjson`（Linux）+ 1GB 单文件 rotate + 10GB 总量 drop 最老 + metric + 启动时 replay（读所有 wal 按时间序重插后删） |
| E8 | **v2 B6 CI lint**：`ci_lint/tenant_filter_lint.py` AST 扫 `ruisheng-gw/src/` 所有 SQLAlchemy query，涉及 12 FORCE RLS 表必须含 `usr_group == X` 或 `.filter_by(usr_group=...)` 或 `.where(Model.usr_group == ...)` 谓词，否则 exit 1；CI job `gw-tenant-lint` |
| E9 | Integration — FakeClock fake device 轮询 10s（压缩时间），DB 写 ≥ 预期 + batch drop-tail 正确 + WAL fallback（forced PG down）+ 重放；testcontainers **session-scope**（v2 E4） |
| E10 | Stage E tag + PROGRESS |

**验收**：1000 帧 /10s → DB 1000 行 + queue 无持续高水位；强制 PG down → WAL 文件生成，PG 恢复后重放 0 丢失；CI tenant-lint 红/绿两路都过。

### Stage F — Pub/sub + Alarm + Tests（~8 task，v2 扩）

**Files**：`pubsub/publisher.py` / `pubsub/schemas.py`（v2 新）+ `tests/replay/pcap_reader.py` / `test_replay_15_corpus.py` / `tests/integration/test_end_to_end.py`。

| # | Task |
|---|---|
| F1 | **v2 B2 `pubsub/schemas.py`**：`RealtimeEvent`（`schema_version: Literal[1]`, `dev_number`, `point_id`, `rt_value`, `org_value`, `recorded_at`）+ `AlarmEvent`（`schema_version: Literal[1]`, `dev_number`, `point_id`, `value`, `threshold`, `level`, `fired_at`）pydantic 模型（v2 MVP 放 gw 内；Plan 1.5 迁 shared） |
| F2 | `pubsub/publisher.py`：`publish_realtime(event: RealtimeEvent)` → `channel:realtime:v1:{dev_number}`；`publish_alarm(event: AlarmEvent)` → `channel:alarm:v1:{dev_number}`；fire-and-forget，失败 log + metric `redis_publish_fail_total{channel}` 不阻塞；**batch_writer flush 成功后才 publish**（顺序保 DB-first） |
| F3 | **v2 B2 contract test**：`tests/integration/test_redis_contract.py` — 订阅 channel，断言每条消息 `RealtimeEvent.model_validate_json(msg.data) not raise`；字段完整 + `schema_version == 1` |
| F4 | **v2 E3 replay 保 timing**：`tests/replay/pcap_reader.py` 保留 `pkt.time`，按 delta `await clock.sleep()`；`--fast` 模式 `÷100` CI 用；nightly 1 slow-replay case 真时间 |
| F5 | `test_replay_15_corpus.py` — 消费 Plan 0 15 corpus（5 device type × 3 seed）；**断言 `row.rt_value == exp_val`**（v2 B4 修列名）+ Redis pub 收到对应事件 + alarm 超阈 case 验 publish_alarm |
| F6 | `test_end_to_end.py` — testcontainers session-scope PG+Redis + fake device + 验 300s 无丢帧 + P95 flush <100ms **assertion**（pytest-benchmark） |
| F7 | **v2 E6 branch coverage**：`.coveragerc` `branch = True`；line 85% + branch 75% 双门槛；F5 coverage 报告对比 |
| F8 | Stage F tag + PROGRESS |

**验收**：replay 15 全绿 + contract test 无 ValidationError + P95 flush <100ms + coverage 双门槛。

### Stage G — CI + release（~5 task）

| # | Task |
|---|---|
| G1 | `.github/workflows/ci.yml` 扩展：`gw-unit` / `gw-integration` / `gw-replay` / `gw-tenant-lint`（v2 新）/ `gw-smoke`（Stage A） |
| G2 | `.github/workflows/release-gw.yml`（类 G7 release-shared.yml，tag `gw-v*.*.*` → GitHub Release，**Python heredoc 提取 CHANGELOG** — 避 mawk/gawk 坑） |
| G3 | `ruisheng-gw/CHANGELOG.md` 0.1.0 初始条目 + `docs/RELEASE.md` 补 gw 段 + `docs/PLAN-1-ROLLBACK-RUNBOOK.md`（v2 D4） |
| G4 | `docs/gw-logs.md` / `docs/gw-metrics.md` 定稿 + README.md 更新 Plan 1 checkbox |
| G5 | tag `plan-1-complete` + `gw-v0.1.0` 真推 → 验证 GitHub Release non-draft |

---

## 4. Data Flow

### 4.1 启动

```
main.py:run_server()
  ├─ config.load(extra="forbid")          # v2 D7 严格校验
  ├─ assert SHARED_SCHEMA_VERSION == REQUIRED  (不匹配 exit 1)
  ├─ assert alembic head current              (未 head exit 2)
  ├─ logging_setup.configure()                # v2 D1 structlog
  ├─ health_app = start_health_server(:9090)  # v2 D2
  ├─ db_engine = create_async_engine(DATABASE_URL)
  ├─ redis = Redis.from_url(REDIS_URL)
  ├─ wal_replay(db_engine)                    # v2 D3 启动时重放
  ├─ registry = await Registry.load_from_db(db_engine)
  ├─ clock = RealClock()                      # v2 E1
  ├─ supervisor = Supervisor(registry, redis, db_engine, clock)
  └─ await asyncio.start_server(supervisor.accept_connection, host, port)
         └─ SIGTERM/SIGINT → graceful shutdown
```

**graceful shutdown**：停 accept → 等当前 poll 完（≤2s）→ flush batch_writer（WAL fallback if DB 不可达）→ close redis/db → exit 0。

### 4.2 DTU 上行（v2 A1+A2+A6 修）

```
DTU connect TCP
    │
    ▼  (asyncio.start_server callback)
accept_connection(reader, writer)
    │
    ▼  loop
Connection.read_loop()
    │  raw_bytes = await reader.read(64KB)
    │
    ▼  heartbeat_stripper.filter(raw_bytes)     ← v2 A6
    │  (剥 ASCII 厂商 keepalive)
    │
    ▼  framer.feed(filtered)                    ← v2 A1 长度感知
    │  (FunCode 分派 bytecount + idle-timeout resync)
    │
    ▼  for frame in framer.pop_frames():
       │
       ├─► FunCode 100 (register)     → session.bind(dev_number, writer)
       │                                 → registry.device.state = ONLINE
       │                                 → devices.is_online=TRUE (batch)
       │
       ├─► FunCode 3 resp (poll data) → parse → point scaling → alarm check
       │                                 → batch_writer.queue.put(row + alarm?)
       │
       ├─► **FunCode | 0x80 (exception)** ← v2 A2
       │                                 → log WARN {dev_number, fc, errcode}
       │                                 → metric modbus_exception_total++
       │
       ├─► FunCode 0x19 (heartbeat)    ← v2 A3
       │                                 → 重置 session.last_heartbeat
       │
       └─► Bad CRC / framing resync   → metric + log warn，不断连
```

### 4.3 gw 主动轮询（v2 B7 writer re-lookup）

```
Supervisor (asyncio.create_task, 手动 supervise — v2 F2)
    ├─ poller_task(dev_number=X)  ───────┐
    │   while True:                        │
    │     await clock.sleep(interval_X)    │   ← v2 E1 Clock 注入
    │     writer, gen = session.get_writer(X)  ← v2 B7 per-poll
    │     if writer is None or gen stale:
    │       continue   # skip this round, wait next
    │     async with bus_locks[bus_id_X]:  │
    │       frame = encode_read_holding(points_X)
    │       await writer.write(frame)      │
    │   (resp 由 read_loop 异步收到)      │
    │
    ├─ poller_task(dev_number=Y) ─────────┘  (same bus → 串行化)
    └─ poller_task(dev_number=Z) ──────────  (不同 bus → 并发)
```

### 4.4 批量写 + WAL fallback（v2 D3 扩）

```
batch_writer.queue (asyncio.Queue, maxsize=10000)
    │ put_nowait → queue full → drop-**tail**（新数据丢，v2 对齐 §5.11）
    │                         + metric batch_drop_total++
    ▼
_flush_loop (Clock-driven):
    while True:
      trigger = await clock.wait_for_next(
        queue_get OR timer 100ms OR buffer >= 500
      )
      if buffer:
        try:
          async with db_engine.begin():
            await bulk_upsert point_data_realtime(buffer)
            await bulk_insert point_data_history(buffer)
          for row in buffer:
            publisher.publish_realtime_nowait(RealtimeEvent.from_row(row))
            if row.alarm_event:
              publisher.publish_alarm_nowait(row.alarm_event)
          buffer.clear()
        except DBError:
          retry 3× 指数退避
          if still fail:
            wal.append(buffer)         # v2 D3
            metric db_write_fail_total++
            buffer.clear()
```

### 4.5 Redis pub 约定（v2 B2 修）

- channel `channel:realtime:v1:{dev_number}` payload = `RealtimeEvent` JSON
- channel `channel:alarm:v1:{dev_number}` payload = `AlarmEvent` JSON
- **DB 写成功后才 pub** — 保 consumer 读 cache 命中时 DB 可查
- fire-and-forget：Redis down 不阻塞 DB 写，metric `redis_publish_fail_total{channel}`
- **api 侧兜底约定**（v2 B3 写入 spec）：api consumer 必须 `SELECT point_data_realtime WHERE updated_at > last_seen` 定期补漏

### 4.6 Registry hot reload — v2 明示不做

Plan 1 不实现 5s `update_flag` watcher（延 Plan 1.5）；启动后 device/point 冻结；配置变更需重启 gw。**运维文档明示期望 service gap ≤ 15s / 重启**。

---

## 5. Error Handling（v2 扩 17 行）

### 5.1 失败响应矩阵

| 故障 | 响应 | Metric | 影响 |
|---|---|---|---|
| CRC mismatch | 丢帧，不断连 | `crc_error_total{dev}++` + log warn | 单帧 |
| Framing resync（v2 A1）| idle 300ms buffer flush + skip | `framer_resync_total++` | 短时 |
| Parse failures ≥10 连续 | 断连 + 重置 buffer（**非 4KB size，v2 A6**）| `framing_error_total++` | 单 DTU |
| **ModBus exception resp（v2 A2）** | log WARN + metric，继续 | `modbus_exception_total{dev,code}++` | 单点异常 |
| Heartbeat FC 0x19 超时 3× | 断连 + device OFFLINE | `heartbeat_timeout_total++` | 单 DTU |
| **Writer stale（v2 B7）** | poller skip 本轮 | `writer_stale_total{dev}++` | 单轮 |
| Poller crash | supervisor restart + 10min 3 次 quarantine | `poller_restart_total{dev,reason}++` | 单 device |
| Bus lock timeout 15s | 放弃本轮 | `bus_lock_timeout_total{bus}++` | 单轮 |
| Queue full | **drop-tail（v2 与 §5.11 对齐）** | `batch_drop_total++` (>10/min P0) | 新数据丢 |
| DB write exception | retry 3× 指数退避 → WAL | `db_write_fail_total++` | 短时 |
| DB 长时 down（WAL >10GB）| 丢最老 + P0 | `wal_overflow_total++` | 极端损失 |
| **WAL replay failed（v2 D3）** | log ERROR + metric + 跳该条 | `wal_replay_fail_total++` | 局部 |
| Redis publish fail | fire-and-forget | `redis_publish_fail_total{channel}++` | api 短不可见 |
| Config invalid startup | raise → exit 3 | stderr | 全 gw 不启 |
| Schema mismatch startup | raise → exit 1 | stderr | 全 gw 不启 |
| alembic 未 head | raise → exit 2 | stderr | 全 gw 不启 |
| SIGTERM | 停 accept → wait poller ≤2s → flush → close → exit 0 | log info | — |

### 5.2 Supervisor restart（v2 F2 改 create_task）

```python
@dataclass
class DeviceHealth:
    dev_number: str
    restart_count: int = 0
    restart_window_start: float = 0.0
    quarantined: bool = False

class Supervisor:
    def __init__(self, clock: Clock, ...):
        self.clock = clock
        self.tasks: dict[str, asyncio.Task] = {}
        self.health: dict[str, DeviceHealth] = {}

    def start_poller(self, dev_number: str) -> None:
        task = asyncio.create_task(self._poller_task(dev_number))
        task.add_done_callback(lambda t: self._on_done(dev_number, t))
        self.tasks[dev_number] = task

    def _on_done(self, dev_number: str, task: asyncio.Task) -> None:
        if task.cancelled(): return  # graceful shutdown
        exc = task.exception()
        if exc is None: return
        # crash path
        h = self.health[dev_number]
        now = self.clock.monotonic()
        if now - h.restart_window_start > 600:
            h.restart_window_start = now
            h.restart_count = 0
        h.restart_count += 1
        if h.restart_count > 3:
            h.quarantined = True
            asyncio.create_task(self._mark_offline(dev_number))
            return
        backoff = min(2 ** h.restart_count, 30)
        asyncio.create_task(self._delayed_restart(dev_number, backoff))
```

**rehab 路径**（v2 D4）：Plan 1 单机模式下无 CLI，运维用 SQL `UPDATE devices SET is_online=TRUE WHERE id=?` 后重启 gw；Plan 2 api 层提供 `POST /admin/devices/{id}/rehabilitate`（记入 Plan 1 的 `docs/PLAN-1-ROLLBACK-RUNBOOK.md`）。

### 5.3 Backpressure

- queue.qsize > 8000（80% full）→ metric `queue_high_watermark` + log warn
- queue full → **drop-tail**（v2 §5.11 对齐）+ metric
- 持续 1min 接近 full → flush timer 加倍（100ms → 50ms）+ metric `backpressure_engaged`

### 5.4 安全边界

| 规则 | gw 实现 |
|---|---|
| gw 禁访 `scene_pages` / `scene_views`（§3.8.2）| repository.py import 白名单；CI lint `gw_no_scene_import` |
| **FORCE RLS 12 表 `usr_group` 过滤 CI lint**（v2 B6）| `ci_lint/tenant_filter_lint.py` AST 扫 |
| BYPASSRLS role 边界 | 单 gw 服务全 tenant；usr_group 过滤强制在 ORM 层 |
| 不连 api 端口 | gw 只 listen DTU + pub Redis + 写 PG；不 HTTP call api |
| 日志脱敏 | connection.py log raw bytes 不超 64B，仅 hex head/tail 各 16B（v2 E7 加 unit test） |

---

## 6. Testing Strategy（v2 大改）

### 6.1 分层

```
F5 replay      15 case  (pcap → gw → DB)      Plan 0 corpus 保 pkt.time
F6 integration 25 case  (testcontainers)      PG+Redis session-scope
unit          130 case  (pytest-asyncio)     Clock-driven，无 wall-clock
property       5 case   (Hypothesis)         modbus_codec round-trip
```

### 6.2 Unit（Clock 注入为主，v2 E1）

| 层 | case 数 | 关键点 |
|---|---|---|
| protocol/modbus_codec | 30 | CRC 8 向量 + wire byte order + round-trip |
| protocol/frames | 24 | FC 3/5/6/16/19/20/21/22/100 + ExceptionResponse × 正常+错码 |
| protocol/framer | 12 | 长度分派 + idle-timeout resync + heartbeat stripper |
| transport/connection | 15 | 粘包 1/2/N + parse-fail 10 连续 + heartbeat |
| domain/device | 8 | 状态机 4 条边 + 非法转换 |
| domain/point | 10 | 标度 + ratio=0/overflow/NaN |
| domain/alarm_simple | 6 | 阈值检查（无阈值/低阈/高阈/正常/NaN/ratio=0） |
| scheduler/clock | 4 | FakeClock.advance + 多 sleeper |
| scheduler/bus_lock | 6 | 同 bus 串行 / 异 bus 并发 / 15s timeout / 中途取消 / 锁 GC / poller restart 交织 |
| scheduler/supervisor | 8 | restart / window roll / quarantine / backoff / rehab |
| scheduler/poller | 6 | interval + writer re-lookup + stale skip |
| persistence/batch_writer | 12 | timer flush / size flush / queue full drop-tail / retry / WAL fallback / 全 Clock-driven |
| persistence/wal | 6 | 写 + rotate 1GB + drop 最老 10GB + replay + replay 部分失败跳 |
| pubsub/publisher | 4 | publish 成功 / 失败不 raise / 两 channel / schema_version |

### 6.3 Integration（25 case，v2 扩）

fixture：**session-scope** PG+Redis 容器（v2 E4 修，event loop 限制只影响 engine/session function-scope，容器可 session-scope），每 test `TRUNCATE` 业务表。

**25 key case**：register+poll / 100 帧批量 / CRC 错 / 心跳超时 / poller crash / quarantine / queue full / **writer stale 重连**（v2 B7）/ DB 短 down 重试 / **DB 长 down WAL 写入 + 重放**（v2 D3）/ Redis down / SIGTERM / 二次 SIGTERM（v2 E7）/ 同 bus 2 device 序列化 / 异 bus 并发 / 非法 dev_number / schema mismatch / alembic mismatch / **config invalid 启动失败**（v2 D7）/ PG down at boot / Redis down at boot / **厂商 heartbeat 混入**（v2 A6）/ **ModBus exception response 响应**（v2 A2）/ **Redis payload contract validate**（v2 B2）/ **日志脱敏 10KB frame**（v2）。

### 6.4 Replay（F5，保 timing，v2 E3）

```python
async def test_replay(gw_server, corpus_case, fast_mode=True):
    pcap_path, expected = corpus_case
    payloads = extract_tcp_payloads_with_timing(pcap_path, port=5020)
    async with open_connection(gw_server.host, gw_server.port) as (r, w):
        prev_ts = None
        for ts, payload in payloads:
            if prev_ts is not None:
                delta = (ts - prev_ts) / (100 if fast_mode else 1)
                await asyncio.sleep(delta)
            w.write(payload)
            await w.drain()
            prev_ts = ts
    await gw_server.batch_writer.drain_and_flush()   # v2 explicit API，非 sleep(0.5)
    rows = await fetch_point_data_realtime(gw_server.db, dev_number=expected["dev_number"])
    assert len(rows) == expected["frames_count"]
    for row, exp_val in zip(rows, expected["values"]):
        assert pytest.approx(row.rt_value) == exp_val   # v2 B4 列名修
```

15 case（5 dev type × 3 seed）全绿 + 1 nightly slow-replay（`fast_mode=False`）。

### 6.5 Property（Hypothesis，v2 E5）

```python
@given(st.binary(min_size=4, max_size=256))
def test_modbus_frame_round_trip(data: bytes):
    frames = list(framer.feed(data).pop_frames())
    for f in frames:
        re_encoded = frames_module.encode(f)
        assert re_encoded == f.raw_bytes  # round-trip
```

每 CI 跑 100 examples；nightly 1000。

### 6.6 Non-functional（F6）

- **P95 flush <100ms assertion**（pytest-benchmark，`@pytest.mark.benchmark` 统计 100 次 flush 样本）
- 1000 连接 flood / 24h soak — 延 Plan 1.5（spec 明示）

### 6.7 Coverage（v2 E6）

- `.coveragerc`：`branch = True`
- line 85% + branch 75% 双门槛
- 全仓加权：shared 91% + gw line 85% ≈ 88%

### 6.8 CI

| job | 内容 |
|---|---|
| `lint` | ruff + mypy (含 gw) |
| `gw-smoke`（v2 A）| `python -m ruisheng_gw --check-only` |
| `unit` | 含 gw unit 130 + property 5 |
| `integration` | testcontainers session-scope；gw integration 25 case |
| `replay` | pcap_gen 15 corpus（fast mode） |
| `gw-tenant-lint`（v2 B6）| `ci_lint/tenant_filter_lint.py` |
| `alembic-check` | 不变 |
| `schema-version-guard` | 不变 |
| nightly `slow-replay` | 1 case 真时间 replay |

---

## 7. Risks & 开放问题

### 7.1 关键风险（v2 更新）

| # | 风险 | 严重度 | 缓解 |
|---|---|---|---|
| R1 | 真 DTU 与 pcap_gen 合成差异 | 高 | **Plan 1 scope 外**（OQ-2 B）；Plan 1.5 现场校准 |
| R2 | `MinTransactionCnt=1` 修法 vague | 中 | E5 implementer pre-dispatch 详读 §7；不清 BLOCKED |
| R3 | bus_id 建模（v2 B1 缓解）| 低 | session key=dev_number 全局 UQ；TCP 重连只换 writer；若 Stage C 实测 1 DTU 多 bus 存在 → BLOCKED 走 OQ-4 shared 0.2.0 |
| R4 | asyncio 子任务异常传播（v2 F2 缓解）| 低 | 放弃 TaskGroup，用 `create_task + add_done_callback` 手动 supervise |
| R5 | Windows / Linux CI 差异 | 中 | signal handler 分 `sys.platform`；integration 只 Linux CI 跑 |
| R6 | Redis pub 与 DB 原子性（v2 B3 缓解）| 低 | api 侧 `updated_at > last_seen` 兜底约定写入 spec；alarm outbox 给 Plan 1.5 |
| R7 | coverage 85% 可达性 | 中 | F7 若 < 85% BLOCKED；(a) 补测试；(b) 记 tech debt |
| R8 | pcap_gen 无多 bus 场景 | 中 | F5 验单 device；per-bus 靠 F6 integration case 14 |
| **R9 (v2)** | **协议正确性实战差异**（长度感知 framer 在真厂商 vendor-specific 帧上 bytecount 计算公式不同）| 中 | Plan 1 只保 standard FC 3/5/6/16/19/20/21/22/100；私有 FC13/26 BLOCKED（v2 A5） |
| **R10 (v2)** | **WAL 文件在 Windows 路径 `D:\ruisheng\gw\wal\` vs Linux 差异** | 低 | `wal.py` 读 `sys.platform` 选路径；integration test 在两平台分别 mock |
| **R11 (v2)** | **tenant_filter_lint 误报**（ORM 动态构造 query）| 中 | lint 只覆盖明确 `filter`/`where` 调用；`# noqa: tenant-lint` 转义（需独立审）|

### 7.2 开放问题（全部已决）

| OQ | 决定 |
|---|---|
| OQ-1 bus_id | **A (v2 修) — session key=dev_number；bus_id 从 registry 推断；shared 不改** |
| OQ-2 真 DTU | **B — Plan 1.5** |
| OQ-3 多租户 | **A — 单 gw BYPASSRLS + CI tenant-filter lint** |
| OQ-4 shared bump | **A — 若需（暂不）** |

---

## 8. Appendix — Plan 0 已就绪可直接消费

| Plan 0 产出 | gw 消费点 |
|---|---|
| ruisheng-shared 0.1.0 | `FunCode.normalize(13) == 3`（仅语义标准 FC，v2 不作私有解码） |
| ORM 26 表 | repository.py + registry.py import |
| validators.rs485 | poller.py `min_poll_interval_decisec(baud, device_count)` |
| alembic 7 迁移 head `959079e6cae9` | 启动时 check |
| docker-compose.dev.yml | `task up` 起 PG+Redis |
| pcap_gen 15 corpus（F5）| replay 测试直读，**v2 保留 pkt.time** |
| testcontainers fixture E1 | **v2 升级 session-scope** |
| embedded_pg E2 | Windows 无 Docker 备胎 |
| CI 5 job 基底 | 扩 `gw-unit/integration/replay/tenant-lint/smoke` |
| pre-commit schema-version-guard | shared 改动自动校 |
| release-shared.yml pattern（G7）| copy → release-gw.yml |

---

**End of v2 spec.** 5-role adversarial review 回应完整。Ready to invoke `superpowers:writing-plans` for detailed Plan 1 implementation plan.
