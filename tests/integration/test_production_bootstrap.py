"""Isolated acceptance coverage for production migrations and explicit demo seeds."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from collections.abc import Iterator
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from sqlalchemy.engine import make_url
from testcontainers.postgres import PostgresContainer

from conftest import _skip_if_docker_unavailable

ROOT = Path(__file__).parents[2]
DATABASE_IMAGE = "timescale/timescaledb:2.16.1-pg15"
DATABASE_NAME = "test_production_bootstrap"
DATABASE_USER = "bootstrap_admin"
TEST_SECRETS = {
    "POSTGRES_PASSWORD": "bootstrap-admin-password-2026",
    "RUISHENG_GW_PASSWORD": "bootstrap-gateway-password-2026",
    "RUISHENG_API_PASSWORD": "bootstrap-api-password-2026",
    "REDIS_PASSWORD": "bootstrap-redis-password-2026",
    "JWT_SECRET": "bootstrap-jwt-secret-2026-at-least-32-characters",
}


def _remove_container(name: str) -> None:
    with suppress(OSError, subprocess.SubprocessError):
        subprocess.run(
            ["docker", "rm", "-f", name],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )


def _run_container(
    command: list[str], *, name: str, timeout: int
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(f"Docker container {name} timed out after {timeout} seconds")
    finally:
        _remove_container(name)


@pytest.fixture(autouse=True)
def require_test_database_target() -> None:
    """Override the suite guard: this module owns an isolated testcontainer."""


@pytest.fixture(autouse=True)
def require_dev_database() -> None:
    """Override the external dev-database fixture for this isolated module."""


@dataclass(frozen=True)
class BootstrapRuntime:
    host_database_url: str
    container_database_url: str
    image: str


@dataclass(frozen=True)
class DatabaseSnapshot:
    migration_version: str | None
    groups: tuple[tuple[object, ...], ...]
    users: tuple[tuple[object, ...], ...]
    devices: tuple[tuple[object, ...], ...]
    points: tuple[tuple[object, ...], ...]

    @property
    def business_counts(self) -> tuple[int, int, int, int]:
        return (len(self.groups), len(self.users), len(self.devices), len(self.points))


@pytest.fixture(scope="module")
def production_image() -> Iterator[str]:
    _skip_if_docker_unavailable()
    image_tag = f"ruisheng-b02-bootstrap-test:{uuid4().hex}"

    try:
        build = subprocess.run(
            ["docker", "build", "-f", "ruisheng-api/Dockerfile", "-t", image_tag, "."],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=300,
        )
        if build.returncode != 0:
            details = build.stderr.strip() or build.stdout.strip() or "no output"
            pytest.fail(f"production API image build failed: {details}")
        yield image_tag
    finally:
        subprocess.run(
            ["docker", "image", "rm", "--force", image_tag],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
        )


@pytest.fixture(scope="module")
def bootstrap_runtime(production_image: str) -> Iterator[BootstrapRuntime]:
    with PostgresContainer(
        DATABASE_IMAGE,
        username=DATABASE_USER,
        password=TEST_SECRETS["POSTGRES_PASSWORD"],
        dbname=DATABASE_NAME,
        driver="asyncpg",
    ) as database:
        host_url = make_url(database.get_connection_url()).set(drivername="postgresql+asyncpg")
        container_url = host_url.set(host="host.docker.internal")
        yield BootstrapRuntime(
            host_database_url=host_url.render_as_string(hide_password=False),
            container_database_url=container_url.render_as_string(hide_password=False),
            image=production_image,
        )


@pytest.mark.integration
def test_production_image_excludes_demo_seed_assets(production_image: str) -> None:
    container_name = f"ruisheng-b02-image-check-{uuid4().hex}"
    result = _run_container(
        [
            "docker",
            "run",
            "--rm",
            "--name",
            container_name,
            "--entrypoint",
            "sh",
            production_image,
            "-c",
            (
                "test -e /app/scripts/entrypoint-migrate.sh "
                "&& test -d /app/alembic "
                "&& test ! -e /app/seeds "
                "&& test ! -e /app/tools/run_seeds.py "
                "&& python -c 'import ruisheng_api'"
            ),
        ],
        name=container_name,
        timeout=60,
    )
    if result.returncode != 0:
        details = result.stderr.strip() or result.stdout.strip() or "no output"
        pytest.fail(f"production API image asset check failed: {details}")


@pytest.mark.integration
@pytest.mark.parametrize(
    ("invalid_name", "invalid_value"),
    (
        ("POSTGRES_PASSWORD", None),
        ("POSTGRES_PASSWORD", "CHANGE_ME_POSTGRES"),
        ("POSTGRES_PASSWORD", "invalid:password"),
        ("POSTGRES_PASSWORD", "too-short"),
        ("JWT_SECRET", "short-jwt"),
    ),
)
def test_production_entrypoint_rejects_invalid_secrets_before_database_access(
    production_image: str, invalid_name: str, invalid_value: str | None
) -> None:
    environment = {
        **TEST_SECRETS,
        "DATABASE_URL": "postgresql+asyncpg://invalid:invalid@127.0.0.1:1/unreachable",
    }
    if invalid_value is None:
        environment.pop(invalid_name)
    else:
        environment[invalid_name] = invalid_value

    container_name = f"ruisheng-b02-invalid-secret-{uuid4().hex}"
    command = ["docker", "run", "--rm", "--name", container_name]
    for name, value in environment.items():
        command.extend(("--env", f"{name}={value}"))
    command.extend(
        (
            "--entrypoint",
            "bash",
            production_image,
            "scripts/entrypoint-migrate.sh",
        )
    )
    result = _run_container(command, name=container_name, timeout=60)
    output = result.stdout + result.stderr
    assert result.returncode == 2
    if invalid_value:
        assert invalid_value not in output
    assert "Running alembic" not in result.stdout


def _run_production_entrypoint(runtime: BootstrapRuntime) -> None:
    environment = {**TEST_SECRETS, "DATABASE_URL": runtime.container_database_url}
    container_name = f"ruisheng-b02-migrate-{uuid4().hex}"
    command = [
        "docker",
        "run",
        "--rm",
        "--name",
        container_name,
        "--add-host",
        "host.docker.internal:host-gateway",
    ]
    for name, value in environment.items():
        command.extend(("--env", f"{name}={value}"))
    command.extend(
        (
            "--entrypoint",
            "bash",
            runtime.image,
            "scripts/entrypoint-migrate.sh",
        )
    )
    result = _run_container(
        command,
        name=container_name,
        timeout=180,
    )
    if result.returncode != 0:
        details = result.stderr.strip() or result.stdout.strip() or "no output"
        pytest.fail(f"production migration entrypoint failed: {details}")


def _run_development_seed(database_url: str) -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "run_seeds.py")],
        cwd=ROOT,
        env={**os.environ, "DATABASE_URL": database_url},
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    if result.returncode != 0:
        details = result.stderr.strip() or result.stdout.strip() or "no output"
        pytest.fail(f"explicit development seed failed: {details}")


async def _read_snapshot(database_url: str) -> DatabaseSnapshot:
    connection = await asyncpg.connect(database_url.replace("+asyncpg", ""))
    try:
        migration_version = await connection.fetchval("SELECT version_num FROM alembic_version")
        groups = await connection.fetch(
            """
            SELECT usr_group, appid, sys_title, company_name
            FROM wx_groups ORDER BY usr_group
            """
        )
        users = await connection.fetch(
            """
            SELECT user_name, authority, control_authority, usr_group
            FROM users ORDER BY user_name
            """
        )
        devices = await connection.fetch(
            """
            SELECT dev_number, dev_ser_number, modbus_addr, baud_rate,
                   usr_group, administrators
            FROM devices ORDER BY dev_number
            """
        )
        points = await connection.fetch(
            """
            SELECT dev_number, point_name, point_number, fun_code, dev_addr, value_type
            FROM device_points ORDER BY dev_number, point_number
            """
        )
    finally:
        await connection.close()

    return DatabaseSnapshot(
        migration_version=migration_version,
        groups=tuple(tuple(row) for row in groups),
        users=tuple(tuple(row) for row in users),
        devices=tuple(tuple(row) for row in devices),
        points=tuple(tuple(row) for row in points),
    )


def _snapshot(database_url: str) -> DatabaseSnapshot:
    return asyncio.run(_read_snapshot(database_url))


def _migration_head() -> str:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config(str(ROOT / "alembic.ini"))
    head = ScriptDirectory.from_config(config).get_current_head()
    assert head is not None
    return head


@pytest.mark.integration
def test_production_bootstrap_is_empty_idempotent_and_preserves_explicit_seed(
    bootstrap_runtime: BootstrapRuntime,
) -> None:
    _run_production_entrypoint(bootstrap_runtime)
    first_migration = _snapshot(bootstrap_runtime.host_database_url)
    assert first_migration.migration_version == _migration_head()
    assert first_migration.business_counts == (0, 0, 0, 0)

    _run_production_entrypoint(bootstrap_runtime)
    assert _snapshot(bootstrap_runtime.host_database_url) == first_migration

    _run_development_seed(bootstrap_runtime.host_database_url)
    _run_development_seed(bootstrap_runtime.host_database_url)
    seeded = _snapshot(bootstrap_runtime.host_database_url)
    assert seeded.business_counts == (1, 2, 1, 2)
    assert seeded.groups == (("demo", "wxDEMOappid", "润盛监控 Demo", "润盛集团 Demo"),)
    assert seeded.users == (
        ("13800138000", "Administrators", 3, "demo"),
        ("13800138001", "Company", 1, "demo"),
    )
    assert seeded.devices == (("60270012", "DEMO-SN-0001", 1, 9600, "demo", "13800138000"),)
    assert seeded.points == (
        ("60270012", "temperature", 0, 3, 1, "字"),
        ("60270012", "pressure", 1, 3, 1, "字"),
    )

    _run_production_entrypoint(bootstrap_runtime)
    assert _snapshot(bootstrap_runtime.host_database_url) == seeded
