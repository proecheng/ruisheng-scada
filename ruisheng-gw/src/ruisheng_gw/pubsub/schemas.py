"""Redis pub contract schemas — gw internal (Plan 1.5 migrates to shared).

channel:realtime:v1:{dev_number} carries RealtimeEvent
channel:alarm:v1:{dev_number}    carries AlarmEvent

schema_version: Literal[1] pinned — any breaking change requires
channel name bump (channel:realtime:v2:...).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class RealtimeEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    dev_number: str
    point_id: int
    rt_value: float | None
    org_value: float | None
    recorded_at: float  # epoch seconds


class AlarmEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    dev_number: str
    point_id: int
    value: float
    threshold: float
    level: int
    fired_at: float
