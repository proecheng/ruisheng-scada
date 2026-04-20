"""通知适配器契约（对应 spec D2）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


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


class INotifier(Protocol):
    name: str

    async def send(self, n: AlarmNotification) -> bool:
        """True on success. Adapter internally retries; returns False for outer fan-out to log failures."""
        ...
