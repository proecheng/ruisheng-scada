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


def _device(**kw):
    defaults = {
        "id": 1,
        "dev_number": "DEV002",
        "dev_ser_number": "SN-D2",
        "dev_name": "New",
        "dev_type": "pump",
        "transport_type": "tcp",
        "serial_port": None,
        "modbus_addr": 2,
        "baud_rate": 9600,
        "dev_ip": None,
        "is_enabled": True,
        "is_online": False,
        "last_call_at": None,
        "last_back_at": None,
        "loss_count": 0,
        "update_interval_decisec": 100,
        "group_company": None,
        "company": None,
        "department": None,
        "usr_group": "g1",
    }
    defaults.update(kw)
    return _FakeDev(**defaults)


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

    async def fake_serial_endpoint(session, *, serial_port, modbus_addr):
        return None

    monkeypatch.setattr(devices_repo, "list_devices", fake_list)
    monkeypatch.setattr(devices_repo, "get_serial_endpoint", fake_serial_endpoint)
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


def test_create_device_validates_contract_and_tenant(monkeypatch):
    _env(monkeypatch)
    app = create_app()
    _install(app, monkeypatch, [])
    captured = {}

    async def fake_get(session, dev_number):
        assert dev_number == "DEV002"

    async def fake_create(session, **fields):
        captured.update(fields)
        return _device(**fields)

    monkeypatch.setattr(devices_repo, "get_by_dev_number", fake_get)
    monkeypatch.setattr(devices_repo, "create_device", fake_create)

    resp = TestClient(app).post(
        "/api/devices",
        headers={"Authorization": f"Bearer {_token(role='Company')}"},
        json={
            "dev_number": "DEV002",
            "dev_ser_number": "SN-D2",
            "modbus_addr": 2,
            "baud_rate": 9600,
        },
    )
    assert resp.status_code == 200
    assert captured == {
        "dev_number": "DEV002",
        "dev_ser_number": "SN-D2",
        "transport_type": "tcp",
        "modbus_addr": 2,
        "baud_rate": 9600,
        "update_interval_decisec": 100,
        "usr_group": "g1",
    }
    assert resp.json()["data"]["dev_ser_number"] == "SN-D2"
    assert resp.json()["data"]["transport_type"] == "tcp"


def test_create_serial_device_validates_contract_and_tenant(monkeypatch):
    _env(monkeypatch)
    app = create_app()
    _install(app, monkeypatch, [])
    captured = {}

    async def fake_get(session, dev_number):
        assert dev_number == "DEV003"

    async def fake_create(session, **fields):
        captured.update(fields)
        return _device(**fields)

    monkeypatch.setattr(devices_repo, "get_by_dev_number", fake_get)
    monkeypatch.setattr(devices_repo, "create_device", fake_create)

    resp = TestClient(app).post(
        "/api/devices",
        headers={"Authorization": f"Bearer {_token(role='Company')}"},
        json={
            "dev_number": "DEV003",
            "dev_ser_number": "SN-D3",
            "transport_type": "serial",
            "serial_port": " COM3 ",
            "modbus_addr": 3,
            "baud_rate": 9600,
        },
    )
    assert resp.status_code == 200
    assert captured == {
        "dev_number": "DEV003",
        "dev_ser_number": "SN-D3",
        "transport_type": "serial",
        "serial_port": "COM3",
        "modbus_addr": 3,
        "baud_rate": 9600,
        "update_interval_decisec": 100,
        "usr_group": "g1",
    }
    assert resp.json()["data"]["serial_port"] == "COM3"


def test_create_serial_device_requires_serial_port(monkeypatch):
    _env(monkeypatch)
    app = create_app()
    _install(app, monkeypatch, [])
    resp = TestClient(app).post(
        "/api/devices",
        headers={"Authorization": f"Bearer {_token(role='Company')}"},
        json={
            "dev_number": "DEV003",
            "dev_ser_number": "SN-D3",
            "transport_type": "serial",
            "modbus_addr": 3,
        },
    )
    assert resp.status_code == 400


def test_create_serial_device_rejects_duplicate_endpoint(monkeypatch):
    _env(monkeypatch)
    app = create_app()
    _install(app, monkeypatch, [])

    async def fake_get(session, dev_number):
        assert dev_number == "DEV004"

    async def fake_endpoint(session, *, serial_port, modbus_addr):
        assert serial_port == "COM3"
        assert modbus_addr == 3
        return _device(dev_number="OTHER", serial_port=serial_port, modbus_addr=modbus_addr)

    monkeypatch.setattr(devices_repo, "get_by_dev_number", fake_get)
    monkeypatch.setattr(devices_repo, "get_serial_endpoint", fake_endpoint)

    resp = TestClient(app).post(
        "/api/devices",
        headers={"Authorization": f"Bearer {_token(role='Company')}"},
        json={
            "dev_number": "DEV004",
            "dev_ser_number": "SN-D4",
            "transport_type": "serial",
            "serial_port": "COM3",
            "modbus_addr": 3,
            "baud_rate": 9600,
        },
    )
    assert resp.status_code == 400
    assert resp.json()["msg"] == "serial_port and modbus_addr already in use"


def test_create_device_rejects_extra_fields(monkeypatch):
    _env(monkeypatch)
    app = create_app()
    _install(app, monkeypatch, [])
    resp = TestClient(app).post(
        "/api/devices",
        headers={"Authorization": f"Bearer {_token(role='Company')}"},
        json={
            "dev_number": "DEV002",
            "dev_ser_number": "SN-D2",
            "modbus_addr": 2,
            "phone": "should-not-pass",
        },
    )
    assert resp.status_code == 400
