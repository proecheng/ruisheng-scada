"""定时清理 30 天前的 pay_orders_seen（每天 02:00）。"""

from __future__ import annotations

from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


async def cleanup_old_pay_seen(factory: async_sessionmaker[AsyncSession]) -> None:
    """Delete pay_orders_seen older than 30 days. Uses gw BYPASSRLS pool."""
    async with factory() as session, session.begin():
        result = await session.execute(
            text(
                "DELETE FROM pay_orders_seen "
                "WHERE notified_at < now() - INTERVAL '30 days' "
                "RETURNING id"
            )
        )
        count = len(result.fetchall())
    if count:
        logger.info("cleaned up {} old pay_orders_seen", count)
