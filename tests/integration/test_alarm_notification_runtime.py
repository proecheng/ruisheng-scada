from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from ruisheng_api.core.tenant import apply_tenant_context
from ruisheng_api.db.repositories.alarms import reset_alarm
from ruisheng_api.services.notification.runtime import (
    AlarmStreamEvent,
    DeliveryOutcome,
    MaterializationError,
    NotificationMetrics,
    claim_deliveries,
    cleanup_notification_audit,
    finalize_delivery,
    materialize_event,
)
from ruisheng_gw.domain.registry import Registry
from ruisheng_gw.persistence.repository import Repository
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

pytestmark = pytest.mark.integration

DEVICE_A = "notify-runtime-a"
DEVICE_B = "notify-runtime-b"
POINT_A = 98101
POINT_B = 98102
CFG_A = 98201
CFG_B = 98202
USER_A = "notifyUserA"
USER_B = "notifyUserB"
ALARM_A = 98301
TRIGGERED_AT = datetime.now(UTC).replace(microsecond=0)


async def _cleanup(conn) -> None:
    await conn.execute(text("SELECT set_config('app.role', 'Administrators', true)"))
    await conn.execute(text("SELECT set_config('app.audit_cleanup', 'on', true)"))
    await conn.execute(
        text("DELETE FROM alarm_outbox WHERE alarm_id = :alarm"),
        {"alarm": ALARM_A},
    )
    await conn.execute(
        text(
            "DELETE FROM notification_dispatches WHERE alarm_id = :alarm "
            "AND alarm_triggered_at = :triggered"
        ),
        {"alarm": ALARM_A, "triggered": TRIGGERED_AT},
    )
    await conn.execute(
        text("DELETE FROM alarm_notification_subscriptions WHERE alarm_cfg_id IN (:cfg_a, :cfg_b)"),
        {"cfg_a": CFG_A, "cfg_b": CFG_B},
    )
    await conn.execute(
        text("DELETE FROM alarm_records WHERE id = :alarm AND triggered_at = :triggered"),
        {"alarm": ALARM_A, "triggered": TRIGGERED_AT},
    )
    await conn.execute(
        text("DELETE FROM device_waring_cfgs WHERE id IN (:cfg_a, :cfg_b)"),
        {"cfg_a": CFG_A, "cfg_b": CFG_B},
    )
    await conn.execute(
        text("DELETE FROM user_emails WHERE user_name IN (:user_a, :user_b)"),
        {"user_a": USER_A, "user_b": USER_B},
    )
    await conn.execute(
        text("DELETE FROM device_points WHERE id IN (:point_a, :point_b)"),
        {"point_a": POINT_A, "point_b": POINT_B},
    )
    await conn.execute(
        text("DELETE FROM devices WHERE dev_number IN (:device_a, :device_b)"),
        {"device_a": DEVICE_A, "device_b": DEVICE_B},
    )
    await conn.execute(
        text("DELETE FROM users WHERE user_name IN (:user_a, :user_b)"),
        {"user_a": USER_A, "user_b": USER_B},
    )


