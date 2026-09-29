from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import fakeredis.aioredis
import pytest
from fastapi.testclient import TestClient
from redis.exceptions import ConnectionError as RedisConnectionError
from ruisheng_api.api import do_control as api
from ruisheng_api.core.security import client_fingerprint, issue_access_token
from ruisheng_api.deps import get_redis, get_session
from ruisheng_api.main import create_app


@pytest.fixture
def setup(monkeypatch):
    for k, v in {
        "API_DB_URL": "postgresql+asyncpg://u:p@h/d",
        "API_GW_DB_URL": "postgresql+asyncpg://u:p@h/d",
        "API_REDIS_URL": "redis://:p@h/0",
        "API_JWT_SECRET": "x" * 64,
    }.items():
        monkeypatch.setenv(k, v)
    session = MagicMock()
    session.begin.return_value.__aenter__ = AsyncMock(return_value=session)
    session.begin.return_value.__aexit__ = AsyncMock(return_value=False)
    rows = MagicMock()
    rows.scalar_one_or_none.return_value = None
    session.execute = AsyncMock(return_value=rows)
    device = SimpleNamespace(
        is_enabled=True, transport_type="serial", read_profile="zero_origin_38", update_flag=26
    )
    get = AsyncMock(return_value=device)
    insert = AsyncMock()
    publish = AsyncMock()
    tenant = AsyncMock()
    profile = AsyncMock(return_value=True)
    monkeypatch.setattr(api.devices_repo, "get_by_dev_number", get)
    monkeypatch.setattr(api.control_repo, "insert_action", insert)
    monkeypatch.setattr(api, "xadd_control_cmd", publish)
    monkeypatch.setattr(api, "apply_tenant_context", tenant)
    monkeypatch.setattr(api, "profile_enabled", profile)
    app = create_app()

    async def sessions():
        yield session

    app.dependency_overrides[get_session] = sessions

    async def redis_for_request():
        async with fakeredis.aioredis.FakeRedis() as redis:
            yield redis

    app.dependency_overrides[get_redis] = redis_for_request
    return SimpleNamespace(
        client=TestClient(app),
        device=device,
        rows=rows,
        get=get,
        insert=insert,
        publish=publish,
        tenant=tenant,
        profile=profile,
    )


def headers(ca=1):
    token = issue_access_token(
        "alice",
        "g1",
        "User",
        ca,
        client_fingerprint("testclient", "testclient"),
        secret="x" * 64,
        ttl_sec=900,
    )
    return {"Authorization": f"Bearer {token}"}


def request(s, **kwargs):
    return s.client.post(
        "/api/devices/DEV001/do-control",
        headers=headers(),
        json={"channels": [{"number": 2, "high": True}], "config_version": 26, **kwargs},
    )


def test_only_selected_channels_enter_audited_command(setup):
    response = request(setup)
    assert response.status_code == 200, response.text
    assert response.json()["data"]["status"] == "pending"
    action = setup.insert.call_args.kwargs["action"]
    assert action["mask"] == 2 and action["value"] == 2 and action["profile"] == api.PROFILE
    assert setup.publish.call_args.kwargs["payload"] == {"dev_number": "DEV001"}
    assert setup.get.call_args.kwargs["for_update"] is True
    setup.tenant.assert_awaited_once_with(setup.get.call_args.args[0], usr_group="g1", role="User")


@pytest.mark.parametrize(
    "channels",
    [
        [],
        [{"number": 0, "high": True}],
        [{"number": 3, "high": True}],
        [{"number": 1, "high": 1}],
        [{"number": 1, "high": True}, {"number": 1, "high": False}],
    ],
)
def test_bad_channel_selection_rejected_before_audit(setup, channels):
    assert request(setup, channels=channels).status_code == 400
    setup.insert.assert_not_called()


@pytest.mark.parametrize("guard", ["disabled", "profile", "version", "pending", "tenant"])
def test_configuration_and_pending_guards_do_not_publish(setup, guard):
    if guard == "disabled":
        setup.device.is_enabled = False
    if guard == "profile":
        setup.profile.return_value = False
    if guard == "version":
        setup.device.update_flag = 27
    if guard == "pending":
        setup.rows.scalar_one_or_none.return_value = 1
    if guard == "tenant":
        setup.get.return_value = None
    response = request(setup)
    assert response.json()["code"] != 0, response.text
    setup.publish.assert_not_called()


def test_permission_and_high_risk_otp_required(setup):
    response = setup.client.post(
        "/api/devices/DEV001/do-control",
        headers=headers(0),
        json={"channels": [{"number": 1, "high": True}], "config_version": 26},
    )
    assert response.status_code == 403
    response = request(setup, high_risk=True)
    assert response.status_code == 403
    setup.insert.assert_not_called()


def test_queue_ack_loss_returns_durable_id_without_retry(setup):
    setup.publish.side_effect = RedisConnectionError("lost reply")
    response = request(setup)
    assert response.status_code == 200
    assert response.json()["data"]["cmd_id"] == setup.insert.call_args.kwargs["cmd_id"]
    setup.publish.assert_awaited_once()
