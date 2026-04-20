from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_name: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=6, max_length=200)


class LoginResponseData(BaseModel):
    access_token: str
    refresh_token: str
    access_ttl_sec: int
    user_name: str
    role: str
    usr_group: str
    control_authority: int


class RefreshRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    refresh_token: str


class LogoutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    refresh_token: str | None = None


class OtpSendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: str = Field(..., pattern="^[a-z_]{3,32}$")
    channel: str = Field(default="sms", pattern="^(sms|email|wechat)$")


class SmsSendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: str = Field(..., pattern="^[a-z_]{3,32}$")
    channel: str = Field(default="sms", pattern="^(sms|email|wechat)$")
    phone_number: str = Field(..., pattern=r"^1[3-9][0-9]{9}$")


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_name: str = Field(..., pattern=r"^1[3-9][0-9]{9}$")
    password: str = Field(..., min_length=8, max_length=200)
    otp_code: str = Field(..., pattern=r"^[0-9]{6}$")
