"""health server: /health /ready /metrics (aiohttp :9090)."""

from __future__ import annotations

import pytest
from aiohttp.test_utils import TestClient, TestServer
from ruisheng_gw.health import HealthState, create_health_app


@pytest.fixture
async def client() -> TestClient:
    state = HealthState()
    state.set_db_ok(True)
    state.set_redis_ok(True)
    state.mark_flush_ok()
    app = create_health_app(state)
    async with TestClient(TestServer(app)) as c:
        yield c


async def test_health_returns_200(client: TestClient) -> None:
    resp = await client.get("/health")
    assert resp.status == 200  # noqa: PLR2004  # HTTP status literal
    data = await resp.json()
    assert data["status"] == "alive"


async def test_ready_returns_200_when_all_healthy(client: TestClient) -> None:
    resp = await client.get("/ready")
    assert resp.status == 200  # noqa: PLR2004  # HTTP status literal
    data = await resp.json()
    assert data["ready"] is True


async def test_ready_returns_503_when_db_down() -> None:
    state = HealthState()
    state.set_db_ok(False)
    state.set_redis_ok(True)
    state.mark_flush_ok()
    app = create_health_app(state)
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/ready")
        assert resp.status == 503  # noqa: PLR2004  # HTTP status literal


async def test_metrics_prometheus_format(client: TestClient) -> None:
    resp = await client.get("/metrics")
    assert resp.status == 200  # noqa: PLR2004  # HTTP status literal
    body = await resp.text()
    assert "# TYPE" in body
