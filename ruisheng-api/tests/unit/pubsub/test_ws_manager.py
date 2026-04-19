import asyncio

import pytest
from ruisheng_api.pubsub.ws_manager import WSClient, WSManager


class _WS:
    pass


@pytest.mark.asyncio
async def test_broadcast_respects_tenant():
    m = WSManager()
    a = WSClient(ws=_WS(), user_name="a", usr_group="g1", role="User")
    b = WSClient(ws=_WS(), user_name="b", usr_group="g2", role="User")
    admin = WSClient(ws=_WS(), user_name="c", usr_group="", role="Administrators")
    await m.add(a)
    await m.add(b)
    await m.add(admin)
    n = await m.broadcast({"x": 1}, tenant_filter="g1")
    assert n == 2  # a + admin


@pytest.mark.asyncio
async def test_broadcast_drops_oldest_when_full():
    m = WSManager()
    c = WSClient(ws=_WS(), user_name="a", usr_group="g1", role="User")
    c.queue = asyncio.Queue(maxsize=1)
    c.queue.put_nowait("old")
    await m.add(c)
    await m.broadcast({"x": 2})
    msg = c.queue.get_nowait()
    assert "2" in msg
