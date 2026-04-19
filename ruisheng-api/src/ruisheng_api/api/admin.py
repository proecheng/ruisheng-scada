"""Admin API：版本/log level/current。"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

import redis.asyncio as redis_async
from fastapi import APIRouter, Depends, Header
from loguru import logger
from ruisheng_shared.errors.codes import BizError, ErrCode

from ruisheng_api import __version__

from ..core.rbac import CurrentUser, check_role
from ..core.response import ApiResponse, ok
from ..deps import get_current_user, get_redis
from ..services import otp as otp_svc

if TYPE_CHECKING:
    _Redis = redis_async.Redis[Any]
else:
    _Redis = redis_async.Redis

router = APIRouter(tags=["admin"])
meta_router = APIRouter(tags=["meta"])

_VALID_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})


@meta_router.get("/api/meta/version", response_model=ApiResponse)
async def version() -> ApiResponse:
    build_hash = os.environ.get("GIT_COMMIT", "local")
    return ok(data={"version": __version__, "build_hash": build_hash})


@router.post("/api/admin/log/level", response_model=ApiResponse)
async def set_log_level(
    level: str,
    otp_code: str | None = Header(default=None, alias="X-OTP-Code"),
    user: CurrentUser = Depends(get_current_user),
    r: _Redis = Depends(get_redis),
) -> ApiResponse:
    check_role(user, allowed=("Administrators",))
    if not otp_code or not await otp_svc.verify_otp(
        r, action="log_level", key=user.user_name, code=otp_code
    ):
        raise BizError(ErrCode.UNAUTHED, "OTP required")
    lvl = level.upper()
    if lvl not in _VALID_LEVELS:
        raise BizError(ErrCode.BAD_PARAM, f"invalid level: {level}")
    logger.remove()
    logger.add(__import__("sys").stderr, level=lvl, serialize=True, enqueue=True)
    await r.set("admin:log:level", lvl)
    await r.publish("channel:admin:log:level", lvl)
    return ok(data={"level": lvl})


@router.get("/api/admin/log/current", response_model=ApiResponse)
async def get_log_level(
    user: CurrentUser = Depends(get_current_user),
    r: _Redis = Depends(get_redis),
) -> ApiResponse:
    check_role(user, allowed=("Administrators",))
    stored = await r.get("admin:log:level")
    return ok(data={"level": str(stored) if stored else "INFO"})
