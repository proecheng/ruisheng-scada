"""OTP 发码/校验，Redis GETDEL 消费（spec §5.13）。"""

from __future__ import annotations

import secrets
from typing import TYPE_CHECKING, Any

import redis.asyncio as redis_async

if TYPE_CHECKING:
    _Redis = redis_async.Redis[Any]
else:
    _Redis = redis_async.Redis


def generate_otp() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def _key(action: str, key: str) -> str:
    return f"otp:{action}:{key}"


async def issue_otp(r: _Redis, *, action: str, key: str, ttl_sec: int) -> str:
    code = generate_otp()
    await r.setex(_key(action, key), ttl_sec, code)
    return code


async def verify_otp(r: _Redis, *, action: str, key: str, code: str) -> bool:
    stored = await r.getdel(_key(action, key))
    if stored is None:
        return False
    stored_str = stored.decode() if isinstance(stored, bytes) else str(stored)
    return stored_str == code
