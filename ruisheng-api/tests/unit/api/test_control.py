import fakeredis.aioredis
from fastapi.testclient import TestClient
from ruisheng_api.core.security import client_fingerprint, issue_access_token
from ruisheng_api.db.repositories import control as control_repo
from ruisheng_api.db.repositories import devices as devices_repo
from ruisheng_api.deps import get_redis, get_session
from ruisheng_api.main import create_app


def _env(m):
    m.setenv("API_DB_URL", "postgresql+asyncpg://u:p@h/d")
    m.setenv("API_GW_DB_URL", "postgresql+asyncpg://u:p@h/d")
    m.setenv("API_REDIS_URL", "redis://:p@h/0")
    m.setenv("API_JWT_SECRET", "x" * 64)


def _tok(ca):
    fp = client_fingerprint("testclient", "testclient")
    return issue_access_token("alice", "g1", "User", ca, fp, secret="x" * 64, ttl_sec=900)


class _S:
    def begin(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        pass

    async def execute(self, *a, **kw):
        return None


def _install_happy(app, monkeypatch):
    r = fakeredis.aioredis.FakeRedis()
    app.dependency_overrides[get_redis] = lambda: r

    async def fake_session():
        yield _S()

    app.dependency_overrides[get_session] = fake_session

    async def fake_get(session, dev_number):
        return type("D", (), {"dev_number": dev_number, "usr_group": "g1"})()

    async def fake_dup(session, **kw):
        return False

    async def fake_ins(session, **kw):
        return 1

    async def fake_ctx(session, usr_group, role):
        return None

    monkeypatch.setattr(devices_repo, "get_by_dev_number", fake_get)
    monkeypatch.setattr(control_repo, "check_recent_duplicate", fake_dup)
    monkeypatch.setattr(control_repo, "insert_action", fake_ins)
    from ruisheng_api.api import control as ctrlapi

    monkeypatch.setattr(ctrlapi, "apply_tenant_context", fake_ctx)
    return r


def test_control_requires_ca_bit0(monkeypatch):
    _env(monkeypatch)
    app = create_app()
    _install_happy(app, monkeypatch)
    resp = TestClient(app).post(
        "/api/devices/60270012/control",
        json={"fun_code": 6, "reg": 0, "value": 1},
        headers={"Authorization": f"Bearer {_tok(ca=0)}"},
    )
    assert resp.status_code == 403


def test_control_happy(monkeypatch):
    _env(monkeypatch)
    app = create_app()
    _install_happy(app, monkeypatch)
    resp = TestClient(app).post(
        "/api/devices/60270012/control",
        json={"fun_code": 6, "reg": 0, "value": 1},
        headers={"Authorization": f"Bearer {_tok(ca=1)}"},
    )
    assert resp.status_code == 200, resp.text
    d = resp.json()["data"]
    assert d["status"] == "pending"
    assert len(d["cmd_id"]) == 26


def test_control_high_risk_requires_otp(monkeypatch):
    _env(monkeypatch)
    app = create_app()
    _install_happy(app, monkeypatch)
    resp = TestClient(app).post(
        "/api/devices/60270012/control",
        json={"fun_code": 6, "reg": 0, "value": 1, "high_risk": True},
        headers={"Authorization": f"Bearer {_tok(ca=0x05)}"},
    )
    assert resp.status_code == 401
