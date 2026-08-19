import fakeredis.aioredis
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from ruisheng_api.api.alarms import _validate_alarm_rule
from ruisheng_api.api.schemas.alarms import AlarmCfgCreateRequest, AlarmCfgUpdateRequest
from ruisheng_api.core.security import client_fingerprint, issue_access_token
from ruisheng_api.db.repositories import alarms as alarms_repo
from ruisheng_api.db.repositories import devices as devices_repo
from ruisheng_api.db.repositories import points as points_repo
from ruisheng_api.deps import get_redis, get_session
from ruisheng_api.main import create_app
from ruisheng_shared.errors.codes import BizError


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

    async def fake_list_cfgs(session, dev_number):
        return []

    async def fake_get_point(session, point_id):
        return type("P", (), {"id": point_id, "dev_number": "60270012"})()

    async def fake_apply(*a, **kw):
        return None

    monkeypatch.setattr(devices_repo, "get_by_dev_number", fake_dev)
    monkeypatch.setattr(alarms_repo, "list_cfgs", fake_list_cfgs)
    monkeypatch.setattr(points_repo, "get_point", fake_get_point)
    from ruisheng_api.api import alarms as alarmsapi

    monkeypatch.setattr(alarmsapi, "apply_tenant_context", fake_apply)
    return r


def test_list_alarm_configs_empty(monkeypatch):
    _env(monkeypatch)
    app = create_app()
    _install(app, monkeypatch)
    resp = TestClient(app).get(
        "/api/devices/60270012/alarms/configs",
        headers={"Authorization": f"Bearer {_tok()}"},
    )
    assert resp.status_code == 200
    assert resp.json()["data"] == {"items": []}


def test_list_alarm_configs_requires_auth(monkeypatch):
    _env(monkeypatch)
    app = create_app()
    r = fakeredis.aioredis.FakeRedis()
    app.dependency_overrides[get_redis] = lambda: r
    assert TestClient(app).get("/api/devices/60270012/alarms/configs").status_code == 401


def test_create_alarm_config_rejects_point_from_other_device(monkeypatch):
    _env(monkeypatch)
    app = create_app()
    _install(app, monkeypatch)

    async def fake_get_point(session, point_id):
        return type("P", (), {"id": point_id, "dev_number": "OTHER"})()

    monkeypatch.setattr(points_repo, "get_point", fake_get_point)
    resp = TestClient(app).post(
        "/api/devices/60270012/alarms/configs",
        headers={"Authorization": f"Bearer {_tok(role='Administrators', ca=0x02)}"},
        json={
            "point_id": 99,
            "alarm_name": "高温",
            "alarm_type": ">",
            "limit_value": 80,
        },
    )
    assert resp.status_code == 400
    assert resp.json()["msg"] == "point not found for device"


def test_create_alarm_config_rejects_non_finite_limit() -> None:
    with pytest.raises(ValidationError):
        AlarmCfgCreateRequest.model_validate(
            {
                "point_id": 1,
                "alarm_name": "bad",
                "alarm_type": ">",
                "limit_value": float("nan"),
            }
        )


@pytest.mark.parametrize(
    ("model", "payload"),
    [
        (
            AlarmCfgCreateRequest,
            {
                "point_id": 1,
                "alarm_name": "bad bit",
                "alarm_type": ">",
                "limit_value": 1,
                "reg_bit": 16,
            },
        ),
        (AlarmCfgUpdateRequest, {"relation_reg_bit": -1}),
    ],
)
def test_alarm_config_rejects_out_of_range_register_bits(model, payload) -> None:
    with pytest.raises(ValidationError):
        model.model_validate(payload)


def test_relation_fields_are_all_or_none() -> None:
    with pytest.raises(BizError, match="all required"):
        _validate_alarm_rule(
            alarm_type=">",
            limit_value=10,
            relation_point_id=None,
            relation_alarm_type=">",
            relation_limit_value=5,
        )


def test_update_alarm_config_explicit_null_clears_relation(monkeypatch):
    _env(monkeypatch)
    app = create_app()
    _install(app, monkeypatch)
    from ruisheng_api.api import alarms as alarmsapi

    cfg = type(
        "Cfg",
        (),
        {
            "id": 9,
            "dev_number": "60270012",
            "point_id": 1,
            "reg_bit": None,
            "alarm_name": "linked",
            "alarm_type": ">",
            "limit_value": 10.0,
            "relation_point_id": 2,
            "relation_reg_bit": 1,
            "relation_alarm_type": "=",
            "relation_limit_value": 1.0,
            "enable": True,
            "phone_alarm": 0,
            "reset_remind": False,
            "waring_flag": False,
            "alarm_msg": None,
        },
    )()
    seen: dict[str, object] = {}

    async def fake_get_cfg(session, cfg_id):
        return cfg

    async def fake_update(session, current, updates):
        seen.update(updates)
        for key, value in updates.items():
            setattr(current, key, value)
        return current

    async def fake_mark(session, dev_number):
        return 4

    async def fake_publish(redis, dev_number, version):
        return None

    monkeypatch.setattr(alarms_repo, "get_cfg", fake_get_cfg)
    monkeypatch.setattr(alarms_repo, "update_cfg", fake_update)
    monkeypatch.setattr(alarmsapi, "_mark_config_changed", fake_mark)
    monkeypatch.setattr(alarmsapi, "_publish_config_changed", fake_publish)
    resp = TestClient(app).put(
        "/api/devices/60270012/alarms/configs/9",
        headers={"Authorization": f"Bearer {_tok(role='Company', ca=0x02)}"},
        json={"relation_point_id": None},
    )
    assert resp.status_code == 200
    assert seen == {
        "relation_point_id": None,
        "relation_reg_bit": None,
        "relation_alarm_type": None,
        "relation_limit_value": None,
    }


