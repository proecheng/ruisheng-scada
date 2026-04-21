# Serial Port Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `ruisheng-gw` 同时支持 TCP/DTU（现有）和 RS485 串口两种设备接入方式。

**Architecture:** pyserial-asyncio 把串口包装成 `(asyncio.StreamReader, asyncio.StreamWriter)`，与 TCP 路径接口完全一致，`Connection`、`SessionMap`、`poller` 零改动。串口设备在启动时静态预注册进 `SessionMap`（无需等待设备发心跳），响应帧通过 `slave_addr → dev_number` 查 Registry 路由。

**Tech Stack:** Python asyncio、pyserial-asyncio 0.6、pydantic-settings、pytest-asyncio；现有 FastAPI/SQLAlchemy/asyncpg 栈不变。

---

## 前置：建立 worktree

```bash
cd /d/江苏润盛
git worktree add -b feature/serial-port .claude/worktrees/serial-port origin/master
cd .claude/worktrees/serial-port
uv sync --all-packages
```

---

## 文件地图

| 操作 | 路径 | 职责 |
|------|------|------|
| 新建 | `alembic/versions/20260420_0008_devices_transport_serial.py` | 给 `devices` 加 `transport_type`、`serial_port` 两列 |
| 修改 | `ruisheng-gw/src/ruisheng_gw/config.py` | 加 `SerialPortConfig` + `serial_ports: list[SerialPortConfig]` |
| 修改 | `ruisheng-gw/src/ruisheng_gw/domain/registry.py` | `RegistryEntry` 加 `transport_type`/`serial_port`/`modbus_addr`；加 `devices_for_serial_port()` |
| 新建 | `ruisheng-gw/src/ruisheng_gw/transport/serial_bus.py` | `SerialBus`：开串口、预注册 SessionMap、驱动 Connection |
| 修改 | `ruisheng-gw/src/ruisheng_gw/main.py` | `run_server()` 里并行启动串口 bus |
| 修改 | `ruisheng-gw/pyproject.toml` | 加 `pyserial-asyncio>=0.6` optional dep |
| 新建 | `ruisheng-gw/tests/unit/test_serial_bus.py` | SerialBus 单元测试（fake reader/writer） |
| 修改 | `ruisheng-gw/tests/unit/test_registry.py` | 补 `devices_for_serial_port` 测试 |
| 修改 | `ruisheng-gw/tests/unit/test_config.py` | 补 `serial_ports` 配置测试 |

---

## Task A：DB Migration — 给 devices 表增加两列

**Files:**
- Create: `alembic/versions/20260420_0008_devices_transport_serial.py`

- [ ] **Step 1: 写迁移脚本**

```python
# alembic/versions/20260420_0008_devices_transport_serial.py
"""devices: add transport_type + serial_port columns

Revision ID: 0008_transport_serial
Revises: 959079e6cae9
Create Date: 2026-04-20
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0008_transport_serial"
down_revision = "959079e6cae9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "devices",
        sa.Column(
            "transport_type",
            sa.String(length=10),
            nullable=False,
            server_default="tcp",
        ),
    )
    op.create_check_constraint(
        "ck_devices_transport_type",
        "devices",
        "transport_type IN ('tcp', 'serial')",
    )
    op.add_column(
        "devices",
        sa.Column("serial_port", sa.String(length=50), nullable=True),
    )
    # serial_port 非空当且仅当 transport_type='serial'
    op.create_check_constraint(
        "ck_devices_serial_port_consistency",
        "devices",
        "(transport_type = 'serial' AND serial_port IS NOT NULL)"
        " OR (transport_type = 'tcp' AND serial_port IS NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_devices_serial_port_consistency", "devices")
    op.drop_constraint("ck_devices_transport_type", "devices")
    op.drop_column("devices", "serial_port")
    op.drop_column("devices", "transport_type")
```

- [ ] **Step 2: 检查 down_revision 是否正确**

```bash
cd /d/江苏润盛/.claude/worktrees/serial-port
uv run alembic heads
```

