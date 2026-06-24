"""定时扫描超时 pay_orders（每 5 min）。"""

from __future__ import annotations

from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


async def expire_stale_pay_orders(factory: async_sessionmaker[AsyncSession]) -> None:
    """Mark pay_orders as expired if pending > 2 hours. Uses gw BYPASSRLS pool."""
    async with factory() as session, session.begin():
        result = await session.execute(
            text(
                "UPDATE pay_orders SET pay_state = 'expired' "
                "WHERE pay_state = 'pending' "
                "AND created_at < now() - INTERVAL '2 hours' "
                "RETURNING out_trade_no"
            )
        )
        count = len(result.fetchall())
    if count:
        logger.info("expired {} stale pay_orders", count)
