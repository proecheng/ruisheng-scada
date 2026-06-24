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


# ---------------------------------------------------------------------------
# Maintenance plan schemas
# ---------------------------------------------------------------------------


class MaintainPlanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="ignore")

    id: int
    dev_number: str
    plan_name: str
    description: str | None
    interval_days: int
    next_due_at: datetime
    enable: bool
    usr_group: str
    created_at: datetime
    updated_at: datetime


class MaintainPlanCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dev_number: str = Field(..., max_length=50)
    plan_name: str = Field(..., max_length=100)
    description: str | None = Field(default=None, max_length=255)
    interval_days: int = Field(..., ge=1, le=3650)
    next_due_at: datetime
    enable: bool = True


class MaintainPlanUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_name: str | None = Field(default=None, max_length=100)
    description: str | None = Field(default=None, max_length=255)
    interval_days: int | None = Field(default=None, ge=1, le=3650)
    next_due_at: datetime | None = None
    enable: bool | None = None


class CompleteMaintenanceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_uuid: str = Field(..., max_length=26)
    dev_number: str = Field(..., max_length=50)
    note: str | None = Field(default=None, max_length=1000)
