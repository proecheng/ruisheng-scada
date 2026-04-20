"""alarm:fired XREADGROUP consumer。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:
    import redis.asyncio as redis_async

from .ws_manager import WSManager

STREAM_ALARM_FIRED = "stream:alarm:fired"
GROUP_API = "api-alarm-consumer"
DLQ_ALARM = "stream:dlq:alarm"


@dataclass
class AlarmConsumerConfig:
    consumer_name: str
    block_ms: int = 5_000
    batch: int = 20
    max_retries: int = 5


async def ensure_group(r: redis_async.Redis[Any], stream: str, group: str) -> None:
    try:
        await r.xgroup_create(stream, group, id="0", mkstream=True)
    except Exception as e:
        if "BUSYGROUP" not in str(e):
            raise


async def _handle_one(
    r: redis_async.Redis[Any],
    entry: dict[str, Any],
    ws: WSManager,
) -> bool:
    event_id = str(entry.get("event_id") or "")
    if not event_id:
        return False
    ok_ = await r.set(f"alarm_seen:{event_id}", "1", nx=True, ex=86_400)
    if not ok_:
        return True  # duplicate — already processed
    payload: dict[str, object] = {
        "type": "alarm",
        "event_id": int(event_id),
        "dev_number": str(entry.get("dev_number") or ""),
        "alarm_name": str(entry.get("alarm_name") or ""),
        "value": float(entry.get("value") or 0),
        "limit": float(entry.get("limit") or 0),
        "ts": str(entry.get("ts") or ""),
    }
    tenant = str(entry.get("usr_group") or "")
    await ws.broadcast(payload, tenant_filter=tenant or None)
    return True


async def consume_once(
    r: redis_async.Redis[Any],
    cfg: AlarmConsumerConfig,
    ws: WSManager,
) -> int:
    # Claim pending entries from crashed consumers
    claimed = await r.xautoclaim(
        STREAM_ALARM_FIRED,
        GROUP_API,
        cfg.consumer_name,
        min_idle_time=30_000,
        count=cfg.batch,
    )
    consumed = 0
    _, items, _ = claimed
    for msg_id, fields in items:
        try:
            if await _handle_one(r, fields, ws):
                await r.xack(STREAM_ALARM_FIRED, GROUP_API, msg_id)  # type: ignore[no-untyped-call]
            consumed += 1
        except Exception:
            logger.exception("claimed entry handle failed, leaving in PEL")

    # Read new entries
    read = await r.xreadgroup(
        GROUP_API,
        cfg.consumer_name,
        {STREAM_ALARM_FIRED: ">"},
        count=cfg.batch,
        block=cfg.block_ms,
    )
    if not read:
        return consumed
    for _stream, msgs in read:
        for msg_id, fields in msgs:
            try:
                if await _handle_one(r, fields, ws):
                    await r.xack(STREAM_ALARM_FIRED, GROUP_API, msg_id)  # type: ignore[no-untyped-call]
                else:
                    await r.xadd(DLQ_ALARM, fields)
                    await r.xack(STREAM_ALARM_FIRED, GROUP_API, msg_id)  # type: ignore[no-untyped-call]
                consumed += 1
            except Exception:
                logger.exception("alarm handle failed msg_id=%s", msg_id)
    return consumed


async def consumer_loop(
    r: redis_async.Redis[Any],
    cfg: AlarmConsumerConfig,
    ws: WSManager,
    stop_event: asyncio.Event,
) -> None:
    await ensure_group(r, STREAM_ALARM_FIRED, GROUP_API)
    while not stop_event.is_set():
        try:
            await consume_once(r, cfg, ws)
        except Exception:
            logger.exception("consume_once crashed; retry in 2s")
            await asyncio.sleep(2)
