"""Redis-backed JWT revocation helpers."""

from __future__ import annotations

from typing import Any

_PREFIX = "jwt_blacklist:"
_LEGACY_SET = "jwt_blacklist"


async def blacklist_jti(r: Any, jti: str, remaining: int) -> None:
    if remaining <= 0:
        return
    await r.setex(f"{_PREFIX}{jti}", remaining, "1")


async def is_jti_blacklisted(r: Any, jti: str) -> bool:
    if not jti:
        return False
    if await r.exists(f"{_PREFIX}{jti}"):
        return True
    # Backward compatibility for tokens revoked before the per-JTI key format.
    return bool(await r.sismember(_LEGACY_SET, jti))