Expected: 输出包含 `959079e6cae9`（即 Plan 2 最终 head）。若不同，把 `down_revision` 改成实际输出值。

- [ ] **Step 3: Commit**

```bash
git add alembic/versions/20260420_0008_devices_transport_serial.py
git commit -m "feat(db): add transport_type + serial_port columns to devices"
```

---

## Task B：Config — 加串口配置

**Files:**
- Modify: `ruisheng-gw/src/ruisheng_gw/config.py`
- Test: `ruisheng-gw/tests/unit/test_config.py`

- [ ] **Step 1: 写失败测试**

在 `test_config.py` 末尾追加：

```python
import json


def test_serial_ports_default_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GW_LISTEN_HOST", "0.0.0.0")
    monkeypatch.setenv("GW_LISTEN_PORT", "6000")
    monkeypatch.setenv("GW_DATABASE_URL", "postgresql+asyncpg://u:p@h:5432/d")
    monkeypatch.setenv("GW_REDIS_URL", "redis://h:6379/0")
    cfg = Config()
    assert cfg.serial_ports == []


def test_serial_ports_parses_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GW_LISTEN_HOST", "0.0.0.0")
    monkeypatch.setenv("GW_LISTEN_PORT", "6000")
    monkeypatch.setenv("GW_DATABASE_URL", "postgresql+asyncpg://u:p@h:5432/d")
    monkeypatch.setenv("GW_REDIS_URL", "redis://h:6379/0")
    monkeypatch.setenv(
        "GW_SERIAL_PORTS",
        json.dumps([{"port": "COM3", "baud_rate": 9600}]),
    )
    cfg = Config()
    assert len(cfg.serial_ports) == 1
    assert cfg.serial_ports[0].port == "COM3"
    assert cfg.serial_ports[0].baud_rate == 9600
```

- [ ] **Step 2: 运行，确认失败**

```bash
cd /d/江苏润盛/.claude/worktrees/serial-port
uv run pytest ruisheng-gw/tests/unit/test_config.py -v -k "serial"
```

Expected: `AttributeError: type object 'Config' has no attribute 'serial_ports'`

- [ ] **Step 3: 修改 config.py**

在 `config.py` 顶部 import 区加 `from pydantic import BaseModel`（pydantic 已是依赖，直接用）。

在 `Config` 类上面新增：

```python
class SerialPortConfig(BaseModel):
    """单个串口总线配置。"""

    port: str
    baud_rate: int = 9600
```

在 `Config` 类体内，`bus_lock_timeout_sec` 字段之后加：

```python
    serial_ports: list[SerialPortConfig] = Field(
        default_factory=list,
        description='JSON list, e.g. [{"port":"COM3","baud_rate":9600}]',
    )
```

- [ ] **Step 4: 运行测试，确认通过**

```bash
uv run pytest ruisheng-gw/tests/unit/test_config.py -v
```

Expected: 全部 PASS（原有测试 + 新增 2 个）

- [ ] **Step 5: Commit**

```bash
git add ruisheng-gw/src/ruisheng_gw/config.py ruisheng-gw/tests/unit/test_config.py
git commit -m "feat(gw/config): add SerialPortConfig + serial_ports list"
```

---

## Task C：Registry — 加串口相关字段和查询方法

**Files:**
- Modify: `ruisheng-gw/src/ruisheng_gw/domain/registry.py`
- Test: `ruisheng-gw/tests/unit/test_registry.py`

- [ ] **Step 1: 写失败测试**

在 `test_registry.py` 末尾追加：

