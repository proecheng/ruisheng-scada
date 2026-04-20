import pytest
from ruisheng_api.db.base import build_engine, build_session_factory
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker


def test_build_engine_returns_async_engine():
    eng = build_engine("postgresql+asyncpg://u:p@h/d", pool_size=5, max_overflow=2)
    assert isinstance(eng, AsyncEngine)


def test_build_session_factory():
    eng = build_engine("postgresql+asyncpg://u:p@h/d", pool_size=5, max_overflow=2)
    factory = build_session_factory(eng)
    assert isinstance(factory, async_sessionmaker)


def test_build_engine_rejects_sync_url():
    with pytest.raises(ValueError, match="async URL required"):
        build_engine("postgresql://u:p@h/d", pool_size=5, max_overflow=2)