@pytest.fixture
async def notification_seed(dev_engine, seed_tenants):
    async with dev_engine.connect() as conn, conn.begin():
        await _cleanup(conn)
        await conn.execute(
            text(
                "INSERT INTO users "
                "(user_name, password_hash, authority, control_authority, usr_group) VALUES "
                "(:user_a, 'not-a-real-hash', 'Company', 2, 'ug_A'), "
                "(:user_b, 'not-a-real-hash', 'Company', 2, 'ug_B')"
            ),
            {"user_a": USER_A, "user_b": USER_B},
        )
        await conn.execute(
            text(
                "INSERT INTO devices "
                "(dev_number, dev_ser_number, modbus_addr, baud_rate, "
                " update_interval_decisec, loss_count, is_online, is_enabled, "
                " update_flag, usr_group) VALUES "
                "(:device_a, 'NOTIFY-SER-A', 1, 9600, 100, 0, false, true, 0, 'ug_A'), "
                "(:device_b, 'NOTIFY-SER-B', 2, 9600, 100, 0, false, true, 0, 'ug_B')"
            ),
            {"device_a": DEVICE_A, "device_b": DEVICE_B},
        )
        await conn.execute(
            text(
                "INSERT INTO device_points "
                "(id, dev_number, point_name, point_number, fun_code, dev_addr, value_type, "
                " point_ratio, point_offset, user_ratio, user_point_offset, show) "
                "VALUES (:point_a, :device_a, 'A', 1, 3, 1, '字', 1, 0, 1, 0, 1), "
                "       (:point_b, :device_b, 'B', 1, 3, 2, '字', 1, 0, 1, 0, 1)"
            ),
            {
                "point_a": POINT_A,
                "point_b": POINT_B,
                "device_a": DEVICE_A,
                "device_b": DEVICE_B,
            },
        )
        await conn.execute(
            text(
                "INSERT INTO device_waring_cfgs "
                "(id, dev_number, point_id, alarm_name, alarm_type, limit_value, "
                " enable, phone_alarm, reset_remind, dev_sync_flag, waring_flag) VALUES "
                "(:cfg_a, :device_a, :point_a, 'A high', '>', 80, true, 0, false, 0, true), "
                "(:cfg_b, :device_b, :point_b, 'B high', '>', 80, true, 0, false, 0, true)"
            ),
            {
                "cfg_a": CFG_A,
                "cfg_b": CFG_B,
                "device_a": DEVICE_A,
                "device_b": DEVICE_B,
                "point_a": POINT_A,
                "point_b": POINT_B,
            },
        )
        await conn.execute(
            text(
                "INSERT INTO user_emails (id, phone_number, email, user_name) VALUES "
                "(98401, '13000000001', 'a@example.test', :user_a), "
                "(98402, '13000000002', 'b@example.test', :user_b)"
            ),
            {"user_a": USER_A, "user_b": USER_B},
        )
        await conn.execute(
            text(
                "INSERT INTO alarm_notification_subscriptions "
                "(id, alarm_cfg_id, user_name, channel, usr_group, created_at, updated_at) VALUES "
                "(98501, :cfg_a, :user_a, 'email', 'ug_A', :created_at, :created_at)"
            ),
            {
                "cfg_a": CFG_A,
                "user_a": USER_A,
                "created_at": TRIGGERED_AT - timedelta(minutes=1),
            },
        )
        await conn.execute(
            text(
                "INSERT INTO alarm_records "
                "(id, alarm_cfg_id, dev_number, point_id, alarm_name, alarm_msg, "
                " alarm_value, alarm_type, limit_value, channels_sent, triggered_at, usr_group) "
                "VALUES (:alarm, :cfg, :device, :point, 'A high', 'temperature high', "
                "95, '>', 80, '{}'::jsonb, :triggered, 'ug_A')"
            ),
            {
                "alarm": ALARM_A,
                "cfg": CFG_A,
                "device": DEVICE_A,
                "point": POINT_A,
                "triggered": TRIGGERED_AT,
            },
        )
    yield
    async with dev_engine.connect() as conn, conn.begin():
        await _cleanup(conn)


def _event(*, dev_number: str = DEVICE_A) -> AlarmStreamEvent:
    return AlarmStreamEvent(
        schema_version=2,
        event_id=ALARM_A,
        triggered_at=TRIGGERED_AT,
        alarm_cfg_id=CFG_A,
        dev_number=dev_number,
        point_id=POINT_A,
        value=95,
        limit=80,
    )


async def test_materialization_is_tenant_safe_and_idempotent(
    api_engine, dev_engine, notification_seed
) -> None:
    factory = async_sessionmaker(api_engine, expire_on_commit=False)
    first, concurrent = await asyncio.gather(
        *(
            materialize_event(
                factory,
                _event(),
                fingerprint_secret="integration-secret",
                provider_enabled={"email": True},
            )
            for _ in range(2)
        )
    )
    replay = await materialize_event(
        factory,
        _event(),
        fingerprint_secret="integration-secret",
        provider_enabled={"email": True},
    )
    assert concurrent.dispatch_id == replay.dispatch_id == first.dispatch_id
    assert first.usr_group == "ug_A"

    async with dev_engine.connect() as conn, conn.begin():
        await conn.execute(text("SELECT set_config('app.role', 'Administrators', true)"))
        deliveries = (
            (
                await conn.execute(
                    text(
                        "SELECT user_name, channel, contact_ref, contact_fingerprint, status "
                        "FROM notification_deliveries WHERE dispatch_id = :dispatch"
                    ),
                    {"dispatch": first.dispatch_id},
                )
            )
            .mappings()
            .all()
        )
        assert len(deliveries) == 1
        assert deliveries[0]["user_name"] == USER_A
        assert deliveries[0]["contact_ref"] == "email:98401"
        assert "example" not in deliveries[0]["contact_fingerprint"]
        assert deliveries[0]["status"] == "pending"

    with pytest.raises(MaterializationError, match="alarm_identity_mismatch"):
        await materialize_event(
            factory,
            _event(dev_number=DEVICE_B),
            fingerprint_secret="integration-secret",
            provider_enabled={"email": True},
        )


