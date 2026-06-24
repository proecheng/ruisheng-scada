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


def test_add_phone_requires_visible_target_user(monkeypatch):
    _env(monkeypatch)
    app = create_app()
    _install(app, monkeypatch)

    async def fake_load(session, user_name):
        return None

    monkeypatch.setattr(users_repo, "load_by_user_name", fake_load)
    resp = TestClient(app).post(
        "/api/orgs/users/bob/phones",
        json={"phone_number": "13800000000"},
        headers={"Authorization": f"Bearer {_tok()}"},
    )
    assert resp.status_code == 400


def test_add_phone_posts_phone_number(monkeypatch):
    _env(monkeypatch)
    app = create_app()
    _install(app, monkeypatch)

    async def fake_load(session, user_name):
        return object()

    async def fake_add_phone(session, *, user_name, phone_number):
        assert user_name == "bob"
        assert phone_number == "13800000000"
        return type("P", (), {"id": 1, "phone_number": phone_number})()

    monkeypatch.setattr(users_repo, "load_by_user_name", fake_load)
    monkeypatch.setattr(users_repo, "add_phone", fake_add_phone)
    resp = TestClient(app).post(
        "/api/orgs/users/bob/phones",
        json={"phone_number": "13800000000"},
        headers={"Authorization": f"Bearer {_tok()}"},
    )
    assert resp.status_code == 200
    assert resp.json()["data"] == {"id": 1, "phone_number": "13800000000"}


def test_delete_phone_scopes_to_path_user(monkeypatch):
    _env(monkeypatch)
    app = create_app()
    _install(app, monkeypatch)
    seen: dict[str, object] = {}

    async def fake_delete_phone(session, *, user_name, phone_id):
        seen["user_name"] = user_name
        seen["phone_id"] = phone_id
        return True

    monkeypatch.setattr(users_repo, "delete_phone", fake_delete_phone)
    resp = TestClient(app).delete(
        "/api/orgs/users/bob/phones/12",
        headers={"Authorization": f"Bearer {_tok()}"},
    )
    assert resp.status_code == 200
    assert seen == {"user_name": "bob", "phone_id": 12}


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


def test_add_email_requires_phone_owned_by_path_user(monkeypatch):
    _env(monkeypatch)
    app = create_app()
    _install(app, monkeypatch)

    async def fake_load(session, user_name):
        return object()

    async def fake_add_email(session, *, user_name, phone_number, email):
        assert user_name == "bob"
        assert phone_number == "13800000000"
        assert email == "ops@example.com"

    monkeypatch.setattr(users_repo, "load_by_user_name", fake_load)
    monkeypatch.setattr(users_repo, "add_email", fake_add_email)
    resp = TestClient(app).post(
        "/api/orgs/users/bob/emails",
        json={"phone_number": "13800000000", "email": "ops@example.com"},
        headers={"Authorization": f"Bearer {_tok()}"},
    )
    assert resp.status_code == 400


def test_add_email_posts_phone_number_and_email(monkeypatch):
    _env(monkeypatch)
    app = create_app()
    _install(app, monkeypatch)

    async def fake_load(session, user_name):
        return object()

    async def fake_add_email(session, *, user_name, phone_number, email):
        return type("E", (), {"id": 2, "phone_number": phone_number, "email": email})()

    monkeypatch.setattr(users_repo, "load_by_user_name", fake_load)
    monkeypatch.setattr(users_repo, "add_email", fake_add_email)
    resp = TestClient(app).post(
        "/api/orgs/users/bob/emails",
        json={"phone_number": "13800000000", "email": "ops@example.com"},
        headers={"Authorization": f"Bearer {_tok()}"},
    )
    assert resp.status_code == 200
    assert resp.json()["data"] == {
        "id": 2,
        "phone_number": "13800000000",
        "email": "ops@example.com",
    }


def test_delete_email_scopes_to_path_user(monkeypatch):
    _env(monkeypatch)
    app = create_app()
    _install(app, monkeypatch)
    seen: dict[str, object] = {}

    async def fake_delete_email(session, *, user_name, email_id):
        seen["user_name"] = user_name
        seen["email_id"] = email_id
        return True

    monkeypatch.setattr(users_repo, "delete_email", fake_delete_email)
    resp = TestClient(app).delete(
        "/api/orgs/users/bob/emails/9",
        headers={"Authorization": f"Bearer {_tok()}"},
    )
    assert resp.status_code == 200
    assert seen == {"user_name": "bob", "email_id": 9}
