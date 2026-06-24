"""Auth API：/api/auth/*。"""

from __future__ import annotations

import time
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
from ..core.rbac import CurrentUser
from ..core.response import ApiResponse, ok
from ..core.security import (
    client_fingerprint,
    hash_password,
    issue_access_token,
    issue_refresh_token,
    verify_password,
    verify_token,
)
from ..core.token_blacklist import blacklist_jti, is_jti_blacklisted
from ..db.repositories import users as users_repo
from ..deps import get_config, get_current_user, get_gw_session, get_redis, get_session
from ..services import otp as otp_svc
from .schemas.auth import (
    LoginRequest,
    LoginResponseData,
    LogoutRequest,
    OtpSendRequest,
    RefreshRequest,
    RegisterRequest,
    SmsSendRequest,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=ApiResponse)
async def login(
    body: LoginRequest,
    request: Request,
    cfg: Config = Depends(get_config),
    r: _Redis = Depends(get_redis),
    session: AsyncSession = Depends(get_gw_session),
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


async def _send_sms(channel: str, phone: str, code: str) -> None:
    # Stage J wires real adapter
    logger.bind(phone_number=phone, channel=channel).info("otp issued")


@router.post("/sms/send", response_model=ApiResponse)
async def sms_send(
    body: SmsSendRequest,
    cfg: Config = Depends(get_config),
    r: _Redis = Depends(get_redis),
) -> ApiResponse:
    code = await otp_svc.issue_otp(
        r, action=body.action, key=body.phone_number, ttl_sec=cfg.otp_ttl_sec
    )
    await _send_sms(body.channel, body.phone_number, code)
    return ok(data={"sent": True, "ttl_sec": cfg.otp_ttl_sec})


@router.post("/register", response_model=ApiResponse)
async def register(
    body: RegisterRequest,
    cfg: Config = Depends(get_config),
    r: _Redis = Depends(get_redis),
    session: AsyncSession = Depends(get_session),
) -> ApiResponse:
    if not await otp_svc.verify_otp(r, action="register", key=body.user_name, code=body.otp_code):
        raise BizError(ErrCode.UNAUTHED, "otp invalid or expired")
    if await users_repo.load_by_user_name(session, body.user_name):
        raise BizError(ErrCode.BAD_PARAM, "user_name already registered")
    await users_repo.create_user(
        session,
        user_name=body.user_name,
        password_hash=hash_password(body.password),
        authority="User",
        usr_group=cfg.default_usr_group,
    )
    return ok(data={"user_name": body.user_name})


@router.post("/refresh", response_model=ApiResponse)
async def refresh(
    body: RefreshRequest,
    request: Request,
    cfg: Config = Depends(get_config),
    r: _Redis = Depends(get_redis),
    session: AsyncSession = Depends(get_gw_session),
) -> ApiResponse:
    ip = request.client.host if request.client else "unknown"
    ua = request.headers.get("user-agent", "")
    fp = client_fingerprint(ip, ua)
    payload = verify_token(
        body.refresh_token,
        secret=cfg.jwt_secret,
        expected_fp=fp,
        expected_typ="refresh",
    )
    old_jti = str(payload["jti"])
    if await is_jti_blacklisted(r, old_jti):
        raise BizError(ErrCode.UNAUTHED, "refresh revoked")
    remaining = int(str(payload["exp"])) - int(time.time())
    await blacklist_jti(r, old_jti, remaining)
    sub = str(payload["sub"])
    current = await users_repo.load_by_user_name(session, sub)
    if current is None:
        raise BizError(ErrCode.UNAUTHED, "refresh user not found")
    grp = current.usr_group
    role = current.authority
    ca = current.control_authority
    access = issue_access_token(
        sub, grp, role, ca, fp, secret=cfg.jwt_secret, ttl_sec=cfg.jwt_access_ttl_sec
    )
    new_refresh = issue_refresh_token(
        sub, grp, role, ca, fp, secret=cfg.jwt_secret, ttl_sec=cfg.jwt_refresh_ttl_sec
    )
    return ok(
        data=LoginResponseData(
            access_token=access,
            refresh_token=new_refresh,
            access_ttl_sec=cfg.jwt_access_ttl_sec,
            user_name=current.user_name,
            role=role,
            usr_group=grp,
            control_authority=ca,
        ).model_dump()
    )


@router.post("/logout", response_model=ApiResponse)
async def logout(
    body: LogoutRequest,
    request: Request,
    cfg: Config = Depends(get_config),
    r: _Redis = Depends(get_redis),
    user: CurrentUser = Depends(get_current_user),
) -> ApiResponse:
    await blacklist_jti(r, user.jti, cfg.jwt_access_ttl_sec)
    if body.refresh_token:
        ip = request.client.host if request.client else "unknown"
        ua = request.headers.get("user-agent", "")
        fp = client_fingerprint(ip, ua)
        try:
            p = verify_token(
                body.refresh_token,
                secret=cfg.jwt_secret,
                expected_fp=fp,
                expected_typ="refresh",
            )
            await blacklist_jti(r, str(p["jti"]), int(str(p["exp"])) - int(time.time()))
        except BizError:
            pass
    return ok(data={"logout": True})


@router.post("/otp/send", response_model=ApiResponse)
async def otp_send(
    body: OtpSendRequest,
    cfg: Config = Depends(get_config),
    r: _Redis = Depends(get_redis),
    user: CurrentUser = Depends(get_current_user),
) -> ApiResponse:
    code = await otp_svc.issue_otp(
        r, action=body.action, key=user.user_name, ttl_sec=cfg.otp_ttl_sec
    )
    await _send_sms(body.channel, user.user_name, code)
    logger.bind(user_name=user.user_name, action=body.action).info("otp to user")
    return ok(data={"sent": True, "ttl_sec": cfg.otp_ttl_sec})
