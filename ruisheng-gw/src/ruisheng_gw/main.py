"""ruisheng-gw 启动入口。

- schema mismatch → exit 1
- alembic mismatch → exit 2
- config invalid → exit 3
- graceful shutdown → exit 0
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

    from ruisheng_gw.config import Config


# hardcoded literal — 与 G7 #28-B 两版本字段分离一致
# 升 shared 时 gw PR 必须同步改此常量
REQUIRED_SHARED_SCHEMA_VERSION: int = 20260415
EXPECTED_ALEMBIC_HEAD: str = "959079e6cae9"


def check_shared_schema_version(required: int = REQUIRED_SHARED_SCHEMA_VERSION) -> None:
    from ruisheng_shared import SHARED_SCHEMA_VERSION  # noqa: PLC0415

    if required != SHARED_SCHEMA_VERSION:
        raise RuntimeError(f"shared mismatch: expect {required}, got {SHARED_SCHEMA_VERSION}")


async def check_alembic_head(
    engine: AsyncEngine,
    expected: str = EXPECTED_ALEMBIC_HEAD,
) -> None:
    from sqlalchemy import text  # noqa: PLC0415

    async with engine.begin() as conn:
        row = await conn.execute(text("SELECT version_num FROM alembic_version"))
        current = row.scalar_one_or_none()
    if current != expected:
        raise RuntimeError(f"alembic mismatch: expect {expected}, got {current}")


async def run_server(config: Config) -> None:  # noqa: C901, PLR0915
    """Wire all components and run the gateway service until SIGTERM/SIGINT.

    Startup sequence:
    1. Create engine + alembic check
    2. WAL replay (drain any rows written during previous crash)
    3. Load Registry from DB
    4. Start BatchWriter background task
    5. Start TCP server
    6. Wait for shutdown signal
    7. Graceful shutdown (stop batch_writer + tcp server)
    """
    from sqlalchemy.ext.asyncio import create_async_engine  # noqa: PLC0415

    from ruisheng_gw.domain.registry import Registry  # noqa: PLC0415
    from ruisheng_gw.persistence.batch_writer import BatchWriter  # noqa: PLC0415
    from ruisheng_gw.persistence.repository import Repository  # noqa: PLC0415
    from ruisheng_gw.persistence.wal import Wal  # noqa: PLC0415
    from ruisheng_gw.scheduler.bus_lock import BusLocks  # noqa: PLC0415
    from ruisheng_gw.scheduler.clock import RealClock  # noqa: PLC0415
    from ruisheng_gw.scheduler.supervisor import Supervisor  # noqa: PLC0415
    from ruisheng_gw.transport.serial_bus import SerialBus  # noqa: PLC0415
    from ruisheng_gw.transport.session import SessionMap  # noqa: PLC0415
    from ruisheng_gw.transport.tcp_server import GwServer  # noqa: PLC0415

    # 1. Engine + alembic check
    engine = create_async_engine(config.database_url)
    await check_alembic_head(engine)

    # 2. WAL replay (before accepting connections)
    wal = Wal(
        wal_dir=config.wal_dir,
        single_file_mb=float(config.wal_single_file_mb),
        total_gb=float(config.wal_total_gb),
    )
    repo = Repository(engine)
    await wal.replay_and_cleanup(sink=repo.flush)

    # 3. Load Registry
    registry = await Registry.load_from_db(engine)

    # 4. Wire BatchWriter
    clock = RealClock()

    class _Sink:
        async def flush(self, rows: list) -> None:  # type: ignore[type-arg]
            await repo.flush(rows)

    batch = BatchWriter(
        sink=_Sink(),
        clock=clock,
        flush_size=config.batch_flush_rows,
        flush_interval_sec=config.batch_flush_ms / 1000.0,
        queue_maxsize=config.batch_queue_maxsize,
        wal_append=wal.append,
    )
    batch_task = asyncio.create_task(batch.run())

    # 5. Create session/scheduling infrastructure
    bus_locks = BusLocks(timeout_sec=float(config.bus_lock_timeout_sec))
    supervisor = Supervisor(clock=clock)
    session_map = SessionMap()

    # Minimal handler: accept connection and discard (full dispatch in Plan 1.5)
    async def _noop_handler(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        writer.close()

    async def _noop_serial_frame(dev_number: str, frame: bytes) -> None:
        pass

    server = GwServer(
        host=config.listen_host,
        port=config.listen_port,
        handler=_noop_handler,
    )
    await server.start()

    # 5b. Start serial buses (if configured)
    serial_buses: list[SerialBus] = []
    serial_tasks: list[asyncio.Task[None]] = []
    for sp_cfg in config.serial_ports:
        bus = SerialBus(
            port=sp_cfg.port,
            baud_rate=sp_cfg.baud_rate,
            registry=registry,
            session_map=session_map,
            on_frame=_noop_serial_frame,
        )
        serial_buses.append(bus)
        task = asyncio.create_task(bus.start())
        serial_tasks.append(task)

    # 6. Wait for SIGTERM/SIGINT
    stop_event = asyncio.Event()

    def _set_stop(*_: object) -> None:
        stop_event.set()

    loop = asyncio.get_running_loop()
    if sys.platform != "win32":
        loop.add_signal_handler(signal.SIGTERM, _set_stop)
        loop.add_signal_handler(signal.SIGINT, _set_stop)
    else:
        # Windows: signals are not supported in asyncio; rely on KeyboardInterrupt
        pass

    try:
        await stop_event.wait()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        # 7. Graceful shutdown
        supervisor.shutdown_sync()
        for bus in serial_buses:
            await bus.shutdown()
        if serial_tasks:
            await asyncio.gather(*serial_tasks, return_exceptions=True)
        batch.stop()
        await server.shutdown()
        with contextlib.suppress(Exception, asyncio.CancelledError):
            await asyncio.wait_for(batch_task, timeout=5.0)
        await engine.dispose()
        # suppress unused-var warnings from type checkers
        _ = registry
        _ = bus_locks
        _ = session_map


async def run_gw_service_for_test(
    postgres_url: str,
    redis_url: str,
    wal_dir: str,
    port: int,
) -> None:
    """Test harness: create a minimal Config and call run_server().

    Intended for integration tests that need a live service.
    The caller must cancel the returned task to stop the service.
    """
    from ruisheng_gw.config import Config  # noqa: PLC0415

    cfg = Config(
        listen_host="127.0.0.1",
        listen_port=port,
        database_url=postgres_url,
        redis_url=redis_url,
        wal_dir=wal_dir,
        wal_single_file_mb=10,
        wal_total_gb=1,
        batch_flush_rows=500,
        batch_flush_ms=100,
        batch_queue_maxsize=1000,
    )
    await run_server(cfg)


def main() -> int:
    import argparse  # noqa: PLC0415

    parser = argparse.ArgumentParser(prog="ruisheng-gw")
    parser.add_argument(
        "--check-only", action="store_true", help="startup checks only, then exit 0"
    )
    parser.add_argument(
        "--print-config", action="store_true", help="print resolved config and exit"
    )
    args = parser.parse_args()

    try:
        check_shared_schema_version()
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    if args.check_only:
        print("ok: shared schema version matches")
        return 0

    if args.print_config:
        from pydantic import ValidationError  # noqa: PLC0415

        from ruisheng_gw.config import Config  # noqa: PLC0415

        try:
            cfg = Config()
        except ValidationError as e:
            print(f"ERROR: config invalid: {e}", file=sys.stderr)
            return 3
        print(cfg.model_dump_json(indent=2))
        return 0

    # TODO A4+ — structlog + health + run_server (后续 task)
    print("ruisheng-gw main not yet fully implemented (Stage A4+)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
