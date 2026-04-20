"""WebSocket 连接管理。每连接独立 asyncio.Queue(maxsize=500)。"""

from __future__ import annotations

import asyncio
import contextlib
import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from starlette.websockets import WebSocket


@dataclass(unsafe_hash=True)
class WSClient:
    ws: WebSocket = field(hash=False)
    user_name: str = field(hash=False)
    usr_group: str = field(hash=False)
    role: str = field(hash=False)
    queue: asyncio.Queue[str] = field(
        default_factory=lambda: asyncio.Queue(maxsize=500), hash=False
    )


class WSManager:
    def __init__(self) -> None:
        self._clients: set[WSClient] = set()
        self._lock = asyncio.Lock()

    async def add(self, client: WSClient) -> None:
        async with self._lock:
            self._clients.add(client)

    async def remove(self, client: WSClient) -> None:
        async with self._lock:
            self._clients.discard(client)

    async def clients_snapshot(self) -> list[WSClient]:
        async with self._lock:
            return list(self._clients)

    async def broadcast(
        self, message: dict[str, object], *, tenant_filter: str | None = None
    ) -> int:
        """广播到租户匹配的所有连接。返回成功入队数。drop-oldest on QueueFull (§3.8.4)。"""
        payload = json.dumps(message, ensure_ascii=False)
        count = 0
        for c in await self.clients_snapshot():
            if tenant_filter and c.role != "Administrators" and c.usr_group != tenant_filter:
                continue
            try:
                c.queue.put_nowait(payload)
                count += 1
            except asyncio.QueueFull:
                with contextlib.suppress(asyncio.QueueEmpty):
                    c.queue.get_nowait()
                try:
                    c.queue.put_nowait(payload)
                    count += 1
                except asyncio.QueueFull:
                    logger.warning("WS queue overflow dropping message")
        return count
