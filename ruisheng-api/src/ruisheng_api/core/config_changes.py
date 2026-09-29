"""Transactional configuration versions and best-effort post-commit notifications."""

from __future__ import annotations

import json
from typing import Any

from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def mark_config_changed(session: AsyncSession, dev_number: str) -> int:
    return int(
        (
            await session.execute(
                text(
                    "UPDATE devices SET update_flag = update_flag + 1, "
                    "is_online = CASE WHEN transport_type = 'serial' THEN false ELSE is_online END, "
                    "last_back_at = CASE WHEN transport_type = 'serial' THEN NULL ELSE last_back_at END "
                    "WHERE dev_number = :dev RETURNING update_flag"
                ),
                {"dev": dev_number},
            )
        ).scalar_one()
    )


async def publish_config_changed(r: Any, dev_number: str, version: int) -> None:
    """Call only after commit; the gateway periodic refresh covers delivery failures."""
    try:
        await r.publish(
            "channel:config:changed",
            json.dumps({"dev_number": dev_number, "version": version}, separators=(",", ":")),
        )
    except Exception:
        logger.exception("config change broadcast failed dev_number={}", dev_number)
