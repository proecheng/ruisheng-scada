"""FastAPI app factory + lifespan。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import redis.asyncio as redis_async
from fastapi import FastAPI

from .api import mount_routers
from .config import Config
from .core.errors import register_exception_handlers
from .db.base import build_engine, build_session_factory
from .logging_setup import configure_logging
from .pubsub.alarm_consumer import AlarmConsumerConfig, consumer_loop
from .pubsub.realtime_bridge import realtime_loop
from .pubsub.ws_manager import WSManager
from .tasks.pay_expire import expire_stale_pay_orders
from .tasks.pay_seen_cleanup import cleanup_old_pay_seen
from .tasks.scheduler import build_scheduler
from .tasks.token_refresh import refresh_all_wechat_tokens
from .tasks.vacuum_hot import vacuum_hot_tables


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    cfg: Config = app.state.config
    configure_logging(level="INFO")
    engine = build_engine(cfg.db_url, pool_size=cfg.db_pool_size, max_overflow=cfg.db_pool_overflow)
    app.state.engine = engine
    app.state.session_factory = build_session_factory(engine)
    app.state.redis = redis_async.from_url(cfg.redis_url, decode_responses=True)
    ws_manager = WSManager()
    app.state.ws_manager = ws_manager
    stop_event = asyncio.Event()
    app.state.stop_event = stop_event
    consumer_cfg = AlarmConsumerConfig(consumer_name=f"api-{cfg.listen_port}")
    tasks = [
        asyncio.create_task(consumer_loop(app.state.redis, consumer_cfg, ws_manager, stop_event)),
        asyncio.create_task(realtime_loop(app.state.redis, ws_manager, stop_event)),
    ]
    scheduler = build_scheduler()
    app.state.scheduler = scheduler
    scheduler.add_job(
        refresh_all_wechat_tokens,
        "interval",
        minutes=50,
        args=[app.state.session_factory],
        id="wx_token_refresh",
    )
    # Build a separate gw_session_factory for BYPASSRLS jobs
    gw_engine = build_engine(cfg.gw_db_url, pool_size=2, max_overflow=2)
    app.state.gw_engine = gw_engine
    gw_session_factory = build_session_factory(gw_engine)
    app.state.gw_session_factory = gw_session_factory
    scheduler.add_job(
        expire_stale_pay_orders,
        "interval",
        minutes=5,
        args=[gw_session_factory],
        id="pay_expire",
    )
    scheduler.add_job(
        cleanup_old_pay_seen,
        "cron",
        hour=2,
        minute=0,
        args=[gw_session_factory],
        id="pay_seen_cleanup",
    )
    scheduler.add_job(
        vacuum_hot_tables,
        "cron",
        hour=3,
        minute=0,
        args=[gw_session_factory],
        id="vacuum_hot",
    )
    scheduler.start()
    try:
        yield
    finally:
        stop_event.set()
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        scheduler.shutdown(wait=False)
        await app.state.redis.aclose()  # type: ignore[attr-defined,unused-ignore]
        await gw_engine.dispose()
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
