"""Secret-free dependency readiness command for container-internal probes."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import urllib.request
from typing import Literal, TypedDict

import redis.asyncio as redis_async
from sqlalchemy import text

from .db.base import build_engine

HTTP_OK = 200


class HealthResult(TypedDict):
    status: Literal["ready", "not_ready"]
    database: Literal["ready", "failed", "unknown"]
    redis: Literal["ready", "failed", "unknown"]
    service: Literal["ready", "failed", "unknown"]


def _probe_local_service(url: str) -> bool:
    with urllib.request.urlopen(url, timeout=5) as response:  # noqa: S310 - fixed loopback URL
        if response.status != HTTP_OK:
            return False
        response.read(65537)
        return True


async def check_dependencies(
    db_url: str,
    redis_url: str,
    service_url: str = "http://127.0.0.1:8000/api/meta/version",
) -> HealthResult:
    """Probe the running API process and its dependencies without exposing secrets."""
    database: Literal["ready", "failed", "unknown"] = "failed"
    redis_status: Literal["ready", "failed", "unknown"] = "failed"
    service: Literal["ready", "failed", "unknown"] = "failed"
    engine = None
    redis_client = None
    try:
        async with asyncio.timeout(5):
            engine = build_engine(db_url, pool_size=1, max_overflow=0)
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
        database = "ready"
    except Exception:
        database = "failed"

    try:
        async with asyncio.timeout(5):
            redis_client = redis_async.from_url(redis_url, decode_responses=True)
            await redis_client.ping()
        redis_status = "ready"
    except Exception:
        redis_status = "failed"
    try:
        service = (
            "ready"
            if await asyncio.wait_for(
                asyncio.to_thread(_probe_local_service, service_url), timeout=6
            )
            else "failed"
        )
    except Exception:
        service = "failed"
    finally:
        if redis_client is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(redis_client.close(), timeout=2)
        if engine is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(engine.dispose(), timeout=2)

    return {
        "status": "ready" if database == redis_status == service == "ready" else "not_ready",
        "database": database,
        "redis": redis_status,
        "service": service,
    }


def run_healthcheck(db_url: str, redis_url: str) -> HealthResult:
    return asyncio.run(check_dependencies(db_url, redis_url))


def main() -> int:
    db_url = os.environ.get("API_DB_URL")
    redis_url = os.environ.get("API_REDIS_URL")
    if not db_url or not redis_url:
        result: HealthResult = {
            "status": "not_ready",
            "database": "failed" if not db_url else "unknown",
            "redis": "failed" if not redis_url else "unknown",
            "service": "unknown",
        }
    else:
        result = run_healthcheck(db_url, redis_url)
    print(json.dumps(result, separators=(",", ":"), sort_keys=True))
    return 0 if result["status"] == "ready" else 1


if __name__ == "__main__":  # pragma: no cover - exercised through the container command
    raise SystemExit(main())
