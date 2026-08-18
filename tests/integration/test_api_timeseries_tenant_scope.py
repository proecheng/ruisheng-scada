"""Real PostgreSQL tenant isolation for API reads from non-RLS time-series tables."""

from __future__ import annotations

import struct
from datetime import UTC, date, datetime

import pytest
from ruisheng_api.api.reports import daily_report
from ruisheng_api.api.schemas.reports import DailyReportRequest
from ruisheng_api.api.waveforms import analyze_waveform, get_waveform_history
from ruisheng_api.core.rbac import CurrentUser
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

pytestmark = pytest.mark.integration

TENANT_A_DEVICE = "tenant-scope-a"
TENANT_B_DEVICE = "tenant-scope-b"


@pytest.fixture
def tenant_a_user() -> CurrentUser:
    return CurrentUser(
        user_name="tenant_scope_user",
        usr_group="ug_A",
        role="User",
        control_authority=0,
        jti="integration-test",
        fp="integration-test",
    )


@pytest.fixture
async def tenant_timeseries(dev_engine, gw_engine, seed_tenants):
    recorded_at = datetime(2026, 8, 18, 23, 59, 59, 999999, tzinfo=UTC)
    async with dev_engine.connect() as conn, conn.begin():
        await conn.execute(text("SELECT set_config('app.role', 'Administrators', true)"))
        await conn.execute(
            text("DELETE FROM point_data_history WHERE dev_number IN (:a, :b)"),
            {"a": TENANT_A_DEVICE, "b": TENANT_B_DEVICE},
        )
        await conn.execute(
            text("DELETE FROM waveform_history WHERE dev_number IN (:a, :b)"),
            {"a": TENANT_A_DEVICE, "b": TENANT_B_DEVICE},
        )
        await conn.execute(
            text("DELETE FROM devices WHERE dev_number IN (:a, :b)"),
            {"a": TENANT_A_DEVICE, "b": TENANT_B_DEVICE},
        )
    async with gw_engine.connect() as conn, conn.begin():
        await conn.execute(
            text("""
                INSERT INTO devices
                    (usr_group, dev_number, dev_ser_number, modbus_addr, baud_rate,
                     update_interval_decisec, loss_count, is_online, is_enabled, update_flag)
                VALUES
                    ('ug_A', :a, 'TENANT-SCOPE-SER-A', 1, 9600, 100, 0, false, true, 0),
                    ('ug_B', :b, 'TENANT-SCOPE-SER-B', 2, 9600, 100, 0, false, true, 0)
            """),
            {"a": TENANT_A_DEVICE, "b": TENANT_B_DEVICE},
        )
        await conn.execute(
            text("""
                INSERT INTO point_data_history
                    (dev_number, point_id, org_value, rt_value, recorded_at)
                VALUES
                    (:a, 1, 11.0, 11.0, :recorded_at),
                    (:b, 1, 99.0, 99.0, :recorded_at)
            """),
            {"a": TENANT_A_DEVICE, "b": TENANT_B_DEVICE, "recorded_at": recorded_at},
        )
        await conn.execute(
            text("""
                INSERT INTO waveform_history
                    (dev_number, point_id, data_array, sample_time_decisec,
                     packet_count, recorded_at)
                VALUES
                    (:a, 1, :samples_a, 10, 2, :recorded_at),
                    (:b, 1, :samples_b, 10, 2, :recorded_at)
            """),
            {
                "a": TENANT_A_DEVICE,
                "b": TENANT_B_DEVICE,
                "samples_a": struct.pack("<2f", 1.0, 2.0),
                "samples_b": struct.pack("<2f", 98.0, 99.0),
                "recorded_at": recorded_at,
            },
        )
    yield recorded_at
    async with dev_engine.connect() as conn, conn.begin():
        await conn.execute(text("SELECT set_config('app.role', 'Administrators', true)"))
        await conn.execute(
            text("DELETE FROM point_data_history WHERE dev_number IN (:a, :b)"),
            {"a": TENANT_A_DEVICE, "b": TENANT_B_DEVICE},
        )
        await conn.execute(
            text("DELETE FROM waveform_history WHERE dev_number IN (:a, :b)"),
            {"a": TENANT_A_DEVICE, "b": TENANT_B_DEVICE},
        )
        await conn.execute(
            text("DELETE FROM devices WHERE dev_number IN (:a, :b)"),
            {"a": TENANT_A_DEVICE, "b": TENANT_B_DEVICE},
        )


async def test_daily_report_only_aggregates_current_tenant_at_day_boundary(
    api_engine, tenant_timeseries, tenant_a_user
) -> None:
    session_factory = async_sessionmaker(api_engine, expire_on_commit=False)
    async with session_factory() as session:
        response = await daily_report(
            DailyReportRequest(day=date(2026, 8, 18), format="json"),
            user=tenant_a_user,
            session=session,
        )

    assert response["data"] == {
        TENANT_A_DEVICE: {1: {"count": 1, "min": 11.0, "max": 11.0, "avg": 11.0}}
    }


async def test_waveform_routes_hide_foreign_tenant_data(
    api_engine, tenant_timeseries, tenant_a_user
) -> None:
    from_ts = datetime(2026, 8, 18, tzinfo=UTC)
    to_ts = datetime(2026, 8, 19, tzinfo=UTC)
    session_factory = async_sessionmaker(api_engine, expire_on_commit=False)

    async with session_factory() as session:
        own_history = await get_waveform_history(
            TENANT_A_DEVICE,
            1,
            from_ts,
            to_ts,
            user=tenant_a_user,
            session=session,
        )
    async with session_factory() as session:
        foreign_history = await get_waveform_history(
            TENANT_B_DEVICE,
            1,
            from_ts,
            to_ts,
            user=tenant_a_user,
            session=session,
        )
    async with session_factory() as session:
        foreign_analysis = await analyze_waveform(
            TENANT_B_DEVICE,
            1,
            from_ts,
            to_ts,
            user=tenant_a_user,
            session=session,
        )

    assert len(own_history.data["waveforms"]) == 1
    assert foreign_history.data["waveforms"] == []
    assert foreign_analysis.data == {"freqs": [], "magnitudes": []}
