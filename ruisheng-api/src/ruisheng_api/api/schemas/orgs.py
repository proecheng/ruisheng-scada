from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="ignore")
    id: int
    user_name: str
    authority: str
    control_authority: int
    group_company: str | None
    company: str | None
    department: str | None
    usr_group: str


class UserCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_name: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=8, max_length=200)
    authority: str = Field(..., pattern=r"^(User|Company|GroupCompany|Administrators)$")
    control_authority: int = Field(default=0, ge=0, le=255)
    group_company: str | None = None
    company: str | None = None
    department: str | None = None


class UserUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    authority: str | None = Field(
        default=None, pattern=r"^(User|Company|GroupCompany|Administrators)$"
    )
    control_authority: int | None = Field(default=None, ge=0, le=255)
    group_company: str | None = None
    company: str | None = None
    department: str | None = None


class WxGroupOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="ignore")
    usr_group: str
    company_name: str | None
    sys_title: str | None
