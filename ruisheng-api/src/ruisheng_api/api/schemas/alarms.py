from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

NotificationChannel = str


class AlarmCfgOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="ignore")
    id: int
    dev_number: str
    point_id: int
    reg_bit: int | None
    alarm_name: str
    alarm_type: str
    limit_value: float = Field(allow_inf_nan=False)
    relation_point_id: int | None
    relation_reg_bit: int | None
    relation_alarm_type: str | None
    relation_limit_value: float | None
    enable: bool
    phone_alarm: int
    reset_remind: bool
    waring_flag: bool
    alarm_msg: str | None


class AlarmCfgCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    point_id: int
    reg_bit: int | None = Field(default=None, ge=0, le=15)
    alarm_name: str = Field(..., max_length=100)
    alarm_type: str = Field(..., pattern=r"^(>|<|=|!=|LX)$")
    limit_value: float = Field(allow_inf_nan=False)
    relation_point_id: int | None = None
    relation_reg_bit: int | None = Field(default=None, ge=0, le=15)
    relation_alarm_type: str | None = Field(default=None, pattern=r"^(>|<|=|!=|LX)$")
    relation_limit_value: float | None = Field(default=None, allow_inf_nan=False)
    enable: bool = True
    phone_alarm: int = Field(default=0, ge=0)
    reset_remind: bool = False
    alarm_msg: str | None = Field(default=None, max_length=255)


class AlarmCfgUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    alarm_name: str | None = None
    alarm_type: str | None = Field(default=None, pattern=r"^(>|<|=|!=|LX)$")
    limit_value: float | None = Field(default=None, allow_inf_nan=False)
    relation_point_id: int | None = None
    relation_reg_bit: int | None = Field(default=None, ge=0, le=15)
    relation_alarm_type: str | None = Field(default=None, pattern=r"^(>|<|=|!=|LX)$")
    relation_limit_value: float | None = Field(default=None, allow_inf_nan=False)
    enable: bool | None = None
    phone_alarm: int | None = None
    reset_remind: bool | None = None
    alarm_msg: str | None = None


class AlarmRecordOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="ignore")
    id: int
    dev_number: str
    point_id: int | None
    alarm_name: str | None
    alarm_msg: str | None
    alarm_value: float | None
    channels_sent: dict[str, object]
    triggered_at: datetime
    reset_at: datetime | None


class AlarmSubscriptionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_name: str = Field(min_length=1, max_length=50)
    channel: str = Field(pattern=r"^(wechat|email|sms_custom_http|voice_custom_http)$")