```python
def test_devices_for_serial_port_returns_matching() -> None:
    device_rows = [
        {
            "dev_number": "SER-001",
            "usr_group": "ug",
            "update_interval_decisec": 10,
            "transport_type": "serial",
            "serial_port": "COM3",
            "modbus_addr": 1,
        },
        {
            "dev_number": "SER-002",
            "usr_group": "ug",
            "update_interval_decisec": 10,
            "transport_type": "serial",
            "serial_port": "COM3",
            "modbus_addr": 2,
        },
        {
            "dev_number": "TCP-001",
            "usr_group": "ug",
            "update_interval_decisec": 10,
            "transport_type": "tcp",
            "serial_port": None,
            "modbus_addr": 1,
        },
    ]
    reg = Registry.build(device_rows=device_rows, point_rows=[])
    result = reg.devices_for_serial_port("COM3")
    dev_numbers = {r.dev_number for r in result}
    addrs = {r.modbus_addr for r in result}
    assert dev_numbers == {"SER-001", "SER-002"}
    assert addrs == {1, 2}


def test_devices_for_serial_port_empty_when_no_match() -> None:
    device_rows = [
        {
            "dev_number": "TCP-001",
            "usr_group": "ug",
            "update_interval_decisec": 10,
            "transport_type": "tcp",
            "serial_port": None,
            "modbus_addr": 1,
        },
    ]
    reg = Registry.build(device_rows=device_rows, point_rows=[])
    assert reg.devices_for_serial_port("COM3") == []


def test_build_preserves_transport_fields() -> None:
    device_rows = [
        {
            "dev_number": "SER-001",
            "usr_group": "ug",
            "update_interval_decisec": 10,
            "transport_type": "serial",
            "serial_port": "COM3",
            "modbus_addr": 5,
        },
    ]
    reg = Registry.build(device_rows=device_rows, point_rows=[])
    entry = reg.get("SER-001")
    assert entry is not None
    assert entry.transport_type == "serial"
    assert entry.serial_port == "COM3"
    assert entry.modbus_addr == 5
```

- [ ] **Step 2: 运行，确认失败**

```bash
uv run pytest ruisheng-gw/tests/unit/test_registry.py -v -k "serial"
```

Expected: `AttributeError: 'RegistryEntry' object has no attribute 'transport_type'`

- [ ] **Step 3: 修改 registry.py — RegistryEntry 加字段**

`RegistryEntry` 改为：

```python
@dataclass
class RegistryEntry:
    device: Device
    update_interval_decisec: int
    transport_type: str = "tcp"       # 'tcp' | 'serial'
    serial_port: str | None = None    # e.g. "COM3"; None for TCP
    modbus_addr: int = 1              # ModBus 从机地址 1-247
    points: dict[int, PointEntry] = field(default_factory=dict)
```

- [ ] **Step 4: 修改 registry.py — build() 读新字段**

`Registry.build()` 的设备行处理改为：

```python
        for dr in device_rows:
            dev = Device(dev_number=dr["dev_number"], usr_group=dr["usr_group"])
            reg._entries[dr["dev_number"]] = RegistryEntry(
                device=dev,
                update_interval_decisec=dr["update_interval_decisec"],
                transport_type=dr.get("transport_type", "tcp"),
                serial_port=dr.get("serial_port"),
                modbus_addr=dr.get("modbus_addr", 1),
            )
```

- [ ] **Step 5: 修改 registry.py — 加 devices_for_serial_port()**

在 `Registry` 类末尾（`entries()` 方法之后）新增：

```python
    def devices_for_serial_port(self, port: str) -> list[RegistryEntry]:
        """Return all entries whose transport is serial and serial_port matches."""
        return [
            e
            for e in self._entries.values()
            if e.transport_type == "serial" and e.serial_port == port
        ]
```

- [ ] **Step 6: 修改 registry.py — load_from_db() 读新列**

`load_from_db()` 里的 SQL 改为（加 3 列）：

```python
            d_rows = (
                (
                    await conn.execute(
                        text(
                            "SELECT dev_number, usr_group, update_interval_decisec, "
                            "       transport_type, serial_port, modbus_addr "
                            "FROM devices "
                            "WHERE usr_group IS NOT NULL "
                            "  AND deleted_at IS NULL"
                        )
                    )
                )
                .mappings()
                .all()
            )
```

- [ ] **Step 7: 运行全部 registry 测试**

```bash
uv run pytest ruisheng-gw/tests/unit/test_registry.py -v
```

Expected: 全部 PASS（原有 3 + 新增 3 = 6 个）

