"""订阅 channel:realtime:* → 广播给 WS。"""

from __future__ import annotations

import asyncio
import json
import math
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:
    import redis.asyncio as redis_async

from .ws_manager import WSManager


def realtime_payload(data: Any) -> dict[str, object]:
    """Translate the gateway v1 event to the browser contract without inventing data."""
    if not isinstance(data, dict):
        raise ValueError("realtime event must be an object")
    dev_number, point_id = data.get("dev_number"), data.get("point_id")
    if not isinstance(dev_number, str) or not dev_number.strip():
        raise ValueError("device number is required")
    if type(point_id) is not int or point_id <= 0:
        raise ValueError("point id must be a positive integer")
    if "schema_version" in data:
        if type(data["schema_version"]) is not int or data["schema_version"] != 1:
            raise ValueError("unsupported realtime schema")
        value = data["rt_value"]
        recorded_at = data["recorded_at"]
        if type(recorded_at) not in (int, float) or not math.isfinite(recorded_at):
            raise ValueError("sample time must be finite epoch seconds")
        timestamp = datetime.fromtimestamp(recorded_at, tz=UTC)
    else:
        # Keep the original unversioned API producers compatible.
        value = data["value"]
        timestamp = datetime.fromisoformat(data["ts"])
        if timestamp.tzinfo is None:
            raise ValueError("sample time must include timezone")
    if value is not None and (type(value) not in (int, float) or not math.isfinite(value)):
        raise ValueError("sample value must be finite or null")
    return {
        "type": "realtime",
        "dev_number": dev_number,
        "point_id": point_id,
        "value": value,
        "ts": timestamp.astimezone(UTC).isoformat(),
    }


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
                payload = realtime_payload(data)
            except (KeyError, TypeError, ValueError, OverflowError, OSError):
                logger.warning("Skipping malformed realtime event")
                continue
            usr_group = data.get("usr_group")
            await ws.broadcast(
                payload, tenant_filter=usr_group if isinstance(usr_group, str) else None
            )
    finally:
        await pubsub.punsubscribe("channel:realtime:*")
        await pubsub.aclose()  # type: ignore[attr-defined]
