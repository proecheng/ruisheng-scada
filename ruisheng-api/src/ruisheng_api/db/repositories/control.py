"""control 仓储。"""

from __future__ import annotations

import json

from sqlalchemy import text
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession


async def insert_action(
    session: AsyncSession,
    *,
    dev_number: str,
    user_name: str,
    cmd_id: str,
    action: dict[str, object],
    usr_group: str,
) -> int:
    sql = text("""
        INSERT INTO user_control_actions (dev_number, user_name, action, cmd_id, result, usr_group)
        VALUES (:d, :u, CAST(:a AS JSONB), :c, 'pending', :g)
        RETURNING id
    """)
    row = await session.execute(
        sql,
        {
            "d": dev_number,
            "u": user_name,
            "a": json.dumps(action),
            "c": cmd_id,
            "g": usr_group,
        },
    )
    return int(row.scalar_one())


async def check_recent_duplicate(
    session: AsyncSession,
    *,
    user_name: str,
    dev_number: str,
    action_key: str,
    seconds: int = 5,
) -> bool:
    sql = text("""
        SELECT 1 FROM user_control_actions
        WHERE user_name = :u AND dev_number = :d
          AND action->>'action_key' = :k
          AND acted_at > now() - make_interval(secs => :s)
        LIMIT 1
    """)
    return (
        await session.execute(
            sql,
            {
                "u": user_name,
                "d": dev_number,
                "k": action_key,
                "s": seconds,
            },
        )
    ).scalar_one_or_none() is not None


async def list_actions(
    session: AsyncSession,
    *,
    user_name: str | None,
    dev_number: str | None,
    offset: int,
    limit: int,
) -> list[dict[str, object]]:
    sql = text("""
        SELECT id, dev_number, user_name, action, cmd_id, result,
               acted_at, completed_at, usr_group
        FROM user_control_actions
        WHERE (:u IS NULL OR user_name = :u)
          AND (:d IS NULL OR dev_number = :d)
        ORDER BY acted_at DESC
        OFFSET :o LIMIT :l
    """)
    rows = await session.execute(
        sql,
        {
            "u": user_name,
            "d": dev_number,
            "o": offset,
            "l": limit,
        },
    )
    return [dict(r._mapping) for r in rows]


async def cancel_action(session: AsyncSession, cmd_id: str) -> bool:
    sql = text("""
        UPDATE user_control_actions
        SET result = 'cancelled', completed_at = now()
        WHERE cmd_id = :c AND result = 'pending'
    """)
    res: CursorResult[tuple[()]] = await session.execute(sql, {"c": cmd_id})  # type: ignore[assignment]
    return (res.rowcount or 0) > 0
