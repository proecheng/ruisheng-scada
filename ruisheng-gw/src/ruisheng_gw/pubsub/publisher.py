"""Redis pub fire-and-forget with metric counters.

Failure behavior: NEVER raises; logs warning + increments counter.
Upstream batch_writer does not block on pub outcome.
"""

from __future__ import annotations

from typing import Any

from ruisheng_gw.pubsub.schemas import AlarmEvent, RealtimeEvent


class Publisher:
    def __init__(self, *, redis: Any) -> None:
        self._redis = redis
        self.stats = {
            "realtime_published": 0,
            "realtime_fail": 0,
            "alarm_published": 0,
            "alarm_fail": 0,
        }

    async def publish_realtime(self, ev: RealtimeEvent) -> None:
        channel = f"channel:realtime:v1:{ev.dev_number}"
        try:
            await self._redis.publish(channel, ev.model_dump_json())
            self.stats["realtime_published"] += 1
        except Exception:
            self.stats["realtime_fail"] += 1

    async def publish_alarm(self, ev: AlarmEvent) -> None:
        channel = f"channel:alarm:v1:{ev.dev_number}"
        try:
            await self._redis.publish(channel, ev.model_dump_json())
            self.stats["alarm_published"] += 1
        except Exception:
            self.stats["alarm_fail"] += 1
