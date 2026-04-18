# Plan 1 — `ruisheng-gw` 设备采集网关设计

**日期**：2026-04-18
**状态**：Brainstorming → ready for writing-plans
**上游 spec**：`docs/superpowers/specs/2026-04-13-ruisheng-iot-design.md` §2.2
**依赖产出**：Plan 0 `plan-0-complete` + `shared-v0.1.0`（ruisheng-shared 0.1.0）

---

## 1. 概览

### 1.1 系统定位

`ruisheng-gw` 是设备采集网关。作为 TCP server 接受 DTU 连接（DTU 下挂 1 或多条 RS485 总线，每总线串接多个 ModBus 从机）。核心职责：

1. **解析设备上行帧**：注册（FunCode 100）、周期上报（FunCode 3 resp）、心跳
2. **主动下发轮询请求**读取实时点位（FunCode 3/5/6/16）
3. **标度换算 + 批量入库**（`point_data_realtime` UPSERT + `point_data_history` INSERT）
4. **实时点位 Redis pub**（`channel:realtime:{dev_number}`）供 api/web 消费

### 1.2 关键决策（Brainstorm 锁定）

| 决策 | 选择 |
|---|---|
| **Scope** | B — MVP-ready core（含 transport + 10 FunCode 协议 + per-device scheduler + batch_writer + device 基本状态机 + Redis realtime pub）|
| **并发 & 容错** | B — Per-device 轮询协程 + asyncio.TaskGroup supervisor 重启 + per-RS485-bus asyncio.Lock 序列化 |
| **测试策略** | B — pytest Unit + testcontainers integration + **Plan 0 pcap_gen 15 corpus replay** |
| **Stage 结构** | A — 7 Stage Plan 0 镜像（A scaffold / B protocol / C transport / D domain / E scheduler+persistence / F pubsub+tests / G CI+release）|
| **bus_id 建模**（OQ-1）| 运行时推断：`bus_id = (dtu_ip:port)`；**不改 shared 0.1.0** |
| **真 DTU 测试**（OQ-2）| 不在 Plan 1 scope，延后 Plan 1.5 专门现场校准阶段 |
| **多租户模型**（OQ-3）| 单 gw 进程 + BYPASSRLS role 服务全租户；tenant 识别靠 device 所属 wx_group FK |
| **Shared bump 策略**（OQ-4）| 若 Plan 1 期间发现 shared 模型需改 → 正式 shared 0.2.0 release + SHARED_SCHEMA_VERSION bump |

### 1.3 明确不包含（Plan 1.5 承接）

- 告警状态机（WaringFlag 转换 + alarm_records 写入）
- 离线命令队列（control:cmd 订阅 + 执行）
- 波形 BLOB 编解码 §4.2.C（waveform_history 写入）
- 5s `update_flag` 配置热更新 watcher
- 真 DTU 接入 + 现场协议差异修补
- Alarm outbox stream（alarm_outbox 表 Plan 0 已建，Plan 1.5 启用 `stream:alarm:fired`）

### 1.4 Plan 1 产出概览

| 项 | 值 |
|---|---|
| Stage 数 | 7 |
| Task 估 | ~37 |
| 新增代码 | ~3-4K LOC |
| 新增测试 | ~155 case（120 unit + 20 integration + 15 replay）|
| Plan bug 预估 | 15-25（类 Plan 0 节奏） |
| 时间估 | 6-8 周（全职） |
| 最终 tag | `plan-1-complete` + `gw-v0.1.0` + GitHub Release |

---

## 2. Architecture

### 2.1 Stage 依赖图

```
          ┌─────────┐
          │ A 骨架  │  uv workspace + main.py + schema check
          └────┬────┘
               │
     ┌─────────┼─────────┐
     ▼         ▼         ▼
 ┌───────┐ ┌───────┐ ┌───────┐
 │ B 协议│ │ C 传输│ │ D 域模型│  (逻辑可并行；subagent-driven 下仍串行派发)
 └───┬───┘ └───┬───┘ └───┬───┘
     │         │         │
     └────┬────┴────┬────┘
          ▼         ▼
       ┌───────────────┐
       │ E 调度+持久化 │  ← 消费 B+C+D 产出
       └───────┬───────┘
               ▼
       ┌───────────────┐
       │ F Pub/sub+测试│  ← 消费 E 落地 + Plan 0 pcap_gen corpus
       └───────┬───────┘
               ▼
       ┌───────────────┐
       │ G CI + tag    │  ← plan-1-complete + gw-v0.1.0
       └───────────────┘
```

