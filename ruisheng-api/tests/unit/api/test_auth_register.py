import fakeredis.aioredis
from fastapi.testclient import TestClient
from ruisheng_api.db.repositories import users as users_repo
from ruisheng_api.deps import get_redis, get_session
from ruisheng_api.main import create_app


def _env(m):
    m.setenv("API_DB_URL", "postgresql+asyncpg://u:p@h/d")
    m.setenv("API_GW_DB_URL", "postgresql+asyncpg://u:p@h/d")
    m.setenv("API_REDIS_URL", "redis://:p@h/0")
    m.setenv("API_JWT_SECRET", "x" * 64)


def test_sms_send(monkeypatch):
    _env(monkeypatch)
    app = create_app()
    r = fakeredis.aioredis.FakeRedis()
    app.dependency_overrides[get_redis] = lambda: r
    resp = TestClient(app).post(
        "/api/auth/sms/send",
        json={"action": "register", "channel": "sms", "phone_number": "13812345678"},
    )
    assert resp.status_code == 200
    assert resp.json()["code"] == 0


def test_register_happy(monkeypatch):
    _env(monkeypatch)
    app = create_app()

    # Use FakeServer for cross-context sharing
    import fakeredis

    server = fakeredis.FakeServer()
    r_sync = fakeredis.FakeRedis(server=server)
    r_async = fakeredis.aioredis.FakeRedis(server=server)
    app.dependency_overrides[get_redis] = lambda: r_async

    async def fake_session():
        yield object()

    app.dependency_overrides[get_session] = fake_session

    created = {}

    async def fake_create(session, **fields):
        created.update(fields)
        return type("U", (), {**fields, "id": 1})()

    async def fake_load(session, uname):
        return None

    monkeypatch.setattr(users_repo, "create_user", fake_create)
    monkeypatch.setattr(users_repo, "load_by_user_name", fake_load)

    # Issue OTP synchronously using sync FakeRedis pointing to same server
    r_sync.set("otp:register:13812345678", "123456")

    resp = TestClient(app).post(
        "/api/auth/register",
        json={"user_name": "13812345678", "password": "hunter2xx", "otp_code": "123456"},
    )
    assert resp.status_code == 200
    assert created["user_name"] == "13812345678"
    assert created["authority"] == "User"


def test_register_bad_otp(monkeypatch):
    _env(monkeypatch)
    app = create_app()
    r = fakeredis.aioredis.FakeRedis()
    app.dependency_overrides[get_redis] = lambda: r

    async def fake_session():
        yield object()

    app.dependency_overrides[get_session] = fake_session
    resp = TestClient(app).post(
        "/api/auth/register",
        json={"user_name": "13812345678", "password": "hunter2xx", "otp_code": "000000"},
    )
    assert resp.status_code == 401
