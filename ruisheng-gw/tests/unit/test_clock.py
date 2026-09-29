"""Clock protocol: RealClock (prod) + FakeClock (test, deterministic)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from ruisheng_gw.scheduler import clock as clock_module
from ruisheng_gw.scheduler.clock import FakeClock, RealClock


async def test_real_clock_sleep_and_monotonic() -> None:
    c = RealClock()
    t0 = c.monotonic()
    await c.sleep(0.01)
    t1 = c.monotonic()
    assert t1 - t0 >= 0.009


async def test_real_clock_rewaits_after_early_timer_wakeups(monkeypatch) -> None:
    clock = RealClock()
    monotonic = MagicMock(side_effect=[100.0, 100.05, 100.125, 100.2])
    sleeper = AsyncMock()
    monkeypatch.setattr(clock, "monotonic", monotonic)
    monkeypatch.setattr(clock_module.asyncio, "sleep", sleeper)

    await clock.sleep(0.2)

    assert [call.args[0] for call in sleeper.await_args_list] == pytest.approx([0.2, 0.15, 0.075])
    assert monotonic.call_count == 4


@pytest.mark.parametrize("seconds", [0.0, -0.5])
async def test_real_clock_non_positive_sleep_still_yields(seconds) -> None:
    turns = []
    asyncio.get_running_loop().call_soon(turns.append, "yielded")
    await RealClock().sleep(seconds)
    assert turns == ["yielded"]


async def test_real_clock_cancellation_during_rewait_propagates(monkeypatch) -> None:
    clock = RealClock()
    sleeper = AsyncMock(side_effect=[None, asyncio.CancelledError()])
    monkeypatch.setattr(clock, "monotonic", MagicMock(side_effect=[100.0, 100.05]))
    monkeypatch.setattr(clock_module.asyncio, "sleep", sleeper)
    with pytest.raises(asyncio.CancelledError):
        await clock.sleep(0.2)
    assert sleeper.await_count == 2


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
