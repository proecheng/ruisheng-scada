"""Container-internal API dependency health command."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from ruisheng_api import healthcheck


class _Connection:
    def __init__(self) -> None:
        self.execute = AsyncMock()

    async def __aenter__(self) -> _Connection:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None


class _Engine:
    def __init__(self) -> None:
        self.connection = _Connection()
        self.disposed = False

    def connect(self) -> _Connection:
        return self.connection

    async def dispose(self) -> None:
        self.disposed = True


@pytest.mark.asyncio
async def test_check_dependencies_uses_environment_urls_and_closes_clients(monkeypatch) -> None:
    engine = _Engine()
    redis_client = SimpleNamespace(ping=AsyncMock(), close=AsyncMock())
    monkeypatch.setattr(healthcheck, "build_engine", lambda *_args, **_kwargs: engine)
    monkeypatch.setattr(healthcheck.redis_async, "from_url", lambda *_args, **_kwargs: redis_client)
    monkeypatch.setattr(healthcheck, "_probe_local_service", lambda *_args: True)

    result = await healthcheck.check_dependencies(
        "postgresql+asyncpg://user:secret@db/ruisheng",
        "redis://:secret@redis:6379/0",
    )

    assert result == {
        "status": "ready",
        "database": "ready",
        "redis": "ready",
        "service": "ready",
    }
    engine.connection.execute.assert_awaited_once()
    redis_client.ping.assert_awaited_once()
    assert engine.disposed
    redis_client.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_check_dependencies_reports_only_component_names(monkeypatch) -> None:
    engine = _Engine()
    engine.connection.execute.side_effect = RuntimeError("postgresql://user:db-secret@db")
    redis_client = SimpleNamespace(
        ping=AsyncMock(side_effect=RuntimeError("redis://:redis-secret@redis")),
        close=AsyncMock(),
    )
    monkeypatch.setattr(healthcheck, "build_engine", lambda *_args, **_kwargs: engine)
    monkeypatch.setattr(healthcheck.redis_async, "from_url", lambda *_args, **_kwargs: redis_client)
    monkeypatch.setattr(healthcheck, "_probe_local_service", lambda *_args: False)

    result = await healthcheck.check_dependencies("db-secret", "redis-secret")

    encoded = json.dumps(result)
    assert result == {
        "status": "not_ready",
        "database": "failed",
        "redis": "failed",
        "service": "failed",
    }
    assert "secret" not in encoded
    assert engine.disposed
    redis_client.close.assert_awaited_once()


def test_main_emits_allowlisted_json_and_exit_code(monkeypatch, capsys) -> None:
    monkeypatch.setenv("API_DB_URL", "db-secret")
    monkeypatch.setenv("API_REDIS_URL", "redis-secret")
    monkeypatch.setattr(
        healthcheck,
        "run_healthcheck",
        lambda *_args: {
            "status": "ready",
            "database": "ready",
            "redis": "ready",
            "service": "ready",
        },
    )

    assert healthcheck.main() == 0
    output = capsys.readouterr().out
    assert json.loads(output) == {
        "status": "ready",
        "database": "ready",
        "redis": "ready",
        "service": "ready",
    }
    assert "secret" not in output


def test_main_fails_closed_when_required_environment_is_missing(monkeypatch, capsys) -> None:
    monkeypatch.delenv("API_DB_URL", raising=False)
    monkeypatch.setenv("API_REDIS_URL", "redis-secret")

    assert healthcheck.main() == 1
    assert json.loads(capsys.readouterr().out) == {
        "status": "not_ready",
        "database": "failed",
        "redis": "unknown",
        "service": "unknown",
    }
