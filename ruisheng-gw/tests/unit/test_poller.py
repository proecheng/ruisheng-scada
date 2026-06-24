"""per-device poller coroutine: interval + writer re-lookup per poll."""

from __future__ import annotations

import asyncio
import contextlib

from ruisheng_gw.scheduler.bus_lock import BusLocks
from ruisheng_gw.scheduler.clock import FakeClock
from ruisheng_gw.scheduler.poller import poll_once, poller_loop
from ruisheng_gw.transport.session import PendingRead


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
        self.pending_read: PendingRead | None = None

    def get(self, dev_number: str):
        if self.writer is None:
            return None
        return _Entry(writer=self, generation=self.generation, bus_id=self.bus_id)

    def write(self, data: bytes) -> None:
        self.write_log.append(data)

    async def drain(self) -> None:
        pass

    def set_pending_read(self, dev_number: str, pending_read: PendingRead | None) -> None:
        self.pending_read = pending_read


class _Entry:
    def __init__(self, writer, generation, bus_id):
        self.writer = writer
        self.generation = generation
        self.bus_id = bus_id


def _add_default_point(entry) -> None:
    from ruisheng_gw.domain.point import Point
    from ruisheng_gw.domain.registry import PointEntry, ThresholdSpec

    entry.points[1] = PointEntry(
        point=Point(
            point_id=1,
            dev_number=entry.device.dev_number,
            point_ratio=1.0,
            point_offset=0.0,
            user_ratio=1.0,
            user_point_offset=0.0,
            point_number=0,
            fun_code=3,
            dev_addr=1,
            value_type="字",
        ),
        threshold=ThresholdSpec(min_val=None, max_val=None, alarm_level=1),
    )


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
        modbus_addr=7,
    )
    _add_default_point(entry)
    await poll_once(dev_number="DEV-001", entry=entry, session=sess, bus_locks=locks)
    assert len(sess.write_log) == 1  # at least one poll frame sent
    assert sess.write_log[0][0] == 7


async def test_poll_once_uses_configured_fun_code_and_address() -> None:
    sess = FakeSession()
    sess.writer = sess
    sess.generation = 1
    locks = BusLocks(timeout_sec=1.0)
    from ruisheng_gw.domain.device import Device
    from ruisheng_gw.domain.point import Point
    from ruisheng_gw.domain.registry import PointEntry, RegistryEntry, ThresholdSpec

    entry = RegistryEntry(
        device=Device(dev_number="DEV-001", usr_group="ug_A"),
        update_interval_decisec=10,
        modbus_addr=7,
    )
    entry.points[10] = PointEntry(
        point=Point(
            point_id=10,
            dev_number="DEV-001",
            point_ratio=1.0,
            point_offset=0.0,
            user_ratio=1.0,
            user_point_offset=0.0,
            point_number=12,
            fun_code=4,
            dev_addr=1,
            value_type="双字",
        ),
        threshold=ThresholdSpec(min_val=None, max_val=None, alarm_level=1),
    )

    await poll_once(dev_number="DEV-001", entry=entry, session=sess, bus_locks=locks)

    assert sess.write_log[0][:6] == bytes.fromhex("0704000C0002")
    assert sess.pending_read is not None
    assert sess.pending_read.fun_code == 4
    assert sess.pending_read.start_addr == 12
    assert sess.pending_read.quantity == 2


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
    _add_default_point(entry)
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
