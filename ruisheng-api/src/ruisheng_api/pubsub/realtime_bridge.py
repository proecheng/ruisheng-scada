"""订阅 channel:realtime:* → 广播给 WS。"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:
    import redis.asyncio as redis_async

from .ws_manager import WSManager


async def realtime_loop(
    r: redis_async.Redis[Any],
    ws: WSManager,
    stop_event: asyncio.Event,
) -> None:
    pubsub = r.pubsub()
    await pubsub.psubscribe("channel:realtime:*")
    try:
        while not stop_event.is_set():
            msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if msg is None:
                continue
            try:
                data = json.loads(msg["data"])
            except (TypeError, ValueError):
                logger.warning("realtime malformed: {!r}", msg)
                continue
            payload: dict[str, object] = {
                "type": "realtime",
                "dev_number": str(data.get("dev_number") or ""),
                "point_id": int(data.get("point_id") or 0),
                "value": float(data.get("value") or 0),
                "ts": str(data.get("ts") or ""),
            }
            usr_group = data.get("usr_group")
            await ws.broadcast(
                payload, tenant_filter=usr_group if isinstance(usr_group, str) else None
            )
    finally:
        await pubsub.punsubscribe("channel:realtime:*")
        await pubsub.close()
