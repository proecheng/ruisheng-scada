"""Device state machine.

States: UNREGISTERED → ONLINE → OFFLINE (→ ONLINE on re-register).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto


class DeviceState(Enum):
    UNREGISTERED = auto()
    ONLINE = auto()
    OFFLINE = auto()


class InvalidTransition(RuntimeError):  # noqa: N818
    pass


@dataclass
class Device:
    dev_number: str
    usr_group: str
    state: DeviceState = DeviceState.UNREGISTERED
    last_seen: float = 0.0

    def register(self, *, now: float) -> None:
        # allowed from UNREGISTERED or OFFLINE (reconnect)
        if self.state is DeviceState.ONLINE:
            # idempotent re-register while online — update timestamp only
            self.last_seen = now
            return
        self.state = DeviceState.ONLINE
        self.last_seen = now

    def heartbeat(self, *, now: float) -> None:
        if self.state is not DeviceState.ONLINE:
            raise InvalidTransition(
                f"cannot heartbeat: dev={self.dev_number} in state {self.state.name}"
            )
        self.last_seen = now

    def mark_offline(self, *, reason: str = "unknown") -> None:
        self.state = DeviceState.OFFLINE
