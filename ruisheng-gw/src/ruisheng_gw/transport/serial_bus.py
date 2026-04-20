"""RS485 serial bus adapter for ruisheng-gw.

Wraps pyserial-asyncio to present the same (reader, writer) interface as
the TCP server, so Connection / SessionMap / poller work unchanged.

Design:
- One SerialBus instance per physical COM port.
- At startup, all devices whose serial_port matches this port are
  pre-registered in SessionMap (static binding, no heartbeat needed).
- The Connection read loop feeds frames into _dispatch(), which maps
  slave_addr (frame[0]) to dev_number via the pre-built addr_map.
- heartbeat_timeout is disabled (float("inf")) since serial ports do not
  send DTU heartbeats.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from ruisheng_gw.transport.connection import Connection
from ruisheng_gw.transport.session import SessionMap

if TYPE_CHECKING:
    from ruisheng_gw.domain.registry import Registry

FrameCallback = Callable[[str, bytes], Awaitable[None]]


class SerialBus:
    """Manages one RS485 serial port: open, pre-register, read loop."""

    def __init__(
        self,
        *,
        port: str,
        baud_rate: int,
        registry: Registry,
        session_map: SessionMap,
        on_frame: FrameCallback,
    ) -> None:
        self._port = port
        self._baud_rate = baud_rate
        self._registry = registry
        self._session_map = session_map
        self._on_frame = on_frame
        self._addr_map: dict[int, str] = {}
        self._read_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Open real serial port and start read loop."""
        import serial_asyncio  # noqa: PLC0415

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
        """Wire streams — extracted for unit-test injection."""
        for entry in self._registry.devices_for_serial_port(self._port):
            if entry.modbus_addr in self._addr_map:
                raise RuntimeError(
                    f"duplicate modbus_addr {entry.modbus_addr} on port {self._port}: "
                    f"devices {self._addr_map[entry.modbus_addr]!r} and "
                    f"{entry.device.dev_number!r}"
                )
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
                return
            await self._on_frame(dev_number, frame)

        conn = Connection(
            reader=reader,
            writer=writer,
            on_frame=_dispatch,
            heartbeat_timeout_sec=float("inf"),
        )
        self._read_task = asyncio.create_task(conn.read_loop())
        await self._read_task

    async def shutdown(self) -> None:
        if self._read_task is not None and not self._read_task.done():
            self._read_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._read_task