### 2.2 模块目录（对应 spec §2.2）

```
ruisheng-gw/
├── pyproject.toml              # uv workspace member
├── src/ruisheng_gw/
│   ├── __init__.py
│   ├── main.py                 # asyncio 入口 + REQUIRED schema check
│   ├── config.py               # pydantic-settings
│   │
│   ├── protocol/               # Stage B — 纯字节↔结构，无 IO
│   │   ├── modbus_rtu.py       # CRC16(0xA001) + 帧编解码
│   │   ├── frames.py           # FunCode 3/5/6/16/20/21/22/100 dataclass
│   │   ├── private_codes.py    # 13/26 归一化（用 shared.FunCode.normalize）
│   │   └── exceptions.py       # ProtocolError/CRCMismatchError/FramingError
│   │
│   ├── transport/              # Stage C
│   │   ├── tcp_server.py       # asyncio.start_server
│   │   ├── connection.py       # 粘包/超时/CRC 状态机
│   │   └── session.py          # connection ↔ DTU ↔ bus_id 映射
│   │
│   ├── domain/                 # Stage D
│   │   ├── device.py           # Device 状态机
│   │   ├── point.py            # 点位 + 标度换算
│   │   └── registry.py         # 内存 device/point registry
│   │
│   ├── scheduler/              # Stage E
│   │   ├── poller.py           # per-device 轮询协程
│   │   ├── supervisor.py       # TaskGroup + restart 策略
│   │   └── bus_lock.py         # per-bus asyncio.Lock
│   │
│   ├── persistence/            # Stage E
│   │   ├── repository.py       # SQLAlchemy 2.0 async
│   │   └── batch_writer.py     # 修 MinTransactionCnt=1
│   │
│   └── pubsub/                 # Stage F
│       └── publisher.py        # channel:realtime:{dev_number}
│
├── tests/
│   ├── unit/                   # ~120 case
│   ├── integration/            # ~20 case (testcontainers)
│   └── replay/                 # ~15 case (pcap_gen corpus)
│
├── CHANGELOG.md                # Stage G
└── README.md                   # Stage G
```

---

## 3. Stage 拆分

### Stage A — 骨架 scaffold（~5 task）

**Files**：`ruisheng-gw/pyproject.toml` / `src/ruisheng_gw/__init__.py` / `main.py` / `config.py`；根 pyproject workspace.members 扩 `"ruisheng-gw"`；`[tool.taskipy.tasks]` 加 `gw`。

| # | Task |
|---|---|
| A1 | 新建子包 pyproject + workspace 注册 + `uv sync --all-packages` 能装 |
| A2 | `main.py` 骨架 + `REQUIRED = 20260415` schema 校验（**hardcoded literal** 于 gw 代码，不自动从 shared 读 — 当 shared 发 0.2.0 bump 时 gw PR 必须同步改 `REQUIRED`，与 G7 #28-B 两版本字段分离一致）；`SHARED_SCHEMA_VERSION != REQUIRED` raise RuntimeError |
| A3 | `config.py` pydantic-settings + `.env.example` + GW env 完整列表（`GW_LISTEN_HOST/PORT/DATABASE_URL/REDIS_URL`）|
| A4 | `test_startup.py`（schema mismatch raise；正常值通过）+ CI job `gw-smoke` |
| A5 | Stage A tag `plan-1-stage-a-complete` + PROGRESS |

**验收**：`uv run python -m ruisheng_gw --check-only` 启动即退，`ruff check .` + `mypy .` clean，2 startup test pass。

### Stage B — 协议层 protocol（~6 task）

**Files** under `ruisheng-gw/src/ruisheng_gw/protocol/`：`modbus_rtu.py` / `frames.py` / `private_codes.py` / `exceptions.py`。

