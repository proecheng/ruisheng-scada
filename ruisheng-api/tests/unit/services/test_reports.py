from datetime import UTC, date, datetime

import pytest
from ruisheng_api.api.reports import daily_report, shanghai_day_bounds
from ruisheng_api.api.schemas.reports import DailyReportRequest
from ruisheng_api.core.rbac import CurrentUser
from ruisheng_api.services.reports.aggregator import aggregate_daily
from ruisheng_api.services.reports.excel_export import export_daily_xlsx


def test_aggregate_basic():
    rows = [
        {"dev_number": "d1", "point_id": 1, "rt_value": 10.0, "recorded_at": None},
        {"dev_number": "d1", "point_id": 1, "rt_value": 20.0, "recorded_at": None},
    ]
    agg = aggregate_daily(rows)
    assert agg["d1"][1] == {"count": 2, "min": 10.0, "max": 20.0, "avg": 15.0}


def test_daily_window_follows_beijing_midnight() -> None:
    start, end = shanghai_day_bounds(datetime(2026, 8, 18).date())
    late_evening = datetime(2026, 8, 18, 15, 59, 59, 999999, tzinfo=UTC)
    next_beijing_day = datetime(2026, 8, 18, 16, 0, tzinfo=UTC)
    previous_tail = datetime(2026, 8, 17, 15, 59, 59, tzinfo=UTC)

    assert start == datetime(2026, 8, 17, 16, 0, tzinfo=UTC)
    assert end == next_beijing_day
    assert start <= late_evening < end
    assert not start <= next_beijing_day < end
    assert not start <= previous_tail < end


class _ReportSession:
    def __init__(self) -> None:
        self.params: dict[str, object] = {}

    def begin(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def execute(
        self, statement: object, params: dict[str, object] | None = None
    ) -> list[object]:
        if params and "s" in params:
            self.params = params
        return []


@pytest.mark.asyncio
async def test_daily_report_queries_the_beijing_window() -> None:
    session = _ReportSession()
    await daily_report(
        DailyReportRequest(day=date(2026, 8, 18), format="json"),
        user=CurrentUser("alice", "tenant", "User", 0, "jti", "fp"),
        session=session,  # type: ignore[arg-type]
    )
    assert session.params["s"] == datetime(2026, 8, 17, 16, 0, tzinfo=UTC)
    assert session.params["e"] == datetime(2026, 8, 18, 16, 0, tzinfo=UTC)


def test_aggregate_skips_null_samples():
    rows = [
        {"dev_number": "d1", "point_id": 1, "rt_value": None, "recorded_at": None},
        {"dev_number": "d1", "point_id": 1, "rt_value": 4.0, "recorded_at": None},
    ]
    assert aggregate_daily(rows)["d1"][1]["count"] == 1


def test_excel_export_returns_xlsx_bytes():
    agg = {"d1": {1: {"count": 2, "min": 10, "max": 20, "avg": 15}}}
    b = export_daily_xlsx(agg, title="t")
    assert b[:2] == b"PK"  # xlsx is a zip file
