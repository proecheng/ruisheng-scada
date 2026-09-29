"""First admin acceptance against owned PostgreSQL and the actual production API image."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass, replace
from pathlib import Path
from uuid import uuid4

import asyncpg
import fakeredis.aioredis
import httpx
import pytest
from ruisheng_api.admin_bootstrap import BootstrapRejected, BootstrapRequest, bootstrap
from ruisheng_api.config import Config
from ruisheng_api.main import create_app
from ruisheng_api.pubsub.publisher import STREAM_CONTROL_CMD
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from conftest import _skip_if_docker_unavailable

ROOT = Path(__file__).parents[2]
SECRETS = {
    "POSTGRES_PASSWORD": "admin-bootstrap-test-owner-password",
    "RUISHENG_GW_PASSWORD": "admin-bootstrap-test-gw-password",
    "RUISHENG_API_PASSWORD": "admin-bootstrap-test-api-password",
    "REDIS_PASSWORD": "admin-bootstrap-test-redis-password",
    "JWT_SECRET": "admin-bootstrap-test-jwt-secret-at-least-32",
}
pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def require_test_database_target() -> None:
    """This module only connects to its own Testcontainers instance."""


@pytest.fixture(autouse=True)
def require_dev_database() -> None:
    """Never depend on, or mutate, the developer's database."""


@dataclass(frozen=True)
class Runtime:
    owner_url: str
    image: str = ""

    def url(self, role: str = "ruisheng_api", *, container: bool = False) -> str:
        key = "RUISHENG_API_PASSWORD" if role == "ruisheng_api" else "RUISHENG_GW_PASSWORD"
        url = make_url(self.owner_url).set(username=role, password=SECRETS[key])
        if container:
            url = url.set(host="host.docker.internal")
        return url.render_as_string(hide_password=False)


@pytest.fixture(scope="module")
def image() -> Iterator[str]:
    _skip_if_docker_unavailable()
    name = f"ruisheng-admin-bootstrap-test:{uuid4().hex}"
    try:
        result = subprocess.run(
            ["docker", "build", "-f", "ruisheng-api/Dockerfile", "-t", name, "."],
            cwd=ROOT,
            capture_output=True,
            timeout=300,
            check=False,
        )
        assert result.returncode == 0, "isolated API image build failed"
        yield name
    finally:
        subprocess.run(
            ["docker", "image", "rm", name], capture_output=True, timeout=60, check=False
        )


@pytest.fixture(scope="module")
def database_server() -> Iterator[str]:
    _skip_if_docker_unavailable()
    with PostgresContainer(
        "timescale/timescaledb:2.16.1-pg15",
        username="bootstrap_test_owner",
        password=SECRETS["POSTGRES_PASSWORD"],
        dbname="test_admin_bootstrap",
        driver="asyncpg",
    ) as database:
        url = make_url(database.get_connection_url()).set(drivername="postgresql+asyncpg")
        yield url.render_as_string(hide_password=False)


@pytest.fixture
def runtime(database_server: str) -> Runtime:
    server_url = database_server
    database_name = f"test_admin_bootstrap_{uuid4().hex}"

    async def create_database() -> None:
        connection = await asyncpg.connect(server_url.replace("+asyncpg", ""))
        try:
            await connection.execute(f'CREATE DATABASE "{database_name}"')
        finally:
            await connection.close()

    asyncio.run(create_database())
    owner_url = (
        make_url(server_url).set(database=database_name).render_as_string(hide_password=False)
    )
    environment = {key: value for key, value in os.environ.items() if not key.startswith("API_")}
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ROOT,
        env={**environment, **SECRETS, "DATABASE_URL": owner_url},
        capture_output=True,
        timeout=180,
        check=False,
    )
    assert result.returncode == 0, "isolated production migrations failed"
    return Runtime(owner_url)


def request() -> BootstrapRequest:
    return BootstrapRequest("create", str(uuid4()), "site-isolated", "rs_admin", "T1" * 16)


async def owner_execute(runtime: Runtime, sql: str, *args: object) -> object:
    connection = await asyncpg.connect(runtime.owner_url.replace("+asyncpg", ""))
    try:
        return await connection.fetch(sql, *args)
    finally:
        await connection.close()


async def counts(runtime: Runtime) -> tuple[int, ...]:
    rows = await owner_execute(
        runtime,
        """
        SELECT (SELECT count(*) FROM wx_groups), (SELECT count(*) FROM users),
          (SELECT count(*) FROM soft_logs), (SELECT count(*) FROM devices),
          (SELECT count(*) FROM device_points), (SELECT count(*) FROM user_control_actions)
    """,
    )
    return tuple(rows[0])


