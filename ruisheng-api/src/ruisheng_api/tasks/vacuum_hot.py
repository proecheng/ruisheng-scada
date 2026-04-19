"""每天 03:00 VACUUM hot tables（gw_db_url BYPASSRLS）。"""

from __future__ import annotations

from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


async def vacuum_hot_tables(factory: async_sessionmaker[AsyncSession]) -> None:
    """VACUUM ANALYZE hot tables. Uses gw BYPASSRLS pool."""
    tables = ["point_data_realtime", "devices", "device_waring_cfgs"]
    async with factory() as session:
        # VACUUM cannot run inside a transaction block — use autocommit mode
        for table in tables:
            try:
                await session.execute(text(f"VACUUM (ANALYZE) {table}"))
                logger.info("vacuumed {}", table)
            except Exception:
                logger.exception("vacuum failed for {}", table)
        await session.commit()
