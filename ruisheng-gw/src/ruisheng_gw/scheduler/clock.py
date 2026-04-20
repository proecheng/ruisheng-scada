"""Clock protocol — injected into all timer-driven gw components.

Production uses RealClock (asyncio.sleep + time.monotonic).
Tests use FakeClock with .advance() for deterministic timing — no
wall-clock sleeps, no GitHub Actions flakiness.
"""

from __future__ import annotations

import asyncio
import heapq
import time
from typing import Protocol


class Clock(Protocol):
    def monotonic(self) -> float: ...
    async def sleep(self, seconds: float) -> None: ...


class RealClock:
    def monotonic(self) -> float:
        return time.monotonic()

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


class FakeClock:
    """Virtual-time clock. Sleepers wake when .advance() moves now past their deadline."""

    def __init__(self, *, now: float = 0.0) -> None:
        self._now = now
        self._waiters: list[tuple[float, int, asyncio.Future[None]]] = []
        self._counter = 0

    def monotonic(self) -> float:
        return self._now

    async def sleep(self, seconds: float) -> None:
        if seconds <= 0:
            return
        deadline = self._now + seconds
        fut: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        heapq.heappush(self._waiters, (deadline, self._counter, fut))
        self._counter += 1
        await fut

    def advance(self, seconds: float) -> None:
        target = self._now + seconds
        while self._waiters and self._waiters[0][0] <= target:
            deadline, _, fut = heapq.heappop(self._waiters)
            self._now = deadline
            if not fut.done():
                fut.set_result(None)
        self._now = target
