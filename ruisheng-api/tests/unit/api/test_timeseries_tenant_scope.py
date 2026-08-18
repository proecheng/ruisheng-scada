from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import fakeredis.aioredis
from fastapi.testclient import TestClient
from ruisheng_api.core.security import client_fingerprint, issue_access_token
from ruisheng_api.deps import get_redis, get_session
from ruisheng_api.main import create_app


def _env(monkeypatch) -> None:
    monkeypatch.setenv("API_DB_URL", "postgresql+asyncpg://u:p@h/d")
    monkeypatch.setenv("API_GW_DB_URL", "postgresql+asyncpg://u:p@h/d")
    monkeypatch.setenv("API_REDIS_URL", "redis://:p@h/0")
    monkeypatch.setenv("API_JWT_SECRET", "x" * 64)


def _token() -> str:
    fingerprint = client_fingerprint("testclient", "testclient")
    return issue_access_token(
        "alice",
        "tenant-a",
        "User",
        0,
        fingerprint,
        secret="x" * 64,
        ttl_sec=900,
    )


class _RowsResult:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = []
        for row in rows:
            item = SimpleNamespace(**row)
            item._mapping = row
            self._rows.append(item)

    def __iter__(self):
        return iter(self._rows)

    def one_or_none(self):
        return self._rows[0] if self._rows else None


class _TenantAwareSession:
    """Emulate RLS: foreign time-series rows disappear only through a devices join."""

    def begin(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def execute(self, statement, params=None):
        sql = " ".join(str(statement).split()).lower()
        if "point_data_history" in sql:
            if "join devices" in sql:
                return _RowsResult([])
            return _RowsResult(
                [
                    {
                        "dev_number": "foreign-device",
                        "point_id": 1,
                        "rt_value": 42.0,
                        "recorded_at": datetime(2026, 8, 18, tzinfo=UTC),
                    }
                ]
            )
        if "waveform_history" in sql:
            if "join devices" in sql:
                return _RowsResult([])
            if "select data_array" not in sql:
                return _RowsResult(
                    [
                        {
                            "dev_number": "foreign-device",
                            "point_id": 1,
                            "sample_time_decisec": 10,
                            "packet_count": 1,
                            "recorded_at": datetime(2026, 8, 18, tzinfo=UTC),
                        }
                    ]
                )
            return _RowsResult(
                [
                    {
                        "data_array": b"\x00\x00\x80?",
                        "sample_time_decisec": 10,
                        "packet_count": 1,
                    }
                ]
            )
        return _RowsResult([])


def _client(monkeypatch) -> TestClient:
    _env(monkeypatch)
    app = create_app()
    redis = fakeredis.aioredis.FakeRedis()
    app.dependency_overrides[get_redis] = lambda: redis

    async def fake_session():
        yield _TenantAwareSession()

    app.dependency_overrides[get_session] = fake_session
    return TestClient(app)


def test_daily_report_does_not_include_foreign_tenant_rows(monkeypatch) -> None:
    response = _client(monkeypatch).post(
        "/api/reports/daily",
        headers={"Authorization": f"Bearer {_token()}"},
        json={"day": "2026-08-18", "format": "json"},
    )

    assert response.status_code == 200
    assert response.json()["data"] == {}


def test_daily_report_rejects_unrepresentable_max_date(monkeypatch) -> None:
    response = _client(monkeypatch).post(
        "/api/reports/daily",
        headers={"Authorization": f"Bearer {_token()}"},
        json={"day": "9999-12-31", "format": "json"},
    )

    assert response.status_code == 400


def test_waveform_history_treats_foreign_device_as_missing(monkeypatch) -> None:
    response = _client(monkeypatch).get(
        "/api/waveforms/foreign-device/1",
        headers={"Authorization": f"Bearer {_token()}"},
        params={
            "from": "2026-08-18T00:00:00Z",
            "to": "2026-08-19T00:00:00Z",
        },
    )

    assert response.status_code == 200
    assert response.json()["data"] == {
        "dev_number": "foreign-device",
        "point_id": 1,
        "waveforms": [],
    }


def test_waveform_analysis_treats_foreign_device_as_missing(monkeypatch) -> None:
    response = _client(monkeypatch).post(
        "/api/waveforms/analyze",
        headers={"Authorization": f"Bearer {_token()}"},
        params={
            "dev_number": "foreign-device",
            "point_id": 1,
            "from": "2026-08-18T00:00:00Z",
            "to": "2026-08-19T00:00:00Z",
        },
    )

    assert response.status_code == 200
    assert response.json()["data"] == {"freqs": [], "magnitudes": []}
