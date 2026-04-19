"""Clock protocol: RealClock (prod) + FakeClock (test, deterministic)."""

from __future__ import annotations

import asyncio

from ruisheng_gw.scheduler.clock import FakeClock, RealClock


async def test_real_clock_sleep_and_monotonic() -> None:
    c = RealClock()
    t0 = c.monotonic()
    await c.sleep(0.01)
    t1 = c.monotonic()
    assert t1 - t0 >= 0.009


async def test_fake_clock_advance_wakes_sleeper() -> None:
    c = FakeClock(now=0.0)

    async def sleeper() -> float:
        await c.sleep(5.0)
        return c.monotonic()

    task = asyncio.create_task(sleeper())
    await asyncio.sleep(0)  # let task enter sleep
    assert not task.done()
    c.advance(5.0)
    await asyncio.sleep(0)
    assert task.done()
    assert await task == 5.0


async def test_fake_clock_multiple_sleepers() -> None:
    c = FakeClock(now=0.0)

    async def sleeper(d: float) -> float:
        await c.sleep(d)
        return c.monotonic()

    t1 = asyncio.create_task(sleeper(1.0))
    t2 = asyncio.create_task(sleeper(3.0))
    await asyncio.sleep(0)
    c.advance(2.0)
    await asyncio.sleep(0)
    assert t1.done() and not t2.done()
    c.advance(2.0)
    await asyncio.sleep(0)
    assert t2.done()
