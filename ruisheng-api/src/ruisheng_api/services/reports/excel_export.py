"""openpyxl 导出日报为 xlsx。"""

from __future__ import annotations

from io import BytesIO

from openpyxl import Workbook  # type: ignore[import-untyped]


def export_daily_xlsx(agg: dict[str, dict[int, dict[str, object]]], *, title: str) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = title[:30]
    ws.append(["dev_number", "point_id", "count", "min", "max", "avg"])
    for dev, pts in agg.items():
        for pid, stat in pts.items():
            ws.append([dev, pid, stat["count"], stat["min"], stat["max"], stat["avg"]])
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
