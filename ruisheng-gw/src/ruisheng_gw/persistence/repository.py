"""SQLAlchemy 2.0 async repository — bulk UPSERT realtime + INSERT history.

Plan 1 uses gw BYPASSRLS role (OQ-3 A). All write/read queries must
include `usr_group` filter if the table has one (12 FORCE RLS tables
per spec §3.7) — enforced by CI lint in E8, NOT at runtime here.

point_data_realtime does NOT have usr_group column (see shared ORM);
history same. Tenant isolation is via FK to devices.dev_number + UQ.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from ruisheng_gw.persistence.batch_writer import BatchRow


@dataclass(frozen=True)
class RealtimeRow:
    dev_number: str
    point_id: int
    org_value: float | None
    rt_value: float | None
    recorded_at: datetime


class Repository:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def flush(self, rows: list[BatchRow]) -> None:
        """Bulk UPSERT point_data_realtime + INSERT point_data_history in one tx."""
        if not rows:
            return
        async with self._engine.begin() as conn:
            # UPSERT realtime
            await conn.execute(
                text(  # tenant-lint: OK — table has no usr_group column
                    """
                INSERT INTO point_data_realtime
                    (dev_number, point_id, org_value, rt_value, recorded_at)
                VALUES
                    (:dev_number, :point_id, :org_value, :rt_value, :recorded_at)
                ON CONFLICT (dev_number, point_id) DO UPDATE
                SET org_value = EXCLUDED.org_value,
                    rt_value = EXCLUDED.rt_value,
                    recorded_at = EXCLUDED.recorded_at
                    """
                ),
                [self._to_mapping(r) for r in rows],
            )
            # INSERT history
            await conn.execute(
                text(  # tenant-lint: OK — table has no usr_group column
                    """
                INSERT INTO point_data_history
                    (dev_number, point_id, org_value, rt_value, recorded_at)
                VALUES
                    (:dev_number, :point_id, :org_value, :rt_value, :recorded_at)
                    """
                ),
                [self._to_mapping(r) for r in rows],
            )

    async def fetch_realtime(self, *, dev_number: str) -> list[RealtimeRow]:
        async with self._engine.begin() as conn:
            result = await conn.execute(
                text("""
                SELECT dev_number, point_id, org_value, rt_value, recorded_at
                FROM point_data_realtime
                WHERE dev_number = :dev_number
                ORDER BY point_id
            """),
                {"dev_number": dev_number},
            )
            return [
                RealtimeRow(
                    dev_number=row.dev_number,
                    point_id=row.point_id,
                    org_value=row.org_value,
                    rt_value=row.rt_value,
                    recorded_at=row.recorded_at,
                )
                for row in result
            ]

    @staticmethod
    def _to_mapping(r: BatchRow) -> dict[str, Any]:
        return {
            "dev_number": r.dev_number,
            "point_id": r.point_id,
            "org_value": r.org_value,
            "rt_value": r.rt_value,
            "recorded_at": datetime.fromtimestamp(r.recorded_at, tz=UTC),
        }
