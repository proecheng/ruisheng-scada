"""per-device poller coroutine: interval + writer re-lookup per poll."""

from __future__ import annotations

import asyncio
import contextlib

from ruisheng_gw.scheduler.bus_lock import BusLocks
from ruisheng_gw.scheduler.clock import FakeClock
from ruisheng_gw.scheduler.poller import poll_once, poller_loop


async def _drain(n: int = 5) -> None:
    """Yield to event loop n times to let pending callbacks run."""
    for _ in range(n):
        await asyncio.sleep(0)


class FakeSession:
    def __init__(self):
        self.writer = None
        self.generation = 0
        self.bus_id = "bus-1"
        self.write_log: list[bytes] = []

    def get(self, dev_number: str):
        if self.writer is None:
            return None
        return _Entry(writer=self, generation=self.generation, bus_id=self.bus_id)

    async def write(self, data: bytes) -> None:
        self.write_log.append(data)

    async def drain(self) -> None:
        pass


class _Entry:
    def __init__(self, writer, generation, bus_id):
        self.writer = writer
        self.generation = generation
        self.bus_id = bus_id


async def test_poll_once_writes_frame() -> None:
    sess = FakeSession()
    sess.writer = sess
    sess.generation = 1
    locks = BusLocks(timeout_sec=1.0)
    from ruisheng_gw.domain.device import Device
    from ruisheng_gw.domain.registry import RegistryEntry

    entry = RegistryEntry(
        device=Device(dev_number="DEV-001", usr_group="ug_A"),
        update_interval_decisec=10,
    )
    await poll_once(dev_number="DEV-001", entry=entry, session=sess, bus_locks=locks)
    assert len(sess.write_log) == 1  # at least one poll frame sent


async def test_poller_loop_respects_interval() -> None:
    sess = FakeSession()
    sess.writer = sess
    sess.generation = 1
    clock = FakeClock(now=0.0)
    locks = BusLocks(timeout_sec=1.0)
    from ruisheng_gw.domain.device import Device
    from ruisheng_gw.domain.registry import RegistryEntry

    entry = RegistryEntry(
        device=Device(dev_number="DEV-001", usr_group="ug_A"),
        update_interval_decisec=10,  # 1.0s
    )
    task = asyncio.create_task(
        poller_loop(
            dev_number="DEV-001",
            entry=entry,
            session=sess,
            bus_locks=locks,
            clock=clock,
        )
    )
    await _drain()
    # no poll yet — first wait
    assert len(sess.write_log) == 0
    clock.advance(1.0)
    await _drain()
    assert len(sess.write_log) >= 1
    clock.advance(1.0)
    await _drain()
    assert len(sess.write_log) >= 2
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


async def test_poller_skips_when_writer_none() -> None:
    sess = FakeSession()
    sess.writer = None  # DTU disconnected
    clock = FakeClock(now=0.0)
    locks = BusLocks(timeout_sec=1.0)
    from ruisheng_gw.domain.device import Device
    from ruisheng_gw.domain.registry import RegistryEntry

    entry = RegistryEntry(
        device=Device(dev_number="DEV-001", usr_group="ug_A"),
        update_interval_decisec=10,
    )
    task = asyncio.create_task(
        poller_loop(
            dev_number="DEV-001",
            entry=entry,
            session=sess,
            bus_locks=locks,
            clock=clock,
        )
    )
    await _drain()
    clock.advance(2.0)
    await _drain()
    # writer None → poll_once skipped; no writes
    assert sess.write_log == []
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
