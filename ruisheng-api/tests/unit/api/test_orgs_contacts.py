"""Tests for G2: wx_groups list + user contacts (phones/emails)."""

from __future__ import annotations

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


def _tok(role="Administrators", user_name="alice"):
    fp = client_fingerprint("testclient", "testclient")
    return issue_access_token(user_name, "g1", role, 0, fp, secret="x" * 64, ttl_sec=900)


def _install(app, monkeypatch):
    r = fakeredis.aioredis.FakeRedis()
    app.dependency_overrides[get_redis] = lambda: r

    async def fake_session():
        yield _S()

    app.dependency_overrides[get_session] = fake_session

    async def fake_apply(*a, **kw):
        return None

    from ruisheng_api.api import orgs as orgsapi

    monkeypatch.setattr(orgsapi, "apply_tenant_context", fake_apply)
    return r


# ---------------------------------------------------------------------------
# WxGroups
# ---------------------------------------------------------------------------


def test_list_wx_groups_empty(monkeypatch):
    _env(monkeypatch)
    app = create_app()
    _install(app, monkeypatch)

    async def fake_wx_groups(session, *, usr_group):
        return []

    monkeypatch.setattr(users_repo, "list_wx_groups", fake_wx_groups)

    resp = TestClient(app).get(
        "/api/orgs/wx_groups",
        headers={"Authorization": f"Bearer {_tok()}"},
    )
    assert resp.status_code == 200
    assert resp.json()["data"] == {"items": []}


def test_list_wx_groups_requires_auth(monkeypatch):
    _env(monkeypatch)
    app = create_app()
    r = fakeredis.aioredis.FakeRedis()
    app.dependency_overrides[get_redis] = lambda: r
    assert TestClient(app).get("/api/orgs/wx_groups").status_code == 401


def test_list_wx_groups_requires_group_company_or_admin(monkeypatch):
    _env(monkeypatch)
    app = create_app()
    _install(app, monkeypatch)

    async def fake_wx_groups(session, *, usr_group):
        return []

    monkeypatch.setattr(users_repo, "list_wx_groups", fake_wx_groups)

    # User role should be forbidden
    resp = TestClient(app).get(
        "/api/orgs/wx_groups",
        headers={"Authorization": f"Bearer {_tok(role='User')}"},
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Phones
# ---------------------------------------------------------------------------


def test_list_phones_empty(monkeypatch):
    _env(monkeypatch)
    app = create_app()
    _install(app, monkeypatch)

    async def fake_phones(session, user_name):
        return []

    monkeypatch.setattr(users_repo, "list_phones", fake_phones)

    resp = TestClient(app).get(
        "/api/orgs/users/alice/phones",
        headers={"Authorization": f"Bearer {_tok()}"},
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["items"] == []


def test_list_phones_self_allowed(monkeypatch):
    """A plain User can list their own phones."""
    _env(monkeypatch)
    app = create_app()
    _install(app, monkeypatch)

    async def fake_phones(session, user_name):
        return []

    monkeypatch.setattr(users_repo, "list_phones", fake_phones)

    resp = TestClient(app).get(
        "/api/orgs/users/alice/phones",
        headers={"Authorization": f"Bearer {_tok(role='User', user_name='alice')}"},
    )
    assert resp.status_code == 200


def test_list_phones_other_user_requires_role(monkeypatch):
    """A plain User cannot list another user's phones."""
    _env(monkeypatch)
    app = create_app()
    _install(app, monkeypatch)

    async def fake_phones(session, user_name):
        return []

    monkeypatch.setattr(users_repo, "list_phones", fake_phones)

    resp = TestClient(app).get(
        "/api/orgs/users/bob/phones",
        headers={"Authorization": f"Bearer {_tok(role='User', user_name='alice')}"},
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Emails
# ---------------------------------------------------------------------------


def test_list_emails_empty(monkeypatch):
    _env(monkeypatch)
    app = create_app()
    _install(app, monkeypatch)

    async def fake_emails(session, user_name):
        return []

    monkeypatch.setattr(users_repo, "list_emails", fake_emails)

    resp = TestClient(app).get(
        "/api/orgs/users/alice/emails",
        headers={"Authorization": f"Bearer {_tok()}"},
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["items"] == []


def test_list_emails_self_allowed(monkeypatch):
    """A plain User can list their own emails."""
    _env(monkeypatch)
    app = create_app()
    _install(app, monkeypatch)

    async def fake_emails(session, user_name):
        return []

    monkeypatch.setattr(users_repo, "list_emails", fake_emails)

    resp = TestClient(app).get(
        "/api/orgs/users/alice/emails",
        headers={"Authorization": f"Bearer {_tok(role='User', user_name='alice')}"},
    )
    assert resp.status_code == 200
