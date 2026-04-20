"""健康检查：live（进程活）/ ready（DB+Redis 可达，Stage A5 只做 live 存根）。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

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