async def test_database_rejects_cross_tenant_subscription(dev_engine, notification_seed) -> None:
    async with dev_engine.connect() as conn, conn.begin():
        await conn.execute(text("SELECT set_config('app.role', 'Administrators', true)"))
        with pytest.raises(Exception, match="notification_tenant_violation"):
            await conn.execute(
                text(
                    "INSERT INTO alarm_notification_subscriptions "
                    "(id, alarm_cfg_id, user_name, channel, usr_group) "
                    "VALUES (98502, :cfg, :user, 'email', 'ug_B')"
                ),
                {"cfg": CFG_A, "user": USER_B},
            )


async def test_alarm_snapshot_is_immutable_but_projection_can_update(
    dev_engine, notification_seed
) -> None:
    async with dev_engine.connect() as conn, conn.begin():
        await conn.execute(text("SELECT set_config('app.role', 'Administrators', true)"))
        with pytest.raises(Exception, match="alarm record snapshot is immutable"):
            await conn.execute(
                text(
                    "UPDATE alarm_records SET alarm_name = 'changed' "
                    "WHERE id = :alarm AND triggered_at = :triggered"
                ),
                {"alarm": ALARM_A, "triggered": TRIGGERED_AT},
            )
    async with dev_engine.connect() as conn, conn.begin():
        await conn.execute(text("SELECT set_config('app.role', 'Administrators', true)"))
        await conn.execute(
            text(
                'UPDATE alarm_records SET channels_sent = \'{"email": {"pending": 1}}\' '
                "WHERE id = :alarm AND triggered_at = :triggered"
            ),
            {"alarm": ALARM_A, "triggered": TRIGGERED_AT},
        )


async def test_gw_has_no_contact_or_notification_table_access(dev_engine) -> None:
    tables = (
        "wx_groups",
        "user_wx_bindings",
        "user_phone_numbers",
        "user_emails",
        "alarm_notification_subscriptions",
        "notification_dispatches",
        "notification_deliveries",
        "notification_delivery_attempts",
    )
    async with dev_engine.connect() as conn:
        for table in tables:
            assert not await conn.scalar(
                text("SELECT has_table_privilege('ruisheng_gw', :table, 'SELECT')"),
                {"table": table},
            )


async def test_expired_worker_cannot_overwrite_new_lease(
    api_engine, dev_engine, notification_seed
) -> None:
    factory = async_sessionmaker(api_engine, expire_on_commit=False)
    await materialize_event(
        factory,
        _event(),
        fingerprint_secret="integration-secret",
        provider_enabled={"email": True},
    )
    first = (await claim_deliveries(factory, worker_id="old", limit=1, lease_sec=10))[0]
    async with dev_engine.connect() as conn, conn.begin():
        await conn.execute(text("SELECT set_config('app.role', 'Administrators', true)"))
        await conn.execute(
            text(
                "UPDATE notification_deliveries SET leased_until = clock_timestamp() - interval '1s' "
                "WHERE id = :id"
            ),
            {"id": first.id},
        )
    second = (await claim_deliveries(factory, worker_id="new", limit=1, lease_sec=10))[0]
    metrics = NotificationMetrics()
    await finalize_delivery(
        factory,
        first,
        DeliveryOutcome("failed", "stale-worker"),
        worker_id="old",
        max_attempts=5,
        metrics=metrics,
    )
    assert metrics.stale_completions == 1
    await finalize_delivery(
        factory,
        second,
        DeliveryOutcome("sent"),
        worker_id="new",
        max_attempts=5,
        metrics=metrics,
    )
    async with dev_engine.connect() as conn, conn.begin():
        await conn.execute(text("SELECT set_config('app.role', 'Administrators', true)"))
        status = await conn.scalar(
            text("SELECT status FROM notification_deliveries WHERE id = :id"),
            {"id": first.id},
        )
    assert status == "sent"


async def test_expired_lease_cannot_finalize_without_being_reclaimed(
    api_engine, dev_engine, notification_seed
) -> None:
    factory = async_sessionmaker(api_engine, expire_on_commit=False)
    await materialize_event(
        factory,
        _event(),
        fingerprint_secret="integration-secret",
        provider_enabled={"email": True},
    )
    claimed = (await claim_deliveries(factory, worker_id="expired", limit=1, lease_sec=10))[0]
    async with dev_engine.connect() as conn, conn.begin():
        await conn.execute(text("SELECT set_config('app.role', 'Administrators', true)"))
        await conn.execute(
            text(
                "UPDATE notification_deliveries "
                "SET leased_until = clock_timestamp() - interval '1s' WHERE id = :id"
            ),
            {"id": claimed.id},
        )
    metrics = NotificationMetrics()
    await finalize_delivery(
        factory,
        claimed,
        DeliveryOutcome("sent"),
        worker_id="expired",
        max_attempts=5,
        metrics=metrics,
    )
    assert metrics.stale_completions == 1
    async with dev_engine.connect() as conn:
        status, attempts = (
            await conn.execute(
                text("SELECT status, attempt_count FROM notification_deliveries WHERE id = :id"),
                {"id": claimed.id},
            )
        ).one()
    assert status == "leased"
    assert attempts == 0


