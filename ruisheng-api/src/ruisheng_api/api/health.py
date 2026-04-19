"""健康检查：live（进程活）/ ready（DB+Redis 可达，Stage A5 只做 live 存根）。"""

from __future__ import annotations

from fastapi import APIRouter

from ..core.response import ok

router = APIRouter(prefix="/api/health", tags=["health"])


@router.get("/live")
async def liveness() -> dict[str, object]:
    return ok(data={"status": "live"}).model_dump()
