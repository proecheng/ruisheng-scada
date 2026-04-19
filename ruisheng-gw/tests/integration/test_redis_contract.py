"""Contract test: subscribe to channel, verify every message round-trips schema."""

from __future__ import annotations

import asyncio

import redis.asyncio as aioredis
from ruisheng_gw.pubsub.publisher import Publisher
from ruisheng_gw.pubsub.schemas import RealtimeEvent


async def test_published_messages_validate_as_schema(redis_url: str) -> None:
    redis_pub = aioredis.Redis.from_url(redis_url)
    redis_sub = aioredis.Redis.from_url(redis_url)
    pub = Publisher(redis=redis_pub)

    pubsub = redis_sub.pubsub()
    await pubsub.subscribe("channel:realtime:v1:D1")

    received: list[str] = []

    async def _collect() -> None:
        async for msg in pubsub.listen():
            if msg["type"] == "subscribe":
                # subscription confirmed — safe to publish now
                break
        async for msg in pubsub.listen():
            if msg["type"] != "message":
                continue
            received.append(msg["data"].decode() if isinstance(msg["data"], bytes) else msg["data"])
            if len(received) >= 3:
                break

    collector = asyncio.create_task(_collect())
    # yield once so _collect enters its first listen() loop before we publish
    await asyncio.sleep(0)

    for i in range(3):
        await pub.publish_realtime(
            RealtimeEvent(
                schema_version=1,
                dev_number="D1",
                point_id=i,
                rt_value=float(i),
                org_value=0.0,
                recorded_at=0.0,
            )
        )

    await asyncio.wait_for(collector, timeout=5.0)
    assert len(received) == 3
    for raw in received:
        # MUST NOT raise — contract preserved
        RealtimeEvent.model_validate_json(raw)

    await pubsub.unsubscribe()
    await redis_pub.aclose()
    await redis_sub.aclose()
