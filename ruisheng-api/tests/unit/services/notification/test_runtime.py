import asyncio
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import pytest
from ruisheng_api.config import Config
from ruisheng_api.services.notification.base import parse_retry_after
from ruisheng_api.services.notification.runtime import (
    ClaimedDelivery,
    ContactTarget,
    MaterializationError,
    NotificationMetrics,
    _load_contacts,
    _update_channels_projection,
    contact_fingerprint,
    delivery_worker_loop,
    parse_alarm_event,
    process_delivery,
)


def _event() -> dict[str, str]:
    return {
        "schema_version": "2",
        "event_id": "1",
        "triggered_at": datetime.now(UTC).isoformat(),
        "alarm_cfg_id": "2",
        "dev_number": "D1",
        "point_id": "3",
        "value": "4.5",
        "limit": "4",
    }


def test_parse_alarm_event_is_strict_and_finite() -> None:
    assert parse_alarm_event(_event()).event_id == 1
    bad = _event()
    bad["value"] = "nan"
    with pytest.raises(MaterializationError, match="invalid_event"):
        parse_alarm_event(bad)
    bad = _event()
    bad["triggered_at"] = datetime.now().isoformat()
    with pytest.raises(MaterializationError, match="invalid_event"):
        parse_alarm_event(bad)
    bad = _event()
    bad["unknown"] = "field"
    with pytest.raises(MaterializationError, match="invalid_event"):
        parse_alarm_event(bad)


def test_parse_alarm_event_rejects_oversized_payload() -> None:
    bad = _event()
    bad["dev_number"] = "D" * 9_000
    with pytest.raises(MaterializationError, match="event_too_large"):
        parse_alarm_event(bad)


def test_contact_fingerprint_is_keyed_and_irreversible() -> None:
    first = contact_fingerprint("secret-a", "alice@example.com")
    second = contact_fingerprint("secret-b", "alice@example.com")
    assert first != second
    assert "alice" not in first


def test_contact_target_keeps_record_reference_separate() -> None:
    target = ContactTarget(ref="email:42", value="alice@example.com")
    assert target.ref == "email:42"
    assert target.value == "alice@example.com"


async def test_phone_contact_query_uses_phone_table() -> None:
    statements: list[str] = []

    class Session:
        async def execute(self, statement, params):
            statements.append(str(statement))
            return []

    assert await _load_contacts(Session(), "g1", "alice", "sms_custom_http") == []  # type: ignore[arg-type]
    assert "FROM user_phone_numbers p" in statements[0]


def test_retry_after_is_bounded() -> None:
    assert parse_retry_after("12") == 12
    assert parse_retry_after("99999") == 3600
    assert parse_retry_after("invalid") is None
    provider_now = datetime(2026, 8, 19, 12, 0, tzinfo=UTC)
    retry_at = format_datetime(provider_now + timedelta(seconds=30), usegmt=True)
    assert parse_retry_after(retry_at, reference_time=provider_now) == 30
    assert parse_retry_after(retry_at) is None


def test_config_rejects_lease_shorter_than_provider_timeout() -> None:
    with pytest.raises(ValueError, match="lease must exceed provider timeout"):
        Config(
            db_url="postgresql+asyncpg://u:p@h/d",
            gw_db_url="postgresql+asyncpg://u:p@h/d",
            redis_url="redis://h/0",
            jwt_secret="x" * 64,
            notification_lease_sec=20,
            notification_provider_timeout_sec=20,
        )


def test_providers_default_disabled(monkeypatch) -> None:
    for key, value in {
        "API_DB_URL": "postgresql+asyncpg://u:p@h/d",
        "API_GW_DB_URL": "postgresql+asyncpg://u:p@h/d",
        "API_REDIS_URL": "redis://h/0",
        "API_JWT_SECRET": "x" * 64,
    }.items():
        monkeypatch.setenv(key, value)
    config = Config()
    assert not config.notification_wechat_enabled
    assert not config.notification_email_enabled
    assert not config.notification_sms_enabled
    assert not config.notification_voice_enabled


