import fakeredis.aioredis
from fastapi.testclient import TestClient
from ruisheng_api.core.security import client_fingerprint, issue_access_token
from ruisheng_api.db.repositories import devices as devices_repo
from ruisheng_api.deps import get_redis, get_session
from ruisheng_api.main import create_app


def _env(m):
    m.setenv("API_DB_URL", "postgresql+asyncpg://u:p@h/d")
    m.setenv("API_GW_DB_URL", "postgresql+asyncpg://u:p@h/d")
    m.setenv("API_REDIS_URL", "redis://:p@h/0")
    m.setenv("API_JWT_SECRET", "x" * 64)


class _FakeDev:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _install(app, monkeypatch, rows):
    r = fakeredis.aioredis.FakeRedis()
    app.dependency_overrides[get_redis] = lambda: r

    class _S:
        def begin(self):
            return self

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        async def execute(self, *a, **kw):
            return None

    async def fake_session():
        yield _S()

    app.dependency_overrides[get_session] = fake_session

    async def fake_list(session, **kw):
        return rows, len(rows)

    async def fake_apply(session, usr_group, role):
        return None

    monkeypatch.setattr(devices_repo, "list_devices", fake_list)
    from ruisheng_api.api import devices as devapi

    monkeypatch.setattr(devapi, "apply_tenant_context", fake_apply)
    return r


def _token(role="User"):
    fp = client_fingerprint("testclient", "testclient")
    return issue_access_token("alice", "g1", role, 0, fp, secret="x" * 64, ttl_sec=900)


def test_list_devices_empty(monkeypatch):
    _env(monkeypatch)
    app = create_app()
    _install(app, monkeypatch, [])
    resp = TestClient(app).get("/api/devices", headers={"Authorization": f"Bearer {_token()}"})
    assert resp.status_code == 200
    d = resp.json()["data"]
    assert d == {"total": 0, "items": []}


def test_list_devices_requires_auth(monkeypatch):
    _env(monkeypatch)
    app = create_app()
    r = fakeredis.aioredis.FakeRedis()
    app.dependency_overrides[get_redis] = lambda: r
    assert TestClient(app).get("/api/devices").status_code == 401
