"""Supervisor: create_task + add_done_callback + 10min/3x quarantine."""

from __future__ import annotations

import asyncio

from ruisheng_gw.scheduler.clock import FakeClock
from ruisheng_gw.scheduler.supervisor import Supervisor


async def test_restart_on_exception() -> None:
    clock = FakeClock(now=0.0)
    call_count = {"n": 0}

    async def flaky() -> None:
        call_count["n"] += 1
        if call_count["n"] <= 2:
            raise RuntimeError("boom")
        await asyncio.sleep(10)  # long run; supervisor won't restart if doesn't crash

    sup = Supervisor(clock=clock, max_restarts=3, window_sec=600, initial_backoff=0.01)
    sup.start_poller(dev_number="DEV-001", coro_factory=flaky)
    await asyncio.sleep(0.1)
    clock.advance(1.0)  # trigger first backoff expiry
    await asyncio.sleep(0.05)
    clock.advance(1.0)
    await asyncio.sleep(0.05)
    # 2 crashes + 1 still-running expected
    assert call_count["n"] >= 3
    assert sup.health["DEV-001"].quarantined is False
    sup.shutdown_sync()


async def test_quarantine_after_threshold() -> None:
    clock = FakeClock(now=0.0)
    call_count = {"n": 0}

    async def always_fail() -> None:
        call_count["n"] += 1
        raise RuntimeError("always")

    sup = Supervisor(clock=clock, max_restarts=3, window_sec=600, initial_backoff=0.01)
    sup.start_poller(dev_number="DEV-001", coro_factory=always_fail)
    for _ in range(10):
        await asyncio.sleep(0.02)
        clock.advance(1.0)
    assert sup.health["DEV-001"].quarantined is True
    sup.shutdown_sync()
