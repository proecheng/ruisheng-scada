import fakeredis.aioredis
from fastapi.testclient import TestClient
from ruisheng_api.core.security import client_fingerprint, issue_access_token
from ruisheng_api.db.repositories import devices as devices_repo
from ruisheng_api.db.repositories import points as points_repo
from ruisheng_api.deps import get_redis, get_session
from ruisheng_api.main import create_app


def _env(m):
    m.setenv("API_DB_URL", "postgresql+asyncpg://u:p@h/d")
    m.setenv("API_GW_DB_URL", "postgresql+asyncpg://u:p@h/d")
    m.setenv("API_REDIS_URL", "redis://:p@h/0")
    m.setenv("API_JWT_SECRET", "x" * 64)


class _S:
    def begin(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        pass

    async def execute(self, *a, **kw):
        return None


def _tok(role="User", ca=0):
    fp = client_fingerprint("testclient", "testclient")
    return issue_access_token("alice", "g1", role, ca, fp, secret="x" * 64, ttl_sec=900)


def _install(app, monkeypatch):
    r = fakeredis.aioredis.FakeRedis()
    app.dependency_overrides[get_redis] = lambda: r

    async def fake_session():
        yield _S()

    app.dependency_overrides[get_session] = fake_session

    async def fake_dev(session, dev_number):
        return type("D", (), {"dev_number": dev_number, "usr_group": "g1"})()

    async def fake_list(session, dev_number):
        return []

    async def fake_apply(*a, **kw):
        return None

    monkeypatch.setattr(devices_repo, "get_by_dev_number", fake_dev)
    monkeypatch.setattr(points_repo, "list_points", fake_list)
    from ruisheng_api.api import points as ptsapi

    monkeypatch.setattr(ptsapi, "apply_tenant_context", fake_apply)
    return r


def test_list_points_empty(monkeypatch):
    _env(monkeypatch)
    app = create_app()
    _install(app, monkeypatch)
    resp = TestClient(app).get(
        "/api/devices/60270012/points",
        headers={"Authorization": f"Bearer {_tok()}"},
    )
    assert resp.status_code == 200
    assert resp.json()["data"] == {"items": []}


def test_list_points_requires_auth(monkeypatch):
    _env(monkeypatch)
    app = create_app()
    r = fakeredis.aioredis.FakeRedis()
    app.dependency_overrides[get_redis] = lambda: r
    assert TestClient(app).get("/api/devices/60270012/points").status_code == 401
