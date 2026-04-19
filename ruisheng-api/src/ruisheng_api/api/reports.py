"""Reports API：/api/reports/daily。"""

from __future__ import annotations

from datetime import UTC, datetime, time

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.rbac import CurrentUser
from ..core.response import ok
from ..core.tenant import apply_tenant_context
from ..deps import get_current_user, get_session
from ..services.reports.aggregator import aggregate_daily
from ..services.reports.excel_export import export_daily_xlsx
from .schemas.reports import DailyReportRequest

router = APIRouter(prefix="/api/reports", tags=["reports"])


@router.post("/daily")
async def daily_report(
    body: DailyReportRequest,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> object:
    start = datetime.combine(body.day, time.min, tzinfo=UTC)
    end = datetime.combine(body.day, time.max, tzinfo=UTC)
    async with session.begin():
        await apply_tenant_context(session, usr_group=user.usr_group, role=user.role)
        rows_q = text("""
            SELECT dev_number, point_id, rt_value, recorded_at
            FROM point_data_history
            WHERE recorded_at >= :s AND recorded_at < :e
              AND (:d IS NULL OR dev_number = :d)
        """)
        result = await session.execute(
            rows_q,
            {
                "s": start,
                "e": end,
                "d": body.dev_number,
            },
        )
        rows = [dict(r._mapping) for r in result]
    agg = aggregate_daily(rows)
    if body.format == "xlsx":
        content = export_daily_xlsx(agg, title=f"daily_{body.day}")
        return Response(
            content=content,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="daily_{body.day}.xlsx"'},
        )
    return ok(data=agg).model_dump()