| # | Task |
|---|---|
| B1 | `modbus_rtu.py` CRC16(0xA001) + 3 向量测试（复用 Plan 0 F2 已验向量 `010300000002=0x0BC4` / 空=0xFFFF / `00`=0x40BF）|
| B2 | `frames.py` FunCode 3（read holding）req + resp dataclass + 序列化/反序列化 |
| B3 | `frames.py` FunCode 5（write single coil）+ 6（write single holding）+ 16（write multi holding）|
| B4 | `frames.py` FunCode 20/21/22（读写 File record — 波形配置相关）+ 100（私有扩展，spec §A.4 注册帧）|
| B5 | `private_codes.py` 13/26 归一化（用 `ruisheng_shared.enums.FunCode.normalize`）+ 单元测试 |
| B6 | Stage B tag + PROGRESS |

**验收**：单元覆盖 10 FunCode × {编码, 解码, CRC 失败 raise} ≥ 30 case，mypy strict clean。

### Stage C — 传输层 transport（~5 task）

**Files** under `ruisheng-gw/src/ruisheng_gw/transport/`：`tcp_server.py` / `connection.py` / `session.py`。

| # | Task |
|---|---|
| C1 | `tcp_server.py` `asyncio.start_server` 骨架 + bind + accept loop + graceful shutdown signal handler（Windows/POSIX 分支）|
| C2 | `connection.py` 粘包 buffer — `recv` 累积 + 按 ModBus RTU CRC 边界切帧 |
| C3 | `connection.py` timeout — 3× 心跳失败断连（spec §2.2.5）+ metrics `connections_active` |
| C4 | `session.py` Device 注册帧解析 → `register_device(dev_number, bus_id=dtu_ip_port)` 内存 session map；断连清理 |
| C5 | Stage C tag + PROGRESS |

**验收**：integration stub client 发"FunCode 100 注册 + FunCode 3 轮询"字节序列，session map 正确 + 心跳超时状态转 offline。

### Stage D — 域模型 domain（~4 task）

**Files** under `ruisheng-gw/src/ruisheng_gw/domain/`：`device.py` / `point.py` / `registry.py`。

| # | Task |
|---|---|
| D1 | `device.py` Device 状态机（UNREGISTERED → ONLINE → OFFLINE）+ transition hook（写 `is_online`）|
| D2 | `point.py` 标度换算：整数 raw → float engineering (`raw × point_ratio + point_offset`) → float display (`× user_ratio + user_point_offset`)；溢出/NaN 处理 |
| D3 | `registry.py` DB load — 启动时 `SELECT * FROM devices + device_points` → 内存 map |
| D4 | Stage D tag + PROGRESS |

**验收**：Device 状态机 4 条边 + Point 标度 6 case（正/负/零 ratio + overflow）单元全绿。

### Stage E — 调度 + 持久化（~7 task，最重）

**Files**：`scheduler/poller.py` / `supervisor.py` / `bus_lock.py` + `persistence/repository.py` / `batch_writer.py`。

| # | Task |
|---|---|
| E1 | `bus_lock.py` per-bus `asyncio.Lock` + `get_bus_lock(bus_id)` 工厂 + 并发序列化测试 |
| E2 | `poller.py` per-device 协程 — interval 读自 `device.update_interval_decisec`（`validators.rs485` 校验，Plan 0 已就绪）|
| E3 | `supervisor.py` 嵌套 TaskGroup：外层管 connection + 内层 per-device group；crash 计数（10min 内 3 次 quarantine）+ 指数退避 2/4/8s |
| E4 | `batch_writer.py` queue + 100ms timer + 500 行双触发 flush + back-pressure（queue full drop oldest + metric）+ retry 3 × 指数退避 + WAL fallback |
| E5 | `repository.py` SQLAlchemy 2.0 async bulk insert `point_data_realtime` UPSERT + `point_data_history` INSERT（BYPASSRLS role，服务全 tenant）|
| E6 | Integration — fake device 轮询 10s，DB 写入 ≥ 预期行数 + 无丢失 + batch timer 正常 flush |
| E7 | Stage E tag + PROGRESS |

