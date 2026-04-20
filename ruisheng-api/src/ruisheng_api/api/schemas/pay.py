from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class CreateOrderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    openid: str = Field(..., min_length=5)
    amount_fen: int = Field(..., ge=1)  # in cents (fen)
    description: str = Field(..., min_length=1, max_length=200)
    usr_group: str | None = None  # if not provided, use user's usr_group
