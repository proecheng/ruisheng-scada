"""SerialBus unit tests using fake reader/writer (no real serial port needed)."""

from __future__ import annotations

import asyncio

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


async def test_serial_bus_preregisters_devices() -> None:
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
    await bus._run_with_streams(reader=reader, writer=writer)  # noqa: SLF001

    assert session.get("SER-001") is not None
    assert session.get("SER-002") is not None
    dev_numbers = [r[0] for r in received]
    assert "SER-001" in dev_numbers
    assert "SER-002" in dev_numbers


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
    await bus._run_with_streams(reader=reader, writer=writer)  # noqa: SLF001
    assert received == []
