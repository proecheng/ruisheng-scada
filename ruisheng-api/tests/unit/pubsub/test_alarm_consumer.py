from datetime import UTC, datetime

import fakeredis.aioredis
from ruisheng_api.pubsub import alarm_consumer
from ruisheng_api.pubsub.alarm_consumer import (
    DLQ_ALARM,
    GROUP_API,
    STREAM_ALARM_FIRED,
    AlarmConsumerConfig,
    consume_once,
    ensure_group,
)
from ruisheng_api.services.notification.runtime import (
    MaterializationResult,
    NotificationMetrics,
)


def _event() -> dict[str, str]:
    return {
        "schema_version": "2",
        "event_id": "42",
        "triggered_at": datetime.now(UTC).isoformat(),
        "alarm_cfg_id": "7",
        "dev_number": "60270012",
        "point_id": "3",
        "value": "95",
        "limit": "80",
    }


class _WS:
    def __init__(self) -> None:
        self.broadcasts: list[tuple[dict[str, object], str | None]] = []

    async def broadcast(
        self, message: dict[str, object], *, tenant_filter: str | None = None
    ) -> None:
        self.broadcasts.append((message, tenant_filter))


async def test_consume_once_materializes_broadcasts_and_acks(monkeypatch) -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    await ensure_group(redis, STREAM_ALARM_FIRED, GROUP_API)
    await redis.xadd(STREAM_ALARM_FIRED, _event())
    ws = _WS()

    async def fake_materialize(*args, **kwargs):
        return MaterializationResult(1, "g1", "overcurrent")

    monkeypatch.setattr(alarm_consumer, "materialize_event", fake_materialize)
    consumed = await consume_once(
        redis,
        AlarmConsumerConfig(consumer_name="t1", block_ms=1),
        ws,  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
    )

    assert consumed == 1
    assert (await redis.xpending(STREAM_ALARM_FIRED, GROUP_API))["pending"] == 0
    assert await redis.xlen(STREAM_ALARM_FIRED) == 0
    assert ws.broadcasts[0][0]["alarm_name"] == "overcurrent"
    assert ws.broadcasts[0][1] == "g1"


async def test_ack_and_delete_uses_atomic_pipeline() -> None:
    calls: list[tuple[object, ...]] = []

    class Pipe:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        def xack(self, *args):
            calls.append(("xack", *args))

        def xdel(self, *args):
            calls.append(("xdel", *args))

        async def execute(self):
            calls.append(("execute",))

    class Redis:
        def pipeline(self, *, transaction):
            calls.append(("pipeline", transaction))
            return Pipe()

    await alarm_consumer._ack_and_delete(Redis(), "1-0")  # type: ignore[arg-type]  # noqa: SLF001

    assert calls == [
        ("pipeline", True),
        ("xack", STREAM_ALARM_FIRED, GROUP_API, "1-0"),
        ("xdel", STREAM_ALARM_FIRED, "1-0"),
        ("execute",),
    ]


async def test_replayed_event_relies_on_database_idempotency(monkeypatch) -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    await ensure_group(redis, STREAM_ALARM_FIRED, GROUP_API)
    await redis.xadd(STREAM_ALARM_FIRED, _event())
    await redis.xadd(STREAM_ALARM_FIRED, _event())
    calls = 0

    async def fake_materialize(*args, **kwargs):
        nonlocal calls
        calls += 1
        return MaterializationResult(1, "g1", "overcurrent")

    monkeypatch.setattr(alarm_consumer, "materialize_event", fake_materialize)
    consumed = await consume_once(
        redis,
        AlarmConsumerConfig(consumer_name="t1", block_ms=1),
        _WS(),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
    )

    assert consumed == 2
    assert calls == 2
    assert await redis.get("alarm_seen:42") is None


async def test_invalid_event_retries_in_pel_before_dlq() -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    await ensure_group(redis, STREAM_ALARM_FIRED, GROUP_API)
    bad = _event()
    bad["schema_version"] = "1"
    await redis.xadd(STREAM_ALARM_FIRED, bad)
    metrics = NotificationMetrics()

    await consume_once(
        redis,
        AlarmConsumerConfig(consumer_name="t1", block_ms=1),
        _WS(),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        metrics=metrics,
    )

    assert (await redis.xpending(STREAM_ALARM_FIRED, GROUP_API))["pending"] == 1
    assert await redis.xlen(STREAM_ALARM_FIRED) == 1
    assert await redis.xlen(DLQ_ALARM) == 0
    assert metrics.materialize_failures == 1


async def test_invalid_event_at_retry_limit_is_dlqd_acked_and_deleted() -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    await ensure_group(redis, STREAM_ALARM_FIRED, GROUP_API)
    bad = _event()
    bad["schema_version"] = "1"
    await redis.xadd(STREAM_ALARM_FIRED, bad)

    await consume_once(
        redis,
        AlarmConsumerConfig(consumer_name="t1", block_ms=1, max_retries=1),
        _WS(),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
    )

    assert (await redis.xpending(STREAM_ALARM_FIRED, GROUP_API))["pending"] == 0
    assert await redis.xlen(STREAM_ALARM_FIRED) == 0
    assert await redis.xlen(DLQ_ALARM) == 1


