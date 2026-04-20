import fakeredis.aioredis
from fastapi.testclient import TestClient
from ruisheng_api.core.security import client_fingerprint, issue_access_token
from ruisheng_api.db.repositories import control as control_repo
from ruisheng_api.deps import get_redis, get_session
from ruisheng_api.main import create_app


def _env(m):
    m.setenv("API_DB_URL", "postgresql+asyncpg://u:p@h/d")
    m.setenv("API_GW_DB_URL", "postgresql+asyncpg://u:p@h/d")
    m.setenv("API_REDIS_URL", "redis://:p@h/0")
    m.setenv("API_JWT_SECRET", "x" * 64)


def _tok(role="User", ca=0):
    fp = client_fingerprint("testclient", "testclient")
    return issue_access_token("alice", "g1", role, ca, fp, secret="x" * 64, ttl_sec=900)


class _S:
    def begin(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        pass

    async def execute(self, *a, **kw):
        return None


def _base_install(app, monkeypatch):
    r = fakeredis.aioredis.FakeRedis()
    app.dependency_overrides[get_redis] = lambda: r

    async def fake_session():
        yield _S()

    app.dependency_overrides[get_session] = fake_session
    from ruisheng_api.api import control as ctrlapi

    async def fake_apply(*a, **kw):
        return None

    monkeypatch.setattr(ctrlapi, "apply_tenant_context", fake_apply)
    return r


def test_list_commands_requires_auth(monkeypatch):
    _env(monkeypatch)
    app = create_app()
    r = fakeredis.aioredis.FakeRedis()
    app.dependency_overrides[get_redis] = lambda: r
    assert TestClient(app).get("/api/control/commands").status_code == 401


def test_list_commands_happy(monkeypatch):
    _env(monkeypatch)
    app = create_app()
    _base_install(app, monkeypatch)

    async def fake_list(session, **kw):
        return []

    monkeypatch.setattr(control_repo, "list_actions", fake_list)
    resp = TestClient(app).get(
        "/api/control/commands",
        headers={"Authorization": f"Bearer {_tok()}"},
    )
    assert resp.status_code == 200
    assert resp.json()["data"] == {"items": []}


def test_cancel_not_found(monkeypatch):
    _env(monkeypatch)
    app = create_app()
    _base_install(app, monkeypatch)

    async def fake_cancel(session, cmd_id):
        return False

    monkeypatch.setattr(control_repo, "cancel_action", fake_cancel)
    resp = TestClient(app).delete(
        "/api/control/commands/non-existent-cmd",
        headers={"Authorization": f"Bearer {_tok()}"},
    )
    assert resp.status_code == 400
