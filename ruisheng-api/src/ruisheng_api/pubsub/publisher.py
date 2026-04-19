"""pubsub publisher：XADD stream:control:cmd。"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any

    import redis.asyncio as redis_async


STREAM_CONTROL_CMD = "stream:control:cmd"


async def xadd_control_cmd(
    r: redis_async.Redis[Any], *, cmd_id: str, payload: dict[str, object]
) -> str:
    """Returns Redis stream entry id. MAXLEN ~50000 prevents unbounded growth."""
    result = await r.xadd(
        STREAM_CONTROL_CMD,
        {"cmd_id": cmd_id, "payload": json.dumps(payload, ensure_ascii=False)},
        maxlen=50_000,
        approximate=True,
    )
    return str(result)
