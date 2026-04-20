import json

import fakeredis.aioredis
import pytest
from ruisheng_api.pubsub.publisher import STREAM_CONTROL_CMD, xadd_control_cmd


@pytest.mark.asyncio
async def test_xadd_control_cmd_writes_stream():
    r = fakeredis.aioredis.FakeRedis()
    await xadd_control_cmd(r, cmd_id="01HA", payload={"x": 1})
    entries = await r.xread({STREAM_CONTROL_CMD: "0-0"}, count=10)
    _, rows = entries[0]
    _, data = rows[0]
    d = {
        k.decode() if isinstance(k, bytes) else k: v.decode() if isinstance(v, bytes) else v
        for k, v in data.items()
    }
    assert d["cmd_id"] == "01HA"
    assert json.loads(d["payload"])["x"] == 1