async def test_claimed_event_without_materializer_reaches_dlq_at_retry_limit(
    monkeypatch,
) -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    await ensure_group(redis, STREAM_ALARM_FIRED, GROUP_API)
    await redis.xadd(STREAM_ALARM_FIRED, _event())

    await consume_once(
        redis,
        AlarmConsumerConfig(consumer_name="t1", block_ms=1, max_retries=2),
        _WS(),  # type: ignore[arg-type]
    )

    original_xautoclaim = redis.xautoclaim

    async def claim_without_idle(*args, **kwargs):
        kwargs["min_idle_time"] = 0
        return await original_xautoclaim(*args, **kwargs)

    monkeypatch.setattr(redis, "xautoclaim", claim_without_idle)
    consumed = await consume_once(
        redis,
        AlarmConsumerConfig(consumer_name="t2", block_ms=1, max_retries=2),
        _WS(),  # type: ignore[arg-type]
    )

    assert consumed == 1
    assert (await redis.xpending(STREAM_ALARM_FIRED, GROUP_API))["pending"] == 0
    assert await redis.xlen(STREAM_ALARM_FIRED) == 0
    entries = await redis.xrange(DLQ_ALARM)
    assert entries[0][1]["reason"] == "materializer_unavailable"


async def test_transient_materialization_failure_stays_pending(monkeypatch) -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    await ensure_group(redis, STREAM_ALARM_FIRED, GROUP_API)
    await redis.xadd(STREAM_ALARM_FIRED, _event())
    metrics = NotificationMetrics()

    async def fail_materialize(*args, **kwargs):
        raise OSError("database unavailable")

    monkeypatch.setattr(alarm_consumer, "materialize_event", fail_materialize)
    await consume_once(
        redis,
        AlarmConsumerConfig(consumer_name="t1", block_ms=1),
        _WS(),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        metrics=metrics,
    )

    assert (await redis.xpending(STREAM_ALARM_FIRED, GROUP_API))["pending"] == 1
    assert await redis.xlen(DLQ_ALARM) == 0
    assert metrics.materialize_failures == 1


async def test_websocket_failure_does_not_block_ack(monkeypatch) -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    await ensure_group(redis, STREAM_ALARM_FIRED, GROUP_API)
    await redis.xadd(STREAM_ALARM_FIRED, _event())

    async def fake_materialize(*args, **kwargs):
        return MaterializationResult(1, "g1", "overcurrent", created=True)

    class FailingWS(_WS):
        async def broadcast(self, *args, **kwargs) -> None:
            raise OSError("socket closed")

    monkeypatch.setattr(alarm_consumer, "materialize_event", fake_materialize)
    await consume_once(
        redis,
        AlarmConsumerConfig(consumer_name="t1", block_ms=1),
        FailingWS(),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
    )
    assert (await redis.xpending(STREAM_ALARM_FIRED, GROUP_API))["pending"] == 0


async def test_replay_broadcasts_only_new_materialization(monkeypatch) -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    await ensure_group(redis, STREAM_ALARM_FIRED, GROUP_API)
    await redis.xadd(STREAM_ALARM_FIRED, _event())
    await redis.xadd(STREAM_ALARM_FIRED, _event())
    calls = 0

    async def fake_materialize(*args, **kwargs):
        nonlocal calls
        calls += 1
        return MaterializationResult(1, "g1", "overcurrent", created=calls == 1)

    ws = _WS()
    monkeypatch.setattr(alarm_consumer, "materialize_event", fake_materialize)
    await consume_once(
        redis,
        AlarmConsumerConfig(consumer_name="t1", block_ms=1),
        ws,  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
    )
    assert len(ws.broadcasts) == 1


async def test_unexpected_failure_reaches_sanitized_dlq_at_retry_limit(monkeypatch) -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    await ensure_group(redis, STREAM_ALARM_FIRED, GROUP_API)
    await redis.xadd(STREAM_ALARM_FIRED, _event())

    async def fail_materialize(*args, **kwargs):
        raise OSError("secret database detail")

    monkeypatch.setattr(alarm_consumer, "materialize_event", fail_materialize)
    await consume_once(
        redis,
        AlarmConsumerConfig(consumer_name="t1", block_ms=1, max_retries=1),
        _WS(),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
    )
    assert (await redis.xpending(STREAM_ALARM_FIRED, GROUP_API))["pending"] == 0
    entries = await redis.xrange(DLQ_ALARM)
    assert entries[0][1]["reason"] == "processing_failed"
    assert "secret" not in str(entries[0][1])
