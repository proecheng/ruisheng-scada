import fakeredis.aioredis
from fastapi.testclient import TestClient
from ruisheng_api.deps import get_redis
from ruisheng_api.main import create_app


def _env(m):
    m.setenv("API_DB_URL", "postgresql+asyncpg://u:p@h/d")
    m.setenv("API_GW_DB_URL", "postgresql+asyncpg://u:p@h/d")
    m.setenv("API_REDIS_URL", "redis://:p@h/0")
    m.setenv("API_JWT_SECRET", "x" * 64)


def test_notify_no_config_returns_fail(monkeypatch):
    _env(monkeypatch)
    app = create_app()
    r = fakeredis.aioredis.FakeRedis()
    app.dependency_overrides[get_redis] = lambda: r
    resp = TestClient(app).post("/wechat/pay/notify", data={})
    assert resp.status_code == 200
    assert "FAIL" in resp.text


def test_notify_bad_sign_returns_fail(monkeypatch):
    _env(monkeypatch)
    monkeypatch.setenv("API_WECHAT_API_V3_KEY", "test_key_32chars_pad_to_enough!!")
    app = create_app()
    r = fakeredis.aioredis.FakeRedis()
    app.dependency_overrides[get_redis] = lambda: r
    resp = TestClient(app).post(
        "/wechat/pay/notify",
        data={
            "out_trade_no": "X",
            "total_fee": "100",
            "time_end": "20260101120000",
            "sign": "BADSIG",
        },
    )
    assert resp.status_code == 200
    assert "FAIL" in resp.text