- [ ] **Step 8: Commit**

```bash
git add ruisheng-gw/src/ruisheng_gw/domain/registry.py ruisheng-gw/tests/unit/test_registry.py
git commit -m "feat(gw/registry): add transport_type/serial_port/modbus_addr + devices_for_serial_port()"
```

---

## Task D：pyproject.toml — 加 pyserial-asyncio 依赖

**Files:**
- Modify: `ruisheng-gw/pyproject.toml`

- [ ] **Step 1: 加 optional dep**

在 `ruisheng-gw/pyproject.toml` 里，`[project]` 段的 `dependencies` 列表末尾加一行：

```toml
  "pyserial-asyncio>=0.6; extra == 'serial'",
```

在文件末尾加：

```toml
[project.optional-dependencies]
serial = ["pyserial-asyncio>=0.6"]
```

- [ ] **Step 2: 同步依赖**

```bash
uv sync --all-packages --extra serial
```

Expected: 无报错，pyserial-asyncio 被安装。

- [ ] **Step 3: Commit**

```bash
git add ruisheng-gw/pyproject.toml uv.lock
git commit -m "feat(gw): add pyserial-asyncio optional dependency"
```

---

## Task E：SerialBus — 核心新文件

**Files:**
- Create: `ruisheng-gw/src/ruisheng_gw/transport/serial_bus.py`
- Create: `ruisheng-gw/tests/unit/test_serial_bus.py`

### 设计要点（实现前必读）

- `pyserial-asyncio.open_serial_connection(url=port, baudrate=baud_rate)` 返回 `(asyncio.StreamReader, asyncio.StreamWriter)`
- `Connection(reader, writer, on_frame, heartbeat_timeout_sec=float("inf"))` — 串口无心跳，永不超时
- `SessionMap.bind(dev_number, writer=serial_writer, bus_id=port)` — 同一串口上所有设备共享同一个 writer
- `on_frame(frame_bytes)`: `frame[0]` = slave_addr → 查 `_addr_map: dict[int, str]`（addr → dev_number）→ 路由

- [ ] **Step 1: 写失败测试（用 fake reader/writer，不需要真实串口）**

新建 `ruisheng-gw/tests/unit/test_serial_bus.py`：