**验收**：testcontainers PG，fake device 发 1000 帧/10s，DB 终态 1000 行，P95 flush < 100ms。

### Stage F — Pub/sub + replay tests（~6 task）

**Files**：`pubsub/publisher.py` + `tests/replay/pcap_reader.py` / `test_replay_15_corpus.py` + `tests/integration/test_end_to_end.py`。

| # | Task |
|---|---|
| F1 | `publisher.py` Redis pub `channel:realtime:{dev_number}` JSON payload，fire-and-forget，失败 log+metric 不阻塞 DB 写 |
| F2 | `tests/replay/pcap_reader.py` — scapy `rdpcap` 提取 TCP payload → 按 connection 分组 → 回放器 |
| F3 | `test_replay_15_corpus.py` — 消费 Plan 0 15 corpus（5 device type × 3 seed），喂 gw，断言 DB+Redis 对齐 `expected.json` |
| F4 | `test_end_to_end.py` — testcontainers PG+Redis + fake device + 验 300s 无丢帧 |
| F5 | coverage ≥ 85%（gw 复杂度高于 shared，阈值略低于 shared 91.09%）|
| F6 | Stage F tag + PROGRESS |

**验收**：replay 15 全绿 + testcontainers 端到端 + coverage 门槛。

### Stage G — CI + release（~4 task）

| # | Task |
|---|---|
| G1 | `.github/workflows/ci.yml` 扩展 `gw-unit` / `gw-integration` / `gw-replay` job（testcontainers PG+Redis）|
| G2 | `.github/workflows/release-gw.yml`（类 G7 release-shared.yml，tag `gw-v*.*.*` → Release，Python heredoc 提取 CHANGELOG — 避 mawk/gawk 坑）|
| G3 | `ruisheng-gw/CHANGELOG.md` 0.1.0 初始条目 + `docs/RELEASE.md` 补 gw 段 |
| G4 | README.md 更新 Plan 1 checkbox + tag `plan-1-complete` + `gw-v0.1.0` 真推验证 Release 发布 non-draft |

---

## 4. Data Flow

### 4.1 启动初始化

```
main.py:run_server()
  ├─ config.load() from env
  ├─ assert SHARED_SCHEMA_VERSION == REQUIRED  (不匹配 raise)
  ├─ assert alembic head current        (迁移未跑 raise)
  ├─ db_engine = create_async_engine(DATABASE_URL)
  ├─ redis = Redis.from_url(REDIS_URL)
  ├─ registry = await Registry.load_from_db(db_engine)
  ├─ supervisor = Supervisor(registry, redis, db_engine)
  └─ await asyncio.start_server(supervisor.accept_connection, host, port)
         └─ 永久运行直至 SIGTERM/SIGINT → graceful shutdown
```

### 4.2 DTU 连接上行

```
DTU connect TCP
    │
    ▼  (asyncio.start_server callback)
accept_connection(reader, writer)
    │
    ▼  loop 粘包 buffer
Connection.read_loop()
    │
    ├─► FunCode 100 (register)     → session.bind(dev_number, bus_id)
    │                                 → registry.device.state = ONLINE
    │                                 → devices.is_online=TRUE (batch_writer)
    │
    ├─► FunCode 3 resp (poll data) → parse → point scaling
    │                                 → batch_writer.queue.put(PointRow)
    │                                 → publisher.publish(channel:realtime:{dev}, json)
    │
    ├─► Heartbeat                   → 重置 timeout timer
    │
    └─► Unknown/Bad CRC             → metric++ + log warn，不断连
```

### 4.3 gw 主动轮询下行

```
Supervisor.TaskGroup
    ├─ poller_task(device_X)  ───────┐
    │   while True:                   │
    │     await asyncio.sleep(it_X)   │
    │     async with bus_locks[bus]:  ├── per-bus mutex 序列化（物理半双工）
    │       frame = encode_read_holding(device_X.points)
    │       await connection.write(frame)
    │   (resp 由 read_loop 异步收到)  │
    │                                  │
    ├─ poller_task(device_Y) ─────────┘  (same bus → 被 lock 序列化)
    └─ poller_task(device_Z) ───────────  (different bus → 并发 OK)
```

