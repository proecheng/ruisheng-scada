import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from ruisheng_api.pubsub.realtime_bridge import realtime_loop
from ruisheng_gw.pubsub.schemas import RealtimeEvent


async def run_bridge(messages):
    stop = asyncio.Event()
    pubsub = AsyncMock()
    pending = iter(messages)

    async def get_message(**kwargs):
        try:
            return {"data": next(pending)}
        except StopIteration:
            stop.set()
            return None

    pubsub.get_message.side_effect = get_message
    redis = type("Redis", (), {"pubsub": lambda self: pubsub})()
    manager = AsyncMock()
    await realtime_loop(redis, manager, stop)
    pubsub.psubscribe.assert_awaited_once_with("channel:realtime:*")
    pubsub.aclose.assert_awaited_once()
    return manager.broadcast.call_args_list


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [56300.0, 0.0, None])
async def test_actual_gateway_event_keeps_value_and_utc_sample_time(value):
    event = RealtimeEvent(
        dev_number="DEV001",
        point_id=21,
        rt_value=value,
        org_value=56300,
        recorded_at=1789442400.125,
    )
    calls = await run_bridge([event.model_dump_json()])
    assert len(calls) == 1
    assert calls[0].args[0] == {
        "type": "realtime",
        "dev_number": "DEV001",
        "point_id": 21,
        "value": value,
        "ts": "2026-09-15T03:20:00.125000+00:00",
    }


@pytest.mark.asyncio
async def test_legacy_event_retains_tenant_and_zero():
    calls = await run_bridge(
        [
            json.dumps(
                {
                    "dev_number": "D1",
                    "point_id": 1,
                    "value": 0,
                    "ts": "2026-09-15T14:00:00+08:00",
                    "usr_group": "g1",
                }
            )
        ]
    )
    assert calls[0].args[0]["value"] == 0
    assert calls[0].args[0]["ts"] == "2026-09-15T06:00:00+00:00"
    assert calls[0].kwargs == {"tenant_filter": "g1"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad",
    [
        "not json",
        "null",
        "[]",
        "{}",
        '{"point_id":"bad"}',
        json.dumps(
            {
                "schema_version": 2,
                "dev_number": "D1",
                "point_id": 1,
                "rt_value": 2,
                "recorded_at": 1789442400,
            }
        ),
        json.dumps({"dev_number": "D1", "point_id": 1, "value": 2, "ts": ""}),
        json.dumps(
            {"schema_version": 1, "dev_number": "D1", "point_id": 1, "recorded_at": 1789442400}
        ),
        json.dumps(
            {
                "schema_version": 1,
                "dev_number": "D1",
                "point_id": 1,
                "rt_value": "NaN",
                "recorded_at": 1789442400,
            }
        ),
        json.dumps(
            {
                "schema_version": 1,
                "dev_number": "D1",
                "point_id": True,
                "rt_value": 1,
                "recorded_at": 1789442400,
            }
        ),
        json.dumps(
            {
                "schema_version": 1,
                "dev_number": "D1",
                "point_id": 1,
                "rt_value": 1,
                "recorded_at": 1e100,
            }
        ),
    ],
)
async def test_bad_event_does_not_zero_values_or_stop_next_sample(bad):
    event = RealtimeEvent(
        dev_number="DEV001", point_id=21, rt_value=10, org_value=10, recorded_at=1789442400
    )
    calls = await run_bridge([bad, event.model_dump_json()])
    assert len(calls) == 1
    assert calls[0].args[0]["value"] == 10
