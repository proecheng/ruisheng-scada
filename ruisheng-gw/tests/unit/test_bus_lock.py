"""per-bus asyncio.Lock serialization."""

from __future__ import annotations

import asyncio
import contextlib

import pytest
from ruisheng_gw.scheduler.bus_lock import BusLocks, BusLockTimeout


async def test_same_bus_serializes() -> None:
    locks = BusLocks(timeout_sec=1.0)
    order: list[str] = []

    async def holder(label: str, hold_sec: float) -> None:
        async with locks.acquire("bus-1"):
            order.append(f"{label}-in")
            await asyncio.sleep(hold_sec)
            order.append(f"{label}-out")

    t1 = asyncio.create_task(holder("A", 0.05))
    t2 = asyncio.create_task(holder("B", 0.01))
    await asyncio.gather(t1, t2)
    # B must wait until A-out before B-in
    assert order.index("A-out") < order.index("B-in")


async def test_different_buses_parallel() -> None:
    locks = BusLocks(timeout_sec=1.0)
    order: list[str] = []

    async def holder(bus: str, label: str) -> None:
        async with locks.acquire(bus):
            order.append(f"{label}-in")
            await asyncio.sleep(0.02)
            order.append(f"{label}-out")

    t1 = asyncio.create_task(holder("bus-1", "A"))
    t2 = asyncio.create_task(holder("bus-2", "B"))
    await asyncio.gather(t1, t2)
    # both should have started before the other finishes (parallel)
    assert order[0].endswith("-in") and order[1].endswith("-in")


async def test_timeout_raises() -> None:
    locks = BusLocks(timeout_sec=0.05)

    async def hog() -> None:
        async with locks.acquire("bus-1"):
            await asyncio.sleep(1.0)

    holder_task = asyncio.create_task(hog())
    await asyncio.sleep(0.01)  # let hog grab lock
    with pytest.raises(BusLockTimeout):
        async with locks.acquire("bus-1"):
            pass
    holder_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await holder_task
