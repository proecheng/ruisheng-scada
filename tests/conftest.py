"""根 conftest：Windows 标识 + D9 dev/api/gw engine + E1 testcontainers/embedded PG 双轨 fixture。"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import AsyncIterator, Iterator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


@pytest.fixture
def is_windows() -> bool:
    return sys.platform == "win32"


# 按 docker-compose.dev.yml POSTGRES_USER/POSTGRES_PASSWORD（都是 "ruisheng_dev"）；CI 可从 env 覆盖
_DEV_DSN = os.environ.get(
    "DEV_DATABASE_URL",
    "postgresql+asyncpg://ruisheng_dev:ruisheng_dev@127.0.0.1:5432/ruisheng",
)


# 注：engine fixtures 使用 function scope 而非 session。
# pytest-asyncio 0.23 auto 模式默认 event_loop 为 function scope；session 级 async
# fixture 会把 asyncpg 连接池绑到首个 test 的 loop，第 2 个 test 跑时 loop 已关闭
# → "Event loop is closed" / "another operation is in progress"。function scope
# 每个 test 重建 engine（~ms 级），简单且对 Windows proactor loop 稳定。
@pytest_asyncio.fixture
async def dev_engine():
    """以 ruisheng_dev (owner) 身份连接。
    owner 无 BYPASSRLS + D6 FORCE RLS 后也受 tenant_isolation 约束
    (test_owner_does_not_bypass_rls 依赖此行为)。
    """
    engine = create_async_engine(_DEV_DSN)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def api_engine():
    """以 ruisheng_api 身份连接（非 BYPASSRLS，受 RLS 约束）。"""
    pw = os.environ["RUISHENG_API_PASSWORD"]
    engine = create_async_engine(f"postgresql+asyncpg://ruisheng_api:{pw}@127.0.0.1:5432/ruisheng")
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def gw_engine():
    """以 ruisheng_gw 身份连接（BYPASSRLS，跨租户读写）。"""
    pw = os.environ["RUISHENG_GW_PASSWORD"]
    engine = create_async_engine(f"postgresql+asyncpg://ruisheng_gw:{pw}@127.0.0.1:5432/ruisheng")
    yield engine
    await engine.dispose()


# ---------------------------------------------------------------------
# E1 testcontainers / embedded PG 双轨 session 级 fixture（Plan bug #10 fix：与 D9 fixture 并存）
# ---------------------------------------------------------------------


def _use_embedded() -> bool:
    """Windows 无 Docker 环境走 tools/embedded_pg.py stub；默认走 testcontainers（需 Docker）。"""
    return os.environ.get("USE_EMBEDDED_PG") == "1"


@pytest.fixture(scope="session")
def postgres_url() -> Iterator[str]:
    """E1 session 级 PostgreSQL URL（testcontainers 新起容器 + alembic upgrade）。

    * **同步** fixture：testcontainers `with PostgresContainer(...)` 本来就是同步 context
      manager，用 `@pytest_asyncio.fixture(scope="session")` 会在 pytest-asyncio 0.23
      auto 模式触发 "Event loop is closed"（D9 conftest.py L26-30 已证）。
    * alembic upgrade 放在这里：每 session 跑一次；`async_engine` fixture function scope
      只做引擎创建/释放，不再重复 upgrade。
    * 与 D9 `dev_engine`（指向 live Docker `127.0.0.1:5432`）**并存但独立**：
      - D9 fixtures 服务 `tests/integration/*` 现有 15 case（依赖 `docker compose up` 的 dev 容器）
      - E1 fixtures 服务未来 Stage E+ 在 CI Linux / 无 dev stack 场景
    """
    if _use_embedded():
        from tools.embedded_pg import EmbeddedPostgres  # lazy import

        pg = EmbeddedPostgres()
        pg.start_sync()  # E2 stub raises NotImplementedError；真实现后同步启动
        try:
            yield pg.url
        finally:
            pg.stop_sync()
        return

    try:
        from testcontainers.postgres import PostgresContainer
    except ImportError:
        pytest.skip("testcontainers not available; set USE_EMBEDDED_PG=1")
    with PostgresContainer("timescale/timescaledb:2.16.1-pg15") as container:
        # testcontainers 默认返回 psycopg2 DSN；改 asyncpg driver
        url = container.get_connection_url().replace("psycopg2", "asyncpg")
        # 新库空表，必须 alembic upgrade head 建全 26 表 + 2 角色 + ... + hypertable
        subprocess.check_call(
            ["uv", "run", "alembic", "upgrade", "head"],
            env={**os.environ, "DATABASE_URL": url},
        )
        yield url


@pytest.fixture(scope="session")
def redis_url() -> Iterator[str]:
    """E1 session 级 Redis URL（testcontainers）。同 `postgres_url` 理由用同步 fixture。"""
    try:
        from testcontainers.redis import RedisContainer
    except ImportError:
        pytest.skip("testcontainers not available")
    with RedisContainer("redis:7-alpine") as r:
        yield f"redis://{r.get_container_host_ip()}:{r.get_exposed_port(6379)}/0"


@pytest_asyncio.fixture  # function scope — 见 D9 conftest.py L26-30 关于 session-scope async 的注释
async def async_engine(postgres_url: str) -> AsyncIterator[AsyncEngine]:
    """从 testcontainer-spawn 的 DB 建 async engine。function scope 避开 event loop pitfall。"""
    engine = create_async_engine(postgres_url, pool_pre_ping=True)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture  # function scope
async def session(async_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """E1 通用 async session（rollback at teardown）。"""
    maker = async_sessionmaker(async_engine, expire_on_commit=False)
    async with maker() as s:
        yield s
        await s.rollback()
