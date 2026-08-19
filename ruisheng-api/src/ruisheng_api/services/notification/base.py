"""通知适配器契约（对应 spec D2）。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Protocol

HTTP_RATE_LIMITED = 429
HTTP_SERVER_ERROR = 500


@dataclass(frozen=True)
class AlarmNotification:
    trace_id: str
    event_id: int
    dev_number: str
    alarm_name: str
    value: float
    limit: float
    user_name: str
    contact: str  # phone / email / openid
    msg: str


@dataclass(frozen=True)
class ProviderResult:
    sent: bool
    retryable: bool = False
    error_class: str | None = None
    http_status: int | None = None
    retry_after_sec: int | None = None


class INotifier(Protocol):
    name: str

    async def send(self, n: AlarmNotification) -> bool:
        """True on success. Adapter internally retries; returns False for outer fan-out to log failures."""
        ...


def parse_http_date(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed


def parse_retry_after(
    value: str | None,
    *,
    reference_time: datetime | None = None,
) -> int | None:
    if value is None:
        return None
    try:
        return min(3600, max(0, int(value)))
    except ValueError:
        retry_at = parse_http_date(value)
        if retry_at is None or reference_time is None:
            return None
        if reference_time.tzinfo is None:
            reference_time = reference_time.replace(tzinfo=UTC)
        return min(3600, max(0, int((retry_at - reference_time).total_seconds())))