**Supervisor restart**：poller 抛异常 → TaskGroup 捕获 → 记 log + metric → 重启（最多 N=3 次 / 10min，超限 quarantine device）。

### 4.4 批量写（修 MinTransactionCnt=1）

```
batch_writer.queue (asyncio.Queue, maxsize=10000)
    │ 入队：read_loop 解包出 PointRow put_nowait
    │ queue full → drop oldest + metric `batch_drop_total++`
    ▼
_flush_loop:
    while True:
      trigger = await wait_for(queue.get(), timeout=0.1)   # 100ms timer
              OR len(buffer) >= 500                          # 500 行阈值
      if buffer:
        async with db_engine.begin():
          await bulk_insert point_data_realtime (UPSERT)
          await bulk_insert point_data_history (INSERT)
        buffer.clear()
        for row in flushed:
          publisher.publish_nowait(channel:realtime:{dev}, row.json())
```

**P95 目标**：flush < 100ms（spec §7 MVP 性能门）。

### 4.5 Redis pub 时序

- **DB 写成功之后** 才 publish — 保证 consumer 读 cache 命中时 DB 已可查
- **fire-and-forget**：Redis down 不阻塞 DB 写，仅 metric `redis_publish_fail_total++`

### 4.6 Registry 热更新（MVP 不做）

Plan 1 不实现 5s `update_flag` watcher；启动后 device/point 冻结；配置变更需重启 gw 生效（Plan 1.5 做）。

---

## 5. Error Handling

### 5.1 失败响应矩阵

| 故障 | 响应 | 可观测 | 影响 |
|---|---|---|---|
| CRC mismatch | 丢帧，不断连 | metric `crc_error_total{dev}++` + log warn | 单帧 |
| Framing error (buffer > 4KB) | 断连 + 重置 buffer | metric `framing_error_total++` | 单 DTU |
| Heartbeat timeout (3× 无帧) | 断连 + device OFFLINE + devices.is_online=FALSE | metric `heartbeat_timeout_total++` | 单 DTU |
| Poller crash | supervisor.restart；10min 3 次 → quarantine | metric `poller_restart_total{dev,reason}++` | 单 device |
| Bus lock timeout (15s) | 放弃本轮 | metric `bus_lock_timeout_total{bus}++` | 单总线单轮 |
| Queue full | drop oldest | metric `batch_drop_total++` (>10/min P0) | 轻微历史损失 |
| DB write exception | 重试 3× 指数退避 → WAL file | metric `db_write_fail_total++` | 短时不丢数据 |
| DB 长时 down (WAL >10GB) | 丢最老 + P0 告警 | metric `wal_overflow_total++` | 极端损失 |
| Redis publish fail | fire-and-forget，不重试 | metric `redis_publish_fail_total++` | api 短时不可见 |
| Schema mismatch startup | raise → exit 1 | stderr | 全 gw 不启 |
| alembic 未 head | raise → exit 2 | stderr | 全 gw 不启 |
| SIGTERM | 停 accept → wait poller（≤2s）→ flush → close → exit 0 | log info | — |

### 5.2 Supervisor restart 策略

```python
@dataclass
class DeviceHealth:
    device_id: int
    restart_count: int = 0
    restart_window_start: float = 0.0
    quarantined: bool = False

async def restart_poller(self, device_id: int, exc: Exception) -> None:
    h = self.health[device_id]
    now = time.monotonic()
    if now - h.restart_window_start > 600:    # 10min 滚动
        h.restart_window_start = now
        h.restart_count = 0
    h.restart_count += 1
    if h.restart_count > 3:
        h.quarantined = True
        await self.mark_device_offline(device_id, reason=f"quarantine_after_{h.restart_count}_restarts")
        return
    await asyncio.sleep(min(2 ** h.restart_count, 30))   # 指数退避
    self.task_group.create_task(self.poller_task(device_id))
```

**恢复**：运维通过 Plan 2 api `POST /admin/devices/{id}/rehabilitate`；Plan 1 仅落 quarantined 标记。

