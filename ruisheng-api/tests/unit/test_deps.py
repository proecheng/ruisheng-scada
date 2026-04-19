import fakeredis.aioredis
from fastapi.testclient import TestClient
from ruisheng_api.core.rbac import CurrentUser
from ruisheng_api.core.security import client_fingerprint, issue_access_token
from ruisheng_api.deps import get_current_user, get_redis
from ruisheng_api.main import create_app


def _base_env(m):
    m.setenv("API_DB_URL", "postgresql+asyncpg://u:p@h/d")
    m.setenv("API_GW_DB_URL", "postgresql+asyncpg://u:p@h/d")
    m.setenv("API_REDIS_URL", "redis://:p@h/0")
    m.setenv("API_JWT_SECRET", "x" * 64)


def test_get_current_user_missing_header(monkeypatch):
    _base_env(monkeypatch)
    app = create_app()
    fake = fakeredis.aioredis.FakeRedis()
    app.dependency_overrides[get_redis] = lambda: fake

    import fastapi

    @app.get("/me")
    async def me(u: CurrentUser = fastapi.Depends(get_current_user)):
        return {"u": u.user_name}

    r = TestClient(app).get("/me")
    assert r.status_code == 401
    assert r.json()["code"] == -101


def test_get_current_user_valid(monkeypatch):
    _base_env(monkeypatch)
    app = create_app()
    fake = fakeredis.aioredis.FakeRedis()
    app.dependency_overrides[get_redis] = lambda: fake

    from fastapi import Depends

    @app.get("/me")
    async def me(u: CurrentUser = Depends(get_current_user)):
        return {"u": u.user_name, "role": u.role}

    client = TestClient(app)
    fp = client_fingerprint("testclient", "testclient")
    tok = issue_access_token("alice", "g1", "User", 1, fp, secret="x" * 64, ttl_sec=900)
    r = client.get("/me", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    assert r.json() == {"u": "alice", "role": "User"}


def test_jti_blacklisted_returns_401(monkeypatch):
    _base_env(monkeypatch)
    app = create_app()
    # Use FakeServer so state is shared across event loop runs
    import fakeredis

    server = fakeredis.FakeServer()
    fake = fakeredis.aioredis.FakeRedis(server=server)
    app.dependency_overrides[get_redis] = lambda: fake

    from fastapi import Depends

    @app.get("/me")
    async def me(u: CurrentUser = Depends(get_current_user)):
        return {}

    client = TestClient(app)
    fp = client_fingerprint("testclient", "testclient")
    tok = issue_access_token("a", "g", "User", 0, fp, secret="x" * 64, ttl_sec=900)
    from jose import jwt

    jti = jwt.decode(tok, "x" * 64, algorithms=["HS256"])["jti"]

    import asyncio

    # Use asyncio.run() to avoid event loop mismatch with FakeRedis internals
    asyncio.run(fakeredis.aioredis.FakeRedis(server=server).sadd("jwt_blacklist", jti))

    r = client.get("/me", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 401
