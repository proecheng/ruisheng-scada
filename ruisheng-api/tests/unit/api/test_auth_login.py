import fakeredis
import fakeredis.aioredis
from fastapi.testclient import TestClient
from ruisheng_api.core.security import hash_password
from ruisheng_api.db.repositories import users as users_repo
from ruisheng_api.deps import get_gw_session, get_redis, get_session
from ruisheng_api.main import create_app


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
