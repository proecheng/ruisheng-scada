"""FastAPI app factory + lifespan。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import redis.asyncio as redis_async
from fastapi import FastAPI

from .api import mount_routers
from .config import Config
from .core.errors import register_exception_handlers
from .db.base import build_engine, build_session_factory
from .logging_setup import configure_logging


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    cfg: Config = app.state.config
    configure_logging(level="INFO")
    engine = build_engine(cfg.db_url, pool_size=cfg.db_pool_size, max_overflow=cfg.db_pool_overflow)
    app.state.engine = engine
    app.state.session_factory = build_session_factory(engine)
    app.state.redis = redis_async.from_url(cfg.redis_url, decode_responses=True)
    try:
        yield
    finally:
        await app.state.redis.aclose()  # type: ignore[attr-defined,unused-ignore]
        await engine.dispose()


def create_app(config: Config | None = None) -> FastAPI:
    cfg = config or Config()
    app = FastAPI(
        title="ruisheng-api",
        version="0.1.0",
        lifespan=_lifespan,
        docs_url="/api/docs" if cfg.env != "prod" else None,
        openapi_url="/api/openapi.json" if cfg.env != "prod" else None,
    )
    app.state.config = cfg
    register_exception_handlers(app)
    mount_routers(app)
    return app
