from types import SimpleNamespace

import fakeredis
import fakeredis.aioredis
import pytest
from fastapi.testclient import TestClient
from jose import jwt
from ruisheng_api.api.auth import login
from ruisheng_api.api.schemas.auth import LoginRequest
from ruisheng_api.config import Config
from ruisheng_api.core.security import hash_password
from ruisheng_api.db.repositories import users as users_repo
from ruisheng_api.deps import get_gw_session, get_redis, get_session
from ruisheng_api.main import create_app
from ruisheng_shared.errors.codes import BizError


def _env(m):
    m.setenv("API_DB_URL", "postgresql+asyncpg://u:p@h/d")
    m.setenv("API_GW_DB_URL", "postgresql+asyncpg://u:p@h/d")
    m.setenv("API_REDIS_URL", "redis://:p@h/0")
    m.setenv("API_JWT_SECRET", "x" * 64)


class _FakeUser:
    def __init__(self, *, user_name, password_hash, authority, usr_group, ca=0):
        self.user_name = user_name
        self.password_hash = password_hash
        self.authority = authority
        self.usr_group = usr_group
        self.control_authority = ca
        self.deleted_at = None


def _install(app, monkeypatch, user):
    server = fakeredis.FakeServer()
    r = fakeredis.aioredis.FakeRedis(server=server)
    r_sync = fakeredis.FakeRedis(server=server)
    app.dependency_overrides[get_redis] = lambda: r

    async def fake_session():
        yield object()

    app.dependency_overrides[get_session] = fake_session
    app.dependency_overrides[get_gw_session] = fake_session

    async def fake_load(session, uname):
        return user if user and uname == user.user_name else None

    monkeypatch.setattr(users_repo, "load_by_user_name", fake_load)
    return r_sync


def test_login_happy(monkeypatch):
    _env(monkeypatch)
    app = create_app()
    _install(
        app,
        monkeypatch,
        _FakeUser(
            user_name="alice",
            password_hash=hash_password("hunter2"),
            authority="User",
            usr_group="g1",
        ),
    )
    resp = TestClient(app).post(
        "/api/auth/login", json={"user_name": "alice", "password": "hunter2"}
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["access_token"] and data["refresh_token"]
    assert data["role"] == "User"
    access = jwt.decode(data["access_token"], "x" * 64, algorithms=["HS256"])
    refresh = jwt.decode(data["refresh_token"], "x" * 64, algorithms=["HS256"])
    assert access["exp"] - access["iat"] == 900
    assert access["session_exp"] - access["iat"] == 24 * 3600
    assert refresh["session_exp"] == access["session_exp"] == refresh["exp"]


def test_login_wrong_password(monkeypatch):
    _env(monkeypatch)
    app = create_app()
    _install(
        app,
        monkeypatch,
        _FakeUser(
            user_name="alice",
            password_hash=hash_password("hunter2"),
            authority="User",
            usr_group="g1",
        ),
    )
    resp = TestClient(app).post(
        "/api/auth/login", json={"user_name": "alice", "password": "wrongpass"}
    )
    assert resp.status_code == 401


def test_login_unknown_user(monkeypatch):
    _env(monkeypatch)
    app = create_app()
    _install(app, monkeypatch, None)
    resp = TestClient(app).post(
        "/api/auth/login", json={"user_name": "nobody", "password": "anypass123"}
    )
    assert resp.status_code == 401


def _request(host: str) -> SimpleNamespace:
    return SimpleNamespace(
        client=SimpleNamespace(host=host),
        headers={
            "user-agent": "browser",
            "x-forwarded-for": "203.0.113.10",
            "x-real-ip": "203.0.113.10",
        },
    )


@pytest.mark.asyncio
async def test_shared_ingress_failures_do_not_block_other_browsers(monkeypatch):
    _env(monkeypatch)
    monkeypatch.setenv("API_TRUSTED_PROXY_CIDRS", "10.254.250.0/24")
    monkeypatch.setenv("API_LOGIN_FAIL_IP_MAX", "2")
    monkeypatch.setattr(users_repo, "load_by_user_name", lambda session, uname: _none())
    redis = fakeredis.aioredis.FakeRedis()
    request = _request("10.254.250.8")
    for name in ("nobody", "nobody", "other"):
        with pytest.raises(BizError, match="invalid credentials"):
            await login(
                LoginRequest(user_name=name, password="anypass123"),
                request,  # type: ignore[arg-type]
                Config(),
                redis,
                object(),
            )
    assert await redis.exists("ip_block:10.254.250.8") == 0
    assert await redis.exists("ip_block:203.0.113.10") == 0


async def _none():
    return None


@pytest.mark.asyncio
async def test_direct_peer_is_still_ip_blocked(monkeypatch):
    _env(monkeypatch)
    monkeypatch.setenv("API_TRUSTED_PROXY_CIDRS", "10.254.250.0/24")
    monkeypatch.setenv("API_LOGIN_FAIL_IP_MAX", "1")
    monkeypatch.setattr(users_repo, "load_by_user_name", lambda session, uname: _none())
    redis = fakeredis.aioredis.FakeRedis()
    request = _request("203.0.113.10")
    with pytest.raises(BizError, match="invalid credentials"):
        await login(
            LoginRequest(user_name="nobody", password="anypass123"),
            request,  # type: ignore[arg-type]
            Config(),
            redis,
            object(),
        )
    with pytest.raises(BizError, match="ip temporarily blocked"):
        await login(
            LoginRequest(user_name="other", password="anypass123"),
            request,  # type: ignore[arg-type]
            Config(),
            redis,
            object(),
        )


@pytest.mark.asyncio
async def test_shared_ingress_still_locks_the_account(monkeypatch):
    _env(monkeypatch)
    monkeypatch.setenv("API_TRUSTED_PROXY_CIDRS", "10.254.250.0/24")
    monkeypatch.setenv("API_LOGIN_FAIL_USER_MAX", "2")
    user = _FakeUser(
        user_name="alice",
        password_hash=hash_password("hunter2"),
        authority="User",
        usr_group="g1",
    )

    async def fake_load(session, uname):
        return user if uname == user.user_name else None

    monkeypatch.setattr(users_repo, "load_by_user_name", fake_load)
    redis = fakeredis.aioredis.FakeRedis()
    request = _request("10.254.250.8")
    body = LoginRequest(user_name="alice", password="wrongpass")
    for _ in range(2):
        with pytest.raises(BizError, match="invalid credentials"):
            await login(body, request, Config(), redis, object())  # type: ignore[arg-type]
    with pytest.raises(BizError, match="account locked"):
        await login(body, request, Config(), redis, object())  # type: ignore[arg-type]


def test_login_locked_user(monkeypatch):
    _env(monkeypatch)
    app = create_app()
    r_sync = _install(
        app,
        monkeypatch,
        _FakeUser(
            user_name="alice",
            password_hash=hash_password("hunter2"),
            authority="User",
            usr_group="g1",
        ),
    )
    # Use sync FakeRedis (same underlying server) to seed the lock key
    r_sync.setex("login_lock:alice", 1800, "1")
    resp = TestClient(app).post(
        "/api/auth/login", json={"user_name": "alice", "password": "hunter2"}
    )
    assert resp.status_code == 403
