"""Live gateway fixture for generated pcap replay tests."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import socket
from dataclasses import dataclass
from pathlib import Path

import pytest_asyncio
from ruisheng_gw.main import run_gw_service_for_test
from ruisheng_gw.persistence.repository import RealtimeRow, Repository
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

HEALTH_TOKEN = "a" * 43
HEALTH_TOKEN_DIGEST = hashlib.sha256(HEALTH_TOKEN.encode("ascii")).hexdigest()


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@dataclass
class LiveGateway:
    host: str
    port: int
    health_port: int
    health_token: str
    repo: Repository
    engine: AsyncEngine

    async def wait_for_realtime(
        self,
        *,
        dev_number: str,
        expected_values: list[int],
        timeout_sec: float = 5.0,
    ) -> list[RealtimeRow]:
        deadline = asyncio.get_running_loop().time() + timeout_sec
        while asyncio.get_running_loop().time() < deadline:
            rows = await self.repo.fetch_realtime(dev_number=dev_number)
            actual_values = [row.rt_value for row in rows]
            if actual_values == [float(value) for value in expected_values]:
                return rows
            await asyncio.sleep(0.05)
        raise AssertionError(f"gateway did not persist final realtime values for {dev_number}")

    async def history_count(self, dev_number: str) -> int:
        async with self.engine.connect() as conn:
            result = await conn.execute(
                text("SELECT count(*) FROM point_data_history WHERE dev_number = :dev_number"),
                {"dev_number": dev_number},
            )
            return int(result.scalar_one())


async def _seed_replay_device(engine: AsyncEngine, dev_number: str) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO wx_groups (usr_group, company_name)
                VALUES ('replay', 'Replay tests')
                ON CONFLICT (usr_group) DO NOTHING
                """
            )
        )
        await conn.execute(
            text(
                """
                INSERT INTO devices (
                    dev_number, dev_ser_number, transport_type, modbus_addr,
                    baud_rate, usr_group, update_interval_decisec,
                    loss_count, is_online, is_enabled, update_flag
                ) VALUES (
                    :dev_number, :dev_number, 'tcp', 1,
                    9600, 'replay', 100, 0, FALSE, TRUE, 0
                )
                ON CONFLICT (dev_number) DO UPDATE
                SET deleted_at = NULL, is_enabled = TRUE
                """
            ),
            {"dev_number": dev_number},
        )
        await conn.execute(
            text(
                """
                INSERT INTO device_points (
                    dev_number, point_name, point_number, fun_code, dev_addr,
                    value_type, point_ratio, point_offset, user_ratio,
                    user_point_offset, show
                )
                SELECT CAST(:dev_number AS varchar), point_name, point_number, 3, 1,
                       '字', 1.0, 0.0, 1.0, 0.0, 1
                FROM (VALUES ('value_0', 0), ('value_1', 1)) AS points(point_name, point_number)
                WHERE NOT EXISTS (
                    SELECT 1 FROM device_points
                    WHERE dev_number = CAST(:dev_number AS varchar)
                      AND point_number = points.point_number
                )
                """
            ),
            {"dev_number": dev_number},
        )
        await conn.execute(
            text("DELETE FROM point_data_realtime WHERE dev_number = :dev_number"),
            {"dev_number": dev_number},
        )
        await conn.execute(
            text("DELETE FROM point_data_history WHERE dev_number = :dev_number"),
            {"dev_number": dev_number},
        )


@pytest_asyncio.fixture
async def gw_server(
    corpus_case,
    postgres_url: str,
    redis_url: str,
    tmp_path: Path,
):
    _pcap, expected = corpus_case
    engine = create_async_engine(postgres_url, pool_pre_ping=True)
    dev_number = str(expected["dev_ser"])
    await _seed_replay_device(engine, dev_number)

    port = _free_port()
    health_port = _free_port()
    task = asyncio.create_task(
        run_gw_service_for_test(
            postgres_url=postgres_url,
            redis_url=redis_url,
            wal_dir=str(tmp_path / "wal"),
            port=port,
            health_port=health_port,
            health_token_sha256=HEALTH_TOKEN_DIGEST,
        )
    )

    for _ in range(100):
        if task.done():
            await task
        try:
            _reader, writer = await asyncio.open_connection("127.0.0.1", port)
        except OSError:
            await asyncio.sleep(0.05)
            continue
        writer.close()
        await writer.wait_closed()
        break
    else:
        task.cancel()
        raise AssertionError("gateway did not start listening")

    try:
        yield LiveGateway(
            host="127.0.0.1",
            port=port,
            health_port=health_port,
            health_token=HEALTH_TOKEN,
            repo=Repository(engine),
            engine=engine,
        )
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        await engine.dispose()
