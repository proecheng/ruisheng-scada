from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ControlAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fun_code: int = Field(..., ge=1, le=100)
    reg: int = Field(..., ge=0, le=65535)
    value: int = Field(..., ge=-(2**31), le=2**31 - 1)
    high_risk: bool = False


class ControlResponseData(BaseModel):
    cmd_id: str
    status: str
    acted_at: datetime
