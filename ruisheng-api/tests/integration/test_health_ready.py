"""API readiness against real PostgreSQL and Redis services."""

import hashlib

import pytest
from fastapi.testclient import TestClient
from ruisheng_api.main import create_app

pytestmark = pytest.mark.integration
MANAGEMENT_TOKEN = "a" * 43
MANAGEMENT_TOKEN_DIGEST = hashlib.sha256(MANAGEMENT_TOKEN.encode("ascii")).hexdigest()


def test_ready_checks_postgres_and_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_MANAGEMENT_TOKEN_SHA256", MANAGEMENT_TOKEN_DIGEST)
    with TestClient(create_app()) as client:
        response = client.get(
            "/api/health/ready",
            headers={"Authorization": f"Bearer {MANAGEMENT_TOKEN}"},
        )

    assert response.status_code == 200
    assert response.json()["data"] == {"status": "ready"}
