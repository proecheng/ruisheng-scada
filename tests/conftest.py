"""根 conftest：目前仅提供标识 Windows 的 fixture。
数据库/Redis fixtures 在 Stage E 添加。"""

from __future__ import annotations

import os
import sys

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine


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
