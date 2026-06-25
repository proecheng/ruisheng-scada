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


def _tok(role="Company", ca=0x02):
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


class _Template:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _template(**kw):
    defaults = {
        "id": 101,
        "name": "泵站模板",
        "dev_type": "pump",
        "payload": {
            "points": [
                {
                    "point_name": "flow",
                    "point_number": 12,
                    "fun_code": 4,
                    "dev_addr": 1,
                    "value_type": "双字",
                }
            ]
        },
    }
    defaults.update(kw)
    return _Template(**defaults)


def _install(app, monkeypatch, rows=None):
    r = fakeredis.aioredis.FakeRedis()
    app.dependency_overrides[get_redis] = lambda: r

    async def fake_session():
        yield _S()

    app.dependency_overrides[get_session] = fake_session

    async def fake_apply(*a, **kw):
        return None

    async def fake_list(session):
        return rows if rows is not None else [_template()]

    monkeypatch.setattr(devices_repo, "list_templates", fake_list)
    from ruisheng_api.api import templates as tplapi

    monkeypatch.setattr(tplapi, "apply_tenant_context", fake_apply)
    return r


def test_list_device_templates(monkeypatch):
    _env(monkeypatch)
    app = create_app()
    _install(app, monkeypatch)
    resp = TestClient(app).get(
        "/api/device-templates",
        headers={"Authorization": f"Bearer {_tok(role='User', ca=0)}"},
    )
    assert resp.status_code == 200
    item = resp.json()["data"]["items"][0]
    assert item["name"] == "泵站模板"
    assert item["payload"]["points"][0]["fun_code"] == 4


def test_create_device_template(monkeypatch):
    _env(monkeypatch)
    app = create_app()
    _install(app, monkeypatch, [])
    captured = {}

    async def fake_create(session, **fields):
        captured.update(fields)
        return _template(**fields)

    monkeypatch.setattr(devices_repo, "create_template", fake_create)
    resp = TestClient(app).post(
        "/api/device-templates",
        headers={"Authorization": f"Bearer {_tok()}"},
        json={
            "name": "压力模板",
            "dev_type": "pressure",
            "payload": {
                "points": [
                    {
                        "point_name": "pressure",
                        "point_number": 2,
                        "fun_code": 3,
                        "dev_addr": 1,
                        "value_type": "字",
                    }
                ]
            },
        },
    )
    assert resp.status_code == 200
    assert captured["name"] == "压力模板"
    assert captured["payload"]["points"][0]["point_name"] == "pressure"


def test_update_device_template(monkeypatch):
    _env(monkeypatch)
    app = create_app()
    _install(app, monkeypatch, [])
    existing = _template()
    captured = {}

    async def fake_get(session, template_id):
        assert template_id == 101
        return existing

    async def fake_update(session, template, updates):
        captured.update(updates)
        template.__dict__.update(updates)
        return template

    monkeypatch.setattr(devices_repo, "get_template", fake_get)
    monkeypatch.setattr(devices_repo, "update_template_fields", fake_update)
    resp = TestClient(app).put(
        "/api/device-templates/101",
        headers={"Authorization": f"Bearer {_tok()}"},
        json={"name": "泵站模板2", "payload": {"points": []}},
    )
    assert resp.status_code == 200
    assert captured == {"name": "泵站模板2", "payload": {"points": []}}
    assert resp.json()["data"]["name"] == "泵站模板2"


def test_delete_device_template(monkeypatch):
    _env(monkeypatch)
    app = create_app()
    _install(app, monkeypatch, [])
    existing = _template()
    deleted = {}

    async def fake_get(session, template_id):
        return existing

    async def fake_delete(session, template):
        deleted["id"] = template.id

    monkeypatch.setattr(devices_repo, "get_template", fake_get)
    monkeypatch.setattr(devices_repo, "delete_template", fake_delete)
    resp = TestClient(app).delete(
        "/api/device-templates/101",
        headers={"Authorization": f"Bearer {_tok()}"},
    )
    assert resp.status_code == 200
    assert deleted == {"id": 101}


def test_create_template_requires_management_role(monkeypatch):
    _env(monkeypatch)
    app = create_app()
    _install(app, monkeypatch, [])
    resp = TestClient(app).post(
        "/api/device-templates",
        headers={"Authorization": f"Bearer {_tok(role='User', ca=0x02)}"},
        json={"name": "x", "payload": {"points": []}},
    )
    assert resp.status_code == 403
