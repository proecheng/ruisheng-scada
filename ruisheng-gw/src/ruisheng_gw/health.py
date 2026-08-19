"""gw health & metrics HTTP endpoints (aiohttp on :9090).

- /health   — liveness (进程存活 = 200)
- /ready    — readiness (DB, Redis, and latest batch flush are healthy)
- /metrics  — Prometheus text format
"""

from __future__ import annotations

from dataclasses import dataclass

from aiohttp import web

OUTBOX_READINESS_FAILURE_THRESHOLD = 3


@dataclass
class HealthState:
    """全局 health 状态，由主 loop 更新。"""

    _db_ok: bool = False
    _redis_ok: bool = False
    _batch_ok: bool = True
    _outbox_pending: int = 0
    _outbox_relay_failures: int = 0
    _outbox_consecutive_failures: int = 0

    def set_db_ok(self, ok: bool) -> None:
        self._db_ok = ok

    def set_redis_ok(self, ok: bool) -> None:
        self._redis_ok = ok

    def mark_flush_ok(self) -> None:
        self._batch_ok = True

    def mark_flush_failed(self) -> None:
        self._batch_ok = False

    def is_ready(self) -> bool:
        return (
            self._db_ok
            and self._redis_ok
            and self._batch_ok
            and self._outbox_consecutive_failures < OUTBOX_READINESS_FAILURE_THRESHOLD
        )

    def set_outbox_pending(self, count: int) -> None:
        self._outbox_pending = count

    def mark_outbox_relay_failed(self) -> None:
        self._outbox_relay_failures += 1
        self._outbox_consecutive_failures += 1

    def mark_outbox_relay_ok(self) -> None:
        self._outbox_consecutive_failures = 0


HEALTH_STATE_KEY = web.AppKey("health_state", HealthState)


async def _health_handler(request: web.Request) -> web.Response:  # noqa: ARG001
    return web.json_response({"status": "alive"})


async def _ready_handler(request: web.Request) -> web.Response:
    state = request.app[HEALTH_STATE_KEY]
    if state.is_ready():
        return web.json_response({"ready": True})
    return web.json_response({"ready": False}, status=503)


async def _metrics_handler(request: web.Request) -> web.Response:
    state = request.app[HEALTH_STATE_KEY]
    body = (
        "# HELP ruisheng_gw_build_info Build info\n"
        "# TYPE ruisheng_gw_build_info gauge\n"
        'ruisheng_gw_build_info{version="0.1.0"} 1\n'
        "# HELP ruisheng_gw_alarm_outbox_pending Unpublished alarm outbox rows\n"
        "# TYPE ruisheng_gw_alarm_outbox_pending gauge\n"
        f"ruisheng_gw_alarm_outbox_pending {state._outbox_pending}\n"
        "# HELP ruisheng_gw_alarm_outbox_relay_failures_total Alarm outbox relay failures\n"
        "# TYPE ruisheng_gw_alarm_outbox_relay_failures_total counter\n"
        f"ruisheng_gw_alarm_outbox_relay_failures_total {state._outbox_relay_failures}\n"
        "# HELP ruisheng_gw_alarm_outbox_relay_consecutive_failures "
        "Consecutive alarm outbox relay failures\n"
        "# TYPE ruisheng_gw_alarm_outbox_relay_consecutive_failures gauge\n"
        f"ruisheng_gw_alarm_outbox_relay_consecutive_failures "
        f"{state._outbox_consecutive_failures}\n"
    )
    return web.Response(text=body, content_type="text/plain; version=0.0.4")


def create_health_app(state: HealthState) -> web.Application:
    app = web.Application()
    app[HEALTH_STATE_KEY] = state
    app.router.add_get("/health", _health_handler)
    app.router.add_get("/ready", _ready_handler)
    app.router.add_get("/metrics", _metrics_handler)
    return app
