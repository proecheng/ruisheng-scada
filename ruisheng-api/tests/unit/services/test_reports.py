from ruisheng_api.services.reports.aggregator import aggregate_daily
from ruisheng_api.services.reports.excel_export import export_daily_xlsx


def test_aggregate_basic():
    rows = [
        {"dev_number": "d1", "point_id": 1, "rt_value": 10.0, "recorded_at": None},
        {"dev_number": "d1", "point_id": 1, "rt_value": 20.0, "recorded_at": None},
    ]
    agg = aggregate_daily(rows)
    assert agg["d1"][1] == {"count": 2, "min": 10.0, "max": 20.0, "avg": 15.0}


def test_excel_export_returns_xlsx_bytes():
    agg = {"d1": {1: {"count": 2, "min": 10, "max": 20, "avg": 15}}}
    b = export_daily_xlsx(agg, title="t")
    assert b[:2] == b"PK"  # xlsx is a zip file
