from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ReadFunCode = Literal[1, 2, 3, 4]
PointValueType = Literal["字", "双字", "有符号字节", "无符号字节", "bit"]
ZERO_ORIGIN_REGISTER_COUNT = 38
HOLDING_REGISTER_FUNCTION = 3


def validate_point_read_profile(
    read_profile: str, *, fun_code: int, point_number: int, value_type: str
) -> None:
    if read_profile != "zero_origin_38":
        return
    span = 2 if value_type == "双字" else 1
    if (
        fun_code != HOLDING_REGISTER_FUNCTION
        or not 0 <= point_number <= ZERO_ORIGIN_REGISTER_COUNT - span
    ):
        raise ValueError("zero_origin_38 points require FC3 and register span within 0..37")


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
    display_bits: int | None = None
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
    display_bits: int | None = Field(default=None, ge=1, le=16)
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
        validate_point_display(
            display_bits=self.display_bits,
            fun_code=self.fun_code,
            value_type=self.value_type,
            point_ratio=self.point_ratio,
            point_offset=self.point_offset,
            user_ratio=self.user_ratio,
            user_point_offset=self.user_point_offset,
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
    display_bits: int | None = Field(default=None, ge=1, le=16)
    point_unit: str | None = None
    point_ratio: float | None = None
    point_offset: float | None = None
    user_ratio: float | None = None
    user_point_offset: float | None = None
    min_value: float | None = None
    max_value: float | None = None
    show: int | None = None


def validate_point_display(
    *,
    display_bits: int | None,
    fun_code: int,
    value_type: str,
    point_ratio: float,
    point_offset: float,
    user_ratio: float,
    user_point_offset: float,
) -> None:
    if display_bits is None:
        return
    if fun_code not in (3, 4) or value_type != "字":
        raise ValueError("多位二进制显示需要 FC3/FC4 整字采集，不能使用单独 bit")
    if (point_ratio, point_offset, user_ratio, user_point_offset) != (1, 0, 1, 0):
        raise ValueError("多位二进制显示的原始/显示倍率必须为1，偏移必须为0")
