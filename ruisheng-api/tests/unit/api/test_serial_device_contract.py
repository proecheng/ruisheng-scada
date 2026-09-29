"""Serial configuration contracts without physical ports or a live database."""

from __future__ import annotations

import io
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import UploadFile
from pydantic import ValidationError
from ruisheng_api.api import devices as api
from ruisheng_api.api import points as point_api
from ruisheng_api.api.schemas.devices import (
    DeviceCreateRequest,
    DeviceEnabledRequest,
    DeviceUpdateRequest,
)
from ruisheng_api.api.schemas.points import (
    PointCreateRequest,
    PointUpdateRequest,
    validate_point_read_profile,
)
from ruisheng_api.core.config_changes import mark_config_changed, publish_config_changed
from ruisheng_api.core.rbac import CurrentUser
from ruisheng_api.db.repositories import devices as repo
from ruisheng_api.db.repositories import points as point_repo
from ruisheng_shared.errors.codes import BizError
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError


class Session:
    def __init__(self):
        self.active = False
        self.commits = 0
        self.version = 0
        self.commit_error = False
        self.flush = AsyncMock()
        self.add = MagicMock()
        self.delete = AsyncMock()

    def begin(self):
        return self

    async def __aenter__(self):
        self.active = True
        return self

    async def __aexit__(self, exc_type, *args):
        self.active = False
        if self.commit_error:
            raise RuntimeError("commit failed")
        if exc_type is None:
            self.commits += 1

    async def execute(self, statement, parameters):
        assert self.active
        assert "update_flag = update_flag + 1" in str(statement)
        assert parameters == {"dev": "D001"}
        self.version += 1
        return SimpleNamespace(scalar_one=lambda: self.version)


@pytest.fixture
def runtime(monkeypatch):
    device = SimpleNamespace(
        id=1,
        dev_number="D001",
        dev_ser_number="SER001",
        dev_name="test",
        dev_type=None,
        transport_type="serial",
        serial_port="COM3",
        read_profile="point_groups",
        dev_ip=None,
        modbus_addr=1,
        baud_rate=9600,
        is_enabled=True,
        is_online=True,
        last_call_at=None,
        last_back_at=None,
        loss_count=0,
        update_interval_decisec=100,
        group_company=None,
        company=None,
        department=None,
        usr_group="tenant-a",
        deleted_at=None,
    )
    session = Session()
    messages = []

    async def publish(channel, payload):
        assert not session.active
        assert session.commits > 0
        messages.append((channel, json.loads(payload)))

    async def get_device(current_session, dev_number, *, for_update=False):
        assert current_session is session and for_update
        return device

    monkeypatch.setattr(repo, "get_by_dev_number", get_device)
    monkeypatch.setattr(repo, "get_serial_endpoint", AsyncMock(return_value=None))
    monkeypatch.setattr(point_repo, "list_points", AsyncMock(return_value=[]))
    monkeypatch.setattr(api, "apply_tenant_context", AsyncMock())
    monkeypatch.setattr(point_api, "apply_tenant_context", AsyncMock())
    return SimpleNamespace(
        device=device,
        session=session,
        redis=SimpleNamespace(publish=publish),
        messages=messages,
        user=CurrentUser("alice", "tenant-a", "Company", 2, "jti", "fp"),
    )


def test_create_profile_default_and_transport():
    base = {"dev_number": "D001", "dev_ser_number": "SER001", "modbus_addr": 1}
    assert DeviceCreateRequest(**base).read_profile == "point_groups"
    with pytest.raises(ValidationError, match="requires serial"):
        DeviceCreateRequest(**base, read_profile="zero_origin_38")
    assert (
        DeviceCreateRequest(
            **base, transport_type="serial", serial_port=" COM3 ", read_profile="zero_origin_38"
        ).serial_port
        == "COM3"
    )


@pytest.mark.parametrize(
    "field",
    ["read_profile", "transport_type", "modbus_addr", "is_enabled", "update_interval_decisec"],
)
def test_update_rejects_null_required_fields(field):
    with pytest.raises(ValidationError):
        DeviceUpdateRequest(**{field: None})


@pytest.mark.parametrize(
    ("fc", "address", "value_type", "valid"),
    [
        (3, 37, "字", True),
        (3, 36, "双字", True),
        (3, 37, "双字", False),
        (3, 38, "字", False),
        (4, 0, "字", False),
        (1, 0, "bit", False),
        (3, 0, "bit", True),
        (3, -1, "字", False),
    ],
)
def test_fixed_profile_register_span(fc, address, value_type, valid):
    kwargs = {"fun_code": fc, "point_number": address, "value_type": value_type}
    if valid:
        validate_point_read_profile("zero_origin_38", **kwargs)
    else:
        with pytest.raises(ValueError, match="span within 0..37"):
            validate_point_read_profile("zero_origin_38", **kwargs)
    validate_point_read_profile("point_groups", **kwargs)


