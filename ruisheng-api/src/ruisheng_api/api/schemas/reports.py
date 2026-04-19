from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict


class DailyReportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dev_number: str | None = None
    day: date
    format: str = "json"  # json / xlsx
