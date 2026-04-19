"""FastAPI app factory + lifespan（Stage A5 只挂 health；后续 Stage 扩 lifespan 启 engine/redis/scheduler）。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .api import mount_routers
from .config import Config
from .core.errors import register_exception_handlers


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Stage B/D 会在此装 engine / redis client
    yield


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
