from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ReadFunCode = Literal[1, 2, 3, 4]
PointValueType = Literal["字", "双字", "bit"]


def validate_point_contract(
    *,
    fun_code: int,
    value_type: str,
    r_bit: int | None,
    min_value: float | None,
    max_value: float | None,
) -> None:
    if fun_code in (1, 2):
        if value_type != "bit":
            raise ValueError("FC1/FC2 only support bit value_type")
        if r_bit is not None:
            raise ValueError("r_bit must be omitted for FC1/FC2")
    elif value_type == "bit":
        if r_bit is None:
            raise ValueError("r_bit is required for register bit points")
    elif r_bit is not None:
        raise ValueError("r_bit is only valid for bit points")

    if value_type == "双字" and fun_code not in (3, 4):
        raise ValueError("double-word points require FC3 or FC4")
    if min_value is not None and max_value is not None and min_value > max_value:
        raise ValueError("min_value must be <= max_value")


class PointOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="ignore")
    id: int
    dev_number: str
    point_name: str
    user_point_name: str | None
    point_number: int
    fun_code: ReadFunCode
    dev_addr: int
    r_bit: int | None
    value_type: PointValueType
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
    fun_code: ReadFunCode
    dev_addr: int = Field(..., ge=1, le=247)
    r_bit: int | None = Field(default=None, ge=0, le=15)
    value_type: PointValueType
    point_unit: str | None = Field(default=None, max_length=20)
    point_ratio: float = 1.0
    point_offset: float = 0.0
    user_ratio: float = 1.0
    user_point_offset: float = 0.0
    min_value: float | None = None
    max_value: float | None = None
    show: int = Field(default=1, ge=0, le=1)

    @model_validator(mode="after")
    def _validate_point(self) -> PointCreateRequest:
        validate_point_contract(
            fun_code=self.fun_code,
            value_type=self.value_type,
            r_bit=self.r_bit,
            min_value=self.min_value,
            max_value=self.max_value,
        )
        return self


class PointUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    point_name: str | None = Field(default=None, min_length=1, max_length=100)
    user_point_name: str | None = None
    point_number: int | None = Field(default=None, ge=0, le=65535)
    fun_code: ReadFunCode | None = None
    dev_addr: int | None = Field(default=None, ge=1, le=247)
    r_bit: int | None = Field(default=None, ge=0, le=15)
    value_type: PointValueType | None = None
    point_unit: str | None = None
    point_ratio: float | None = None
    point_offset: float | None = None
    user_ratio: float | None = None
    user_point_offset: float | None = None
    min_value: float | None = None
    max_value: float | None = None
    show: int | None = None
