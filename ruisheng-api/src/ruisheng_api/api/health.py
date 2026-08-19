"""健康检查：live（进程活）/ ready（DB+Redis 可达，Stage A5 只做 live 存根）。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse

from ..core.response import ok

router = APIRouter(prefix="/api/health", tags=["health"])


@router.get("/live")
async def liveness() -> dict[str, object]:
    return ok(data={"status": "live"}).model_dump()


@router.get("/ready")
async def readiness(request: Request) -> Any:
    from sqlalchemy import text

    errors: list[str] = []
    # Check DB
    try:
        factory = request.app.state.session_factory
        async with factory() as session:
            await session.execute(text("SELECT 1"))
    except Exception:
        errors.append("db")
    # Check Redis
    try:
        r = request.app.state.redis
        await r.ping()
    except Exception:
        errors.append("redis")
    if errors:
        from fastapi.responses import JSONResponse
        from ruisheng_shared.errors.codes import ErrCode

        from ..core.response import fail

        return JSONResponse(
            status_code=503,
            content=fail(ErrCode.INTERNAL, f"not ready: {errors}").model_dump(),
        )
    return ok(data={"status": "ready"}).model_dump()


@router.get("/metrics", response_class=PlainTextResponse)
async def metrics(request: Request) -> PlainTextResponse:
    state = request.app.state.notification_metrics
    lines = [
        "# TYPE ruisheng_api_notification_materialize_failures_total counter",
        f"ruisheng_api_notification_materialize_failures_total {state.materialize_failures}",
        "# TYPE ruisheng_api_notification_pending gauge",
        f"ruisheng_api_notification_pending {state.pending}",
        "# TYPE ruisheng_api_notification_oldest_age_seconds gauge",
        f"ruisheng_api_notification_oldest_age_seconds {state.oldest_age_sec}",
        "# TYPE ruisheng_api_notification_stale_completions_total counter",
        f"ruisheng_api_notification_stale_completions_total {state.stale_completions}",
    ]
    for error_class, count in sorted(state.failures.items()):
        lines.append(
            'ruisheng_api_notification_failures_total{error_class="' + error_class + f'"}} {count}'
        )
    return PlainTextResponse("\n".join(lines) + "\n")
