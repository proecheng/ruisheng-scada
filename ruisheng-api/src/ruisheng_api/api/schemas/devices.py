from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class DeviceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="ignore")
    id: int
    dev_number: str
    dev_ser_number: str
    dev_name: str | None
    dev_type: str | None
    modbus_addr: int
    baud_rate: int | None
    is_online: bool
    last_call_at: datetime | None
    last_back_at: datetime | None
    loss_count: int
    update_interval_decisec: int
    group_company: str | None
    company: str | None
    department: str | None
    usr_group: str


class DeviceCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dev_number: str = Field(..., pattern=r"^[A-Za-z0-9_\-]{3,50}$")
    dev_ser_number: str = Field(..., min_length=3, max_length=50)
    iccid: str | None = Field(default=None, max_length=50)
    dev_name: str | None = Field(default=None, max_length=100)
    dev_type: str | None = Field(default=None, max_length=50)
    modbus_addr: int = Field(..., ge=1, le=247)
    baud_rate: int | None = Field(default=None)
    update_interval_decisec: int = Field(default=100, ge=10, le=1000)
    group_company: str | None = None
    company: str | None = None
    department: str | None = None


class DeviceUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dev_name: str | None = None
    dev_type: str | None = None
    baud_rate: int | None = None
    update_interval_decisec: int | None = Field(default=None, ge=10, le=1000)
    group_company: str | None = None
    company: str | None = None
    department: str | None = None


class DeviceListQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=50, ge=1, le=500)
    online_only: bool = False
    q: str | None = Field(default=None, max_length=100)
