import fakeredis
import fakeredis.aioredis
from fastapi.testclient import TestClient
from jose import jwt
from ruisheng_api.core.security import (
    client_fingerprint,
    issue_access_token,
    issue_refresh_token,
)
from ruisheng_api.db.repositories import users as users_repo
from ruisheng_api.deps import get_gw_session, get_redis
from ruisheng_api.main import create_app


def _env(m):
    m.setenv("API_DB_URL", "postgresql+asyncpg://u:p@h/d")
    m.setenv("API_GW_DB_URL", "postgresql+asyncpg://u:p@h/d")
    m.setenv("API_REDIS_URL", "redis://:p@h/0")
    m.setenv("API_JWT_SECRET", "x" * 64)


# TestClient sends IP="testclient", user-agent="testclient"
_FP = client_fingerprint("testclient", "testclient")
_SECRET = "x" * 64


def _install_user(app, monkeypatch, *, authority="User", usr_group="g", ca=0):
    async def fake_session():
        yield object()

    app.dependency_overrides[get_gw_session] = fake_session

    async def fake_load(session, user_name):
        return type(
            "U",
            (),
            {
                "user_name": user_name,
                "usr_group": usr_group,
                "authority": authority,
                "control_authority": ca,
            },
        )()

    monkeypatch.setattr(users_repo, "load_by_user_name", fake_load)


def test_refresh_rotates(monkeypatch):
    _env(monkeypatch)
    app = create_app()
    _install_user(app, monkeypatch)
    server = fakeredis.FakeServer()
    r_sync = fakeredis.FakeRedis(server=server)
    r_async = fakeredis.aioredis.FakeRedis(server=server)
    app.dependency_overrides[get_redis] = lambda: r_async

    old = issue_refresh_token("a", "g", "User", 0, _FP, secret=_SECRET, ttl_sec=3600)
    resp = TestClient(app).post("/api/auth/refresh", json={"refresh_token": old})
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["access_token"]
    assert data["refresh_token"] != old

    old_jti = jwt.decode(old, _SECRET, algorithms=["HS256"])["jti"]
    assert r_sync.exists(f"jwt_blacklist:{old_jti}")
    assert not r_sync.sismember("jwt_blacklist", old_jti)


def test_refresh_uses_current_user_claims(monkeypatch):
    _env(monkeypatch)
    app = create_app()
    _install_user(app, monkeypatch, authority="Company", usr_group="g2", ca=7)
    server = fakeredis.FakeServer()
    r_async = fakeredis.aioredis.FakeRedis(server=server)
    app.dependency_overrides[get_redis] = lambda: r_async

    old = issue_refresh_token("a", "old", "User", 0, _FP, secret=_SECRET, ttl_sec=3600)
    resp = TestClient(app).post("/api/auth/refresh", json={"refresh_token": old})
    payload = jwt.decode(resp.json()["data"]["access_token"], _SECRET, algorithms=["HS256"])
    assert payload["usr_group"] == "g2"
    assert payload["role"] == "Company"
    assert payload["ca"] == 7


def test_refresh_rejects_access_token(monkeypatch):
    _env(monkeypatch)
    app = create_app()
    _install_user(app, monkeypatch)
    server = fakeredis.FakeServer()
    r_async = fakeredis.aioredis.FakeRedis(server=server)
    app.dependency_overrides[get_redis] = lambda: r_async

    access = issue_access_token("a", "g", "User", 0, _FP, secret=_SECRET, ttl_sec=900)
    assert (
        TestClient(app).post("/api/auth/refresh", json={"refresh_token": access}).status_code == 401
    )


def test_logout_blacklists_jti(monkeypatch):
    _env(monkeypatch)
    app = create_app()
    server = fakeredis.FakeServer()
    r_sync = fakeredis.FakeRedis(server=server)
    r_async = fakeredis.aioredis.FakeRedis(server=server)
    app.dependency_overrides[get_redis] = lambda: r_async

    access = issue_access_token("a", "g", "User", 0, _FP, secret=_SECRET, ttl_sec=900)
    jti = jwt.decode(access, _SECRET, algorithms=["HS256"])["jti"]

    resp = TestClient(app).post(
        "/api/auth/logout",
        json={},
        headers={"Authorization": f"Bearer {access}"},
    )
    assert resp.status_code == 200, resp.text
    assert r_sync.exists(f"jwt_blacklist:{jti}")
    assert not r_sync.sismember("jwt_blacklist", jti)


def test_otp_send_requires_login(monkeypatch):
    _env(monkeypatch)
    app = create_app()
    server = fakeredis.FakeServer()
    r_async = fakeredis.aioredis.FakeRedis(server=server)
    app.dependency_overrides[get_redis] = lambda: r_async

    assert (
        TestClient(app)
        .post(
            "/api/auth/otp/send",
            json={"action": "cross_tenant", "channel": "sms"},
        )
        .status_code
        == 401
    )