### 5.3 Backpressure

- queue.qsize() > 8000 → metric `queue_high_watermark` + log warn
- queue full → drop oldest + metric
- 持续 > 1min 接近 full → flush timer 加倍（100ms → 50ms）+ metric `backpressure_engaged`

**不做**：动态扩容 queue / 磁盘 spillover（Plan 1.5 议）

### 5.4 安全边界

| 规则 | gw 实现 |
|---|---|
| gw 禁访 `scene_pages` / `scene_views`（spec §3.8.2）| `repository.py` 只 import 允许 table；CI lint `gw_no_scene_import` |
| 多租户（OQ-3 A）| 单 gw BYPASSRLS role 服务全 tenant；tenant 识别靠 device.wx_group FK |
| 不连 api 端口（spec §2.1）| gw 只 listen DTU + pub Redis + 写 PG，不 HTTP call api |
| 日志脱敏（spec §2.8）| connection.py log raw bytes 不超 64B，仅 hex head/tail 各 16B |

---

## 6. Testing Strategy

### 6.1 分层

```
F3 replay      15 case  (pcap → gw → DB)     consume Plan 0 corpus
F4 integration 20 case  (testcontainers)     PG + Redis + fake device
unit          120 case  (pytest-asyncio)    per module mock
```

### 6.2 单元测试（Stage B/C/D/E）

| 层 | 覆盖 | case 估 |
|---|---|---|
| protocol/modbus_rtu | CRC16 + frame pack/unpack 10 FunCode × 正常+异常 | 30 |
| protocol/frames | 10 FunCode round-trip | 20 |
| transport/connection | 粘包 1/2/N + CRC 错 + heartbeat timeout | 15 |
| domain/device | 状态机 4 条边 + 非法转换 raise | 8 |
| domain/point | 标度 6 case | 10 |
| scheduler/bus_lock | 同 bus 序列化 + 异 bus 并发 | 4 |
| scheduler/supervisor | restart / window / quarantine | 8 |
| scheduler/poller | interval + validators.rs485 | 6 |
| persistence/batch_writer | timer/size flush + queue full + retry + WAL | 15 |
| pubsub/publisher | 成功 / 失败 不 raise | 4 |

### 6.3 Integration（Stage F4）

fixture 基于 Plan 0 E1 双轨（`postgres_url` / `redis_url` testcontainers + alembic upgrade head + seed）。

**20 关键 case**：register + poll 流 / 100 帧批量 / CRC 错 / 心跳超时 / poller crash + supervisor restart / quarantine / queue full / DB 短时 down 重试 / DB 长时 down WAL / Redis down / SIGTERM graceful / 2 device 同 bus 序列化 / 2 device 异 bus 并发 / 非法 dev_number / schema mismatch startup / ...

### 6.4 Replay（Stage F3，消费 Plan 0 pcap_gen）

```python
@pytest.fixture(params=list(CORPUS_DIR.glob("*.pcap")))
def corpus_case(request):
    pcap = request.param
    return pcap, json.loads(pcap.with_suffix(".expected.json").read_text())

async def test_replay(gw_server, corpus_case):
    pcap_path, expected = corpus_case
    payloads = extract_tcp_payloads(pcap_path, port=5020)
    async with open_connection(gw_server.host, gw_server.port) as (r, w):
        for ts, payload in payloads:
            w.write(payload)
            await w.drain()
    await asyncio.sleep(0.5)  # 让 gw flush
    rows = await fetch_point_data_realtime(gw_server.db, dev=expected["dev_number"])
    assert len(rows) == expected["frames_count"]
    for row, exp_val in zip(rows, expected["values"]):
        assert pytest.approx(row.point_value) == exp_val
```

15 case（5 device type × 3 seed）全绿 = Plan 0 → Plan 1 契约验证。

### 6.5 Mock 策略

| 组件 | 单元 | integration/replay |
|---|---|---|
| PostgreSQL | fakeredis 类物 | testcontainers |
| Redis | fakeredis | testcontainers |
| asyncio Streams | 手工构造 Reader/Protocol | 真 TCP 到 gw 端口 |
| 时间 | `pytest.approx` + `wait_for` timeout | 真时间 |
| 设备 | — | asyncio TCP client stub |