```python
"""SerialBus 单元测试：用 fake reader/writer 避免依赖真实串口。"""

from __future__ import annotations

import asyncio

import pytest

from ruisheng_gw.domain.device import Device, DeviceState
from ruisheng_gw.domain.point import Point
from ruisheng_gw.domain.registry import PointEntry, Registry, RegistryEntry, ThresholdSpec
from ruisheng_gw.protocol.modbus_codec import append_crc_to_frame
from ruisheng_gw.transport.serial_bus import SerialBus
from ruisheng_gw.transport.session import SessionMap


def _make_registry_with_serial(port: str) -> Registry:
    """Helper: registry with two serial devices on the same port."""
    reg = Registry()
    for addr, num in [(1, "SER-001"), (2, "SER-002")]:
        reg._entries[num] = RegistryEntry(
            device=Device(dev_number=num, usr_group="ug"),
            update_interval_decisec=10,
            transport_type="serial",
            serial_port=port,
            modbus_addr=addr,
        )
    return reg


def _make_fake_writer() -> asyncio.StreamWriter:
    """Return a minimal mock StreamWriter that records written bytes."""

    class FakeTransport(asyncio.Transport):
        def __init__(self) -> None:
            super().__init__()
            self.written: list[bytes] = []

        def write(self, data: bytes) -> None:
            self.written.append(data)

        def is_closing(self) -> bool:
            return False

    transport = FakeTransport()
    protocol = asyncio.StreamReaderProtocol(asyncio.StreamReader())
    loop = asyncio.get_event_loop()
    writer = asyncio.StreamWriter(transport, protocol, asyncio.StreamReader(), loop)
    return writer


async def test_serial_bus_preregisters_devices() -> None:
    """All serial devices on the port are bound into SessionMap at start."""
    port = "COM3"
    reg = _make_registry_with_serial(port)
    session = SessionMap()
    received: list[tuple[str, bytes]] = []

    async def on_frame(dev_number: str, frame: bytes) -> None:
        received.append((dev_number, frame))

    # Fake reader: two valid ModBus FC3 responses (slave 1 and slave 2), then EOF
    body1 = bytes([0x01, 0x03, 0x02, 0x00, 0x0A])
    body2 = bytes([0x02, 0x03, 0x02, 0x00, 0x1E])
    fake_data = append_crc_to_frame(body1) + append_crc_to_frame(body2)
    reader = asyncio.StreamReader()
    reader.feed_data(fake_data)
    reader.feed_eof()

    writer = _make_fake_writer()

    bus = SerialBus(
        port=port,
        baud_rate=9600,
        registry=reg,
        session_map=session,
        on_frame=on_frame,
    )
    # Inject fake reader/writer instead of opening real serial port
    await bus._run_with_streams(reader=reader, writer=writer)

    # Both devices should be registered
    assert session.get("SER-001") is not None
    assert session.get("SER-002") is not None
    # Both frames should be routed to the right device
    dev_numbers = [r[0] for r in received]
    assert "SER-001" in dev_numbers
    assert "SER-002" in dev_numbers


async def test_serial_bus_unknown_slave_addr_ignored() -> None:
    """Frame with unknown slave addr is silently dropped (no crash)."""
    port = "COM3"
    reg = _make_registry_with_serial(port)
    session = SessionMap()
    received: list[tuple[str, bytes]] = []

    async def on_frame(dev_number: str, frame: bytes) -> None:
        received.append((dev_number, frame))

    # slave_addr=99 is not in registry
    body = bytes([0x63, 0x03, 0x02, 0x00, 0x0A])
    fake_data = append_crc_to_frame(body)
    reader = asyncio.StreamReader()
    reader.feed_data(fake_data)
    reader.feed_eof()

    writer = _make_fake_writer()
    bus = SerialBus(
        port=port,
        baud_rate=9600,
        registry=reg,
        session_map=session,
        on_frame=on_frame,
    )
    await bus._run_with_streams(reader=reader, writer=writer)
    assert received == []
```

- [ ] **Step 2: 运行，确认失败**

```bash
uv run pytest ruisheng-gw/tests/unit/test_serial_bus.py -v
```

Expected: `ImportError: cannot import name 'SerialBus'`

- [ ] **Step 3: 实现 serial_bus.py**

新建 `ruisheng-gw/src/ruisheng_gw/transport/serial_bus.py`：

```python
"""RS485 serial bus adapter for ruisheng-gw.

Wraps pyserial-asyncio to present the same (reader, writer) interface as
the TCP server, so Connection / SessionMap / poller work unchanged.

Design:
- One SerialBus instance per physical COM port.
- At startup, all devices whose `serial_port` matches this port are
  pre-registered in SessionMap (static binding, no heartbeat needed).
- The Connection read loop feeds frames into _dispatch(), which maps
  slave_addr (frame[0]) → dev_number via the pre-built addr_map.
- heartbeat_timeout is disabled (float("inf")) since serial ports do not
  send DTU heartbeats.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from ruisheng_gw.transport.connection import Connection
from ruisheng_gw.transport.session import SessionMap

if __debug__:
    from ruisheng_gw.domain.registry import Registry

FrameCallback = Callable[[str, bytes], Awaitable[None]]


class SerialBus:
    """Manages one RS485 serial port: open, pre-register, read loop."""

    def __init__(
        self,
        *,
        port: str,
        baud_rate: int,
        registry: "Registry",
        session_map: SessionMap,
        on_frame: FrameCallback,
    ) -> None:
        self._port = port
        self._baud_rate = baud_rate
        self._registry = registry
        self._session_map = session_map
        self._on_frame = on_frame
        # slave_addr (1-247) → dev_number for this port
        self._addr_map: dict[int, str] = {}
        self._read_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Open real serial port and start read loop."""
        import serial_asyncio  # type: ignore[import-untyped]  # noqa: PLC0415

        reader, writer = await serial_asyncio.open_serial_connection(
            url=self._port,
            baudrate=self._baud_rate,
        )
        await self._run_with_streams(reader=reader, writer=writer)

    async def _run_with_streams(
        self,
        *,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Internal: wire streams (extracted for unit-test injection)."""
        # Build addr_map and pre-register all devices for this port
        for entry in self._registry.devices_for_serial_port(self._port):
            self._addr_map[entry.modbus_addr] = entry.device.dev_number
            self._session_map.bind(
                dev_number=entry.device.dev_number,
                writer=writer,
                bus_id=self._port,
            )

        async def _dispatch(frame: bytes) -> None:
            if not frame:
                return
            slave_addr = frame[0]
            dev_number = self._addr_map.get(slave_addr)
            if dev_number is None:
                return  # unknown slave addr — silently drop
            await self._on_frame(dev_number, frame)

        conn = Connection(
            reader=reader,
            writer=writer,
            on_frame=_dispatch,
            heartbeat_timeout_sec=float("inf"),  # serial has no DTU heartbeat
        )
        self._read_task = asyncio.create_task(conn.read_loop())
        await self._read_task

    async def shutdown(self) -> None:
        if self._read_task is not None and not self._read_task.done():
            self._read_task.cancel()
            try:
                await self._read_task
            except asyncio.CancelledError:
                pass
```

