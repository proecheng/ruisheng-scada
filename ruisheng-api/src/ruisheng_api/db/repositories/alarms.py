from __future__ import annotations

from datetime import datetime

from ruisheng_shared.models.alarms import DeviceWaringCfg
from sqlalchemy import select, text
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession


async def list_cfgs(session: AsyncSession, dev_number: str) -> list[DeviceWaringCfg]:
    stmt = select(DeviceWaringCfg).where(DeviceWaringCfg.dev_number == dev_number)
    return list((await session.execute(stmt)).scalars())


async def get_cfg(session: AsyncSession, cfg_id: int) -> DeviceWaringCfg | None:
    return (
        await session.execute(select(DeviceWaringCfg).where(DeviceWaringCfg.id == cfg_id))
    ).scalar_one_or_none()


async def create_cfg(
    session: AsyncSession, *, dev_number: str, **fields: object
) -> DeviceWaringCfg:
    c = DeviceWaringCfg(dev_number=dev_number, **fields)
    session.add(c)
    await session.flush()
    return c


async def update_cfg(
    session: AsyncSession, cfg: DeviceWaringCfg, updates: dict[str, object]
) -> DeviceWaringCfg:
    for k, v in updates.items():
        setattr(cfg, k, v)
    await session.flush()
    return cfg


async def delete_cfg(session: AsyncSession, cfg: DeviceWaringCfg) -> None:
    await session.delete(cfg)
    await session.flush()


async def list_records(
    session: AsyncSession,
    *,
    dev_number: str | None,
    active_only: bool,
    from_ts: datetime | None,
    to_ts: datetime | None,
    offset: int,
    limit: int,
) -> list[dict[str, object]]:
    sql = text("""
        SELECT id, dev_number, point_id, alarm_name, alarm_msg, alarm_value,
               channels_sent, triggered_at, reset_at, usr_group
        FROM alarm_records
        WHERE (:d IS NULL OR dev_number = :d)
          AND (:active = false OR reset_at IS NULL)
          AND (:f IS NULL OR triggered_at >= :f)
          AND (:t IS NULL OR triggered_at < :t)
        ORDER BY triggered_at DESC
        OFFSET :o LIMIT :l
    """)
    rows = await session.execute(
        sql,
        {
            "d": dev_number,
            "active": active_only,
            "f": from_ts,
            "t": to_ts,
            "o": offset,
            "l": limit,
        },
    )
    return [dict(r._mapping) for r in rows]


async def reset_alarm(session: AsyncSession, alarm_id: int) -> bool:
    sql = text("""
        UPDATE alarm_records
        SET reset_at = now()
        WHERE id = :i AND reset_at IS NULL
    """)
    res: CursorResult[tuple[()]] = await session.execute(sql, {"i": alarm_id})  # type: ignore[assignment]
    return (res.rowcount or 0) > 0
