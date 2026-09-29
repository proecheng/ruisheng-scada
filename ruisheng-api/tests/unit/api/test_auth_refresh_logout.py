import time

import fakeredis
import fakeredis.aioredis
import pytest
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

    async def request_redis():
        async with fakeredis.aioredis.FakeRedis(server=server) as r_async:
            yield r_async

    app.dependency_overrides[get_redis] = request_redis

    old = issue_refresh_token("a", "g", "User", 0, _FP, secret=_SECRET, ttl_sec=3600)
    resp = TestClient(app).post("/api/auth/refresh", json={"refresh_token": old})
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["access_token"]
    assert data["refresh_token"] != old

    old_jti = jwt.decode(old, _SECRET, algorithms=["HS256"])["jti"]
    assert r_sync.exists(f"jwt_blacklist:{old_jti}")
    assert not r_sync.sismember("jwt_blacklist", old_jti)
    assert TestClient(app).post("/api/auth/refresh", json={"refresh_token": old}).status_code == 401


def test_refresh_keeps_original_24_hour_deadline_and_caps_access(monkeypatch):
    _env(monkeypatch)
    app = create_app()
    _install_user(app, monkeypatch)
    server = fakeredis.FakeServer()

    async def request_redis():
        async with fakeredis.aioredis.FakeRedis(server=server) as r_async:
            yield r_async

    app.dependency_overrides[get_redis] = request_redis
    now = int(time.time())
    deadline = now + 86400
    token = issue_refresh_token(
        "a",
        "g",
        "User",
        0,
        _FP,
        secret=_SECRET,
        ttl_sec=7 * 86400,
        session_expires_at=deadline,
    )
    client = TestClient(app)
    for elapsed in (14 * 60, 23 * 3600, 86400 - 30):
        monkeypatch.setattr(time, "time", lambda elapsed=elapsed: now + elapsed)
        response = client.post("/api/auth/refresh", json={"refresh_token": token})
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        token = data["refresh_token"]
        access = jwt.get_unverified_claims(data["access_token"])
        refresh = jwt.get_unverified_claims(token)
        assert access["session_exp"] == refresh["session_exp"] == deadline
        assert access["exp"] == min(now + elapsed + 900, deadline)
        assert refresh["exp"] == deadline
    monkeypatch.setattr(time, "time", lambda: deadline)
    assert client.post("/api/auth/refresh", json={"refresh_token": token}).status_code == 401


def test_pre_upgrade_refresh_cannot_extend_past_24_hours(monkeypatch):
    _env(monkeypatch)
    app = create_app()
    _install_user(app, monkeypatch)
    r = fakeredis.aioredis.FakeRedis()
    app.dependency_overrides[get_redis] = lambda: r
    now = int(time.time())
    monkeypatch.setattr(time, "time", lambda: now - 25 * 3600)
    old = issue_refresh_token("a", "g", "User", 0, _FP, secret=_SECRET, ttl_sec=7 * 86400)
    monkeypatch.setattr(time, "time", lambda: now)
    assert TestClient(app).post("/api/auth/refresh", json={"refresh_token": old}).status_code == 401


@pytest.mark.asyncio
async def test_concurrent_refresh_consumes_token_once():
    import asyncio

    from ruisheng_api.core.token_blacklist import consume_refresh_jti

    r = fakeredis.aioredis.FakeRedis()
    results = await asyncio.gather(*(consume_refresh_jti(r, "same-token", 60) for _ in range(8)))
    assert results.count(True) == 1
    await r.aclose()


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
