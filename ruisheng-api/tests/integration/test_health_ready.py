"""API readiness against real PostgreSQL and Redis services."""

from fastapi.testclient import TestClient
from ruisheng_api.main import create_app


def test_ready_checks_postgres_and_redis() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/api/health/ready")

    assert response.status_code == 200
    assert response.json()["data"] == {"status": "ready"}
