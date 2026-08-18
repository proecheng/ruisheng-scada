from __future__ import annotations

from datetime import date, timedelta

from pydantic import BaseModel, ConfigDict, Field


class DailyReportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dev_number: str | None = None
    day: date = Field(le=date.max - timedelta(days=1))
    format: str = "json"  # json / xlsx
