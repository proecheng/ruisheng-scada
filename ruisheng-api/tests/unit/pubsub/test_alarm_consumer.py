import fakeredis.aioredis
import pytest
from ruisheng_api.pubsub.alarm_consumer import (
    STREAM_ALARM_FIRED,
    AlarmConsumerConfig,
    consume_once,
    ensure_group,
)
from ruisheng_api.pubsub.ws_manager import WSManager


@pytest.mark.asyncio
async def test_consume_once_broadcasts_and_acks():
    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    await ensure_group(r, STREAM_ALARM_FIRED, "api-alarm-consumer")
    await r.xadd(
        STREAM_ALARM_FIRED,
        {
            "event_id": "42",
            "dev_number": "60270012",
            "alarm_name": "overcurrent",
            "value": "95",
            "limit": "80",
            "ts": "2026-04-19T10:30:00Z",
            "usr_group": "g1",
        },
    )
    ws = WSManager()
    cfg = AlarmConsumerConfig(consumer_name="t1", block_ms=100, batch=10)
    n = await consume_once(r, cfg, ws)
    assert n == 1
    pending = await r.xpending(STREAM_ALARM_FIRED, "api-alarm-consumer")
    assert pending["pending"] == 0


@pytest.mark.asyncio
async def test_consume_once_deduplicates():
    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    await ensure_group(r, STREAM_ALARM_FIRED, "api-alarm-consumer")
    await r.set("alarm_seen:42", "1")  # already processed
    await r.xadd(
        STREAM_ALARM_FIRED,
        {
            "event_id": "42",
            "dev_number": "d",
            "usr_group": "g1",
        },
    )
    ws = WSManager()
    cfg = AlarmConsumerConfig(consumer_name="t1", block_ms=100)
    await consume_once(r, cfg, ws)
    pending = await r.xpending(STREAM_ALARM_FIRED, "api-alarm-consumer")
    assert pending["pending"] == 0
