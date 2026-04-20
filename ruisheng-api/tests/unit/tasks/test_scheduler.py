from apscheduler.schedulers.asyncio import AsyncIOScheduler
from ruisheng_api.tasks.scheduler import build_scheduler


def test_build_scheduler_returns_asyncio_scheduler():
    s = build_scheduler()
    assert isinstance(s, AsyncIOScheduler)
    # Check job defaults
    assert s._job_defaults["coalesce"] is True
    assert s._job_defaults["max_instances"] == 1
    assert s._job_defaults["misfire_grace_time"] == 60