- [ ] **Step 4: 运行测试，确认通过**

```bash
uv run pytest ruisheng-gw/tests/unit/test_serial_bus.py -v
```

Expected: 全部 PASS（2 个测试）

- [ ] **Step 5: 运行全部 unit 测试，确认无回归**

```bash
uv run pytest ruisheng-gw/tests/unit/ -v
```

Expected: 全部 PASS

- [ ] **Step 6: Commit**

```bash
git add ruisheng-gw/src/ruisheng_gw/transport/serial_bus.py \
        ruisheng-gw/tests/unit/test_serial_bus.py
git commit -m "feat(gw/transport): add SerialBus — RS485 serial port adapter"
```

---

## Task F：main.py — 并行启动串口总线

**Files:**
- Modify: `ruisheng-gw/src/ruisheng_gw/main.py`

- [ ] **Step 1: 修改 run_server()**

在 `main.py` 的 `run_server()` 函数中：

1. 在顶部 import 区（lazy import 块）加入：
   ```python
   from ruisheng_gw.transport.serial_bus import SerialBus  # noqa: PLC0415
   ```

2. 在 `# 5. Create session/scheduling infrastructure` 段的 `await server.start()` **之后**，`# 6. Wait for SIGTERM/SIGINT` **之前**，插入：

```python
    # 5b. Start serial buses (if configured)
    serial_buses: list[SerialBus] = []
    serial_tasks: list[asyncio.Task[None]] = []
    for sp_cfg in config.serial_ports:
        bus = SerialBus(
            port=sp_cfg.port,
            baud_rate=sp_cfg.baud_rate,
            registry=registry,
            session_map=session_map,
            on_frame=_noop_serial_frame,
        )
        serial_buses.append(bus)
        task = asyncio.create_task(bus.start())
        serial_tasks.append(task)
```

3. 在 `_noop_handler` 定义附近加：

```python
    async def _noop_serial_frame(dev_number: str, frame: bytes) -> None:
        pass
```

4. 在 `finally:` 的 shutdown 块里（`batch.stop()` 之前）加：

```python
        for bus in serial_buses:
            await bus.shutdown()
        if serial_tasks:
            await asyncio.gather(*serial_tasks, return_exceptions=True)
```

- [ ] **Step 2: 运行全部 unit 测试确认无回归**

```bash
uv run pytest ruisheng-gw/tests/unit/ -v
```

Expected: 全部 PASS

- [ ] **Step 3: Commit**

```bash
git add ruisheng-gw/src/ruisheng_gw/main.py
git commit -m "feat(gw/main): start SerialBus instances alongside TCP server"
```

---

## Task G：静态检查 + 全量测试