@pytest.mark.asyncio
async def test_concurrent_create_reconcile_and_real_login(runtime: Runtime) -> None:
    engine = create_async_engine(runtime.url(), hide_parameters=True)
    gw_engine = create_async_engine(runtime.url("ruisheng_gw"), hide_parameters=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    first = request()
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    try:
        assert (await bootstrap(factory, replace(first, action="status")))["status"] == "empty"
        results = await asyncio.gather(
            bootstrap(factory, first), bootstrap(factory, first), return_exceptions=True
        )
        assert sum(isinstance(result, BootstrapRejected) for result in results) == 1
        assert (
            sum(isinstance(result, dict) and result["status"] == "created" for result in results)
            == 1
        )
        assert await counts(runtime) == (1, 1, 1, 0, 0, 0)
        assert (await bootstrap(factory, replace(first, action="status")))["status"] == "confirmed"
        for conflict in (
            replace(first, operation_id=str(uuid4())),
            replace(first, site_id="site-other"),
            replace(first, user_name="rs_other"),
            replace(first, password="Z2" * 16),
        ):
            assert (await bootstrap(factory, replace(conflict, action="status")))[
                "status"
            ] == "conflict"
        with pytest.raises(BootstrapRejected):
            await bootstrap(factory, first)

        # Real role sessions and routes, with Redis isolated and background jobs not started.
        config = Config(
            db_url=runtime.url(),
            gw_db_url=runtime.url("ruisheng_gw"),
            redis_url="redis://127.0.0.1:1",
            jwt_secret=SECRETS["JWT_SECRET"],
            env="test",
        )
        app = create_app(config)
        app.state.session_factory = factory
        app.state.gw_session_factory = async_sessionmaker(gw_engine, expire_on_commit=False)
        app.state.redis = redis
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://bootstrap.test"
        ) as client:
            login = await client.post(
                "/api/auth/login", json={"user_name": first.user_name, "password": first.password}
            )
            assert login.status_code == 200
            data = login.json()["data"]
            assert data["role"] == "Administrators" and data["control_authority"] == 0
            refresh = await client.post(
                "/api/auth/refresh", json={"refresh_token": data["refresh_token"]}
            )
            assert refresh.status_code == 200
            data = refresh.json()["data"]
            headers = {"Authorization": f"Bearer {data['access_token']}"}
            listing = await client.get("/api/orgs/users", headers=headers)
            assert listing.status_code == 200 and listing.json()["data"]["total"] == 1
            assert "password_hash" not in listing.text
            denied = await client.post(
                "/api/devices/not-installed/control",
                headers=headers,
                json={"fun_code": 6, "reg": 0, "value": 1},
            )
            assert denied.status_code == 403
            assert await redis.xlen(STREAM_CONTROL_CMD) == 0
            logout = await client.post(
                "/api/auth/logout", headers=headers, json={"refresh_token": data["refresh_token"]}
            )
            assert logout.status_code == 200
        assert await counts(runtime) == (1, 1, 1, 0, 0, 0)
    finally:
        await redis.aclose()
        await gw_engine.dispose()
        await engine.dispose()


