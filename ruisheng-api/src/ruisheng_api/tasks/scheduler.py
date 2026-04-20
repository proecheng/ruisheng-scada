"""APScheduler 工厂。"""

from __future__ import annotations

from apscheduler.schedulers.asyncio import AsyncIOScheduler  # type: ignore[import-untyped]


def build_scheduler() -> AsyncIOScheduler:
    return AsyncIOScheduler(
        job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 60},
    )
