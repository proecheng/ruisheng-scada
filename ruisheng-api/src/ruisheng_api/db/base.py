"""SQLAlchemy 2.0 async engine + Session factory。"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def build_engine(url: str, *, pool_size: int, max_overflow: int) -> AsyncEngine:
    if "+asyncpg" not in url and "+aiosqlite" not in url:
        raise ValueError("async URL required (e.g. postgresql+asyncpg://...)")
    return create_async_engine(
        url,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_pre_ping=True,
        future=True,
    )


def build_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False, autoflush=False)
