"""Unit tests for /api/scenes/pages (scene_pages + scene_views) endpoints."""

import fakeredis.aioredis
from fastapi.testclient import TestClient
from ruisheng_api.core.security import client_fingerprint, issue_access_token
from ruisheng_api.db.repositories import scenes as scenes_repo
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

    async def fake_list_pages(session):
        return []

    async def fake_apply(*a, **kw):
        return None

    monkeypatch.setattr(scenes_repo, "list_pages", fake_list_pages)
    from ruisheng_api.api import scenes as scenesapi

    monkeypatch.setattr(scenesapi, "apply_tenant_context", fake_apply)
    return r


def test_list_pages_requires_auth(monkeypatch):
    _env(monkeypatch)
    app = create_app()
    r = fakeredis.aioredis.FakeRedis()
    app.dependency_overrides[get_redis] = lambda: r
    assert TestClient(app).get("/api/scenes/pages").status_code == 401


def test_list_pages_empty(monkeypatch):
    _env(monkeypatch)
    app = create_app()
    _install(app, monkeypatch)
    resp = TestClient(app).get(
        "/api/scenes/pages",
        headers={"Authorization": f"Bearer {_tok()}"},
    )
    assert resp.status_code == 200
    assert resp.json()["data"] == {"items": []}


def test_delete_page_not_found(monkeypatch):
    _env(monkeypatch)
    app = create_app()
    _install(app, monkeypatch)

    async def fake_get(session, page_id):
        return None

    async def fake_apply(*a, **kw):
        return None

    monkeypatch.setattr(scenes_repo, "get_page", fake_get)
    from ruisheng_api.api import scenes as scenesapi

    monkeypatch.setattr(scenesapi, "apply_tenant_context", fake_apply)

    resp = TestClient(app).delete(
        "/api/scenes/pages/999",
        headers={"Authorization": f"Bearer {_tok(role='Company')}"},
    )
    assert resp.status_code == 400


def test_list_pages_requires_role_for_create(monkeypatch):
    _env(monkeypatch)
    app = create_app()
    _install(app, monkeypatch)
    user_tok = _tok(role="User")
    resp = TestClient(app).post(
        "/api/scenes/pages",
        json={
            "page_name": "Test Page",
            "pos_x": "0.00",
            "pos_y": "0.00",
            "radius": "1.00",
        },
        headers={"Authorization": f"Bearer {user_tok}"},
    )
    assert resp.status_code == 403


def test_list_views_requires_auth(monkeypatch):
    _env(monkeypatch)
    app = create_app()
    r = fakeredis.aioredis.FakeRedis()
    app.dependency_overrides[get_redis] = lambda: r
    assert TestClient(app).get("/api/scenes/pages/1/views").status_code == 401


def test_list_views_page_not_found(monkeypatch):
    _env(monkeypatch)
    app = create_app()
    _install(app, monkeypatch)

    async def fake_get_page(session, page_id):
        return None

    async def fake_apply(*a, **kw):
        return None

    monkeypatch.setattr(scenes_repo, "get_page", fake_get_page)
    from ruisheng_api.api import scenes as scenesapi

    monkeypatch.setattr(scenesapi, "apply_tenant_context", fake_apply)

    resp = TestClient(app).get(
        "/api/scenes/pages/999/views",
        headers={"Authorization": f"Bearer {_tok()}"},
    )
    assert resp.status_code == 400
