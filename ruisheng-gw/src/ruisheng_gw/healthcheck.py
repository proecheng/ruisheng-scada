"""Secret-free readiness command querying the running GW process over a Unix socket."""

from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Literal, TypedDict

from aiohttp import ClientSession, UnixConnector

HTTP_OK = 200
MAX_OBSERVATION_AGE_SECONDS = 5


class HealthResult(TypedDict):
    status: Literal["ready", "not_ready"]
    database: Literal["ready", "failed", "unknown"]
    redis: Literal["ready", "failed", "unknown"]
    batch: Literal["ready", "failed", "unknown"]
    outbox: Literal["ready", "failed", "unknown"]
    service: Literal["ready", "failed", "unknown"]


async def check_internal_ready(socket_path: str) -> HealthResult:
    failed: HealthResult = {
        "status": "not_ready",
        "database": "unknown",
        "redis": "unknown",
        "batch": "unknown",
        "outbox": "unknown",
        "service": "failed",
    }
    try:
        async with asyncio.timeout(5):
            async with ClientSession(connector=UnixConnector(path=socket_path)) as session:
                async with session.get("http://localhost/ready") as response:
                    payload = await response.json()
        states = {key: payload.get(key) for key in ("database", "redis", "batch", "outbox")}
        if any(value not in {"ready", "failed"} for value in states.values()):
            return failed
        if not isinstance(payload.get("pid"), int) or payload["pid"] <= 0:
            return failed
        observed_at = payload.get("observed_at")
        if (
            not isinstance(observed_at, int | float)
            or abs(time.time() - observed_at) > MAX_OBSERVATION_AGE_SECONDS
        ):
            return failed
        ready = (
            response.status == HTTP_OK
            and payload.get("status") == "ready"
            and all(value == "ready" for value in states.values())
        )
        return {
            "status": "ready" if ready else "not_ready",
            "database": states["database"],
            "redis": states["redis"],
            "batch": states["batch"],
            "outbox": states["outbox"],
            "service": "ready",
        }
    except Exception:
        return failed


def run_healthcheck(socket_path: str) -> HealthResult:
    return asyncio.run(check_internal_ready(socket_path))


def main() -> int:
    socket_path = os.environ.get("GW_INTERNAL_HEALTH_SOCKET", "/tmp/ruisheng-gw-health.sock")
    result = run_healthcheck(socket_path)
    print(json.dumps(result, separators=(",", ":"), sort_keys=True))
    return 0 if result["status"] == "ready" else 1


if __name__ == "__main__":  # pragma: no cover - exercised through the container command
    raise SystemExit(main())
