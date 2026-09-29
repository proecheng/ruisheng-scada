"""SerialBus unit tests using fake reader/writer (no real serial port needed)."""

from __future__ import annotations

import asyncio

import pytest
from ruisheng_gw.domain.device import Device
from ruisheng_gw.domain.registry import Registry, RegistryEntry
from ruisheng_gw.protocol.modbus_codec import append_crc_to_frame
from ruisheng_gw.transport.serial_bus import SerialBus
from ruisheng_gw.transport.session import SessionMap


def _make_registry_with_serial(port: str) -> Registry:
    reg = Registry()
    for addr, num in [(1, "SER-001"), (2, "SER-002")]:
        reg._entries[num] = RegistryEntry(  # noqa: SLF001
            device=Device(dev_number=num, usr_group="ug"),
            update_interval_decisec=10,
            transport_type="serial",
            serial_port=port,
            modbus_addr=addr,
        )
    return reg


def _make_fake_writer() -> asyncio.StreamWriter:
    """Minimal writer that accepts write() calls without a real transport."""
    from unittest.mock import AsyncMock, MagicMock

    w = MagicMock(spec=asyncio.StreamWriter)
    w.is_closing.return_value = False
    w.close.return_value = None
    w.wait_closed = AsyncMock(return_value=None)
    return w


async def test_serial_bus_unsolicited_frames_ignored_and_disconnect_cleans_sessions() -> None:
    port = "COM3"
    reg = _make_registry_with_serial(port)
    session = SessionMap()
    received: list[tuple[str, bytes]] = []

    async def on_frame(dev_number: str, frame: bytes) -> None:
        received.append((dev_number, frame))

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
    with pytest.raises(ConnectionError, match="serial connection ended"):
        await bus._run_with_streams(reader=reader, writer=writer)  # noqa: SLF001
    assert len(session) == 0
    assert received == []
    writer.close.assert_called_once()


@pytest.mark.parametrize("disconnect", ["io_error", "eof", "write_error"])
async def test_start_retries_absent_port_and_reopens_after_usb_disconnect(monkeypatch, disconnect):  # noqa: PLR0915
    import sys
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from ruisheng_gw.scheduler.clock import FakeClock

    registry = Registry.build(
        device_rows=[
            {
                "dev_number": "DEV001",
                "usr_group": "tenant",
                "transport_type": "serial",
                "serial_port": "COM3",
                "modbus_addr": 1,
                "read_profile": "zero_origin_38",
                "update_interval_decisec": 50,
            }
        ],
        point_rows=[
            {
                "id": 1,
                "dev_number": "DEV001",
                "point_number": 0,
                "point_ratio": 1.0,
                "point_offset": 0.0,
                "user_ratio": 1.0,
                "user_point_offset": 0.0,
            }
        ],
    )
    clock = FakeClock()
    readers = [asyncio.StreamReader(), asyncio.StreamReader()]
    writers = [_make_fake_writer(), _make_fake_writer()]
    for writer in writers:
        writer.drain = AsyncMock()
    opened = AsyncMock(side_effect=[OSError("port absent"), *zip(readers, writers, strict=True)])
    monkeypatch.setitem(
        sys.modules, "serial_asyncio", SimpleNamespace(open_serial_connection=opened)
    )
    sessions = SessionMap()
    received = AsyncMock()
    bus = SerialBus(
        port="COM3",
        baud_rate=9600,
        registry=registry,
        session_map=sessions,
        on_frame=received,
        clock=clock,
    )
    task = asyncio.create_task(bus.start())

    async def settle():
        for _ in range(50):
            await asyncio.sleep(0)

    try:
        await settle()
        assert not task.done(), "an absent adapter must not permanently stop the bus"
        assert opened.await_count == 1
        clock.advance(2)
        await settle()
        assert opened.await_count == 2
        assert writers[0].write.call_args.args[0].hex() == "010300000026c410"
        frame = append_crc_to_frame(bytes([1, 3, 76]) + b"\x00\x03" * 38)
        readers[0].feed_data(frame)
        await settle()
        received.assert_awaited_once_with("DEV001", frame)
        if disconnect == "io_error":
            readers[0].set_exception(OSError("device disconnected"))
        elif disconnect == "eof":
            readers[0].feed_eof()
        else:
            writers[0].write.side_effect = OSError("device disconnected")
            clock.advance(5.1)
        await settle()
        assert not task.done()
        assert len(sessions) == 0
        writers[0].close.assert_called_once()
        clock.advance(2)
        await settle()
        assert opened.await_count == 3
        assert writers[1].write.call_args.args[0].hex() == "010300000026c410"
        assert sessions.get("DEV001").writer is writers[1]
        readers[1].feed_data(frame)
        await settle()
        assert received.await_count == 2
        await bus.shutdown()
        await asyncio.wait_for(asyncio.shield(task), timeout=1)
        clock.advance(30)
        await settle()
        assert opened.await_count == 3
        assert len(sessions) == 0
        writers[1].close.assert_called_once()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_shutdown_cancels_pending_open(monkeypatch):
    import sys
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    entered = asyncio.Event()
    cancelled = asyncio.Event()

    async def opening(**_):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setitem(
        sys.modules, "serial_asyncio", SimpleNamespace(open_serial_connection=opening)
    )
    bus = SerialBus(
        port="COM3",
        baud_rate=9600,
        registry=Registry(),
        session_map=SessionMap(),
        on_frame=AsyncMock(),
    )
    task = asyncio.create_task(bus.start())
    try:
        await asyncio.wait_for(entered.wait(), timeout=1)
        await asyncio.wait_for(bus.shutdown(), timeout=0.5)
        assert task.done() and cancelled.is_set()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_shutdown_interrupts_reconnect_delay_without_reopening(monkeypatch):
    import sys
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    opened = AsyncMock(side_effect=OSError("port absent"))
    monkeypatch.setitem(
        sys.modules, "serial_asyncio", SimpleNamespace(open_serial_connection=opened)
    )
    bus = SerialBus(
        port="COM3",
        baud_rate=9600,
        registry=Registry(),
        session_map=SessionMap(),
        on_frame=AsyncMock(),
    )
    task = asyncio.create_task(bus.start())
    for _ in range(20):
        await asyncio.sleep(0)
    try:
        assert not task.done()
        await asyncio.wait_for(bus.shutdown(), timeout=0.5)
        await asyncio.wait_for(asyncio.shield(task), timeout=0.5)
        assert opened.await_count == 1
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_serial_bus_unknown_slave_addr_ignored() -> None:
    port = "COM3"
    reg = _make_registry_with_serial(port)
    session = SessionMap()
    received: list[tuple[str, bytes]] = []

    async def on_frame(dev_number: str, frame: bytes) -> None:
        received.append((dev_number, frame))

    body = bytes([0x63, 0x03, 0x02, 0x00, 0x0A])  # slave_addr=99, not in registry
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
    with pytest.raises(ConnectionError):
        await bus._run_with_streams(reader=reader, writer=writer)  # noqa: SLF001
    assert received == []


