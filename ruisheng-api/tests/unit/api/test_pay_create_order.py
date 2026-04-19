import fakeredis.aioredis
from fastapi.testclient import TestClient
from ruisheng_api.deps import get_redis
from ruisheng_api.main import create_app


def _env(m):
    m.setenv("API_DB_URL", "postgresql+asyncpg://u:p@h/d")
    m.setenv("API_GW_DB_URL", "postgresql+asyncpg://u:p@h/d")
    m.setenv("API_REDIS_URL", "redis://:p@h/0")
    m.setenv("API_JWT_SECRET", "x" * 64)


def test_create_order_requires_auth(monkeypatch):
    _env(monkeypatch)
    app = create_app()
    r = fakeredis.aioredis.FakeRedis()
    app.dependency_overrides[get_redis] = lambda: r
    resp = TestClient(app).post(
        "/api/pay/orders", json={"openid": "abcde", "amount_fen": 100, "description": "test"}
    )
    assert resp.status_code == 401
