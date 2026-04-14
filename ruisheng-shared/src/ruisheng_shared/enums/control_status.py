"""控制命令状态机。对应 spec §3.2 生命周期与 §3.4.1 WS control_result 契约。"""

from __future__ import annotations

from enum import Enum


class ControlStatus(str, Enum):
    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self is not ControlStatus.PENDING
