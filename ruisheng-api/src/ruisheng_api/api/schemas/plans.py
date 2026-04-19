"""Plans schemas (timing_plans / maintain_plans)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class TimingPlanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="ignore")

    id: int
    dev_number: str
    action_at: datetime
    action: int
    repetition: int
    enable: bool
    update_flag: int
    usr_group: str
    created_at: datetime
    updated_at: datetime


class TimingPlanCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dev_number: str = Field(..., max_length=50)
    action_at: datetime
    action: int
    repetition: int = Field(default=0, ge=0)
    enable: bool = True


class TimingPlanUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_at: datetime | None = None
    action: int | None = None
    repetition: int | None = Field(default=None, ge=0)
    enable: bool | None = None
