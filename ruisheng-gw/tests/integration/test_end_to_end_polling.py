"""Integration: startup sequence (WAL replay + Registry load + TCP server) runs without error."""

from __future__ import annotations

import asyncio
from pathlib import Path

from ruisheng_gw.persistence.batch_writer import BatchRow
from ruisheng_gw.persistence.repository import Repository
from ruisheng_gw.persistence.wal import Wal
from ruisheng_gw.scheduler.clock import RealClock
from sqlalchemy.ext.asyncio import create_async_engine


async def test_startup_wires_wal_replay(postgres_url: str, tmp_path: Path) -> None:
    """Startup: WAL replay runs before service accepts connections."""
    # Pre-create a WAL file with some rows (simulates a previous crash leaving WAL data)
    wal = Wal(wal_dir=str(tmp_path), single_file_mb=10, total_gb=1)
    await wal.append(
        [
            BatchRow(
                dev_number="STARTUP001",
                point_id=1,
                rt_value=1.0,
                org_value=1.0,
                recorded_at=1_700_000_000.0,
            ),
        ]
    )

    # Verify the WAL file was written (sync glob is intentional here)
    wal_files_before = list(tmp_path.glob("*.ndjson"))  # noqa: ASYNC240
    assert len(wal_files_before) >= 1, "WAL file should exist before replay"

    # Simulate startup: replay into repository (mirrors run_server step 2)
    engine = create_async_engine(postgres_url)
    repo = Repository(engine)
    await wal.replay_and_cleanup(sink=repo.flush)

    # Verify row was replayed to DB
    rt = await repo.fetch_realtime(dev_number="STARTUP001")
    assert len(rt) == 1
    assert rt[0].rt_value == 1.0

    # Verify WAL files are cleaned up after successful replay (sync glob intentional)
    remaining = list(tmp_path.glob("*.ndjson"))  # noqa: ASYNC240
    assert remaining == [], "WAL files should be deleted after replay"

    await engine.dispose()


async def test_empty_wal_replay_is_noop(postgres_url: str, tmp_path: Path) -> None:
    """Startup: replaying an empty WAL directory completes without error."""
    wal = Wal(wal_dir=str(tmp_path), single_file_mb=10, total_gb=1)
    engine = create_async_engine(postgres_url)
    repo = Repository(engine)

    # Should complete without raising even when no WAL files exist
    await wal.replay_and_cleanup(sink=repo.flush)

    await engine.dispose()


async def test_registry_loads_from_db(postgres_url: str) -> None:
    """Registry.load_from_db() returns a Registry without errors (may be empty)."""
    from ruisheng_gw.domain.registry import Registry  # noqa: PLC0415

    engine = create_async_engine(postgres_url)
    registry = await Registry.load_from_db(engine)
    # Registry may be empty (no seed data in test DB) but load must succeed
    assert registry is not None
    await engine.dispose()


async def test_tcp_server_listens(tmp_path: Path) -> None:  # noqa: ARG001
    """GwServer binds to a port and accepts TCP connections."""
    from ruisheng_gw.transport.tcp_server import GwServer  # noqa: PLC0415

    connected = asyncio.Event()

    async def _handler(
        reader: asyncio.StreamReader,  # noqa: ARG001
        writer: asyncio.StreamWriter,
    ) -> None:
        connected.set()
        writer.close()

    server = GwServer(host="127.0.0.1", port=0, handler=_handler)
    await server.start()
    assert server.is_listening()

    port = server.actual_port()
    assert port > 0

    # Connect a real TCP client to verify the server accepts connections
    _reader, writer = await asyncio.open_connection("127.0.0.1", port)
    await asyncio.wait_for(connected.wait(), timeout=2.0)
    writer.close()
    await writer.wait_closed()

    await server.shutdown()
    assert not server.is_listening()


async def test_batch_writer_flushes_to_db(postgres_url: str, tmp_path: Path) -> None:
    """BatchWriter drains its queue into the repository."""
    from ruisheng_gw.persistence.batch_writer import BatchWriter  # noqa: PLC0415

    wal = Wal(wal_dir=str(tmp_path), single_file_mb=10, total_gb=1)
    engine = create_async_engine(postgres_url)
    repo = Repository(engine)
    clock = RealClock()

    class _Sink:
        async def flush(self, rows: list) -> None:  # type: ignore[type-arg]
            await repo.flush(rows)

    batch = BatchWriter(
        sink=_Sink(),
        clock=clock,
        flush_size=10,
        flush_interval_sec=0.05,
        queue_maxsize=100,
        wal_append=wal.append,
    )

    task = asyncio.create_task(batch.run())

    batch.submit(
        BatchRow(
            dev_number="E2ETEST001",
            point_id=1,
            rt_value=42.0,
            org_value=42.0,
            recorded_at=1_700_000_000.0,
        )
    )

    # Give the writer enough time to flush
    await asyncio.sleep(0.3)
    batch.stop()
    await asyncio.wait_for(task, timeout=2.0)

    rt = await repo.fetch_realtime(dev_number="E2ETEST001")
    assert len(rt) == 1
    assert rt[0].rt_value == 42.0

    await engine.dispose()
