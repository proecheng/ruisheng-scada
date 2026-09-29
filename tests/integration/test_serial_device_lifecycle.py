"""Real API/RLS/GW persistence with owned PostgreSQL and in-memory serial streams."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import asyncpg
import fakeredis.aioredis
import httpx
import pytest
from ruisheng_api.config import Config
from ruisheng_api.core.security import client_fingerprint, issue_access_token
from ruisheng_api.db.repositories import devices as devices_repo
from ruisheng_api.main import create_app
from ruisheng_gw.domain.registry import Registry
from ruisheng_gw.ingest import FrameIngestor
from ruisheng_gw.main import _periodic_configuration_refresh, _subscribe_configuration_changes
from ruisheng_gw.persistence.batch_writer import BatchWriter
from ruisheng_gw.persistence.device_status import sync_serial_status
from ruisheng_gw.persistence.repository import Repository
from ruisheng_gw.protocol.frames import ExceptionResponse, encode_exception_response
from ruisheng_gw.protocol.modbus_codec import append_crc_to_frame
from ruisheng_gw.pubsub.publisher import Publisher
from ruisheng_gw.scheduler.clock import FakeClock, RealClock
from ruisheng_gw.transport.serial_bus import SerialBus
from ruisheng_gw.transport.session import SessionMap
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from conftest import _skip_if_docker_unavailable, require_test_database

ROOT = Path(__file__).parents[2]
HEAD = "0013_serial_polling_profile"
PREVIOUS = "0012_alarm_notification_runtime"
PORT = "SIMULATED-RS485"
SECRET = "serial-integration-only-signing-key-at-least-32"
ROLE_PASSWORDS = {
    "RUISHENG_GW_PASSWORD": "serial-test-gw-password",
    "RUISHENG_API_PASSWORD": "serial-test-api-password",
}
pytestmark = pytest.mark.integration


async def test_serial_status_valid_reply_expiry_recovery_and_config_fencing(runtime):  # noqa: PLR0915
    await runtime.add_device(1)
    registry = await Registry.load_from_db(runtime.gw_engine)
    entry = registry.get("SIM001")
    before = (await runtime.database.rows("SELECT update_flag, updated_at FROM devices"))[0]

    async def sync():
        await sync_serial_status(runtime.gw_engine, registry, now=datetime.now(UTC).timestamp())

    async def assert_visible(online):
        detail = (await runtime.request("GET", "/api/devices/SIM001"))["data"]
        assert detail["is_online"] is online
        listed = (await runtime.request("GET", "/api/devices", params={"online_only": True}))[
            "data"
        ]
        assert listed["total"] == int(online)
        assert len(listed["items"]) == int(online)
        other = (await runtime.request("GET", "/api/devices", tenant="tenant_b"))["data"]
        assert other["total"] == 0
        return detail

    await sync()
    await assert_visible(False)
    async with StreamRig(runtime, registry) as rig:
        await rig.next_request()
        rig.reader.feed_data(response_frame(2))  # wrong slave cannot set availability
        await settle()
        await sync()
        await assert_visible(False)
        await rig.respond(response_frame(1))
        await sync()
        detail = await assert_visible(True)
        assert detail["last_call_at"] and detail["last_back_at"] and detail["loss_count"] == 0
        observed = (await runtime.database.rows("SELECT update_flag, updated_at FROM devices"))[0]
        assert observed["update_flag"] == before["update_flag"]
        await sync()
        assert (await runtime.database.rows("SELECT update_flag, updated_at FROM devices"))[
            0
        ] == observed
        entry.loss_count = 3
        await sync()
        await assert_visible(False)
        await rig.quiet()
        await rig.next_request()
        await rig.respond(response_frame(1))
        await sync()
        await assert_visible(True)

        # A stopped/crashed gateway cannot leave the public API permanently online.
        await runtime.database.rows("UPDATE devices SET last_back_at=now()-interval '11 seconds'")
        await assert_visible(False)
        await runtime.database.rows("UPDATE devices SET last_back_at=now()+interval '1 hour'")
        await assert_visible(False)
        entry.last_back -= 11
        await sync()
        assert not (await runtime.database.rows("SELECT is_online FROM devices"))[0][0]

        # Old in-flight observations cannot overwrite a new configuration or resurrect it.
        entry.last_back = datetime.now(UTC).timestamp()
        await runtime.request("PUT", "/api/devices/SIM001", json={"modbus_addr": 2})
        await sync()
        detail = await assert_visible(False)
        assert detail["last_back_at"] is None
        await rig.refresh()
        await sync()
        await assert_visible(False)


async def test_serial_status_rejects_other_tenant_disabled_and_deleted(runtime):
    await runtime.add_device(1)
    registry = await Registry.load_from_db(runtime.gw_engine)
    entry = registry.get("SIM001")
    entry.last_call = entry.last_back = datetime.now(UTC).timestamp()
    entry.device.usr_group = "tenant_b"
    await sync_serial_status(runtime.gw_engine, registry, now=entry.last_back)
    assert not (await runtime.database.rows("SELECT is_online FROM devices"))[0][0]
    entry.device.usr_group = "tenant_a"
    await runtime.database.rows("UPDATE devices SET is_enabled=false")
    await sync_serial_status(runtime.gw_engine, registry, now=entry.last_back)
    assert not (await runtime.database.rows("SELECT is_online FROM devices"))[0][0]
    await runtime.database.rows("UPDATE devices SET is_enabled=true, deleted_at=now()")
    await sync_serial_status(runtime.gw_engine, registry, now=entry.last_back)
    assert not (await runtime.database.rows("SELECT is_online FROM devices"))[0][0]


@pytest.fixture(autouse=True)
def require_test_database_target() -> None:
    """Override legacy fixtures: every target is an owned, explicitly test-named database."""


@pytest.fixture(autouse=True)
def require_dev_database() -> None:
    """Never connect to the pre-existing developer or deployed databases."""


@dataclass(frozen=True)
class Database:
    owner_url: str

    def role_url(self, role: str) -> str:
        require_test_database(self.owner_url)
        password = ROLE_PASSWORDS[f"{role.upper()}_PASSWORD"]
        return (
            make_url(self.owner_url)
            .set(username=role, password=password)
            .render_as_string(hide_password=False)
        )

    def migrate(self, direction: str, revision: str) -> subprocess.CompletedProcess[str]:
        require_test_database(self.owner_url)
        return subprocess.run(
            [sys.executable, "-m", "alembic", direction, revision],
            cwd=ROOT,
            env={**os.environ, **ROLE_PASSWORDS, "DATABASE_URL": self.owner_url},
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=180,
            check=False,
        )

    async def rows(self, sql: str, *args: object) -> list[asyncpg.Record]:
        require_test_database(self.owner_url)
        connection = await asyncpg.connect(self.owner_url.replace("+asyncpg", ""))
        try:
            return await connection.fetch(sql, *args)
        finally:
            await connection.close()


@pytest.fixture(scope="module")
def database_server() -> Iterator[str]:
    _skip_if_docker_unavailable()
    with PostgresContainer(
        "timescale/timescaledb:2.16.1-pg15",
        username="serial_test_owner",
        password="serial-test-owner-password",
        dbname="test_serial_server",
        driver="asyncpg",
    ) as container:
        yield (
            make_url(container.get_connection_url())
            .set(drivername="postgresql+asyncpg")
            .render_as_string(hide_password=False)
        )


@pytest.fixture
def database(database_server, request) -> Database:
    database_name = f"test_serial_lifecycle_{uuid4().hex}"

    async def create_database():
        connection = await asyncpg.connect(database_server.replace("+asyncpg", ""))
        try:
            await connection.execute(f'CREATE DATABASE "{database_name}"')
        finally:
            await connection.close()

    asyncio.run(create_database())
    database = Database(
        make_url(database_server).set(database=database_name).render_as_string(hide_password=False)
    )
    result = database.migrate("upgrade", getattr(request, "param", HEAD))
    assert result.returncode == 0, result.stderr
    return database


class RecordingRedis(fakeredis.aioredis.FakeRedis):
    def __init__(self, gw_engine):
        super().__init__(decode_responses=True)
        self.gw_engine = gw_engine
        self.notifications = []

    async def publish(self, channel, message):
        if channel == "channel:config:changed":
            payload = json.loads(message)
            # A new GW-role transaction must already see the announced committed version.
            async with self.gw_engine.connect() as connection:
                version = await connection.scalar(
                    text("SELECT update_flag FROM devices WHERE dev_number = :dev"),
                    {"dev": payload["dev_number"]},
                )
            self.notifications.append((payload, version))
        return await super().publish(channel, message)


@dataclass
class Runtime:
    database: Database
    client: httpx.AsyncClient
    gw_engine: object
    redis: RecordingRedis

    def headers(self, tenant="tenant_a"):
        fingerprint = client_fingerprint("127.0.0.1", "serial-integration")
        token = issue_access_token(
            f"user_{tenant}", tenant, "Company", 2, fingerprint, secret=SECRET, ttl_sec=900
        )
        return {"Authorization": f"Bearer {token}", "User-Agent": "serial-integration"}

    async def request(self, method, url, *, tenant="tenant_a", expected=200, **kwargs):
        response = await self.client.request(method, url, headers=self.headers(tenant), **kwargs)
        assert response.status_code == expected, response.text
        return response.json()

    async def add_device(self, address, *, name=None, tenant="tenant_a", with_point=True):
        name = name or f"SIM{address:03}"
        device = (
            await self.request(
                "POST", "/api/devices", tenant=tenant, json=device_body(address, name=name)
            )
        )["data"]
        point = None
        if with_point:
            point = (
                await self.request(
                    "POST", f"/api/devices/{name}/points", tenant=tenant, json=point_body(address)
                )
            )["data"]
        return device, point


@pytest.fixture
async def runtime(database) -> AsyncIterator[Runtime]:
    await database.rows("INSERT INTO wx_groups(usr_group) VALUES ('tenant_a'), ('tenant_b')")
    api_engine = create_async_engine(database.role_url("ruisheng_api"), hide_parameters=True)
    gw_engine = create_async_engine(database.role_url("ruisheng_gw"), hide_parameters=True)
    redis = RecordingRedis(gw_engine)
    app = create_app(
        Config(
            db_url=database.role_url("ruisheng_api"),
            gw_db_url=database.role_url("ruisheng_gw"),
            redis_url="redis://127.0.0.1:1",
            jwt_secret=SECRET,
            env="test",
        )
    )
    app.state.session_factory = async_sessionmaker(api_engine, expire_on_commit=False)
    app.state.gw_session_factory = async_sessionmaker(gw_engine, expire_on_commit=False)
    app.state.redis = redis
    try:
        async with api_engine.connect() as connection:
            row = (
                await connection.execute(
                    text(
                        "SELECT current_user, rolbypassrls FROM pg_roles WHERE rolname=current_user"
                    )
                )
            ).one()
            assert row == ("ruisheng_api", False)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://serial.test"
        ) as client:
            yield Runtime(database, client, gw_engine, redis)
    finally:
        await redis.aclose()
        await gw_engine.dispose()
        await api_engine.dispose()


def device_body(address, *, name):
    return {
        "dev_number": name,
        "dev_ser_number": f"SER-{name}",
        "dev_name": name,
        "transport_type": "serial",
        "serial_port": PORT,
        "modbus_addr": address,
        "baud_rate": 9600,
        "read_profile": "zero_origin_38",
        "update_interval_decisec": 10,
    }


def point_body(address):
    return {
        "point_name": "simulated_temperature",
        "point_number": 35,
        "fun_code": 3,
        "dev_addr": address,
        "value_type": "字",
        "point_ratio": 0.1,
        "point_offset": 1.0,
        "user_ratio": 2.0,
        "user_point_offset": 3.0,
    }


def response_frame(address, value=123, *, count=38, fc=3):
    registers = [0] * count
    registers[min(35, count - 1)] = value
    return append_crc_to_frame(
        bytes([address, fc, count * 2]) + b"".join(value.to_bytes(2, "big") for value in registers)
    )


async def settle():
    for _ in range(25):
        await asyncio.sleep(0)


async def wait_until(predicate):
    async def wait():
        while not predicate():  # noqa: ASYNC110 - observe an existing worker without changing it
            await asyncio.sleep(0.005)

    await asyncio.wait_for(wait(), timeout=5)


class StreamRig:
    def __init__(self, runtime, registry):
        self.runtime = runtime
        self.registry = registry
        self.sessions = SessionMap()
        self.clock = FakeClock()
        self.requests = asyncio.Queue()
        self.received = asyncio.Queue()
        self.sent = []
        self.reader = asyncio.StreamReader()
        self.writer = MagicMock(spec=asyncio.StreamWriter)
        self.writer.transport = MagicMock()
        self.writer.transport.serial = None
        self.writer.is_closing.return_value = False
        self.writer.drain = AsyncMock()
        self.writer.wait_closed = AsyncMock()
        self.writer.write.side_effect = self._write
        self.repository = Repository(runtime.gw_engine, redis=runtime.redis)
        self.batch = BatchWriter(sink=self.repository, clock=RealClock(), flush_interval_sec=0.01)
        self.ingestor = FrameIngestor(
            registry=registry,
            batch=self.batch,
            publisher=Publisher(redis=runtime.redis),
            alarm_repository=self.repository,
        )
        self.bus = SerialBus(
            port=PORT,
            baud_rate=9600,
            registry=registry,
            session_map=self.sessions,
            on_frame=self._on_frame,
            clock=self.clock,
        )

    def _write(self, frame):
        assert (
            sum(
                self.sessions.get(entry.device.dev_number).pending_read is not None
                for entry in self.registry.entries()
                if self.sessions.get(entry.device.dev_number) is not None
            )
            == 1
        )
        self.sent.append(frame)
        self.requests.put_nowait(frame)

    async def _on_frame(self, dev_number, frame):
        pending = self.sessions.get(dev_number).pending_read
        assert pending is not None
        await self.ingestor.process_frame_for_pending(
            dev_number=dev_number, frame=frame, pending_read=pending
        )
        self.received.put_nowait(dev_number)

    async def __aenter__(self):
        self.batch_task = asyncio.create_task(self.batch.run())
        self.task = asyncio.create_task(
            self.bus._run_with_streams(reader=self.reader, writer=self.writer)
        )
        await settle()
        return self

    async def __aexit__(self, *args):
        self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)
        self.batch.stop()
        await asyncio.wait_for(self.batch_task, timeout=10)
        assert len(self.sessions) == 0
        assert self.batch.stats["flush_error_total"] == 0
        assert self.batch.stats["drop_total"] == 0

    async def refresh(self):
        changed = await self.registry.reload_serial_configuration(self.runtime.gw_engine)
        self.ingestor.invalidate_devices(changed)
        return changed

    async def next_request(self):
        async def next_due():
            while self.requests.empty():
                assert not self.task.done()
                if self.bus.poller._active is None:
                    # Fewer than five devices can finish a round before the one-second interval.
                    self.clock.advance(0.051)
                await settle()
            return await self.requests.get()

        frame = await asyncio.wait_for(next_due(), timeout=5)
        assert frame[1:6] == bytes.fromhex("0300000026")
        assert not self.task.done()
        return frame

    async def respond(self, frame):
        assert len(frame) == 81
        self.reader.feed_data(frame[:40])
        await settle()
        assert self.received.empty()
        self.reader.feed_data(frame[40:])
        device = await asyncio.wait_for(self.received.get(), timeout=5)
        await wait_until(lambda: self.bus.poller._active is None)
        return device

    async def quiet(self):
        await wait_until(lambda: self.bus.poller._active is None)
        await settle()
        assert self.requests.empty()
        self.clock.advance(0.199)
        await settle()
        assert self.requests.empty()
        self.clock.advance(0.002)
        await settle()


async def wait_history_count(runtime, expected):
    async def wait():
        while True:
            rows = await runtime.database.rows("SELECT count(*) FROM point_data_history")
            count = rows[0][0]
            if count == expected:
                return
            assert count < expected
            await asyncio.sleep(0.01)

    await asyncio.wait_for(wait(), timeout=5)


def history_params(point_id=None):
    now = datetime.now(UTC)
    result = {
        "from": (now - timedelta(minutes=5)).isoformat(),
        "to": (now + timedelta(minutes=5)).isoformat(),
        "sample_interval_s": 1,
    }
    if point_id is not None:
        result["point_id"] = point_id
    return result


async def cycle(rig, expected, *, value_offset=0, advance_first=False):
    if advance_first:
        await rig.quiet()
    seen = []
    for index in range(len(expected)):
        frame = await rig.next_request()
        address = frame[0]
        device = await rig.respond(response_frame(address, address * 100 + value_offset))
        assert expected[address] == device
        seen.append(address)
        if index < len(expected) - 1:
            await rig.quiet()
    assert sorted(seen) == sorted(expected)


async def test_five_devices_two_rounds_hot_lifecycle_and_tenant_history(runtime):
    points = {}
    for address in range(1, 6):
        _, point = await runtime.add_device(address)
        points[address] = point["id"]
    registry = await Registry.load_from_db(runtime.gw_engine)
    expected = {address: f"SIM{address:03}" for address in range(1, 6)}
    async with StreamRig(runtime, registry) as rig:
        await cycle(rig, expected)
        await cycle(rig, expected, value_offset=1, advance_first=True)
        assert [frame.hex() for frame in rig.sent] == [
            "010300000026c410",
            "020300000026c423",
            "030300000026c5f2",
            "040300000026c445",
            "050300000026c594",
        ] * 2
        await wait_history_count(runtime, 10)
        for address, name in expected.items():
            realtime = (await runtime.request("GET", f"/api/devices/{name}/realtime"))["data"]
            (item,) = realtime["points"]
            assert (item["dev_number"], item["point_id"]) == (name, points[address])
            assert item["org_value"] == address * 100 + 1
            assert item["rt_value"] == pytest.approx((address * 100 + 1) * 0.2 + 5)
            rows = (
                await runtime.request(
                    "GET", f"/api/devices/{name}/history", params=history_params(points[address])
                )
            )["data"]["rows"]
            assert [row["org_value"] for row in rows] == [address * 100, address * 100 + 1]
            assert [row["rt_value"] for row in rows] == pytest.approx(
                [address * 20 + 5, (address * 100 + 1) * 0.2 + 5]
            )
            assert all(
                row["point_id"] == points[address] and row["dev_number"] == name for row in rows
            )
            await runtime.request(
                "GET", f"/api/devices/{name}/realtime", tenant="tenant_b", expected=400
            )
            await runtime.request(
                "GET",
                f"/api/devices/{name}/history",
                tenant="tenant_b",
                params=history_params(),
                expected=400,
            )

        await runtime.add_device(6)
        assert await rig.refresh() == {"SIM006"}
        expected[6] = "SIM006"
        await cycle(rig, expected, value_offset=2, advance_first=True)
        await wait_history_count(runtime, 16)
        await runtime.request("PUT", "/api/devices/SIM006/enabled", json={"is_enabled": False})
        assert await rig.refresh() == {"SIM006"}
        del expected[6]
        await cycle(rig, expected, value_offset=3, advance_first=True)
        await wait_history_count(runtime, 21)
        conflict = await runtime.request(
            "POST", "/api/devices", expected=400, json=device_body(6, name="DISABLED_CONFLICT")
        )
        assert conflict["msg"] == "serial_port and modbus_addr already in use"

        old_history = await runtime.database.rows(
            "SELECT * FROM point_data_history WHERE dev_number='SIM002' ORDER BY recorded_at"
        )
        await runtime.request("DELETE", "/api/devices/SIM002")
        assert await rig.refresh() == {"SIM002"}
        del expected[2]
        await cycle(rig, expected, value_offset=4, advance_first=True)
        await wait_history_count(runtime, 25)
        _, replacement = await runtime.add_device(2, name="SIMNEW002")
        assert replacement["id"] != points[2]
        assert await rig.refresh() == {"SIMNEW002"}
        expected[2] = "SIMNEW002"
        await cycle(rig, expected, value_offset=5, advance_first=True)
        await wait_history_count(runtime, 30)
        assert (
            await runtime.database.rows(
                "SELECT * FROM point_data_history WHERE dev_number='SIM002' ORDER BY recorded_at"
            )
            == old_history
        )
        rows = (
            await runtime.request("GET", "/api/devices/SIMNEW002/history", params=history_params())
        )["data"]["rows"]
        assert len(rows) == 1
        assert rows[0]["point_id"] == replacement["id"] and rows[0]["org_value"] == 205
        assert all(payload["version"] == actual for payload, actual in runtime.redis.notifications)


async def test_empty_start_invalid_frames_and_old_generation_never_write(runtime):
    registry = Registry()
    async with StreamRig(runtime, registry) as rig:
        assert rig.sent == []
        await runtime.add_device(1, with_point=False)
        await rig.refresh()
        rig.clock.advance(0.051)
        await settle()
        assert rig.sent == []
        await runtime.request("POST", "/api/devices/SIM001/points", json=point_body(1))
        await runtime.add_device(2)
        assert await rig.refresh() == {"SIM001", "SIM002"}
        rig.clock.advance(0.051)
        assert (await rig.next_request())[0] == 1
        bad_crc = bytearray(response_frame(1))
        bad_crc[-1] ^= 0xFF
        for invalid in (
            bytes(bad_crc),
            response_frame(1, fc=4),
            response_frame(1, count=1),
            response_frame(99),
        ):
            rig.reader.feed_data(invalid)
            await settle()
            assert rig.received.empty()
            assert rig.requests.empty()
        assert (await runtime.database.rows("SELECT count(*) FROM point_data_history"))[0][0] == 0
        rig.clock.advance(1.001)
        await wait_until(lambda: rig.bus.poller._active is None)
        assert rig.bus.poller.diagnostics["timeouts"] == 1
        await rig.quiet()
        assert (await rig.next_request())[0] == 2
        rig.reader.feed_data(encode_exception_response(ExceptionResponse(2, 3, 2)))
        await wait_until(lambda: rig.bus.poller._active is None)
        assert rig.bus.poller.diagnostics["exceptions"] == 1
        assert rig.received.empty()
        await rig.quiet()
        assert (await rig.next_request())[0] == 1
        await runtime.request("PUT", "/api/devices/SIM001", json={"dev_name": "new generation"})
        assert await rig.refresh() == {"SIM001"}
        rig.reader.feed_data(response_frame(1, 999))
        await settle()
        assert rig.received.empty()
        rig.clock.advance(1.001)
        await wait_until(lambda: rig.bus.poller._active is None)
        await rig.quiet()
        assert (await rig.next_request())[0] == 2
        assert await rig.respond(response_frame(2, 222)) == "SIM002"
        await wait_history_count(runtime, 1)
        rows = await runtime.database.rows("SELECT dev_number, org_value FROM point_data_history")
        assert [tuple(row) for row in rows] == [("SIM002", 222)]


async def test_production_notification_and_five_second_fallback_refresh(runtime):
    from ruisheng_gw.config import Config as GatewayConfig

    registry = Registry()
    lock = asyncio.Lock()
    log = MagicMock()
    tasks = []
    async with StreamRig(runtime, registry) as rig:
        refreshed = []
        fallback_changed = asyncio.Event()

        async def refresh():
            await rig.refresh()
            refreshed.append(True)
            if (entry := registry.get("SIM001")) is not None and entry.modbus_addr == 7:
                fallback_changed.set()

        subscriber = asyncio.create_task(
            _subscribe_configuration_changes(
                refresh=refresh, lock=lock, registry=registry, redis=runtime.redis, log=log
            )
        )
        tasks.append(subscriber)
        try:

            async def subscribed():
                while not (await runtime.redis.pubsub_numsub("channel:config:changed"))[0][1]:  # noqa: ASYNC110 - observe subscription readiness
                    await asyncio.sleep(0.005)

            await asyncio.wait_for(subscribed(), timeout=5)
            await runtime.add_device(1)
            await wait_until(
                lambda: registry.get("SIM001") is not None and registry.get("SIM001").points
            )
            assert refreshed
            assert (await rig.next_request())[0] == 1
            await rig.respond(response_frame(1, 101))
            await wait_history_count(runtime, 1)

            subscriber.cancel()
            await asyncio.gather(subscriber, return_exceptions=True)
            interval = GatewayConfig.model_fields["alarm_reload_interval_sec"].default
            assert interval == 5
            refreshed.clear()
            periodic = asyncio.create_task(
                _periodic_configuration_refresh(
                    refresh=refresh, lock=lock, interval_sec=interval, log=log
                )
            )
            tasks.append(periodic)
            await wait_until(lambda: bool(refreshed))
            await runtime.request("PUT", "/api/devices/SIM001", json={"modbus_addr": 7})
            assert registry.get("SIM001").modbus_addr == 1

            await asyncio.wait_for(fallback_changed.wait(), timeout=8)
            assert len(refreshed) >= 2
            assert (await rig.next_request())[0] == 7
            await rig.respond(response_frame(7, 707))
            await wait_history_count(runtime, 2)
            log.exception.assert_not_called()
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


async def test_versions_validation_concurrent_unique_and_hidden_tenant_conflict(
    runtime, monkeypatch
):
    _, point = await runtime.add_device(1)
    for name in ("first", "second"):
        await runtime.request("PUT", "/api/devices/SIM001", json={"dev_name": name})
    for ratio in (0.2, 0.3):
        await runtime.request(
            "PUT", f"/api/devices/SIM001/points/{point['id']}", json={"point_ratio": ratio}
        )
    assert [payload["version"] for payload, _ in runtime.redis.notifications] == list(range(1, 7))
    assert all(payload["version"] == actual for payload, actual in runtime.redis.notifications)
    before = len(runtime.redis.notifications)
    for invalid in (
        {**point_body(1), "fun_code": 4},
        {**point_body(1), "point_number": 37, "value_type": "双字"},
    ):
        await runtime.request("POST", "/api/devices/SIM001/points", json=invalid, expected=400)
    assert len(runtime.redis.notifications) == before
    await runtime.request(
        "PUT", f"/api/devices/SIM001/points/{point['id']}", json={"fun_code": 4}, expected=400
    )
    assert len(runtime.redis.notifications) == before

    original_lookup = devices_repo.get_serial_endpoint
    barrier = asyncio.Event()
    lookups = 0

    async def simultaneous_lookup(session, *, serial_port, modbus_addr):
        nonlocal lookups
        result = await original_lookup(session, serial_port=serial_port, modbus_addr=modbus_addr)
        if modbus_addr == 7:
            assert result is None
            lookups += 1
            if lookups == 2:
                barrier.set()
            await asyncio.wait_for(barrier.wait(), timeout=5)
        return result

    monkeypatch.setattr(devices_repo, "get_serial_endpoint", simultaneous_lookup)
    responses = await asyncio.gather(
        *[
            runtime.client.post(
                "/api/devices", headers=runtime.headers(), json=device_body(7, name=name)
            )
            for name in ("RACE_A", "RACE_B")
        ]
    )
    assert sorted(response.status_code for response in responses) == [200, 400]
    failed = next(response for response in responses if response.status_code == 400)
    assert failed.json()["msg"] == "serial_port and modbus_addr already in use"
    monkeypatch.setattr(devices_repo, "get_serial_endpoint", original_lookup)
    hidden = await runtime.request(
        "POST",
        "/api/devices",
        tenant="tenant_b",
        expected=400,
        json=device_body(1, name="HIDDEN_CONFLICT"),
    )
    assert hidden["msg"] == "serial_port and modbus_addr already in use"
    assert "SIM001" not in str(hidden) and "tenant_a" not in str(hidden)
    listing = (await runtime.request("GET", "/api/devices", tenant="tenant_b"))["data"]
    assert listing["total"] == 0
    assert (
        await runtime.database.rows(
            "SELECT count(*) FROM devices WHERE serial_port=$1 AND modbus_addr=7", PORT
        )
    )[0][0] == 1


async def test_capacity_counts_disabled_devices_across_real_tenants(runtime):
    await runtime.add_device(1)
    registry = await Registry.load_from_db(runtime.gw_engine)
    original = registry.get("SIM001")
    await runtime.database.rows(
        """
        INSERT INTO devices(dev_number,dev_ser_number,modbus_addr,transport_type,serial_port,
                            update_interval_decisec,loss_count,is_online,update_flag,usr_group,is_enabled)
        SELECT 'CAP'||i, 'CAP-SER'||i, i, 'serial', $1, 10, 0, false, 0,
               CASE WHEN i % 2 = 0 THEN 'tenant_a' ELSE 'tenant_b' END, false
        FROM generate_series(2,128) AS i
    """,
        PORT,
    )
    assert await registry.reload_serial_configuration(runtime.gw_engine) == set()
    assert registry.get("SIM001") is original
    await runtime.add_device(129, tenant="tenant_b", with_point=False)
    assert await registry.reload_serial_configuration(runtime.gw_engine) == set()
    assert registry.get("SIM001") is original
    assert registry.get("SIM129") is None


@pytest.mark.parametrize("database", [PREVIOUS], indirect=True)
async def test_migration_default_roundtrip_and_reused_address_refuses_downgrade(database):
    await database.rows("INSERT INTO wx_groups(usr_group) VALUES ('migration_tenant')")
    await database.rows(
        """
        INSERT INTO devices(dev_number,dev_ser_number,modbus_addr,transport_type,serial_port,
                            update_interval_decisec,loss_count,is_online,update_flag,usr_group)
        VALUES('OLD_DEVICE','OLD_SERIAL',1,'serial',$1,10,0,false,0,'migration_tenant')
    """,
        PORT,
    )
    result = await asyncio.to_thread(database.migrate, "upgrade", HEAD)
    assert result.returncode == 0, result.stderr
    assert (await database.rows("SELECT read_profile FROM devices"))[0][0] == "point_groups"
    result = await asyncio.to_thread(database.migrate, "downgrade", PREVIOUS)
    assert result.returncode == 0, result.stderr
    result = await asyncio.to_thread(database.migrate, "upgrade", HEAD)
    assert result.returncode == 0, result.stderr
    await database.rows("UPDATE devices SET deleted_at=now() WHERE dev_number='OLD_DEVICE'")
    await database.rows(
        """
        INSERT INTO devices(dev_number,dev_ser_number,modbus_addr,transport_type,serial_port,
                            update_interval_decisec,loss_count,is_online,update_flag,usr_group)
        VALUES('NEW_DEVICE','NEW_SERIAL',1,'serial',$1,10,0,false,0,'migration_tenant')
    """,
        PORT,
    )
    point = (
        await database.rows("""
        INSERT INTO device_points(dev_number,point_name,point_number,fun_code,dev_addr,value_type,
                                  show,point_ratio,point_offset,user_ratio,user_point_offset)
        VALUES('OLD_DEVICE','historical_temperature',35,3,1,'字',1,1.0,0.0,1.0,0.0) RETURNING id
    """)
    )[0][0]
    await database.rows(
        """
        INSERT INTO point_data_history(dev_number,point_id,org_value,rt_value,recorded_at)
        VALUES('OLD_DEVICE',$1,123,12.3,now())
    """,
        point,
    )
    before = await database.rows("SELECT * FROM point_data_history")
    result = await asyncio.to_thread(database.migrate, "downgrade", PREVIOUS)
    assert result.returncode != 0
    assert "Cannot downgrade: reused serial addresses conflict with the old index" in result.stderr
    assert (await database.rows("SELECT version_num FROM alembic_version"))[0][0] == HEAD
    assert len(await database.rows("SELECT read_profile FROM devices")) == 2
    assert await database.rows("SELECT * FROM point_data_history") == before
    index = (
        await database.rows("""
        SELECT indexdef FROM pg_indexes WHERE indexname='uq_devices_serial_port_modbus_addr'
    """)
    )[0][0]
    assert "deleted_at IS NULL" in index
