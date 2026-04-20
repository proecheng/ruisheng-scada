"""Publisher: fire-and-forget Redis pub with structured metrics."""

from __future__ import annotations

from fakeredis.aioredis import FakeRedis
from ruisheng_gw.pubsub.publisher import Publisher
from ruisheng_gw.pubsub.schemas import AlarmEvent, RealtimeEvent


async def test_publish_realtime_to_channel() -> None:
    redis = FakeRedis()
    pub = Publisher(redis=redis)
    ev = RealtimeEvent(
        schema_version=1,
        dev_number="D1",
        point_id=1,
        rt_value=10.0,
        org_value=100.0,
        recorded_at=0.0,
    )
    await pub.publish_realtime(ev)
    assert pub.stats["realtime_published"] == 1


async def test_publish_alarm_different_channel() -> None:
    redis = FakeRedis()
    pub = Publisher(redis=redis)
    ev = AlarmEvent(
        schema_version=1,
        dev_number="D1",
        point_id=1,
        value=150.0,
        threshold=100.0,
        level=1,
        fired_at=0.0,
    )
    await pub.publish_alarm(ev)
    assert pub.stats["alarm_published"] == 1


async def test_publish_failure_increments_metric() -> None:
    class BrokenRedis:
        async def publish(self, *a, **kw):
            raise RuntimeError("redis down")

    pub = Publisher(redis=BrokenRedis())
    ev = RealtimeEvent(
        schema_version=1,
        dev_number="D1",
        point_id=1,
        rt_value=1.0,
        org_value=1.0,
        recorded_at=0.0,
    )
    await pub.publish_realtime(ev)  # no raise
    assert pub.stats["realtime_fail"] == 1
