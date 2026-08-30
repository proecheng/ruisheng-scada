"""Bearer-token digest checks for unauthenticated management endpoints."""

from __future__ import annotations

import hashlib
import re
import secrets

MIN_MANAGEMENT_TOKEN_LENGTH = 43
MAX_MANAGEMENT_TOKEN_LENGTH = 256
_SHA256_HEX_RE = re.compile(r"[0-9a-f]{64}")
_BEARER_TOKEN_RE = re.compile(r"[A-Za-z0-9\-._~+/]+=*")


def normalize_sha256_digest(value: str | None) -> str | None:
    """Return a validated lowercase SHA-256 digest, or ``None`` when unset."""
    if value is None or not value.strip():
        return None
    if _SHA256_HEX_RE.fullmatch(value) is None:
        raise ValueError("management token digest must be exactly 64 lowercase hex characters")
    return value


def _bearer_token_bytes(authorization: str | None) -> bytes | None:
    if authorization is None:
        return None
    scheme, separator, token = authorization.partition(" ")
    if separator != " " or scheme.casefold() != "bearer":
        return None
    if not MIN_MANAGEMENT_TOKEN_LENGTH <= len(token) <= MAX_MANAGEMENT_TOKEN_LENGTH:
        return None
    try:
        token_bytes = token.encode("ascii")
    except UnicodeEncodeError:
        return None
    if _BEARER_TOKEN_RE.fullmatch(token) is None:
        return None
    return token_bytes


def management_bearer_matches(authorization: str | None, expected_digest: str | None) -> bool:
    """Validate an RFC 6750-style Bearer token against a configured digest."""
    if expected_digest is None:
        return False
    token_bytes = _bearer_token_bytes(authorization)
    if token_bytes is None:
        return False
    actual_digest = hashlib.sha256(token_bytes).hexdigest()
    return secrets.compare_digest(actual_digest, expected_digest)
