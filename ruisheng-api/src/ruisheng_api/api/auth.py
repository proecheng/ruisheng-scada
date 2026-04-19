"""Auth API：/api/auth/*。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import redis.asyncio as redis_async
from fastapi import APIRouter, Depends, Request
from loguru import logger
from ruisheng_shared.errors.codes import BizError, ErrCode
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    _Redis = redis_async.Redis[Any]
else:
    _Redis = redis_async.Redis

from ..config import Config
from ..core.login_limit import (
    clear_login_fail,
    is_ip_blocked,
    is_user_locked,
    record_login_fail,
)
from ..core.response import ApiResponse, ok
from ..core.security import (
    client_fingerprint,
    issue_access_token,
    issue_refresh_token,
    verify_password,
)
from ..db.repositories import users as users_repo
from ..deps import get_config, get_redis, get_session
from .schemas.auth import LoginRequest, LoginResponseData

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=ApiResponse)
async def login(
    body: LoginRequest,
    request: Request,
    cfg: Config = Depends(get_config),
    r: _Redis = Depends(get_redis),
    session: AsyncSession = Depends(get_session),
) -> ApiResponse:
    ip = request.client.host if request.client else "unknown"
    ua = request.headers.get("user-agent", "")
    if await is_user_locked(r, body.user_name):
        raise BizError(ErrCode.FORBIDDEN, "account locked; try later")
    if await is_ip_blocked(r, ip):
        raise BizError(ErrCode.FORBIDDEN, "ip temporarily blocked")
    user = await users_repo.load_by_user_name(session, body.user_name)
    if user is None or not verify_password(body.password, user.password_hash):
        await record_login_fail(
            r,
            body.user_name,
            ip,
            user_max=cfg.login_fail_user_max,
            ip_max=cfg.login_fail_ip_max,
            window=cfg.login_fail_user_window_sec,
            lock_ttl=cfg.login_lock_ttl_sec,
            ip_block_ttl=cfg.ip_block_ttl_sec,
        )
        raise BizError(ErrCode.UNAUTHED, "invalid credentials")
    await clear_login_fail(r, body.user_name)
    fp = client_fingerprint(ip, ua)
    access = issue_access_token(
        user.user_name,
        user.usr_group,
        user.authority,
        user.control_authority,
        fp,
        secret=cfg.jwt_secret,
        ttl_sec=cfg.jwt_access_ttl_sec,
    )
    refresh = issue_refresh_token(
        user.user_name,
        user.usr_group,
        user.authority,
        user.control_authority,
        fp,
        secret=cfg.jwt_secret,
        ttl_sec=cfg.jwt_refresh_ttl_sec,
    )
    logger.bind(user_name=user.user_name, usr_group=user.usr_group).info("login ok")
    return ok(
        data=LoginResponseData(
            access_token=access,
            refresh_token=refresh,
            access_ttl_sec=cfg.jwt_access_ttl_sec,
            user_name=user.user_name,
            role=user.authority,
            usr_group=user.usr_group,
            control_authority=user.control_authority,
        ).model_dump()
    )
