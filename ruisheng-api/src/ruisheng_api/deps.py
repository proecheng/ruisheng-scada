"""依赖注入：config / db session / redis / current_user。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

import redis.asyncio as redis_async
from fastapi import Depends, Header, Request
from ruisheng_shared.errors.codes import BizError, ErrCode
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .config import Config
from .core.rbac import CurrentUser
from .core.security import client_fingerprint, verify_token
from .core.token_blacklist import is_jti_blacklisted

if TYPE_CHECKING:
    _Redis = redis_async.Redis[Any]
else:
    _Redis = redis_async.Redis


def get_config(request: Request) -> Config:
    return request.app.state.config  # type: ignore[no-any-return]


def get_session_factory(request: Request) -> async_sessionmaker[AsyncSession]:
    return request.app.state.session_factory  # type: ignore[no-any-return]


def get_gw_session_factory(request: Request) -> async_sessionmaker[AsyncSession]:
    # ruisheng_gw role has BYPASSRLS — used for cross-tenant reads (e.g. login user lookup)
    return request.app.state.gw_session_factory  # type: ignore[no-any-return]


def get_redis(request: Request) -> _Redis:
    return request.app.state.redis  # type: ignore[no-any-return]


async def get_session(
    factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
) -> AsyncIterator[AsyncSession]:
    async with factory() as session:
        yield session


async def get_gw_session(
    factory: async_sessionmaker[AsyncSession] = Depends(get_gw_session_factory),
) -> AsyncIterator[AsyncSession]:
    async with factory() as session:
        yield session


async def _extract_bearer(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise BizError(ErrCode.UNAUTHED, "missing Authorization header")
    return authorization.split(" ", 1)[1].strip()


async def get_current_user(
    request: Request,
    authorization: str | None = Header(default=None),
    cfg: Config = Depends(get_config),
    r: _Redis = Depends(get_redis),
) -> CurrentUser:
    token = await _extract_bearer(authorization)
    client_host = request.client.host if request.client else "unknown"
    ua = request.headers.get("user-agent", "")
    fp = client_fingerprint(client_host, ua)
    payload = verify_token(token, secret=cfg.jwt_secret, expected_fp=fp)
    jti = str(payload.get("jti") or "")
    if await is_jti_blacklisted(r, jti):
        raise BizError(ErrCode.UNAUTHED, "token revoked")
    ca_raw = payload.get("ca")
    ca = int(ca_raw) if isinstance(ca_raw, int | float | str) else 0
    return CurrentUser(
        user_name=str(payload["sub"]),
        usr_group=str(payload.get("usr_group") or ""),
        role=str(payload["role"]),
        control_authority=ca,
        jti=jti,
        fp=fp,
    )
