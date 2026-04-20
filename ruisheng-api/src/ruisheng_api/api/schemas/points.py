from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class PointOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="ignore")
    id: int
    dev_number: str
    point_name: str
    user_point_name: str | None
    point_number: int
    fun_code: int
    dev_addr: int
    r_bit: int | None
    value_type: str
    point_unit: str | None
    point_ratio: float
    point_offset: float
    user_ratio: float
    user_point_offset: float
    min_value: float | None
    max_value: float | None
    show: int


class PointCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    point_name: str = Field(..., min_length=1, max_length=100)
    user_point_name: str | None = Field(default=None, max_length=100)
    point_number: int = Field(..., ge=0, le=65535)
    fun_code: int = Field(..., ge=1, le=4)
    dev_addr: int = Field(..., ge=1, le=247)
    r_bit: int | None = None
    value_type: str = Field(..., pattern=r"^(字|双字|bit)$")
    point_unit: str | None = Field(default=None, max_length=20)
    point_ratio: float = 1.0
    point_offset: float = 0.0
    user_ratio: float = 1.0
    user_point_offset: float = 0.0
    min_value: float | None = None
    max_value: float | None = None
    show: int = Field(default=1, ge=0, le=1)


class PointUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_point_name: str | None = None
    point_unit: str | None = None
    point_ratio: float | None = None
    point_offset: float | None = None
    user_ratio: float | None = None
    user_point_offset: float | None = None
    min_value: float | None = None
    max_value: float | None = None
    show: int | None = None
