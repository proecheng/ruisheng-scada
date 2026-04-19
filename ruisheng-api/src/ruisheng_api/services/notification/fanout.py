"""并发 fan-out 到所有通知器 + 持久化 channels_sent。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

from .base import AlarmNotification, INotifier


async def fan_out(
    alarm: AlarmNotification,
    notifiers: Mapping[str, INotifier],
) -> dict[str, bool]:
    """并发调所有 notifier.send()。异常视为 False。返回 {name: success}。"""
    if not notifiers:
        return {}
    names = list(notifiers)
    results = await asyncio.gather(
        *(notifiers[n].send(alarm) for n in names), return_exceptions=True
    )
    out: dict[str, bool] = {}
    for name, r in zip(names, results, strict=False):
        if isinstance(r, Exception):
            logger.bind(notifier=name, trace_id=alarm.trace_id).exception("fanout exc")
            out[name] = False
        else:
            out[name] = bool(r)
    return out


async def persist_channels_sent(
    session: AsyncSession,
    alarm_id: int,
    channels: dict[str, bool],
) -> None:
    from sqlalchemy import text as sa_text

    await session.execute(
        sa_text("UPDATE alarm_records SET channels_sent = CAST(:c AS JSONB) WHERE id = :i"),
        {"c": json.dumps(channels), "i": alarm_id},
    )
