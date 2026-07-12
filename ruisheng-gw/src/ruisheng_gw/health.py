"""gw health & metrics HTTP endpoints (aiohttp on :9090).

- /health   — liveness (进程存活 = 200)
- /ready    — readiness (DB, Redis, and latest batch flush are healthy)
- /metrics  — Prometheus text format
"""

from __future__ import annotations

from dataclasses import dataclass

from aiohttp import web


@dataclass
class HealthState:
    """全局 health 状态，由主 loop 更新。"""

    _db_ok: bool = False
    _redis_ok: bool = False
    _batch_ok: bool = True

    def set_db_ok(self, ok: bool) -> None:
        self._db_ok = ok

    def set_redis_ok(self, ok: bool) -> None:
        self._redis_ok = ok

    def mark_flush_ok(self) -> None:
        self._batch_ok = True

    def mark_flush_failed(self) -> None:
        self._batch_ok = False

    def is_ready(self) -> bool:
        return self._db_ok and self._redis_ok and self._batch_ok


HEALTH_STATE_KEY = web.AppKey("health_state", HealthState)


async def _health_handler(request: web.Request) -> web.Response:  # noqa: ARG001
    return web.json_response({"status": "alive"})


async def _ready_handler(request: web.Request) -> web.Response:
    state = request.app[HEALTH_STATE_KEY]
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
    app[HEALTH_STATE_KEY] = state
    app.router.add_get("/health", _health_handler)
    app.router.add_get("/ready", _ready_handler)
    app.router.add_get("/metrics", _metrics_handler)
    return app
