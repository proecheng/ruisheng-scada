"""gw health & metrics HTTP endpoints (aiohttp on :9090).

- /health   — liveness (进程存活 = 200)
- /ready    — readiness (db+redis+batch flush < 5s = 200 else 503)
- /metrics  — Prometheus text format
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from aiohttp import web


@dataclass
class HealthState:
    """全局 health 状态，由主 loop 更新。"""

    _db_ok: bool = False
    _redis_ok: bool = False
    _last_flush_ts: float = 0.0
    ready_max_flush_age_sec: float = 5.0

    def set_db_ok(self, ok: bool) -> None:
        self._db_ok = ok

    def set_redis_ok(self, ok: bool) -> None:
        self._redis_ok = ok

    def mark_flush_ok(self) -> None:
        self._last_flush_ts = time.monotonic()

    def is_ready(self, now: float | None = None) -> bool:
        now = now if now is not None else time.monotonic()
        flush_fresh = (now - self._last_flush_ts) < self.ready_max_flush_age_sec
        return self._db_ok and self._redis_ok and flush_fresh


async def _health_handler(request: web.Request) -> web.Response:  # noqa: ARG001
    return web.json_response({"status": "alive"})


async def _ready_handler(request: web.Request) -> web.Response:
    state: HealthState = request.app["health_state"]
    if state.is_ready():
        return web.json_response({"ready": True})
    return web.json_response({"ready": False}, status=503)


async def _metrics_handler(request: web.Request) -> web.Response:  # noqa: ARG001
    # 初版：只暴露 build info；完整 metric 在 F5 task 接 prometheus_client
    body = (
        "# HELP ruisheng_gw_build_info Build info\n"
        "# TYPE ruisheng_gw_build_info gauge\n"
        'ruisheng_gw_build_info{version="0.1.0"} 1\n'
    )
    return web.Response(text=body, content_type="text/plain; version=0.0.4")


def create_health_app(state: HealthState) -> web.Application:
    app = web.Application()
    app["health_state"] = state
    app.router.add_get("/health", _health_handler)
    app.router.add_get("/ready", _ready_handler)
    app.router.add_get("/metrics", _metrics_handler)
    return app
