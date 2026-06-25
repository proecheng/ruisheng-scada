from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .points import PointCreateRequest


class DeviceTemplatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    points: list[PointCreateRequest] = Field(default_factory=list)


class DeviceTemplateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="ignore")
    id: int
    name: str
    dev_type: str | None
    payload: dict[str, Any]


class DeviceTemplateCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(..., min_length=1, max_length=100)
    dev_type: str | None = Field(default=None, max_length=50)
    payload: DeviceTemplatePayload = Field(default_factory=DeviceTemplatePayload)


class DeviceTemplateUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=100)
    dev_type: str | None = Field(default=None, max_length=50)
    payload: DeviceTemplatePayload | None = None
