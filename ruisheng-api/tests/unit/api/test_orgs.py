import fakeredis.aioredis
from fastapi.testclient import TestClient
from ruisheng_api.core.security import client_fingerprint, issue_access_token
from ruisheng_api.db.repositories import users as users_repo
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


def _tok(role="Company", ca=0):
    fp = client_fingerprint("testclient", "testclient")
    return issue_access_token("alice", "g1", role, ca, fp, secret="x" * 64, ttl_sec=900)


def _install(app, monkeypatch):
    r = fakeredis.aioredis.FakeRedis()
    app.dependency_overrides[get_redis] = lambda: r

    async def fake_session():
        yield _S()

    app.dependency_overrides[get_session] = fake_session

    async def fake_list(session, **kw):
        return [], 0

    async def fake_apply(*a, **kw):
        return None

    monkeypatch.setattr(users_repo, "list_users", fake_list)
    from ruisheng_api.api import orgs as orgsapi

    monkeypatch.setattr(orgsapi, "apply_tenant_context", fake_apply)
    return r


def test_list_users_requires_auth(monkeypatch):
    _env(monkeypatch)
    app = create_app()
    r = fakeredis.aioredis.FakeRedis()
    app.dependency_overrides[get_redis] = lambda: r
    assert TestClient(app).get("/api/orgs/users").status_code == 401


def _install_admin_target(app, monkeypatch):
    _install(app, monkeypatch)
    deleted = {"called": False}

    async def fake_load(session, user_name):
        return type("U", (), {"user_name": user_name, "authority": "Administrators"})()

    async def fake_delete(session, user):
        deleted["called"] = True

    monkeypatch.setattr(users_repo, "load_by_user_name", fake_load)
    monkeypatch.setattr(users_repo, "soft_delete_user", fake_delete)
    return deleted


def test_company_cannot_delete_administrator(monkeypatch):
    _env(monkeypatch)
    app = create_app()
    deleted = _install_admin_target(app, monkeypatch)
    response = TestClient(app).delete(
        "/api/orgs/users/admin",
        headers={"Authorization": f"Bearer {_tok(role='Company')}"},
    )
    assert response.status_code == 403
    assert deleted["called"] is False


def test_company_cannot_demote_administrator(monkeypatch):
    _env(monkeypatch)
    app = create_app()
    _install_admin_target(app, monkeypatch)
    response = TestClient(app).put(
        "/api/orgs/users/admin",
        headers={"Authorization": f"Bearer {_tok(role='Company')}"},
        json={"authority": "User"},
    )
    assert response.status_code == 403


def test_list_users_empty(monkeypatch):
    _env(monkeypatch)
    app = create_app()
    _install(app, monkeypatch)
    resp = TestClient(app).get(
        "/api/orgs/users",
        headers={"Authorization": f"Bearer {_tok()}"},
    )
    assert resp.status_code == 200
    assert resp.json()["data"] == {"total": 0, "items": []}
