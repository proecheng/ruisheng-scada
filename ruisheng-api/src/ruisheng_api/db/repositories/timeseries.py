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
    point_ids: list[int] | None,
    from_ts: datetime,
    to_ts: datetime,
    sample_interval_s: int,
    offset: int,
    limit: int,
) -> list[dict[str, object]]:
    params: dict[str, object] = {
        "d": dev_number,
        "f": from_ts,
        "t": to_ts,
        "s": sample_interval_s,
        "o": offset,
        "l": limit,
    }
    point_filter = ""
    if point_ids:
        point_params = {f"p{i}": point_id for i, point_id in enumerate(point_ids)}
        params.update(point_params)
        point_filter = f"AND point_id IN ({', '.join(f':{name}' for name in point_params)})"
    if sample_interval_s <= 1:
        sql = text(f"""
            SELECT dev_number, point_id, org_value, rt_value, recorded_at
            FROM point_data_history
            WHERE dev_number = :d
              {point_filter}
              AND recorded_at >= :f AND recorded_at < :t
            ORDER BY recorded_at ASC
            OFFSET :o LIMIT :l
        """)
    else:
        sql = text(f"""
            WITH bucketed AS (
                SELECT dev_number, point_id, org_value, rt_value,
                       time_bucket(make_interval(secs => :s), recorded_at) AS bucket_at
                FROM point_data_history
                WHERE dev_number = :d
                  {point_filter}
                  AND recorded_at >= :f AND recorded_at < :t
            )
            SELECT dev_number, point_id,
                   avg(org_value) AS org_value,
                   avg(rt_value)  AS rt_value,
                   bucket_at AS recorded_at
            FROM bucketed
            GROUP BY dev_number, point_id, bucket_at
            ORDER BY bucket_at ASC, point_id ASC
            OFFSET :o LIMIT :l
        """)
    result = await session.execute(sql, params)
    return [dict(row._mapping) for row in result]
