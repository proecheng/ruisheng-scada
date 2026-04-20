"""JWT 签发/校验 + bcrypt 密码 + client fingerprint。对应 spec §5.13。"""

from __future__ import annotations

import hashlib
import time

import bcrypt
import ulid
from jose import JWTError, jwt  # type: ignore[import-untyped]
from ruisheng_shared.errors.codes import BizError, ErrCode

_ALG = "HS256"


def hash_password(raw: str) -> str:
    result: bytes = bcrypt.hashpw(raw.encode(), bcrypt.gensalt())
    return result.decode()


def verify_password(raw: str, hashed: str) -> bool:
    try:
        result: bool = bcrypt.checkpw(raw.encode(), hashed.encode())
        return result
    except Exception:
        return False


def client_fingerprint(ip: str, user_agent: str) -> str:
    return hashlib.sha256(f"{ip}|{user_agent}".encode()).hexdigest()[:16]


def _issue(
    *,
    sub: str,
    usr_group: str,
    role: str,
    ca: int,
    fp: str,
    typ: str,
    secret: str,
    ttl_sec: int,
) -> str:
    now = int(time.time())
    payload = {
        "sub": sub,
        "usr_group": usr_group,
        "role": role,
        "ca": ca,
        "fp": fp,
        "jti": str(ulid.ULID()),
        "typ": typ,
        "iat": now,
        "exp": now + ttl_sec,
    }
    encoded: str = jwt.encode(payload, secret, algorithm=_ALG)
    return encoded


def issue_access_token(
    sub: str, usr_group: str, role: str, ca: int, fp: str, *, secret: str, ttl_sec: int
) -> str:
    return _issue(
        sub=sub,
        usr_group=usr_group,
        role=role,
        ca=ca,
        fp=fp,
        typ="access",
        secret=secret,
        ttl_sec=ttl_sec,
    )


def issue_refresh_token(
    sub: str, usr_group: str, role: str, ca: int, fp: str, *, secret: str, ttl_sec: int
) -> str:
    return _issue(
        sub=sub,
        usr_group=usr_group,
        role=role,
        ca=ca,
        fp=fp,
        typ="refresh",
        secret=secret,
        ttl_sec=ttl_sec,
    )


def verify_token(
    token: str, *, secret: str, expected_fp: str, expected_typ: str = "access"
) -> dict[str, object]:
    try:
        payload: dict[str, object] = jwt.decode(token, secret, algorithms=[_ALG])
    except JWTError as e:
        raise BizError(ErrCode.UNAUTHED, f"invalid token: {e}") from e
    if payload.get("typ") != expected_typ:
        raise BizError(ErrCode.UNAUTHED, f"wrong token type: {payload.get('typ')}")
    if payload.get("fp") != expected_fp:
        raise BizError(ErrCode.UNAUTHED, "fingerprint mismatch")
    return payload