async def test_delivery_worker_survives_database_error(monkeypatch) -> None:
    for key, value in {
        "API_DB_URL": "postgresql+asyncpg://u:p@h/d",
        "API_GW_DB_URL": "postgresql+asyncpg://u:p@h/d",
        "API_REDIS_URL": "redis://h/0",
        "API_JWT_SECRET": "x" * 64,
    }.items():
        monkeypatch.setenv(key, value)
    stop_event = asyncio.Event()
    calls = 0

    async def fake_claim(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("database unavailable")
        stop_event.set()
        return []

    async def fake_refresh(*args, **kwargs):
        return None

    monkeypatch.setattr("ruisheng_api.services.notification.runtime.claim_deliveries", fake_claim)
    monkeypatch.setattr(
        "ruisheng_api.services.notification.runtime.refresh_notification_metrics",
        fake_refresh,
    )

    await delivery_worker_loop(object(), Config(), stop_event, NotificationMetrics())  # type: ignore[arg-type]
    assert calls == 2


async def test_projection_locks_alarm_row_before_aggregating() -> None:
    statements: list[str] = []

    class Result:
        def __init__(self, *, scalar=None, rows=None):
            self.scalar = scalar
            self.rows = rows or []

        def scalar_one_or_none(self):
            return self.scalar

        def mappings(self):
            return self

        def all(self):
            return self.rows

    class Session:
        async def execute(self, statement, params):
            sql = str(statement)
            statements.append(sql)
            if "FOR UPDATE" in sql:
                return Result(scalar=1)
            if "GROUP BY" in sql:
                return Result(rows=[])
            return Result()

    await _update_channels_projection(  # type: ignore[arg-type]
        Session(),
        1,
        datetime.now(UTC),
    )
    assert "FOR UPDATE" in statements[0]
    assert "GROUP BY" in statements[1]
    assert "UPDATE alarm_records" in statements[2]


async def test_provider_call_renews_lease_immediately_before_send(monkeypatch) -> None:
    order: list[str] = []

    class Transaction:
        async def __aenter__(self):
            return None

        async def __aexit__(self, *args):
            return None

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        def begin(self):
            return Transaction()

    class Factory:
        def __call__(self):
            return Session()

    class Provider:
        async def send(self, notification):
            order.append("send")
            return True

    async def fake_resolve(session, claimed, *, worker_id):
        order.append("resolve")
        return (
            {
                "trace_id": "t",
                "alarm_id": 1,
                "dev_number": "D1",
                "alarm_name": "high",
                "alarm_value": 2.0,
                "limit_value": 1.0,
                "user_name": "alice",
                "alarm_msg": "high",
                "channel": "email",
            },
            "a@example.test",
        )

    async def fake_provider(*args, **kwargs):
        return Provider()

    async def fake_now(session):
        return datetime.now(UTC)

    async def fake_renew(*args, **kwargs):
        order.append("renew")
        return datetime.now(UTC)

    async def fake_finalize(*args, **kwargs):
        order.append("finalize")

    monkeypatch.setattr(
        "ruisheng_api.services.notification.runtime._resolve_delivery", fake_resolve
    )
    monkeypatch.setattr("ruisheng_api.services.notification.runtime._build_provider", fake_provider)
    monkeypatch.setattr("ruisheng_api.services.notification.runtime._db_now", fake_now)
    monkeypatch.setattr(
        "ruisheng_api.services.notification.runtime.renew_delivery_lease", fake_renew
    )
    monkeypatch.setattr(
        "ruisheng_api.services.notification.runtime.finalize_delivery", fake_finalize
    )

    await process_delivery(
        Factory(),  # type: ignore[arg-type]
        ClaimedDelivery(id=1, usr_group="g1", lease_version=2),
        cfg=Config(
            db_url="postgresql+asyncpg://u:p@h/d",
            gw_db_url="postgresql+asyncpg://u:p@h/d",
            redis_url="redis://h/0",
            jwt_secret="x" * 64,
            notification_email_enabled=True,
            notification_email_host="smtp.test",
            notification_email_user="user",
            notification_email_password="password",
        ),
        worker_id="worker-1",
        metrics=NotificationMetrics(),
    )
    assert order == ["resolve", "renew", "send", "finalize"]
