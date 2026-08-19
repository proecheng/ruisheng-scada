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
    await session.execute(
        text(
            "UPDATE alarm_notification_subscriptions "
            "SET deleted_at = COALESCE(deleted_at, clock_timestamp()), "
            "updated_at = clock_timestamp() WHERE alarm_cfg_id = :cfg"
        ),
        {"cfg": cfg.id},
    )
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
        SELECT id, alarm_cfg_id AS cfg_id, dev_number, point_id, alarm_name, alarm_msg,
               alarm_value, limit_value, channels_sent, triggered_at, reset_at, usr_group
        FROM alarm_records
        WHERE (CAST(:d AS text) IS NULL OR dev_number = CAST(:d AS text))
          AND (:active = false OR reset_at IS NULL)
          AND (CAST(:f AS timestamptz) IS NULL OR triggered_at >= CAST(:f AS timestamptz))
          AND (CAST(:t AS timestamptz) IS NULL OR triggered_at < CAST(:t AS timestamptz))
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
    return [_public_alarm_record(dict(r._mapping)) for r in rows]


def _public_alarm_record(row: dict[str, object]) -> dict[str, object]:
    raw = row.get("channels_sent")
    visible: dict[str, dict[str, int]] = {}
    if isinstance(raw, dict):
        for channel, counts in raw.items():
            if not isinstance(channel, str) or not isinstance(counts, dict):
                continue
            visible[channel] = {
                key: int(counts.get(key, 0))
                for key in ("sent", "failed", "skipped", "pending")
                if isinstance(counts.get(key, 0), int)
            }
    row["channels_sent"] = visible
    return row


async def reset_alarm(session: AsyncSession, alarm_id: int) -> bool:
    sql = text("""
        WITH target AS (
            SELECT id, triggered_at, alarm_cfg_id
            FROM alarm_records
            WHERE id = :i AND reset_at IS NULL
            ORDER BY triggered_at DESC
            LIMIT 1
        ), locked_cfg AS (
            SELECT c.id, c.waring_flag
            FROM device_waring_cfgs c
            JOIN target t ON t.alarm_cfg_id = c.id
            FOR UPDATE
        ), cfg AS (
            UPDATE device_waring_cfgs c
            SET waring_flag = false
            FROM locked_cfg l
            WHERE c.id = l.id AND l.waring_flag IS true
            RETURNING c.id
        )
        UPDATE alarm_records a
        SET reset_at = clock_timestamp()
        FROM target t
        WHERE a.id = t.id AND a.triggered_at = t.triggered_at AND a.reset_at IS NULL
          AND (
            NOT EXISTS (SELECT 1 FROM locked_cfg)
            OR EXISTS (SELECT 1 FROM cfg)
            OR EXISTS (SELECT 1 FROM locked_cfg WHERE waring_flag IS false)
          )
    """)
    res: CursorResult[tuple[()]] = await session.execute(sql, {"i": alarm_id})  # type: ignore[assignment]
    return (res.rowcount or 0) > 0


async def list_subscriptions(
    session: AsyncSession, cfg_id: int, usr_group: str
) -> list[dict[str, object]]:
    rows = await session.execute(
        text(
            "SELECT id, alarm_cfg_id, user_name, channel, created_at "
            "FROM alarm_notification_subscriptions WHERE alarm_cfg_id = :cfg "
            "AND usr_group = :tenant "
            "AND deleted_at IS NULL "
            "ORDER BY user_name, channel"
        ),
        {"cfg": cfg_id, "tenant": usr_group},
    )
    return [dict(row._mapping) for row in rows]


async def create_subscription(
    session: AsyncSession,
    *,
    cfg_id: int,
    user_name: str,
    channel: str,
    usr_group: str,
) -> dict[str, object]:
    row = (
        (
            await session.execute(
                text(
                    "INSERT INTO alarm_notification_subscriptions "
                    "(alarm_cfg_id, user_name, channel, usr_group) "
                    "VALUES (:cfg, :user, :channel, :tenant) "
                    "ON CONFLICT (alarm_cfg_id, user_name, channel) "
                    "WHERE deleted_at IS NULL DO UPDATE "
                    "SET updated_at = clock_timestamp() "
                    "RETURNING id, alarm_cfg_id, user_name, channel, created_at"
                ),
                {
                    "cfg": cfg_id,
                    "user": user_name,
                    "channel": channel,
                    "tenant": usr_group,
                },
            )
        )
        .mappings()
        .one()
    )
    return dict(row)


async def delete_subscription(
    session: AsyncSession, cfg_id: int, subscription_id: int, usr_group: str
) -> bool:
    result = await session.execute(
        text(
            "UPDATE alarm_notification_subscriptions SET deleted_at = clock_timestamp(), "
            "updated_at = clock_timestamp() WHERE id = :id AND alarm_cfg_id = :cfg "
            "AND usr_group = :tenant "
            "AND deleted_at IS NULL"
        ),
        {"id": subscription_id, "cfg": cfg_id, "tenant": usr_group},
    )
    return (getattr(result, "rowcount", 0) or 0) == 1


async def list_delivery_audit(
    session: AsyncSession, cfg_id: int, usr_group: str, *, limit: int
) -> list[dict[str, object]]:
    rows = await session.execute(
        text(
            "SELECT d.id, d.channel, d.status, d.attempt_count, d.last_error_class, "
            "       d.created_at, d.updated_at, d.sent_at, "
            "       COALESCE((SELECT jsonb_agg(jsonb_build_object("
            "         'attempt_no', a.attempt_no, 'outcome', a.outcome, "
            "         'error_class', a.error_class, 'http_status', a.http_status, "
            "         'retry_after_sec', a.retry_after_sec, 'started_at', a.started_at, "
            "         'finished_at', a.finished_at) ORDER BY a.attempt_no) "
            "         FROM notification_delivery_attempts a "
            "         WHERE a.delivery_id = d.id AND a.usr_group = :tenant), '[]'::jsonb) "
            "       AS attempts "
            "FROM notification_deliveries d "
            "JOIN notification_dispatches p ON p.id = d.dispatch_id "
            "WHERE p.alarm_cfg_id = :cfg AND p.usr_group = :tenant "
            "AND d.usr_group = :tenant ORDER BY d.created_at DESC LIMIT :limit"
        ),
        {"cfg": cfg_id, "tenant": usr_group, "limit": limit},
    )
    return [dict(row._mapping) for row in rows]
