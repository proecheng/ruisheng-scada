"""loguru 配置 + 敏感字段脱敏（对应 spec §5.12.3）。"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:
    from loguru import Record

SENSITIVE_KEYS: frozenset[str] = frozenset(
    {
        "password",
        "password_hash",
        "secret",
        "appsecret",
        "api_key",
        "jwt",
        "token",
        "authorization",
        "cookie",
    }
)

# Minimum phone string length for partial masking (show first 3 + last 4 chars).
_PHONE_MASK_MIN_LEN: int = 7

# Mutable container avoids PLW0603 (global statement to update variable).
_state: dict[str, Any] = {"configured": False}


def redact_record(record: dict[str, Any]) -> None:
    extra = record.get("extra") or {}
    for key in list(extra):
        low = key.lower()
        if low in SENSITIVE_KEYS:
            extra[key] = "***"
            continue
        if low in {"phone_number", "msisdn"}:
            v = str(extra[key])
            extra[key] = f"{v[:3]}****{v[-4:]}" if len(v) >= _PHONE_MASK_MIN_LEN else "***"


def _patcher(record: Record) -> None:
    redact_record(record)  # type: ignore[arg-type]


def configure_logging(*, level: str = "INFO") -> None:
    logger.remove()
    logger.add(
        sys.stderr,
        level=level,
        serialize=True,
        enqueue=True,
        backtrace=False,
        diagnose=False,
    )
    logger.configure(patcher=_patcher)
    _state["configured"] = True
