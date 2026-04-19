import fakeredis.aioredis
from fastapi.testclient import TestClient
from ruisheng_api.deps import get_redis
from ruisheng_api.main import create_app


def _env(m):
    m.setenv("API_DB_URL", "postgresql+asyncpg://u:p@h/d")
    m.setenv("API_GW_DB_URL", "postgresql+asyncpg://u:p@h/d")
    m.setenv("API_REDIS_URL", "redis://:p@h/0")
    m.setenv("API_JWT_SECRET", "x" * 64)


def test_meta_version(monkeypatch):
    _env(monkeypatch)
    app = create_app()
    r = fakeredis.aioredis.FakeRedis()
    app.dependency_overrides[get_redis] = lambda: r
    resp = TestClient(app).get("/api/meta/version")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert "version" in data
    assert data["version"] == "0.1.0"


def test_admin_log_level_requires_auth(monkeypatch):
    _env(monkeypatch)
    app = create_app()
    r = fakeredis.aioredis.FakeRedis()
    app.dependency_overrides[get_redis] = lambda: r
    resp = TestClient(app).post("/api/admin/log/level?level=DEBUG")
    assert resp.status_code == 401