- [ ] **Step 1: ruff + mypy**

```bash
uv run ruff check ruisheng-gw/src/
uv run mypy ruisheng-gw/src/
```

Expected: 无错误。常见问题：
- `serial_asyncio` 无类型 stubs → 已在 serial_bus.py 用 `# type: ignore[import-untyped]` 处理
- `Registry._entries` 直接访问（测试 helper）→ 测试文件加 `# noqa: SLF001`

- [ ] **Step 2: 全量 unit 测试**

```bash
uv run pytest ruisheng-gw/tests/unit/ -v --tb=short
```

Expected: 全部 PASS（原有 + 新增共约 110 个）

- [ ] **Step 3: tag + Commit**

```bash
git tag serial-port-complete
git commit --allow-empty -m "chore: serial-port-complete tag checkpoint"
```

---

## Task H：PR + 文档

- [ ] **Step 1: push 分支**

```bash
git push -u origin feature/serial-port
```

- [ ] **Step 2: 更新架构说明文档**

在 `docs/系统架构说明.md` 的"3. 四个核心进程"表格后，在"5. Docker 在这里起什么作用"之前插入新章节：

```markdown
## 3.5 设备接入方式

| 接入方式 | 适用场景 | 配置 | 标识设备 |
|---------|---------|------|---------|
| TCP/DTU | 远程设备，4G 网络接入 | 默认，无需配置 | 设备发心跳帧（含序列号） |
| RS485 串口 | 本地设备，直接串口线连接 | `GW_SERIAL_PORTS=[{"port":"COM3","baud_rate":9600}]` | DB 里的 `modbus_addr` + `serial_port` |
```

- [ ] **Step 3: 更新 PROGRESS.md**

在 `docs/superpowers/plans/PROGRESS.md` 顶部新增：

```markdown
## Serial Port Support（2026-04-20）
- Task A DB migration: ✅
- Task B Config: ✅
- Task C Registry: ✅
- Task D pyproject: ✅
- Task E SerialBus: ✅
- Task F main.py: ✅
- Task G 检查: ✅
- tag: serial-port-complete
```

- [ ] **Step 4: Commit + PR**

```bash
git add docs/
git commit -m "docs: update architecture + PROGRESS for serial port support"
gh pr create --title "feat(gw): RS485 serial port support alongside TCP/DTU" \
  --body "$(cat <<'EOF'
## Summary
- 新增 `transport_type`/`serial_port` DB 列（alembic migration 0008）
- `SerialPortConfig` + `GW_SERIAL_PORTS` env 配置
- `SerialBus`：开串口、预注册 SessionMap、驱动 Connection read loop
- `Registry.devices_for_serial_port()` 按端口名过滤设备
- TCP 路径零改动，全部原有测试通过

## Test plan
- [ ] `uv run pytest ruisheng-gw/tests/unit/ -v` → 全 PASS
- [ ] `uv run ruff check ruisheng-gw/src/ && uv run mypy ruisheng-gw/src/` → clean
- [ ] 手工测试：接 RS485 设备到 COM 口，设 `GW_SERIAL_PORTS`，观察日志

🤖 Generated with Claude Code
EOF
)"
```

---

## 快速验证清单（手工测试时）

```bash
# 1. 环境变量（Windows）
set GW_LISTEN_HOST=0.0.0.0
set GW_LISTEN_PORT=6000
set GW_DATABASE_URL=postgresql+asyncpg://ruisheng_dev:ruisheng_dev@localhost/ruisheng
set GW_REDIS_URL=redis://:dev-redis-pw@localhost:6379/0
set GW_SERIAL_PORTS=[{"port":"COM3","baud_rate":9600}]

# 2. DB 里给测试设备加 serial 配置（仅测试用）
# UPDATE devices SET transport_type='serial', serial_port='COM3', modbus_addr=1
# WHERE dev_number='YOUR-TEST-DEVICE';

# 3. 启动（需要真实串口）
uv run python -m ruisheng_gw

# 预期日志：
# "serial bus started" port=COM3 devices=1
```
