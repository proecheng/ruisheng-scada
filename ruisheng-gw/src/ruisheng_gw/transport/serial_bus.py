"""Serial transport lifetime and the single transaction worker for one configured port."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from ruisheng_gw.scheduler.clock import RealClock
from ruisheng_gw.scheduler.serial_poller import SerialPoller
from ruisheng_gw.transport.connection import Connection
from ruisheng_gw.transport.session import SessionMap

if TYPE_CHECKING:
    from ruisheng_gw.domain.registry import Registry
    from ruisheng_gw.scheduler.clock import Clock

FrameCallback = Callable[[str, bytes], Awaitable[None]]
logger = logging.getLogger(__name__)
_RECONNECT_DELAY_SEC = 2.0
_OPEN_TIMEOUT_SEC = 5.0


class SerialBus:
    def __init__(
        self,
        *,
        port: str,
        baud_rate: int,
        registry: Registry,
        session_map: SessionMap,
        on_frame: FrameCallback,
        clock: Clock | None = None,
        response_timeout_sec: float = 1.0,
        quiet_time_sec: float = 0.2,
        read_timeout_retries: int = 1,
    ) -> None:
        self._port = port
        self._baud_rate = baud_rate
        self._registry = registry
        self._session_map = session_map
        self._on_frame = on_frame
        self._clock = clock or RealClock()
        self._response_timeout_sec = response_timeout_sec
        self._quiet_time_sec = quiet_time_sec
        self._read_timeout_retries = read_timeout_retries
        self._tasks: list[asyncio.Task[None]] = []
        self._writer: asyncio.StreamWriter | None = None
        self.poller: SerialPoller | None = None
        self._stopping = False
        self._runner: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Keep one worker alive across missing ports, EOF and serial I/O failures."""
        import serial_asyncio  # noqa: PLC0415

        if self._runner is not None:
            raise RuntimeError("serial bus already started")
        self._runner = asyncio.current_task()
        try:
            while not self._stopping:
                try:
                    reader, writer = await asyncio.wait_for(
                        serial_asyncio.open_serial_connection(
                            url=self._port, baudrate=self._baud_rate
                        ),
                        timeout=_OPEN_TIMEOUT_SEC,
                    )
                    logger.info("serial port opened port=%s", self._port)
                    await self._run_with_streams(reader=reader, writer=writer)
                except OSError as exc:
                    # SerialException, EOF/closed writer and bounded send/open timeout
                    # all reach here. Ordinary response timeouts stay inside the poller.
                    logger.warning("serial reconnect pending port=%s error=%s", self._port, exc)
                if not self._stopping:
                    await self._clock.sleep(_RECONNECT_DELAY_SEC)
        except asyncio.CancelledError:
            if not self._stopping:
                raise
        finally:
            await self._close_connection()
            self._runner = None

    async def _run_with_streams(
        self,
        *,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Inject streams for isolated testing; production opens only configured ports."""
        self._writer = writer

        async def _dispatch(frame: bytes) -> None:
            if self.poller is not None:
                await self.poller.receive_frame(frame)

        conn = Connection(
            reader=reader,
            writer=writer,
            on_frame=_dispatch,
            heartbeat_timeout_sec=float("inf"),
            strip_dtu_heartbeats=False,
            parse_fail_budget=2**31,
        )

        def _discard_input() -> None:
            conn.discard_input()
            serial = getattr(writer.transport, "serial", None)
            if serial is not None:
                serial.reset_input_buffer()

        self.poller = SerialPoller(
            port=self._port,
            registry=self._registry,
            session_map=self._session_map,
            writer=writer,
            on_frame=self._on_frame,
            discard_input=_discard_input,
            clock=self._clock,
            response_timeout_sec=self._response_timeout_sec,
            quiet_time_sec=self._quiet_time_sec,
            read_timeout_retries=self._read_timeout_retries,
        )
        try:
            self.poller.reconcile_sessions()
            self._tasks = [
                asyncio.create_task(conn.read_loop()),
                asyncio.create_task(self.poller.run()),
            ]
            done, _ = await asyncio.wait(self._tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                await task
            raise ConnectionError(f"serial connection ended: {self._port}")
        finally:
            await self._close_connection()

    async def shutdown(self) -> None:
        self._stopping = True
        if self._runner is not None and self._runner is not asyncio.current_task():
            self._runner.cancel()
            await asyncio.gather(self._runner, return_exceptions=True)
        await self._close_connection()

    async def _close_connection(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []
        if self.poller is not None:
            self.poller.release_sessions()
            self.poller = None
        if self._writer is not None:
            self._writer.close()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._writer.wait_closed(), timeout=1.0)
            self._writer = None
