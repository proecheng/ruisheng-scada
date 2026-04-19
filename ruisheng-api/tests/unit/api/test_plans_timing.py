"""Unit tests for /api/plans/timing CRUD endpoints."""

import fakeredis.aioredis
from fastapi.testclient import TestClient
from ruisheng_api.core.security import client_fingerprint, issue_access_token
from ruisheng_api.db.repositories import plans as plans_repo
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


def _tok(role="Company"):
    fp = client_fingerprint("testclient", "testclient")
    return issue_access_token("alice", "g1", role, 0, fp, secret="x" * 64, ttl_sec=900)


def _install(app, monkeypatch):
    r = fakeredis.aioredis.FakeRedis()
    app.dependency_overrides[get_redis] = lambda: r

    async def fake_session():
        yield _S()

    app.dependency_overrides[get_session] = fake_session

    async def fake_list(session, dev_number=None):
        return []

    async def fake_apply(*a, **kw):
        return None

    monkeypatch.setattr(plans_repo, "list_timing_plans", fake_list)
    from ruisheng_api.api import plans as plansapi

    monkeypatch.setattr(plansapi, "apply_tenant_context", fake_apply)
    return r


def test_list_timing_plans_requires_auth(monkeypatch):
    _env(monkeypatch)
    app = create_app()
    r = fakeredis.aioredis.FakeRedis()
    app.dependency_overrides[get_redis] = lambda: r
    assert TestClient(app).get("/api/plans/timing").status_code == 401


def test_list_timing_plans_empty(monkeypatch):
    _env(monkeypatch)
    app = create_app()
    _install(app, monkeypatch)
    resp = TestClient(app).get(
        "/api/plans/timing",
        headers={"Authorization": f"Bearer {_tok()}"},
    )
    assert resp.status_code == 200
    assert resp.json()["data"] == {"items": []}


def test_list_timing_plans_with_dev_filter(monkeypatch):
    _env(monkeypatch)
    app = create_app()
    _install(app, monkeypatch)
    resp = TestClient(app).get(
        "/api/plans/timing?dev_number=60270012",
        headers={"Authorization": f"Bearer {_tok()}"},
    )
    assert resp.status_code == 200
    assert resp.json()["data"] == {"items": []}


def test_create_timing_plan_requires_company_role(monkeypatch):
    _env(monkeypatch)
    app = create_app()
    _install(app, monkeypatch)
    # User role is not allowed to create
    user_tok = _tok(role="User")
    resp = TestClient(app).post(
        "/api/plans/timing",
        json={
            "dev_number": "60270012",
            "action_at": "2026-05-01T08:00:00Z",
            "action": 1,
        },
        headers={"Authorization": f"Bearer {user_tok}"},
    )
    assert resp.status_code == 403


def test_delete_timing_plan_not_found(monkeypatch):
    _env(monkeypatch)
    app = create_app()
    _install(app, monkeypatch)

    async def fake_get(session, plan_id):
        return None

    async def fake_apply(*a, **kw):
        return None

    monkeypatch.setattr(plans_repo, "get_timing_plan", fake_get)
    from ruisheng_api.api import plans as plansapi

    monkeypatch.setattr(plansapi, "apply_tenant_context", fake_apply)

    resp = TestClient(app).delete(
        "/api/plans/timing/999",
        headers={"Authorization": f"Bearer {_tok(role='Company')}"},
    )
    assert resp.status_code == 400


def test_update_timing_plan_no_fields(monkeypatch):
    _env(monkeypatch)
    app = create_app()
    _install(app, monkeypatch)

    async def fake_apply(*a, **kw):
        return None

    from ruisheng_api.api import plans as plansapi

    monkeypatch.setattr(plansapi, "apply_tenant_context", fake_apply)

    # sending empty body → BAD_PARAM "no fields"
    resp = TestClient(app).put(
        "/api/plans/timing/1",
        json={},
        headers={"Authorization": f"Bearer {_tok(role='Company')}"},
    )
    assert resp.status_code == 400
