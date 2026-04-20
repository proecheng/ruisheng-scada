"""WAL integration: DB down → WAL fallback → replay on reconnect."""

from __future__ import annotations

from pathlib import Path

from ruisheng_gw.persistence.batch_writer import BatchRow, BatchWriter
from ruisheng_gw.persistence.repository import Repository
from ruisheng_gw.persistence.wal import Wal
from ruisheng_gw.scheduler.clock import RealClock
from sqlalchemy.ext.asyncio import create_async_engine


async def test_wal_fallback_and_replay(postgres_url: str, tmp_path: Path) -> None:
    """When DB flush fails (simulated), rows go to WAL. Replay restores them."""
    engine = create_async_engine(postgres_url)
    wal = Wal(wal_dir=str(tmp_path), single_file_mb=10, total_gb=1)
    repo = Repository(engine)

    # Simulate WAL fallback: write rows directly to WAL as if DB was down
    rows = [
        BatchRow(
            dev_number="WALTEST001",
            point_id=i,
            rt_value=float(i),
            org_value=0.0,
            recorded_at=1_700_000_000.0,
        )
        for i in range(3)
    ]
    await wal.append(rows)

    # Verify rows went to WAL
    wal_files = list(tmp_path.glob("*.ndjson"))  # noqa: ASYNC240
    assert len(wal_files) >= 1, "Expected WAL file"

    # replay: DB is back up now
    await wal.replay_and_cleanup(sink=repo.flush)
    rt = await repo.fetch_realtime(dev_number="WALTEST001")
    assert len(rt) == 3

    # WAL files cleaned up after successful replay
    assert not list(tmp_path.glob("*.ndjson")), "WAL files should be deleted after replay"  # noqa: ASYNC240

    await engine.dispose()


async def test_wal_writer_fallback_on_persistent_failure(tmp_path: Path) -> None:
    """BatchWriter falls back to WAL after all retries exhausted."""
    import asyncio
    import contextlib

    wal = Wal(wal_dir=str(tmp_path), single_file_mb=10, total_gb=1)

    async def always_failing_flush(rows: list[BatchRow]) -> None:
        raise RuntimeError("simulated persistent DB down")

    clock = RealClock()
    writer = BatchWriter(
        sink=type("S", (), {"flush": staticmethod(always_failing_flush)})(),
        clock=clock,
        flush_size=3,
        flush_interval_sec=0.05,
        queue_maxsize=100,
        wal_append=wal.append,
        retry_initial_backoff=0.01,  # fast retry for test
    )

    rows = [
        BatchRow(
            dev_number="WALBATCH001",
            point_id=i,
            rt_value=float(i),
            org_value=0.0,
            recorded_at=1_700_000_000.0,
        )
        for i in range(3)
    ]
    task = asyncio.create_task(writer.run())
    for r in rows:
        writer.submit(r)
    await asyncio.sleep(1.0)  # enough time for retries + WAL
    writer.stop()
    with contextlib.suppress(Exception):
        await task

    wal_files = list(tmp_path.glob("*.ndjson"))  # noqa: ASYNC240
    assert len(wal_files) >= 1, "Expected WAL file after persistent failure"
    assert writer.stats["wal_fallback_total"] >= 1