async def test_concurrent_finalizers_produce_stable_channels_projection(
    api_engine, dev_engine, notification_seed
) -> None:
    async with dev_engine.connect() as conn, conn.begin():
        await conn.execute(
            text(
                "INSERT INTO user_emails (id, phone_number, email, user_name) "
                "VALUES (98403, '13000000003', 'a2@example.test', :user)"
            ),
            {"user": USER_A},
        )
    factory = async_sessionmaker(api_engine, expire_on_commit=False)
    await materialize_event(
        factory,
        _event(),
        fingerprint_secret="integration-secret",
        provider_enabled={"email": True},
    )
    claimed = await claim_deliveries(factory, worker_id="projection", limit=2, lease_sec=10)
    assert len(claimed) == 2
    metrics = NotificationMetrics()
    await asyncio.gather(
        finalize_delivery(
            factory,
            claimed[0],
            DeliveryOutcome("sent"),
            worker_id="projection",
            max_attempts=5,
            metrics=metrics,
        ),
        finalize_delivery(
            factory,
            claimed[1],
            DeliveryOutcome("failed", "invalid_target"),
            worker_id="projection",
            max_attempts=5,
            metrics=metrics,
        ),
    )
    async with dev_engine.connect() as conn:
        projection = await conn.scalar(
            text(
                "SELECT channels_sent FROM alarm_records "
                "WHERE id = :alarm AND triggered_at = :triggered"
            ),
            {"alarm": ALARM_A, "triggered": TRIGGERED_AT},
        )
    assert projection["email"] == {
        "sent": 1,
        "failed": 1,
        "skipped": 0,
        "pending": 0,
    }


async def test_each_gateway_replica_observes_same_config_version(
    gw_engine, dev_engine, notification_seed
) -> None:
    first = await Registry.load_from_db(gw_engine)
    second = await Registry.load_from_db(gw_engine)
    async with dev_engine.connect() as conn, conn.begin():
        await conn.execute(text("SELECT set_config('app.role', 'Administrators', true)"))
        await conn.execute(
            text("UPDATE device_waring_cfgs SET limit_value = 81 WHERE id = :cfg"),
            {"cfg": CFG_A},
        )
        await conn.execute(
            text("UPDATE devices SET update_flag = update_flag + 1 WHERE dev_number = :dev"),
            {"dev": DEVICE_A},
        )

    assert await first.reload_alarm_rules_if_changed(gw_engine) == {DEVICE_A}
    assert await second.reload_alarm_rules_if_changed(gw_engine) == {DEVICE_A}
    assert first.get(DEVICE_A).points[POINT_A].alarms[0].limit_value == 81  # type: ignore[union-attr]
    assert second.get(DEVICE_A).points[POINT_A].alarms[0].limit_value == 81  # type: ignore[union-attr]
    async with dev_engine.connect() as conn:
        assert (
            await conn.scalar(
                text("SELECT update_flag FROM devices WHERE dev_number = :dev"),
                {"dev": DEVICE_A},
            )
            == 1
        )


async def test_automatic_recovery_and_manual_reset_have_one_winner(
    api_engine, gw_engine, dev_engine, notification_seed
) -> None:
    api_factory = async_sessionmaker(api_engine, expire_on_commit=False)
    gateway = Repository(gw_engine)

    async def manual_reset() -> bool:
        async with api_factory() as session, session.begin():
            await apply_tenant_context(session, usr_group="ug_A", role="Company")
            return await reset_alarm(session, ALARM_A)

    automatic, manual = await asyncio.gather(
        gateway.apply_alarm_reading(
            alarm_cfg_id=CFG_A,
            value=70,
            observed_at=(TRIGGERED_AT + timedelta(minutes=1)).timestamp(),
        ),
        manual_reset(),
    )
    assert int(automatic) + int(manual) == 1
    async with dev_engine.connect() as conn, conn.begin():
        await conn.execute(text("SELECT set_config('app.role', 'Administrators', true)"))
        assert not await conn.scalar(
            text("SELECT waring_flag FROM device_waring_cfgs WHERE id = :cfg"),
            {"cfg": CFG_A},
        )
        assert await conn.scalar(
            text(
                "SELECT reset_at IS NOT NULL FROM alarm_records "
                "WHERE id = :alarm AND triggered_at = :triggered"
            ),
            {"alarm": ALARM_A, "triggered": TRIGGERED_AT},
        )