**不引入**：pytest-mock / asynctest（Plan 0 既有工具栈足够）。

### 6.6 Coverage 目标

- Plan 1 新增代码 ≥ 85%
- 全仓加权 ≥ 88%（shared 91% + gw 85%）
- 不新增 mutation job（Plan 2 前评估）

### 6.7 CI 新增

| CI job | 新增 |
|---|---|
| unit | +gw unit 120 case |
| integration | +gw integration 20 case |
| replay（新）| pcap_gen replay 15 case |
| alembic-check | 不变 |
| schema-version-guard | 不变 |
| lint | +gw ruff/mypy |
| gw-smoke（新，Stage A）| `python -m ruisheng_gw --check-only` 启动即退 |

---

## 7. Risks & 开放问题（已决）

### 7.1 关键风险

| # | 风险 | 严重度 | 缓解 |
|---|---|---|---|
| R1 | 真 DTU 与 Plan 0 pcap_gen 合成差异 | 高 | **Plan 1 scope 外**（OQ-2 B）；Plan 1.5 专门做 |
| R2 | `MinTransactionCnt=1` 修法 vague | 中 | E4 implementer pre-dispatch 先详读 spec §7；不清 → BLOCKED |
| R3 | bus_id 建模 | 中（**OQ-1 A 已缓解**）| 运行时推断 `bus_id = dtu_ip:port`；shared 不改；**若 Stage C 实测发现 1 DTU 多 bus 真实存在** → BLOCKED 回来走 OQ-4 A shared 0.2.0 流程 |
| R4 | asyncio.TaskGroup 子任务 raise 杀整组 | 中 | **嵌套 TaskGroup**：外层 connection，内层 per-device group |
| R5 | Windows 本地 / Linux CI 差异 | 中 | main.py signal handler 分 `sys.platform`；integration 只在 Linux CI 跑完整 |
| R6 | Redis pub 与 DB 原子性 | 低（Plan 1 scope）| 只 channel pub 不用 stream；alarm outbox 给 Plan 1.5 |
| R7 | coverage 85% 可达性 | 中 | F5 若 < 85% BLOCKED；(a) 补测；(b) 调门槛 + tech debt |
| R8 | pcap_gen 不含多 device/bus 场景 | 中 | F3 验单 device；per-bus 靠 F4 integration case 12 |

### 7.2 开放问题（已决）

| OQ | 决定 |
|---|---|
| OQ-1 bus_id 建模 | **A — 运行时推断，shared 不改** |
| OQ-2 真 DTU 测试 | **B — 延 Plan 1.5，Stage G 不加 runbook** |
| OQ-3 多租户模型 | **A — 单 gw BYPASSRLS 服务全 tenant** |
| OQ-4 shared bump 策略 | **A — 正式 shared 0.2.0 release（若触发）** |

---

## 8. Appendix — Plan 0 已就绪可直接消费

| Plan 0 产出 | gw 消费点 |
|---|---|
| ruisheng-shared 0.1.0 | `from ruisheng_shared.enums import FunCode`；`FunCode.normalize(13) == 3` |
| ruisheng-shared ORM 26 表 | repository.py import Device/DevicePoint/PointDataRealtime/PointDataHistory |
| ruisheng-shared validators.rs485 | poller.py `min_poll_interval_decisec(baud, device_count)` |
| alembic 7 迁移 head `959079e6cae9` | gw 启动时 alembic check |
| docker-compose.dev.yml | `uv run task up` 起 PG + Redis |
| pcap_gen 15 corpus (F5) | replay 测试直接读 |
| testcontainers + embedded_pg E1 fixture | integration test 复用 |
| CI 5 job 基底（G1）| 扩展 gw-unit/integration/replay job |
| pre-commit schema-version-guard（G2）| 改 shared 时自动校 |
| release-shared.yml pattern（G7）| 复制为 release-gw.yml |

---

**End of spec.** Ready to invoke `superpowers:writing-plans` for Plan 1 detailed plan document.
