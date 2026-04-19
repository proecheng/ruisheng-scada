"""分布式登录失败计数 + 用户锁 + IP 黑名单（spec §5.13 B-S8）。"""

from __future__ import annotations

import redis.asyncio as redis_async


async def record_login_fail(
    r: redis_async.Redis[bytes],
    user_name: str,
    ip: str,
    *,
    user_max: int,
    ip_max: int,
    window: int,
    lock_ttl: int,
    ip_block_ttl: int,
) -> bool:
    """Returns True if user is locked due to this failure."""
    key_user = f"login_fail:{user_name}"
    n_user = await r.incr(key_user)
    if n_user == 1:
        await r.expire(key_user, window)
    locked = False
    if n_user >= user_max:
        await r.setex(f"login_lock:{user_name}", lock_ttl, "1")
        locked = True

    key_ip = f"login_fail_ip:{ip}"
    n_ip = await r.incr(key_ip)
    if n_ip == 1:
        await r.expire(key_ip, window)
    if n_ip >= ip_max:
        await r.setex(f"ip_block:{ip}", ip_block_ttl, "1")
    return locked


async def is_user_locked(r: redis_async.Redis[bytes], user_name: str) -> bool:
    return bool(await r.exists(f"login_lock:{user_name}"))


async def is_ip_blocked(r: redis_async.Redis[bytes], ip: str) -> bool:
    return bool(await r.exists(f"ip_block:{ip}"))


async def clear_login_fail(r: redis_async.Redis[bytes], user_name: str) -> None:
    await r.delete(f"login_fail:{user_name}", f"login_lock:{user_name}")
