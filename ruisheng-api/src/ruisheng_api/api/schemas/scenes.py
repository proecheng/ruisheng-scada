from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class ScenePageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="ignore")

    id: int
    owner_user_name: str
    page_name: str
    sonpage_name: str | None
    sonpage_pic: str | None
    pos_x: Decimal
    pos_y: Decimal
    radius: Decimal
    usr_group: str
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ScenePageCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page_name: str = Field(..., min_length=1, max_length=100)
    owner_user_name: str | None = Field(default=None, max_length=50)
    sonpage_name: str | None = Field(default=None, max_length=100)
    sonpage_pic: str | None = Field(default=None, max_length=500)
    pos_x: Decimal = Field(..., ge=-1_000_000, le=1_000_000)
    pos_y: Decimal = Field(..., ge=-1_000_000, le=1_000_000)
    radius: Decimal = Field(..., ge=Decimal("0.01"), le=100_000)


class SceneViewOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="ignore")

    id: int
    scene_page_id: int
    owner_user_name: str
    company: str | None
    department: str | None
    dev_number: str
    pos_x: Decimal
    pos_y: Decimal
    radius: Decimal
    usr_group: str
    created_at: datetime | None = None
    updated_at: datetime | None = None


class SceneViewCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dev_number: str = Field(..., min_length=1, max_length=50)
    owner_user_name: str | None = Field(default=None, max_length=50)
    pos_x: Decimal = Field(..., ge=-1_000_000, le=1_000_000)
    pos_y: Decimal = Field(..., ge=-1_000_000, le=1_000_000)
    radius: Decimal = Field(..., ge=Decimal("0.01"), le=100_000)
