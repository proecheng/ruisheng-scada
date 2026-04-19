"""时序数据仓储（realtime + history）。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

MAX_HISTORY_ROWS = 5000
SAMPLE_TIERS: tuple[tuple[int, int], ...] = (
    (86_400, 1),  # <=1d → raw
    (86_400 * 7, 300),  # <=7d → 5min
    (86_400 * 30, 3600),  # <=30d → 1h
    (10**9, 86_400),  # >30d → 1d
)


def pick_sample_interval(duration_sec: int) -> int:
    for threshold, interval in SAMPLE_TIERS:
        if duration_sec <= threshold:
            return interval
    return 86_400


async def load_realtime(session: AsyncSession, dev_number: str) -> list[dict[str, object]]:
    stmt = text("""
        SELECT r.dev_number, r.point_id, r.org_value, r.rt_value, r.recorded_at,
               p.point_name, p.user_point_name, p.point_unit
        FROM point_data_realtime r
        LEFT JOIN device_points p ON p.id = r.point_id
        WHERE r.dev_number = :d
        ORDER BY r.point_id
    """)
    result = await session.execute(stmt, {"d": dev_number})
    return [dict(row._mapping) for row in result]


async def load_history(
    session: AsyncSession,
    *,
    dev_number: str,
    point_id: int | None,
    from_ts: datetime,
    to_ts: datetime,
    sample_interval_s: int,
    offset: int,
    limit: int,
) -> list[dict[str, object]]:
    if sample_interval_s <= 1:
        sql = text("""
            SELECT dev_number, point_id, org_value, rt_value, recorded_at
            FROM point_data_history
            WHERE dev_number = :d
              AND (:p IS NULL OR point_id = :p)
              AND recorded_at >= :f AND recorded_at < :t
            ORDER BY recorded_at ASC
            OFFSET :o LIMIT :l
        """)
    else:
        sql = text("""
            SELECT dev_number, point_id,
                   avg(org_value) AS org_value,
                   avg(rt_value)  AS rt_value,
                   time_bucket(make_interval(secs => :s), recorded_at) AS recorded_at
            FROM point_data_history
            WHERE dev_number = :d
              AND (:p IS NULL OR point_id = :p)
              AND recorded_at >= :f AND recorded_at < :t
            GROUP BY dev_number, point_id, recorded_at
            ORDER BY recorded_at ASC
            OFFSET :o LIMIT :l
        """)
    result = await session.execute(
        sql,
        {
            "d": dev_number,
            "p": point_id,
            "f": from_ts,
            "t": to_ts,
            "s": sample_interval_s,
            "o": offset,
            "l": limit,
        },
    )
    return [dict(row._mapping) for row in result]
