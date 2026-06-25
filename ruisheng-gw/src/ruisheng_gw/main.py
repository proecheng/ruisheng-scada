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
from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

    from ruisheng_gw.config import Config
    from ruisheng_gw.domain.registry import RegistryEntry

PollerFactory = Callable[[], Coroutine[Any, Any, None]]


# hardcoded literal — 与 G7 #28-B 两版本字段分离一致
# 升 shared 时 gw PR 必须同步改此常量
REQUIRED_SHARED_SCHEMA_VERSION: int = 20260415
EXPECTED_ALEMBIC_HEAD: str = "0011_device_enable_flag"
_PEER_HOST_PORT_LEN = 2


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
    import redis.asyncio as redis_async  # noqa: PLC0415
    from aiohttp import web  # noqa: PLC0415
    from sqlalchemy.ext.asyncio import create_async_engine  # noqa: PLC0415

    from ruisheng_gw.domain.registry import Registry  # noqa: PLC0415
    from ruisheng_gw.health import HealthState, create_health_app  # noqa: PLC0415
    from ruisheng_gw.ingest import FrameIngestor  # noqa: PLC0415
    from ruisheng_gw.logging_setup import get_logger  # noqa: PLC0415
    from ruisheng_gw.persistence.batch_writer import BatchWriter  # noqa: PLC0415
    from ruisheng_gw.persistence.repository import Repository  # noqa: PLC0415
    from ruisheng_gw.persistence.wal import Wal  # noqa: PLC0415
    from ruisheng_gw.protocol.exceptions import ProtocolError  # noqa: PLC0415
    from ruisheng_gw.protocol.frames import RegisterFrame, decode_frame_by_funcode  # noqa: PLC0415
    from ruisheng_gw.pubsub.publisher import Publisher  # noqa: PLC0415
    from ruisheng_gw.scheduler.bus_lock import BusLocks  # noqa: PLC0415
    from ruisheng_gw.scheduler.clock import RealClock  # noqa: PLC0415
    from ruisheng_gw.scheduler.poller import poller_loop  # noqa: PLC0415
    from ruisheng_gw.scheduler.supervisor import Supervisor  # noqa: PLC0415
    from ruisheng_gw.transport.connection import Connection  # noqa: PLC0415
    from ruisheng_gw.transport.serial_bus import SerialBus  # noqa: PLC0415
    from ruisheng_gw.transport.session import SessionMap  # noqa: PLC0415
    from ruisheng_gw.transport.tcp_server import GwServer  # noqa: PLC0415

    log = get_logger(__name__)

    # 0. Health endpoint (starts before DB check so /health always responds)
    health_state = HealthState()
    health_app = create_health_app(health_state)
    runner = web.AppRunner(health_app)
    await runner.setup()
    health_site = web.TCPSite(runner, "0.0.0.0", config.health_port)
    await health_site.start()
    log.info("health endpoint started", port=config.health_port)

    # 1. Engine + alembic check
    engine = create_async_engine(config.database_url)
    await check_alembic_head(engine)
    health_state.set_db_ok(True)

    # 2. WAL replay (before accepting connections)
    wal = Wal(
        wal_dir=config.wal_dir,
        single_file_mb=float(config.wal_single_file_mb),
        total_gb=float(config.wal_total_gb),
    )
    repo = Repository(engine)
    await wal.replay_and_cleanup(sink=repo.flush)
    redis = redis_async.from_url(config.redis_url, decode_responses=True)

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
    publisher = Publisher(redis=redis)
    ingestor = FrameIngestor(registry=registry, batch=batch, publisher=publisher)

    def _tcp_bus_id(writer: asyncio.StreamWriter) -> str:
        peer = writer.get_extra_info("peername")
        if isinstance(peer, tuple) and len(peer) >= _PEER_HOST_PORT_LEN:
            return f"tcp:{peer[0]}:{peer[1]}"
        return f"tcp:{id(writer)}"

    def _tcp_peer_ip(writer: asyncio.StreamWriter) -> str | None:
        peer = writer.get_extra_info("peername")
        if isinstance(peer, tuple) and len(peer) >= _PEER_HOST_PORT_LEN:
            return str(peer[0])
        return None

    async def _tcp_handler(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        bus_id = _tcp_bus_id(writer)
        peer_ip = _tcp_peer_ip(writer)
        bound_dev_number: str | None = None

        def _is_allowed_tcp_source(entry: RegistryEntry) -> bool:
            return entry.dev_ip is None or peer_ip == entry.dev_ip

        def _log_and_check_tcp_source(entry: RegistryEntry, *, event: str) -> bool:
            if _is_allowed_tcp_source(entry):
                return True
            log.warning(
                event,
                dev_number=entry.device.dev_number,
                peer_ip=peer_ip,
                expected_ip=entry.dev_ip,
            )
            return False

        async def _handle_register_frame(frame: bytes, decoded: RegisterFrame) -> None:
            nonlocal bound_dev_number
            entry = registry.tcp_device_for_dev_ser_number(decoded.dev_ser_number)
            if entry is None:
                log.warning(
                    "tcp register ignored: unknown or ambiguous serial",
                    dev_ser_number=decoded.dev_ser_number,
                )
                return
            if not _log_and_check_tcp_source(
                entry, event="tcp register ignored: source ip mismatch"
            ):
                return
            bound_dev_number = entry.device.dev_number
            session_map.bind(dev_number=bound_dev_number, writer=writer, bus_id=bus_id)
            await ingestor.process_frame(dev_number=bound_dev_number, frame=frame)

        async def _handle_data_frame(frame: bytes) -> None:
            nonlocal bound_dev_number
            entry = (
                registry.get(bound_dev_number) if bound_dev_number else None
            ) or registry.tcp_device_for_modbus_addr(frame[0])
            if entry is None:
                log.warning("tcp frame ignored: unknown or ambiguous slave", slave=frame[0])
                return
            if not _log_and_check_tcp_source(entry, event="tcp frame ignored: source ip mismatch"):
                return
            dev_number = entry.device.dev_number
            bound_dev_number = dev_number
            session_map.bind(dev_number=dev_number, writer=writer, bus_id=bus_id)
            pending_read = session_map.get(dev_number).pending_read  # type: ignore[union-attr]
            await ingestor.process_frame_for_pending(
                dev_number=dev_number,
                frame=frame,
                pending_read=pending_read,
            )
            session_map.set_pending_read(dev_number, None)

        async def _on_frame(frame: bytes) -> None:
            if not frame:
                return
            try:
                decoded = decode_frame_by_funcode(frame)
            except ProtocolError:
                return
            if isinstance(decoded, RegisterFrame):
                await _handle_register_frame(frame, decoded)
            else:
                await _handle_data_frame(frame)

        conn = Connection(
            reader=reader,
            writer=writer,
            on_frame=_on_frame,
            heartbeat_timeout_sec=float(config.heartbeat_timeout_sec),
        )
        await conn.read_loop()

    async def _serial_frame(dev_number: str, frame: bytes) -> None:
        entry = session_map.get(dev_number)
        pending_read = entry.pending_read if entry else None
        await ingestor.process_frame_for_pending(
            dev_number=dev_number,
            frame=frame,
            pending_read=pending_read,
        )
        session_map.set_pending_read(dev_number, None)

    def _make_poller(entry: RegistryEntry, dev_number: str) -> PollerFactory:
        async def _run() -> None:
            await poller_loop(
                dev_number=dev_number,
                entry=entry,
                session=session_map,
                bus_locks=bus_locks,
                clock=clock,
            )

        return _run

    for entry in registry.entries():
        dev_number = entry.device.dev_number
        supervisor.start_poller(
            dev_number=dev_number,
            coro_factory=_make_poller(entry, dev_number),
        )

    server = GwServer(
        host=config.listen_host,
        port=config.listen_port,
        handler=_tcp_handler,
    )
    await server.start()
    log.info("TCP server started", host=config.listen_host, port=config.listen_port)

    # 5b. Start serial buses (if configured)
    serial_buses: list[SerialBus] = []
    serial_tasks: list[asyncio.Task[None]] = []
    for sp_cfg in config.serial_ports:
        bus = SerialBus(
            port=sp_cfg.port,
            baud_rate=sp_cfg.baud_rate,
            registry=registry,
            session_map=session_map,
            on_frame=_serial_frame,
        )
        serial_buses.append(bus)
        task = asyncio.create_task(bus.start())
        port_name = sp_cfg.port

        def _on_bus_done(t: asyncio.Task[None], _port: str = port_name) -> None:
            exc = t.exception() if not t.cancelled() else None
            if exc is not None:
                import logging  # noqa: PLC0415

                logging.getLogger(__name__).error("serial bus %s failed: %s", _port, exc)

        task.add_done_callback(_on_bus_done)
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
        log.info("shutting down")
        supervisor.shutdown_sync()
        for bus in serial_buses:
            await bus.shutdown()
        if serial_tasks:
            await asyncio.gather(*serial_tasks, return_exceptions=True)
        batch.stop()
        await server.shutdown()
        with contextlib.suppress(Exception, asyncio.CancelledError):
            await asyncio.wait_for(batch_task, timeout=5.0)
        await runner.cleanup()
        await redis.close()
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


def main() -> int:  # noqa: PLR0911
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

    from pydantic import ValidationError  # noqa: PLC0415

    from ruisheng_gw.config import Config  # noqa: PLC0415
    from ruisheng_gw.logging_setup import configure_logging  # noqa: PLC0415

    configure_logging()

    try:
        cfg = Config()
    except ValidationError as e:
        print(f"ERROR: config invalid: {e}", file=sys.stderr)
        return 3

    try:
        asyncio.run(run_server(cfg))
    except KeyboardInterrupt:
        pass
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
