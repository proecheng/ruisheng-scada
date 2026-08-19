"""alarm:fired XREADGROUP consumer。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:
    import redis.asyncio as redis_async
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..services.notification.runtime import (
    MaterializationError,
    NotificationMetrics,
    materialize_event,
    parse_alarm_event,
)
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
    max_event_age_sec: int = 7 * 24 * 3600


@dataclass(frozen=True)
class HandledAlarm:
    tenant: str
    broadcast_payload: dict[str, object] | None


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
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    fingerprint_secret: str = "",
    provider_enabled: dict[str, bool] | None = None,
    max_event_age_sec: int = 7 * 24 * 3600,
) -> HandledAlarm | None:
    if session_factory is None:
        return None
    event = parse_alarm_event(entry)
    result = await materialize_event(
        session_factory,
        event,
        fingerprint_secret=fingerprint_secret,
        provider_enabled=provider_enabled or {},
        max_event_age_sec=max_event_age_sec,
    )
    payload: dict[str, object] = {
        "type": "alarm",
        "event_id": event.event_id,
        "dev_number": event.dev_number,
        "alarm_name": result.alarm_name,
        "value": event.value,
        "limit": event.limit,
        "ts": event.triggered_at.isoformat(),
    }
    return HandledAlarm(
        tenant=result.usr_group,
        broadcast_payload=payload if result.created else None,
    )


async def _broadcast_after_ack(ws: WSManager, handled: HandledAlarm) -> None:
    if handled.broadcast_payload is None:
        return
    try:
        await ws.broadcast(handled.broadcast_payload, tenant_filter=handled.tenant)
    except Exception:
        logger.exception("alarm websocket broadcast failed after ack")


async def _pending_delivery_count(r: redis_async.Redis[Any], msg_id: str) -> int:
    rows = await r.xpending_range(
        STREAM_ALARM_FIRED,
        GROUP_API,
        min=msg_id,
        max=msg_id,
        count=1,
    )
    if not rows:
        return 1
    row = rows[0]
    if isinstance(row, dict):
        return int(row.get("times_delivered", 1))
    return int(getattr(row, "times_delivered", 1))


async def _ack_and_delete(r: redis_async.Redis[Any], msg_id: str) -> None:
    async with r.pipeline(transaction=True) as pipe:
        pipe.xack(STREAM_ALARM_FIRED, GROUP_API, msg_id)
        pipe.xdel(STREAM_ALARM_FIRED, msg_id)
        await pipe.execute()


async def _handle_failure(
    r: redis_async.Redis[Any],
    cfg: AlarmConsumerConfig,
    msg_id: str,
    *,
    reason: str,
) -> bool:
    if await _pending_delivery_count(r, msg_id) < cfg.max_retries:
        return False
    await r.xadd(
        DLQ_ALARM,
        {"reason": reason[:120], "message_id": str(msg_id)},
    )
    await _ack_and_delete(r, msg_id)
    return True


async def consume_once(  # noqa: PLR0912
    r: redis_async.Redis[Any],
    cfg: AlarmConsumerConfig,
    ws: WSManager,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    fingerprint_secret: str = "",
    provider_enabled: dict[str, bool] | None = None,
    metrics: NotificationMetrics | None = None,
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
            handled = await _handle_one(
                r,
                fields,
                ws,
                session_factory,
                fingerprint_secret,
                provider_enabled,
                cfg.max_event_age_sec,
            )
            if handled is not None:
                await _ack_and_delete(r, msg_id)
                await _broadcast_after_ack(ws, handled)
            elif not await _handle_failure(
                r,
                cfg,
                msg_id,
                reason="materializer_unavailable",
            ):
                continue
            consumed += 1
        except MaterializationError as exc:
            if metrics is not None:
                metrics.materialize_failures += 1
            if await _handle_failure(r, cfg, msg_id, reason=str(exc)):
                consumed += 1
        except Exception:
            if metrics is not None:
                metrics.materialize_failures += 1
            logger.exception("claimed entry handle failed")
            if await _handle_failure(r, cfg, msg_id, reason="processing_failed"):
                consumed += 1

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
                handled = await _handle_one(
                    r,
                    fields,
                    ws,
                    session_factory,
                    fingerprint_secret,
                    provider_enabled,
                    cfg.max_event_age_sec,
                )
                if handled is not None:
                    await _ack_and_delete(r, msg_id)
                    await _broadcast_after_ack(ws, handled)
                elif not await _handle_failure(
                    r,
                    cfg,
                    msg_id,
                    reason="materializer_unavailable",
                ):
                    continue
                consumed += 1
            except MaterializationError as exc:
                if metrics is not None:
                    metrics.materialize_failures += 1
                if await _handle_failure(r, cfg, msg_id, reason=str(exc)):
                    consumed += 1
            except Exception:
                if metrics is not None:
                    metrics.materialize_failures += 1
                logger.exception("alarm handle failed msg_id=%s", msg_id)
                if await _handle_failure(r, cfg, msg_id, reason="processing_failed"):
                    consumed += 1
    return consumed


async def consumer_loop(
    r: redis_async.Redis[Any],
    cfg: AlarmConsumerConfig,
    ws: WSManager,
    stop_event: asyncio.Event,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    fingerprint_secret: str = "",
    provider_enabled: dict[str, bool] | None = None,
    metrics: NotificationMetrics | None = None,
) -> None:
    await ensure_group(r, STREAM_ALARM_FIRED, GROUP_API)
    while not stop_event.is_set():
        try:
            await consume_once(
                r,
                cfg,
                ws,
                session_factory,
                fingerprint_secret,
                provider_enabled,
                metrics,
            )
        except Exception:
            logger.exception("consume_once crashed; retry in 2s")
            await asyncio.sleep(2)
