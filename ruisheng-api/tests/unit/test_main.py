from fastapi.testclient import TestClient
from ruisheng_api.main import create_app


def test_app_health_liveness(monkeypatch):
    monkeypatch.setenv("API_DB_URL", "postgresql+asyncpg://u:p@h/d")
    monkeypatch.setenv("API_GW_DB_URL", "postgresql+asyncpg://u:p@h/d")
    monkeypatch.setenv("API_REDIS_URL", "redis://:p@h/0")
    monkeypatch.setenv("API_JWT_SECRET", "x" * 64)

    app = create_app()
    r = TestClient(app).get("/api/health/live")
    assert r.status_code == 200
    assert r.json()["code"] == 0
    assert r.json()["data"]["status"] == "live"


def test_app_openapi_title(monkeypatch):
    monkeypatch.setenv("API_DB_URL", "postgresql+asyncpg://u:p@h/d")
    monkeypatch.setenv("API_GW_DB_URL", "postgresql+asyncpg://u:p@h/d")
    monkeypatch.setenv("API_REDIS_URL", "redis://:p@h/0")
    monkeypatch.setenv("API_JWT_SECRET", "x" * 64)

    app = create_app()
    spec = app.openapi()
    assert spec["info"]["title"] == "ruisheng-api"
