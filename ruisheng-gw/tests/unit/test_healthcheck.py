"""Container-internal GW process readiness command."""

from __future__ import annotations

import json
import time

import pytest
from ruisheng_gw import healthcheck


class _Response:
    def __init__(self, payload: dict[str, object], status: int = 200) -> None:
        self.payload = payload
        self.status = status

    async def __aenter__(self) -> _Response:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def json(self) -> dict[str, object]:
        return self.payload


class _Session:
    def __init__(self, response: _Response, **_kwargs: object) -> None:
        self.response = response

    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    def get(self, _url: str) -> _Response:
        return self.response


def _payload(**overrides: object) -> dict[str, object]:
    result: dict[str, object] = {
        "status": "ready",
        "database": "ready",
        "redis": "ready",
        "batch": "ready",
        "outbox": "ready",
        "pid": 1,
        "observed_at": time.time(),
    }
    result.update(overrides)
    return result


@pytest.mark.asyncio
async def test_internal_socket_proves_running_process_and_runtime_state(monkeypatch) -> None:
    response = _Response(_payload())
    monkeypatch.setattr(healthcheck, "UnixConnector", lambda **_kwargs: object())
    monkeypatch.setattr(healthcheck, "ClientSession", lambda **kwargs: _Session(response, **kwargs))

    assert await healthcheck.check_internal_ready("/run/gw.sock") == {
        "status": "ready",
        "database": "ready",
        "redis": "ready",
        "batch": "ready",
        "outbox": "ready",
        "service": "ready",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload,status",
    [
        (_payload(observed_at=time.time() - 60), 200),
        (_payload(batch="failed", status="not_ready"), 503),
        (_payload(pid=0), 200),
    ],
)
async def test_internal_socket_fails_closed_for_stale_or_unready_state(
    monkeypatch, payload: dict[str, object], status: int
) -> None:
    response = _Response(payload, status)
    monkeypatch.setattr(healthcheck, "UnixConnector", lambda **_kwargs: object())
    monkeypatch.setattr(healthcheck, "ClientSession", lambda **kwargs: _Session(response, **kwargs))

    result = await healthcheck.check_internal_ready("/run/gw.sock")

    assert result["status"] == "not_ready"


def test_main_emits_allowlisted_json(monkeypatch, capsys) -> None:
    expected: healthcheck.HealthResult = {
        "status": "ready",
        "database": "ready",
        "redis": "ready",
        "batch": "ready",
        "outbox": "ready",
        "service": "ready",
    }
    monkeypatch.setattr(healthcheck, "run_healthcheck", lambda *_args: expected)

    assert healthcheck.main() == 0
    assert json.loads(capsys.readouterr().out) == expected
