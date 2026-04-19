"""per-RS485-bus asyncio.Lock factory with 15s default acquire timeout."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager


class BusLockTimeout(RuntimeError):  # noqa: N818
    pass


class BusLocks:
    def __init__(self, *, timeout_sec: float = 15.0) -> None:
        self._locks: dict[str, asyncio.Lock] = {}
        self._timeout_sec = timeout_sec

    def _get(self, bus_id: str) -> asyncio.Lock:
        lock = self._locks.get(bus_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[bus_id] = lock
        return lock

    @asynccontextmanager
    async def acquire(self, bus_id: str) -> AsyncIterator[None]:
        lock = self._get(bus_id)
        try:
            await asyncio.wait_for(lock.acquire(), timeout=self._timeout_sec)
        except TimeoutError as e:
            raise BusLockTimeout(f"bus_id={bus_id} acquire timeout") from e
        try:
            yield
        finally:
            lock.release()
