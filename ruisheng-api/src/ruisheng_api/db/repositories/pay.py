from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_PAY_ORDER_COLUMNS = """
    out_trade_no, openid, total_fee, body, pay_state, created_at, updated_at, paid_at, refund_at
"""


async def create_pay_order(
    session: AsyncSession,
    *,
    out_trade_no: str,
    openid: str,
    total_fee: int,
    description: str,
    usr_group: str,
) -> dict[str, object]:
    sql = text(f"""
        INSERT INTO pay_orders (out_trade_no, openid, total_fee, body, usr_group, pay_state)
        VALUES (:o, :oi, :f, :d, :g, 'pending')
        RETURNING {_PAY_ORDER_COLUMNS}
    """)
    result = await session.execute(
        sql,
        {
            "o": out_trade_no,
            "oi": openid,
            "f": total_fee,
            "d": description,
            "g": usr_group,
        },
    )
    row = result.one()
    return dict(row._mapping)


async def list_pay_orders(
    session: AsyncSession,
    *,
    openid: str | None = None,
    status: str | None = None,
    limit: int = 100,
) -> list[dict[str, object]]:
    sql = text(f"""
        SELECT {_PAY_ORDER_COLUMNS}
        FROM pay_orders
        WHERE deleted_at IS NULL
          AND (CAST(:openid AS text) IS NULL OR openid = CAST(:openid AS text))
          AND (CAST(:status AS text) IS NULL OR pay_state = CAST(:status AS text))
        ORDER BY created_at DESC
        LIMIT :limit
    """)
    result = await session.execute(
        sql,
        {
            "openid": openid,
            "status": status,
            "limit": limit,
        },
    )
    return [dict(row._mapping) for row in result]


async def get_pay_order(session: AsyncSession, out_trade_no: str) -> dict[str, object] | None:
    sql = text(f"""
        SELECT {_PAY_ORDER_COLUMNS}
        FROM pay_orders
        WHERE out_trade_no = :out_trade_no
          AND deleted_at IS NULL
    """)
    result = await session.execute(sql, {"out_trade_no": out_trade_no})
    row = result.one_or_none()
    return dict(row._mapping) if row is not None else None