async def test_outbox_failure_rolls_back_and_can_relay_again(
    gw_engine, dev_engine, notification_seed
) -> None:
    async with gw_engine.connect() as conn, conn.begin():
        await conn.execute(
            text(
                "INSERT INTO alarm_outbox (alarm_id, payload, published) "
                "VALUES (:alarm, '{\"schema_version\": 2}', false)"
            ),
            {"alarm": ALARM_A},
        )

    class FailingRedis:
        async def ping(self):
            return True

        async def xadd(self, *args, **kwargs):
            raise OSError("redis unavailable")

    gateway = Repository(gw_engine)
    with pytest.raises(OSError, match="redis unavailable"):
        await gateway.relay_alarm_outbox_once(FailingRedis())
    async with dev_engine.connect() as conn:
        assert not await conn.scalar(
            text("SELECT published FROM alarm_outbox WHERE alarm_id = :alarm"),
            {"alarm": ALARM_A},
        )

    class WorkingRedis:
        def __init__(self) -> None:
            self.events: list[dict[str, str]] = []

        async def ping(self):
            return True

        async def xadd(self, stream, fields, *, maxlen, approximate):
            assert maxlen == 100_000
            assert approximate is True
            self.events.append(fields)

    redis = WorkingRedis()
    assert await gateway.relay_alarm_outbox_once(redis) == 1
    assert redis.events == [{"schema_version": "2"}]
    async with dev_engine.connect() as conn:
        assert await conn.scalar(
            text("SELECT published FROM alarm_outbox WHERE alarm_id = :alarm"),
            {"alarm": ALARM_A},
        )


async def test_cleanup_removes_only_terminal_audit_after_180_days(
    api_engine, dev_engine, notification_seed
) -> None:
    factory = async_sessionmaker(api_engine, expire_on_commit=False)
    result = await materialize_event(
        factory,
        _event(),
        fingerprint_secret="integration-secret",
        provider_enabled={"email": True},
    )
    old = datetime.now(UTC) - timedelta(days=181)
    async with dev_engine.connect() as conn, conn.begin():
        await conn.execute(text("SELECT set_config('app.role', 'Administrators', true)"))
        await conn.execute(
            text(
                "UPDATE notification_deliveries SET status = 'sent' WHERE dispatch_id = :dispatch"
            ),
            {"dispatch": result.dispatch_id},
        )
        await conn.execute(
            text(
                "ALTER TABLE notification_dispatches "
                "DISABLE TRIGGER trg_notification_dispatches_protect_update"
            )
        )
        await conn.execute(
            text("UPDATE notification_dispatches SET created_at = :old WHERE id = :dispatch"),
            {"old": old, "dispatch": result.dispatch_id},
        )
        await conn.execute(
            text(
                "ALTER TABLE notification_dispatches "
                "ENABLE TRIGGER trg_notification_dispatches_protect_update"
            )
        )
    assert await cleanup_notification_audit(factory) == 0
    async with dev_engine.connect() as conn, conn.begin():
        await conn.execute(text("SELECT set_config('app.role', 'Administrators', true)"))
        await conn.execute(
            text(
                "ALTER TABLE notification_deliveries "
                "DISABLE TRIGGER trg_notification_deliveries_updated"
            )
        )
        await conn.execute(
            text(
                "UPDATE notification_deliveries SET updated_at = :old WHERE dispatch_id = :dispatch"
            ),
            {"old": old, "dispatch": result.dispatch_id},
        )
        await conn.execute(
            text(
                "ALTER TABLE notification_deliveries "
                "ENABLE TRIGGER trg_notification_deliveries_updated"
            )
        )
    assert await cleanup_notification_audit(factory) == 1
    async with dev_engine.connect() as conn, conn.begin():
        await conn.execute(text("SELECT set_config('app.role', 'Administrators', true)"))
        count = await conn.scalar(
            text("SELECT count(*) FROM notification_dispatches WHERE id = :dispatch"),
            {"dispatch": result.dispatch_id},
        )
    assert count == 0


async def test_dispatch_and_attempt_audit_are_not_updateable_by_api(dev_engine) -> None:
    async with dev_engine.connect() as conn:
        for table in ("notification_dispatches", "notification_delivery_attempts"):
            assert not await conn.scalar(
                text("SELECT has_table_privilege('ruisheng_api', :table, 'UPDATE')"),
                {"table": table},
            )