@pytest.mark.parametrize("damaged_first_reply", [False, True])
async def test_live_bus_hot_add_receives_split_binary_payload_without_heartbeat_stripping(
    damaged_first_reply,
):
    from unittest.mock import AsyncMock

    from ruisheng_gw.scheduler.clock import FakeClock

    registry = Registry()
    reader = asyncio.StreamReader()
    writer = _make_fake_writer()
    writer.drain = AsyncMock()
    session = SessionMap()
    clock = FakeClock()
    received = AsyncMock()
    bus = SerialBus(
        port="COM3",
        baud_rate=9600,
        registry=registry,
        session_map=session,
        on_frame=received,
        clock=clock,
    )
    task = asyncio.create_task(bus._run_with_streams(reader=reader, writer=writer))

    async def settle():
        for _ in range(30):
            await asyncio.sleep(0)

    try:
        await settle()
        writer.write.assert_not_called()
        registry.reconcile_serial(
            Registry.build(
                device_rows=[
                    {
                        "dev_number": "NEW",
                        "usr_group": "tenant",
                        "update_interval_decisec": 50,
                        "transport_type": "serial",
                        "serial_port": "COM3",
                        "modbus_addr": 7,
                        "read_profile": "zero_origin_38",
                    }
                ],
                point_rows=[
                    {
                        "id": 1,
                        "dev_number": "NEW",
                        "point_number": 35,
                        "point_ratio": 1.0,
                        "point_offset": 0.0,
                        "user_ratio": 1.0,
                        "user_point_offset": 0.0,
                    }
                ],
            )
        )
        clock.advance(0.05)
        await settle()
        assert session.get("NEW").pending_read is not None
        assert writer.write.call_args.args[0][:6] == bytes.fromhex("070300000026")
        if damaged_first_reply:
            reader.feed_data(bytes.fromhex("07034c00"))  # truncated first reply
            await settle()
            clock.advance(1)
            await settle()
            clock.advance(0.201)
            await settle()
            assert writer.write.call_count == 2
            assert writer.write.call_args_list[0] == writer.write.call_args_list[1]
        payload = b"\x00" * 10 + b"\r\nABC\r\n" + b"\x00" * 59
        frame = append_crc_to_frame(bytes([7, 3, 76]) + payload)
        assert len(frame) == 81
        reader.feed_data(frame[:40])
        await settle()
        received.assert_not_awaited()
        reader.feed_data(frame[40:] + frame)
        await settle()
        received.assert_awaited_once_with("NEW", frame)
        assert session.get("NEW").pending_read is None
        assert bus.poller.diagnostics["retry_recovered"] == int(damaged_first_reply)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert len(session) == 0
    writer.close.assert_called_once()