@pytest.mark.asyncio
async def test_audit_failure_rolls_back_and_rls_cannot_hide_existing_data(runtime: Runtime) -> None:
    engine = create_async_engine(runtime.url(), hide_parameters=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    first = request()
    try:
        await owner_execute(runtime, "REVOKE INSERT ON soft_logs FROM ruisheng_api")
        with pytest.raises(DBAPIError) as denied:
            await bootstrap(factory, first)
        assert denied.value.orig.sqlstate == "42501"
        assert await counts(runtime) == (0, 0, 0, 0, 0, 0)
        await owner_execute(runtime, "GRANT INSERT ON soft_logs TO ruisheng_api")
        await owner_execute(runtime, "INSERT INTO wx_groups (usr_group) VALUES ('site-hidden')")
        await owner_execute(
            runtime,
            """
            INSERT INTO users(user_name,password_hash,authority,control_authority,usr_group,deleted_at)
            VALUES('hidden_user','test-only','User',0,'site-hidden',now())
        """,
        )
        async with factory() as session:
            assert await session.scalar(text("SELECT count(*) FROM users")) == 0
        before = await counts(runtime)
        with pytest.raises(BootstrapRejected):
            await bootstrap(factory, first)
        assert (await bootstrap(factory, replace(first, action="status")))["status"] == "conflict"
        assert await counts(runtime) == before
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_lock_timeout_and_wrong_role_preserve_empty_database(runtime: Runtime) -> None:
    engine = create_async_engine(runtime.url(), hide_parameters=True)
    gw_engine = create_async_engine(runtime.url("ruisheng_gw"), hide_parameters=True)
    connection = await asyncpg.connect(runtime.owner_url.replace("+asyncpg", ""))
    try:
        await connection.execute("BEGIN; LOCK TABLE wx_groups IN SHARE ROW EXCLUSIVE MODE")
        with pytest.raises(DBAPIError) as timed_out:
            await asyncio.wait_for(bootstrap(async_sessionmaker(engine), request()), timeout=8)
        assert timed_out.value.orig.sqlstate == "55P03"
        await connection.execute("ROLLBACK")
        with pytest.raises(BootstrapRejected):
            await bootstrap(async_sessionmaker(gw_engine), request())
        assert await counts(runtime) == (0, 0, 0, 0, 0, 0)
    finally:
        await connection.close()
        await gw_engine.dispose()
        await engine.dispose()


@pytest.mark.asyncio
async def test_status_never_recreates_or_accepts_missing_audit(runtime: Runtime) -> None:
    engine = create_async_engine(runtime.url(), hide_parameters=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    first = request()
    try:
        await bootstrap(factory, first)
        await owner_execute(runtime, "UPDATE users SET control_authority=1")
        assert (await bootstrap(factory, replace(first, action="status")))["status"] == "conflict"
        await owner_execute(runtime, "UPDATE users SET control_authority=0")
        await owner_execute(runtime, "DELETE FROM soft_logs")
        assert (await bootstrap(factory, replace(first, action="status")))["status"] == "conflict"
        with pytest.raises(BootstrapRejected):
            await bootstrap(factory, first)
        assert await counts(runtime) == (1, 1, 0, 0, 0, 0)
    finally:
        await engine.dispose()


def test_actual_image_cli_and_production_entrypoint(runtime: Runtime, image: str) -> None:
    runtime = replace(runtime, image=image)
    first = request()
    environment = {
        **SECRETS,
        "API_DB_URL": runtime.url(container=True),
        "API_GW_DB_URL": runtime.url("ruisheng_gw", container=True),
        "API_REDIS_URL": "redis://127.0.0.1:1",
        "API_JWT_SECRET": SECRETS["JWT_SECRET"],
        "API_ENV": "test",
    }
    base = ["docker", "run", "--rm", "-i", "--add-host", "host.docker.internal:host-gateway"]
    for key, value in environment.items():
        base.extend(["-e", f"{key}={value}"])
    value = {
        "schema_version": 1,
        "action": "create",
        "operation_id": first.operation_id,
        "site_id": first.site_id,
        "user_name": first.user_name,
        "password": first.password,
    }
    for action, expected in [
        ("create", "created"),
        ("status", "confirmed"),
        ("create", "rejected"),
    ]:
        value["action"] = action
        name = f"ruisheng-admin-bootstrap-cli-{uuid4().hex}"
        try:
            result = subprocess.run(
                base
                + [
                    "--name",
                    name,
                    "--entrypoint",
                    "/app/.venv/bin/python",
                    runtime.image,
                    "-I",
                    "-m",
                    "ruisheng_api.admin_bootstrap",
                ],
                input=json.dumps(value),
                text=True,
                capture_output=True,
                timeout=60,
                check=False,
            )
            assert first.password not in result.stdout + result.stderr
            assert not result.stderr
            assert result.returncode == (2 if expected == "rejected" else 0)
            assert json.loads(result.stdout) == first.receipt(expected)
        finally:
            subprocess.run(
                ["docker", "rm", "-f", name], capture_output=True, timeout=30, check=False
            )
    assert asyncio.run(counts(runtime)) == (1, 1, 1, 0, 0, 0)
    # The normal entrypoint still contains no administrator creation or demo seed.
    name = f"ruisheng-admin-bootstrap-assets-{uuid4().hex}"
    try:
        result = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--name",
                name,
                "--entrypoint",
                "sh",
                runtime.image,
                "-c",
                "test -x /usr/bin/getent && /usr/bin/getent ahostsv4 localhost >/dev/null && "
                "test ! -e /app/seeds && test ! -e /app/tools/run_seeds.py && "
                "! grep -q admin_bootstrap /app/scripts/entrypoint-migrate.sh",
            ],
            capture_output=True,
            timeout=60,
            check=False,
        )
        assert result.returncode == 0
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=30, check=False)