async def test_partial_update_validates_full_stored_transport(runtime):
    runtime.device.transport_type = "tcp"
    runtime.device.serial_port = None
    with pytest.raises(BizError, match="requires serial"):
        await api.update_device(
            "D001",
            DeviceUpdateRequest(read_profile="zero_origin_38"),
            runtime.user,
            runtime.session,
            runtime.redis,
        )
    assert runtime.session.commits == 0
    assert runtime.messages == []


async def test_switch_to_tcp_requires_compatible_stored_profile(runtime):
    runtime.device.read_profile = "zero_origin_38"
    with pytest.raises(BizError, match="requires serial"):
        await api.update_device(
            "D001",
            DeviceUpdateRequest(transport_type="tcp"),
            runtime.user,
            runtime.session,
            runtime.redis,
        )
    response = await api.update_device(
        "D001",
        DeviceUpdateRequest(transport_type="tcp", read_profile="point_groups"),
        runtime.user,
        runtime.session,
        runtime.redis,
    )
    assert response.data["serial_port"] is None


async def test_partial_update_cannot_clear_serial_port(runtime):
    with pytest.raises(BizError, match="serial_port is required"):
        await api.update_device(
            "D001",
            DeviceUpdateRequest(serial_port=None),
            runtime.user,
            runtime.session,
            runtime.redis,
        )
    assert runtime.messages == []


async def test_profile_switch_rejects_existing_invalid_point(runtime, monkeypatch):
    monkeypatch.setattr(
        point_repo,
        "list_points",
        AsyncMock(return_value=[SimpleNamespace(fun_code=3, point_number=37, value_type="双字")]),
    )
    with pytest.raises(BizError, match="span within 0..37"):
        await api.update_device(
            "D001",
            DeviceUpdateRequest(read_profile="zero_origin_38"),
            runtime.user,
            runtime.session,
            runtime.redis,
        )
    assert runtime.device.read_profile == "point_groups"
    assert runtime.messages == []


async def test_repeated_changes_increment_and_publish_after_commit(runtime):
    for address in (4, 7):
        await api.update_device(
            "D001",
            DeviceUpdateRequest(modbus_addr=address),
            runtime.user,
            runtime.session,
            runtime.redis,
        )
    assert [body["version"] for _, body in runtime.messages] == [1, 2]
    assert all(channel == "channel:config:changed" for channel, _ in runtime.messages)


@pytest.mark.parametrize("dedicated", [False, True])
async def test_both_disable_paths_clear_online_and_notify(runtime, dedicated):
    if dedicated:
        response = await api.set_device_enabled(
            "D001",
            DeviceEnabledRequest(is_enabled=False),
            runtime.user,
            runtime.session,
            runtime.redis,
        )
    else:
        response = await api.update_device(
            "D001",
            DeviceUpdateRequest(is_enabled=False),
            runtime.user,
            runtime.session,
            runtime.redis,
        )
    assert response.data["is_online"] is False
    assert response.data["is_enabled"] is False
    assert len(runtime.messages) == 1


async def test_delete_retains_record_and_marks_version(runtime):
    await api.delete_device("D001", runtime.user, runtime.session, runtime.redis)
    assert runtime.device.deleted_at is not None
    assert runtime.device.is_online is False
    assert runtime.messages[0][1] == {"dev_number": "D001", "version": 1}


async def test_commit_failure_never_broadcasts(runtime):
    runtime.session.commit_error = True
    with pytest.raises(RuntimeError, match="commit failed"):
        await api.update_device(
            "D001", DeviceUpdateRequest(modbus_addr=2), runtime.user, runtime.session, runtime.redis
        )
    assert runtime.messages == []


async def test_publish_failure_is_best_effort():
    redis = SimpleNamespace(publish=AsyncMock(side_effect=RuntimeError("unavailable")))
    await publish_config_changed(redis, "D001", 3)
    redis.publish.assert_awaited_once()


async def test_mark_uses_atomic_database_increment():
    session = Session()
    async with session.begin():
        assert await mark_config_changed(session, "D001") == 1
        assert await mark_config_changed(session, "D001") == 2


