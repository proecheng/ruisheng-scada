"""Serial availability requires recent, validated responses, including after GW loss."""

from __future__ import annotations

SERIAL_LOSS_LIMIT = 3
SERIAL_MIN_GRACE_SECONDS = 10.0


def serial_online_timeout_seconds(interval_decisec: int) -> float:
    return max(SERIAL_MIN_GRACE_SECONDS, SERIAL_LOSS_LIMIT * interval_decisec / 10.0)


def serial_response_is_recent(*, last_back: float, now: float, interval_decisec: int) -> bool:
    return last_back > 0 and 0 <= now - last_back < serial_online_timeout_seconds(interval_decisec)
