"""asyncio TCP server for DTU connections.

- host/port bind
- TCP_NODELAY on accepted sockets (per spec §5.4 / v2 A1)
- handler coroutine per connection
- graceful shutdown: close server + wait all handlers
"""

from __future__ import annotations

import asyncio
import contextlib
import socket
from collections.abc import Awaitable, Callable

Handler = Callable[[asyncio.StreamReader, asyncio.StreamWriter], Awaitable[None]]


class GwServer:
    def __init__(self, *, host: str, port: int, handler: Handler) -> None:
        self._host = host
        self._port = port
        self._handler = handler
        self._server: asyncio.Server | None = None
        self._handlers: set[asyncio.Task[None]] = set()

    async def start(self) -> None:
        self._server = await asyncio.start_server(
            self._on_connected,
            host=self._host,
            port=self._port,
        )

    async def _on_connected(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        # Disable Nagle: ModBus RTU frames are small and latency-sensitive.
        sock = writer.get_extra_info("socket")
        if sock is not None:
            with contextlib.suppress(OSError):
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        task = asyncio.current_task()
        if task is not None:
            self._handlers.add(task)
            task.add_done_callback(self._handlers.discard)
        try:
            await self._handler(reader, writer)
        finally:
            if not writer.is_closing():
                writer.close()
                with contextlib.suppress(Exception):
                    await writer.wait_closed()

    def is_listening(self) -> bool:
        return self._server is not None and self._server.is_serving()

    def actual_port(self) -> int:
        assert self._server is not None
        return int(self._server.sockets[0].getsockname()[1])

    async def shutdown(self) -> None:
        if self._server is None:
            return
        self._server.close()
        await self._server.wait_closed()
        self._server = None
        if self._handlers:
            await asyncio.gather(*self._handlers, return_exceptions=True)
