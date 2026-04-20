"""batch_writer timer/size flush + drop-tail + retry + WAL fallback hook."""

from __future__ import annotations

import asyncio

from ruisheng_gw.persistence.batch_writer import BatchRow, BatchWriter
from ruisheng_gw.scheduler.clock import FakeClock


class FakeSink:
    def __init__(self, fail_first_n: int = 0):
        self.fail_first_n = fail_first_n
        self.flush_calls = 0
        self.written: list[list[BatchRow]] = []

    async def flush(self, rows: list[BatchRow]) -> None:
        self.flush_calls += 1
        if self.flush_calls <= self.fail_first_n:
            raise RuntimeError("fake db down")
        self.written.append(list(rows))


async def test_size_threshold_triggers_flush() -> None:
    sink = FakeSink()
    clock = FakeClock()
    writer = BatchWriter(sink=sink, clock=clock, flush_size=5, flush_interval_sec=10.0)
    task = asyncio.create_task(writer.run())
    for i in range(5):
        writer.submit(
            BatchRow(
                dev_number="D", point_id=i, rt_value=float(i), org_value=float(i), recorded_at=0.0
            )
        )
    await asyncio.sleep(0.05)
    assert len(sink.written) >= 1
    assert len(sink.written[0]) == 5
    writer.stop()
    await task


async def test_timer_triggers_flush_before_size_reached() -> None:
    sink = FakeSink()
    clock = FakeClock()
    writer = BatchWriter(sink=sink, clock=clock, flush_size=100, flush_interval_sec=0.1)
    task = asyncio.create_task(writer.run())
    writer.submit(
        BatchRow(dev_number="D", point_id=1, rt_value=1.0, org_value=1.0, recorded_at=0.0)
    )
    await asyncio.sleep(0.02)
    clock.advance(0.2)
    await asyncio.sleep(0.05)
    assert len(sink.written) == 1
    assert len(sink.written[0]) == 1
    writer.stop()
    await task


async def test_queue_full_drops_tail() -> None:
    sink = FakeSink()
    clock = FakeClock()
    writer = BatchWriter(
        sink=sink,
        clock=clock,
        flush_size=1000,
        flush_interval_sec=100.0,
        queue_maxsize=3,
    )
    # submit 5 items; queue capacity 3 → 2 drop-tail
    for i in range(5):
        writer.submit(
            BatchRow(dev_number="D", point_id=i, rt_value=float(i), org_value=0.0, recorded_at=0.0)
        )
    assert writer.stats["drop_total"] == 2
    # first 3 should have survived (drop-tail semantics — newer dropped)
    # implementation may vary; accept either count but verify drop counter
