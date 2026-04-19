from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def create_pay_order(
    session: AsyncSession,
    *,
    out_trade_no: str,
    openid: str,
    total_fee: int,
    description: str,
    usr_group: str,
) -> dict[str, object]:
    sql = text("""
        INSERT INTO pay_orders (out_trade_no, openid, total_fee, body, usr_group, pay_state)
        VALUES (:o, :oi, :f, :d, :g, 'pending')
        RETURNING out_trade_no, pay_state
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
