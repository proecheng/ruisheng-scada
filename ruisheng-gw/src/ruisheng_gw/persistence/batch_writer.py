"""Batch writer fixing MinTransactionCnt=1 legacy behavior.

- asyncio.Queue(maxsize=queue_maxsize=10000)
- Clock-driven flush timer (default 100ms)
- Size threshold (default 500 rows)
- Queue-full policy: drop-TAIL (new rows rejected; v2 §5.11 alignment)
- Retry 3× exponential backoff on sink flush exception
- After retry exhausted, delegate to wal_append (injected) for disk fallback
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from ruisheng_gw.scheduler.clock import Clock


@dataclass(frozen=True)
class BatchRow:
    dev_number: str
    point_id: int
    rt_value: float
    org_value: float
    recorded_at: float  # epoch sec (converted to timestamptz at repository layer)


WalAppend = Callable[[list["BatchRow"]], Awaitable[None]]

_MAX_RETRIES = 3


class BatchWriter:
    def __init__(
        self,
        *,
        sink: object,
        clock: Clock,
        flush_size: int = 500,
        flush_interval_sec: float = 0.1,
        queue_maxsize: int = 10000,
        wal_append: WalAppend | None = None,
        retry_initial_backoff: float = 0.5,
    ) -> None:
        self._sink = sink
        self._clock = clock
        self._flush_size = flush_size
        self._flush_interval_sec = flush_interval_sec
        self._wal_append = wal_append
        self._retry_initial_backoff = retry_initial_backoff
        self._queue: asyncio.Queue[BatchRow] = asyncio.Queue(maxsize=queue_maxsize)
        self._stop_event = asyncio.Event()
        self.stats = {
            "drop_total": 0,
            "flush_total": 0,
            "wal_fallback_total": 0,
            "flush_error_total": 0,
        }

    def submit(self, row: BatchRow) -> None:
        try:
            self._queue.put_nowait(row)
        except asyncio.QueueFull:
            self.stats["drop_total"] += 1

    def stop(self) -> None:
        self._stop_event.set()

    async def run(self) -> None:
        buffer: list[BatchRow] = []
        timer_task: asyncio.Task[None] | None = None

        async def _stop_waiter() -> None:
            await self._stop_event.wait()

        stop_task: asyncio.Task[None] = asyncio.create_task(_stop_waiter())

        async def _timer() -> None:
            await self._clock.sleep(self._flush_interval_sec)

        async def _cancel(t: asyncio.Task) -> None:  # type: ignore[type-arg]
            t.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t

        while not self._stop_event.is_set():
            if timer_task is None or timer_task.done():
                timer_task = asyncio.create_task(_timer())

            # Get one item or wait for timer/stop
            get_task: asyncio.Task[BatchRow] = asyncio.create_task(self._queue.get())
            done, _ = await asyncio.wait(
                [get_task, timer_task, stop_task],
                return_when=asyncio.FIRST_COMPLETED,
            )

            if stop_task in done:
                # Shutdown: cancel pending operations and break
                await _cancel(get_task)
                break

            if get_task in done:
                buffer.append(get_task.result())
            else:
                await _cancel(get_task)

            # Flush if size threshold or timer fired
            timer_fired = timer_task in done
            if len(buffer) >= self._flush_size or (buffer and (timer_fired or self._queue.empty())):
                await self._flush(buffer)
                buffer = []
                if timer_fired:
                    timer_task = None

        # cancel timer if still running
        if timer_task and not timer_task.done():
            await _cancel(timer_task)
        if not stop_task.done():
            await _cancel(stop_task)

        # final flush
        while not self._queue.empty():
            buffer.append(self._queue.get_nowait())
        if buffer:
            await self._flush(buffer)

    async def _flush(self, buffer: list[BatchRow]) -> None:
        self.stats["flush_total"] += 1
        backoff = self._retry_initial_backoff
        for attempt in range(_MAX_RETRIES):
            try:
                await self._sink.flush(buffer)  # type: ignore[attr-defined]
                return
            except Exception:
                if attempt < _MAX_RETRIES - 1:
                    await self._clock.sleep(backoff)
                    backoff *= 2
        self.stats["flush_error_total"] += 1
        if self._wal_append is not None:
            self.stats["wal_fallback_total"] += 1
            await self._wal_append(buffer)
