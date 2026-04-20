"""Per-device poller supervisor — v2 F2 manual create_task + add_done_callback.

Plan v1 claimed asyncio.TaskGroup, but TaskGroup cancels all siblings on
any child exception. Per v2 F2, manual supervise provides true per-
device isolation.

Restart policy (§5.2):
- 10min rolling window of crash count
- > 3 crashes in window → quarantine (mark device offline, stop
  restarting)
- Exponential backoff: 2, 4, 8s (capped at 30)
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

from ruisheng_gw.scheduler.clock import Clock


@dataclass
class DeviceHealth:
    dev_number: str
    restart_count: int = 0
    restart_window_start: float = 0.0
    quarantined: bool = False


CoroFactory = Callable[[], Coroutine[Any, Any, None]]


class Supervisor:
    def __init__(
        self,
        *,
        clock: Clock,
        max_restarts: int = 3,
        window_sec: float = 600,
        initial_backoff: float = 2.0,
        max_backoff: float = 30.0,
    ) -> None:
        self._clock = clock
        self._max_restarts = max_restarts
        self._window_sec = window_sec
        self._initial_backoff = initial_backoff
        self._max_backoff = max_backoff
        self.tasks: dict[str, asyncio.Task[None]] = {}
        self.health: dict[str, DeviceHealth] = {}
        self._factories: dict[str, CoroFactory] = {}
        self._respawn_tasks: set[asyncio.Task[None]] = set()
        self._shutdown = False

    def start_poller(self, *, dev_number: str, coro_factory: CoroFactory) -> None:
        self._factories[dev_number] = coro_factory
        self.health.setdefault(dev_number, DeviceHealth(dev_number=dev_number))
        self._spawn(dev_number)

    def _spawn(self, dev_number: str) -> None:
        task: asyncio.Task[None] = asyncio.create_task(self._factories[dev_number]())
        task.add_done_callback(lambda t: self._on_done(dev_number, t))
        self.tasks[dev_number] = task

    def _on_done(self, dev_number: str, task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is None:
            return  # clean exit, don't restart
        h = self.health[dev_number]
        now = self._clock.monotonic()
        if now - h.restart_window_start > self._window_sec:
            h.restart_window_start = now
            h.restart_count = 0
        h.restart_count += 1
        if h.restart_count > self._max_restarts:
            h.quarantined = True
            return
        backoff = min(self._initial_backoff * (2 ** (h.restart_count - 1)), self._max_backoff)

        async def _delayed_respawn() -> None:
            await self._clock.sleep(backoff)
            if not h.quarantined and not self._shutdown:
                self._spawn(dev_number)

        t = asyncio.create_task(_delayed_respawn())
        self._respawn_tasks.add(t)
        t.add_done_callback(self._respawn_tasks.discard)

    def shutdown_sync(self) -> None:
        self._shutdown = True
        for t in (*self.tasks.values(), *self._respawn_tasks):
            t.cancel()