@pytest.mark.parametrize("operation", ["create", "update", "import"])
async def test_invalid_fixed38_points_rejected_before_write(runtime, monkeypatch, operation):
    runtime.device.read_profile = "zero_origin_38"
    writer = AsyncMock()
    for name in ("create_point", "update_point", "create_points"):
        monkeypatch.setattr(point_repo, name, writer)
    monkeypatch.setattr(
        point_repo,
        "get_point",
        AsyncMock(
            return_value=SimpleNamespace(
                dev_number="D001",
                fun_code=3,
                point_number=37,
                value_type="字",
                r_bit=None,
                min_value=None,
                max_value=None,
                point_ratio=1,
                point_offset=0,
                user_ratio=1,
                user_point_offset=0,
            )
        ),
    )
    with pytest.raises(BizError, match="span within 0..37"):
        if operation == "create":
            await point_api.create_point(
                "D001",
                PointCreateRequest(
                    point_name="p", fun_code=3, point_number=37, value_type="双字", dev_addr=1
                ),
                runtime.user,
                runtime.session,
                runtime.redis,
            )
        elif operation == "update":
            await point_api.update_point(
                "D001",
                1,
                PointUpdateRequest(value_type="双字"),
                runtime.user,
                runtime.session,
                runtime.redis,
            )
        else:
            # Use a valid generic point contract so rejection comes from the profile.
            file = UploadFile(
                file=io.BytesIO(
                    "point_name,point_number,fun_code,dev_addr,value_type\nbad,38,3,1,字\n".encode()
                )
            )
            await point_api.import_points(
                "D001", file, runtime.user, runtime.session, runtime.redis
            )
    writer.assert_not_awaited()
    assert runtime.messages == []


@pytest.mark.parametrize("operation", ["create", "update", "import", "delete"])
async def test_point_mutations_increment_and_notify_after_commit(runtime, monkeypatch, operation):
    runtime.device.read_profile = "zero_origin_38"
    body = PointCreateRequest(
        point_name="last", fun_code=3, point_number=37, value_type="字", dev_addr=1
    )
    point = SimpleNamespace(id=1, dev_number="D001", **body.model_dump())
    monkeypatch.setattr(point_repo, "create_point", AsyncMock(return_value=point))
    monkeypatch.setattr(point_repo, "create_points", AsyncMock(return_value=[point]))
    monkeypatch.setattr(point_repo, "get_point", AsyncMock(return_value=point))
    monkeypatch.setattr(point_repo, "delete_point", AsyncMock())
    if operation == "create":
        await point_api.create_point("D001", body, runtime.user, runtime.session, runtime.redis)
    elif operation == "update":
        await point_api.update_point(
            "D001",
            1,
            PointUpdateRequest(point_name="changed"),
            runtime.user,
            runtime.session,
            runtime.redis,
        )
    elif operation == "import":
        file = UploadFile(
            file=io.BytesIO(
                "point_name,point_number,fun_code,dev_addr,value_type\nlast,37,3,1,字\n".encode()
            )
        )
        await point_api.import_points("D001", file, runtime.user, runtime.session, runtime.redis)
    else:
        await point_api.delete_point("D001", 1, runtime.user, runtime.session, runtime.redis)
    assert runtime.messages == [("channel:config:changed", {"dev_number": "D001", "version": 1})]


async def test_create_notifies_after_commit(runtime, monkeypatch):
    monkeypatch.setattr(repo, "get_by_dev_number", AsyncMock(return_value=None))
    monkeypatch.setattr(repo, "create_device", AsyncMock(return_value=runtime.device))
    await api.create_device(
        DeviceCreateRequest(dev_number="D001", dev_ser_number="SER001", modbus_addr=1),
        runtime.user,
        runtime.session,
        runtime.redis,
    )
    assert runtime.messages == [("channel:config:changed", {"dev_number": "D001", "version": 1})]


async def test_cross_tenant_missing_device_never_mutates_or_publishes(runtime, monkeypatch):
    monkeypatch.setattr(repo, "get_by_dev_number", AsyncMock(return_value=None))
    with pytest.raises(BizError, match="device not found"):
        await api.delete_device("D001", runtime.user, runtime.session, runtime.redis)
    assert runtime.messages == []
    runtime.session.flush.assert_not_awaited()


async def test_concurrent_address_collision_returns_sanitized_business_error():
    conflict = Exception("sensitive other tenant details")
    conflict.constraint_name = "uq_devices_serial_port_modbus_addr"
    session = SimpleNamespace(
        add=MagicMock(),
        flush=AsyncMock(
            side_effect=IntegrityError(
                "INSERT",
                {},
                conflict,
            )
        ),
    )
    with pytest.raises(BizError, match="serial_port and modbus_addr already in use") as error:
        await repo.create_device(session, dev_number="D001")
    assert "sensitive" not in error.value.msg


async def test_serial_address_lookup_includes_disabled_but_excludes_deleted():
    session = SimpleNamespace(
        execute=AsyncMock(
            return_value=SimpleNamespace(
                scalar_one_or_none=lambda: None,
            )
        )
    )
    await repo.get_serial_endpoint(session, serial_port="COM3", modbus_addr=1)
    stmt = session.execute.call_args.args[0]
    sql = str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    predicate = sql.split("WHERE", 1)[1]
    assert "deleted_at IS NULL" in predicate
    assert "is_enabled" not in predicate
