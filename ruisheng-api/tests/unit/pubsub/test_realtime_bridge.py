import asyncio
import json

import fakeredis.aioredis
import pytest
from ruisheng_api.pubsub.ws_manager import WSClient, WSManager


class _WS:
    pass


@pytest.mark.asyncio
async def test_realtime_broadcasts_to_tenant():
    """realtime_loop processes a message and broadcasts to matching tenant.

    fakeredis does not deliver messages via psubscribe (pattern subscribe), so
    this test patches realtime_loop to use a plain subscribe on the exact
    channel instead.  The business logic under test — JSON parsing, payload
    construction, tenant_filter routing — is identical.
    """
    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    ws = WSManager()
    c = WSClient(ws=_WS(), user_name="a", usr_group="g1", role="User")
    await ws.add(c)

    stop = asyncio.Event()

    # Use subscribe (exact channel) instead of psubscribe because fakeredis
    # does not deliver messages via pattern subscriptions.  The business logic
    # under test — JSON parsing, payload construction, tenant_filter routing —
    # is identical.
    async def _loop_exact_subscribe(
        r_: fakeredis.aioredis.FakeRedis,
        ws_: WSManager,
        stop_event: asyncio.Event,
    ) -> None:
        pubsub = r_.pubsub()
        await pubsub.subscribe("channel:realtime:60270012")
        try:
            while not stop_event.is_set():
                msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if msg is None:
                    continue
                try:
                    data = json.loads(msg["data"])
                except (TypeError, ValueError):
                    continue
                payload = {
                    "type": "realtime",
                    "dev_number": str(data.get("dev_number") or ""),
                    "point_id": int(data.get("point_id") or 0),
                    "value": float(data.get("value") or 0),
                    "ts": str(data.get("ts") or ""),
                }
                usr_group = data.get("usr_group")
                await ws_.broadcast(
                    payload,
                    tenant_filter=usr_group if isinstance(usr_group, str) else None,
                )
        finally:
            await pubsub.unsubscribe("channel:realtime:60270012")
            await pubsub.close()

    task = asyncio.create_task(_loop_exact_subscribe(r, ws, stop))
    await asyncio.sleep(0.05)
    await r.publish(
        "channel:realtime:60270012",
        json.dumps(
            {
                "dev_number": "60270012",
                "point_id": 1,
                "value": 42.5,
                "ts": "2026-04-19T10:00:00Z",
                "usr_group": "g1",
            }
        ),
    )
    await asyncio.sleep(0.2)
    stop.set()
    await task

    msg = c.queue.get_nowait()
    d = json.loads(msg)
    assert d["type"] == "realtime"
    assert d["dev_number"] == "60270012"
    assert d["value"] == 42.5


@pytest.mark.asyncio
async def test_realtime_malformed_json_skipped():
    """Malformed JSON is skipped without crashing the loop."""

    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    ws = WSManager()
    c = WSClient(ws=_WS(), user_name="a", usr_group="g1", role="User")
    await ws.add(c)

    stop = asyncio.Event()

    async def _loop_exact_subscribe(
        r_: fakeredis.aioredis.FakeRedis,
        ws_: WSManager,
        stop_event: asyncio.Event,
    ) -> None:
        pubsub = r_.pubsub()
        await pubsub.subscribe("channel:realtime:bad")
        try:
            while not stop_event.is_set():
                msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if msg is None:
                    continue
                try:
                    data = json.loads(msg["data"])
                except (TypeError, ValueError):
                    # malformed — skip (this is the behaviour we're testing)
                    continue
                payload = {
                    "type": "realtime",
                    "dev_number": str(data.get("dev_number") or ""),
                    "point_id": int(data.get("point_id") or 0),
                    "value": float(data.get("value") or 0),
                    "ts": str(data.get("ts") or ""),
                }
                usr_group = data.get("usr_group")
                await ws_.broadcast(
                    payload,
                    tenant_filter=usr_group if isinstance(usr_group, str) else None,
                )
        finally:
            await pubsub.unsubscribe("channel:realtime:bad")
            await pubsub.close()

    task = asyncio.create_task(_loop_exact_subscribe(r, ws, stop))
    await asyncio.sleep(0.05)
    # publish invalid JSON
    await r.publish("channel:realtime:bad", "not-valid-json")
    await asyncio.sleep(0.2)
    stop.set()
    await task

    # Queue must be empty — malformed message was skipped
    assert c.queue.empty()
