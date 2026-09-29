from __future__ import annotations

from datetime import UTC, datetime
from ipaddress import IPv4Address, IPv6Address
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from ruisheng_shared.device_status import SERIAL_LOSS_LIMIT, serial_response_is_recent

ReadProfile = Literal["point_groups", "zero_origin_38"]


def validate_device_transport(
    *, transport_type: str, serial_port: str | None, read_profile: str
) -> None:
    if transport_type == "serial":
        if serial_port is None or not serial_port.strip():
            raise ValueError("serial_port is required for serial devices")
    elif serial_port is not None:
        raise ValueError("serial_port must be omitted for tcp devices")
    if read_profile == "zero_origin_38" and transport_type != "serial":
        raise ValueError("zero_origin_38 requires serial transport")


class DeviceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="ignore")
    id: int
    dev_number: str
    dev_ser_number: str
    dev_name: str | None
    dev_type: str | None
    transport_type: Literal["tcp", "serial"]
    serial_port: str | None
    read_profile: ReadProfile = "point_groups"
    dev_ip: IPv4Address | IPv6Address | None
    modbus_addr: int
    baud_rate: int | None
    is_enabled: bool
    is_online: bool
    last_call_at: datetime | None
    last_back_at: datetime | None
    loss_count: int
    update_interval_decisec: int
    group_company: str | None
    company: str | None
    department: str | None
    usr_group: str

    @model_validator(mode="after")
    def _expire_serial_status(self) -> DeviceOut:
        if self.transport_type == "serial":
            self.is_online = bool(
                self.is_online
                and self.is_enabled
                and self.loss_count < SERIAL_LOSS_LIMIT
                and self.last_back_at is not None
                and serial_response_is_recent(
                    last_back=self.last_back_at.timestamp(),
                    now=datetime.now(UTC).timestamp(),
                    interval_decisec=self.update_interval_decisec,
                )
            )
        return self


class DeviceCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dev_number: str = Field(..., pattern=r"^[A-Za-z0-9_\-]{3,50}$")
    dev_ser_number: str = Field(..., min_length=3, max_length=50)
    iccid: str | None = Field(default=None, max_length=50)
    dev_name: str | None = Field(default=None, max_length=100)
    dev_type: str | None = Field(default=None, max_length=50)
    transport_type: Literal["tcp", "serial"] = "tcp"
    serial_port: str | None = Field(default=None, min_length=1, max_length=50)
    read_profile: ReadProfile = "point_groups"
    dev_ip: IPv4Address | IPv6Address | None = None
    modbus_addr: int = Field(..., ge=1, le=247)
    baud_rate: int | None = Field(default=None)
    update_interval_decisec: int = Field(default=100, ge=10, le=1000)
    group_company: str | None = None
    company: str | None = None
    department: str | None = None

    @model_validator(mode="after")
    def _validate_transport(self) -> DeviceCreateRequest:
        validate_device_transport(
            transport_type=self.transport_type,
            serial_port=self.serial_port,
            read_profile=self.read_profile,
        )
        if self.serial_port is not None:
            self.serial_port = self.serial_port.strip()
        return self


class DeviceUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dev_name: str | None = None
    dev_type: str | None = None
    transport_type: Literal["tcp", "serial"] | None = None
    serial_port: str | None = Field(default=None, min_length=1, max_length=50)
    read_profile: ReadProfile | None = None
    dev_ip: IPv4Address | IPv6Address | None = None
    modbus_addr: int | None = Field(default=None, ge=1, le=247)
    baud_rate: int | None = None
    update_interval_decisec: int | None = Field(default=None, ge=10, le=1000)
    is_enabled: bool | None = None
    group_company: str | None = None
    company: str | None = None
    department: str | None = None

    @model_validator(mode="after")
    def _validate_transport(self) -> DeviceUpdateRequest:
        required = {
            "transport_type",
            "read_profile",
            "modbus_addr",
            "update_interval_decisec",
            "is_enabled",
        }
        if any(getattr(self, name) is None for name in required & self.model_fields_set):
            raise ValueError("required device fields cannot be null")
        if self.serial_port is not None:
            if not self.serial_port.strip():
                raise ValueError("serial_port cannot be blank")
            self.serial_port = self.serial_port.strip()
        if self.transport_type == "tcp" and self.serial_port is not None:
            raise ValueError("serial_port must be omitted when switching to tcp")
        return self


class DeviceEnabledRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    is_enabled: bool


class DeviceListQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=50, ge=1, le=500)
    online_only: bool = False
    q: str | None = Field(default=None, max_length=100)