def test_alarm_subscriptions_require_notification_admin_permissions(monkeypatch):
    _env(monkeypatch)
    app = create_app()
    _install(app, monkeypatch)
    url = "/api/devices/60270012/alarms/configs/9/subscriptions"

    with TestClient(app) as client:
        assert client.get(url, headers={"Authorization": f"Bearer {_tok()}"}).status_code == 403
        assert (
            client.get(
                url,
                headers={"Authorization": f"Bearer {_tok(role='Company', ca=0)}"},
            ).status_code
            == 403
        )


def test_list_alarm_subscriptions_is_tenant_scoped(monkeypatch):
    _env(monkeypatch)
    app = create_app()
    _install(app, monkeypatch)
    seen: dict[str, object] = {}

    async def fake_get_cfg(session, cfg_id):
        return type("Cfg", (), {"id": cfg_id, "dev_number": "60270012"})()

    async def fake_list(session, cfg_id, usr_group):
        seen.update(cfg_id=cfg_id, usr_group=usr_group)
        return [
            {
                "id": 3,
                "alarm_cfg_id": cfg_id,
                "user_name": "bob",
                "channel": "email",
                "created_at": "2026-08-18T00:00:00Z",
            }
        ]

    monkeypatch.setattr(alarms_repo, "get_cfg", fake_get_cfg)
    monkeypatch.setattr(alarms_repo, "list_subscriptions", fake_list)
    resp = TestClient(app).get(
        "/api/devices/60270012/alarms/configs/9/subscriptions",
        headers={"Authorization": f"Bearer {_tok(role='Company', ca=0x02)}"},
    )
    assert resp.status_code == 200
    assert seen == {"cfg_id": 9, "usr_group": "g1"}
    assert resp.json()["data"]["items"][0]["user_name"] == "bob"


def test_admin_cannot_cross_tenant_for_subscriptions(monkeypatch):
    _env(monkeypatch)
    app = create_app()
    _install(app, monkeypatch)
    seen: dict[str, object] = {}

    async def fake_get_cfg(session, cfg_id):
        return type("Cfg", (), {"id": cfg_id, "dev_number": "60270012"})()

    async def fake_device(session, dev_number):
        return type("Device", (), {"dev_number": dev_number, "usr_group": "tenant-b"})()

    async def fake_list(session, cfg_id, usr_group):
        seen.update(cfg_id=cfg_id, usr_group=usr_group)
        return []

    monkeypatch.setattr(alarms_repo, "get_cfg", fake_get_cfg)
    monkeypatch.setattr(devices_repo, "get_by_dev_number", fake_device)
    monkeypatch.setattr(alarms_repo, "list_subscriptions", fake_list)
    resp = TestClient(app).get(
        "/api/devices/60270012/alarms/configs/9/subscriptions",
        headers={"Authorization": f"Bearer {_tok(role='Administrators', ca=0x02)}"},
    )

    assert resp.status_code == 403
    assert seen == {}


def test_delivery_audit_response_is_sanitized(monkeypatch):
    _env(monkeypatch)
    app = create_app()
    _install(app, monkeypatch)

    async def fake_get_cfg(session, cfg_id):
        return type("Cfg", (), {"id": cfg_id, "dev_number": "60270012"})()

    async def fake_audit(session, cfg_id, usr_group, *, limit):
        return [
            {
                "id": 7,
                "channel": "email",
                "status": "failed",
                "attempt_count": 2,
                "last_error_class": "authentication",
            }
        ]

    monkeypatch.setattr(alarms_repo, "get_cfg", fake_get_cfg)
    monkeypatch.setattr(alarms_repo, "list_delivery_audit", fake_audit)
    resp = TestClient(app).get(
        "/api/devices/60270012/alarms/configs/9/delivery-audit",
        headers={"Authorization": f"Bearer {_tok(role='Company', ca=0x02)}"},
    )
    assert resp.status_code == 200
    item = resp.json()["data"]["items"][0]
    assert item["last_error_class"] == "authentication"
    assert "contact" not in item
    assert "provider" not in item
