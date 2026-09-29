"""Run inside a verified isolated candidate API container with fresh database storage."""

from __future__ import annotations

import asyncio
import io
import json
import os
from datetime import UTC, datetime, timedelta

from fastapi import UploadFile
from ruisheng_api.api import points as api
from ruisheng_api.api.schemas.points import PointCreateRequest, PointUpdateRequest
from ruisheng_api.config import Config
from ruisheng_api.core.rbac import CurrentUser
from ruisheng_api.core.tenant import apply_tenant_context
from ruisheng_api.db.repositories import devices, points, timeseries
from ruisheng_shared.errors.codes import BizError
from ruisheng_shared.models.devices import Device, DeviceStaticData
from ruisheng_shared.models.tenants import WxGroup
from ruisheng_shared.models.timeseries import PointDataHistory, PointDataRealtime
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


class Notices:
    async def publish(self, *_args):
        pass


async def main():  # noqa: PLR0915 - one isolated database acceptance lifecycle
    if not os.environ.get("RUISHENG_ISOLATED_TEST", "").startswith("plan-date-apps-"):
        raise RuntimeError("isolated_container_required")
    engine = create_async_engine(Config().db_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    actor = CurrentUser("display_test", "display-test", "Administrators", 2, "test", "test")
    notices = Notices()

    async def context(session):
        await apply_tenant_context(session, usr_group=actor.usr_group, role=actor.role)

    try:
        async with sessions() as session, session.begin():
            await context(session)
            assert (
                await session.execute(select(func.count()).select_from(Device))
            ).scalar_one() == 0
            session.add(WxGroup(usr_group=actor.usr_group))
            await session.flush()
            session.add(
                Device(
                    dev_number="DISPLAYTEST",
                    dev_ser_number="DISPLAYTEST-SN",
                    dev_name="display test",
                    modbus_addr=1,
                    transport_type="tcp",
                    usr_group=actor.usr_group,
                    is_enabled=False,
                    update_interval_decisec=50,
                )
            )

        async def create(name, number, width=None):
            async with sessions() as session:
                response = await api.create_point(
                    "DISPLAYTEST",
                    PointCreateRequest(
                        point_name=name,
                        point_number=number,
                        fun_code=3,
                        dev_addr=1,
                        value_type="字",
                        display_bits=width,
                    ),
                    actor,
                    session,
                    notices,
                )
                return response.data["id"]

        digital = await create("DI", 0, 2)
        analog = await create("temperature", 1)

        async def current():
            async with sessions() as session:
                response = await api.list_points("DISPLAYTEST", actor, session)
                return {p["id"]: p for p in response.data["items"]}

        assert (await current())[digital]["display_bits"] == 2
        async with sessions() as session:
            await api.update_point(
                "DISPLAYTEST", digital, PointUpdateRequest(display_bits=4), actor, session, notices
            )
        assert (await current())[digital]["display_bits"] == 4
        for invalid in (
            PointUpdateRequest(value_type="bit", r_bit=0),
            PointUpdateRequest(user_ratio=10),
        ):
            async with sessions() as session:
                try:
                    await api.update_point("DISPLAYTEST", digital, invalid, actor, session, notices)
                except BizError:
                    pass
                else:
                    raise AssertionError("invalid partial update was accepted")
        assert (await current())[digital]["display_bits"] == 4

        # Roll back the entire metadata change and then read in a new session.
        try:
            async with sessions() as session, session.begin():
                await context(session)
                await devices.get_by_dev_number(session, "DISPLAYTEST", for_update=True)
                point = await points.get_point(session, digital)
                await points.update_point(session, point, {"display_bits": 8})
                raise RuntimeError("test_rollback")
        except RuntimeError as error:
            assert str(error) == "test_rollback"
        assert (await current())[digital]["display_bits"] == 4

        async with sessions() as session:
            response = await api.export_points("DISPLAYTEST", actor, session)
            csv = b"".join([chunk async for chunk in response.body_iterator])
        assert b"display_bits" in csv
        async with sessions() as session:
            imported = await api.import_points(
                "DISPLAYTEST", UploadFile(file=io.BytesIO(csv)), actor, session, notices
            )
        imported_digital = next(p for p in imported.data["items"] if p["point_name"] == "DI")
        assert imported_digital["display_bits"] == 4
        async with sessions() as session:
            await api.delete_point("DISPLAYTEST", imported_digital["id"], actor, session, notices)
        async with sessions() as session, session.begin():
            await context(session)
            count = await session.execute(
                select(func.count())
                .select_from(DeviceStaticData)
                .where(
                    DeviceStaticData.base_msg_name == f"point_display_bits:{imported_digital['id']}"
                )
            )
            assert count.scalar_one() == 0

        start = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
        async with sessions() as session, session.begin():
            await context(session)
            for point_id, values in ((digital, (0, 3)), (analog, (10, 20))):
                for index, value in enumerate(values):
                    session.add(
                        PointDataHistory(
                            dev_number="DISPLAYTEST",
                            point_id=point_id,
                            recorded_at=start + timedelta(seconds=10 + index * 10),
                            org_value=value,
                            rt_value=value,
                        )
                    )
                session.add(
                    PointDataRealtime(
                        dev_number="DISPLAYTEST",
                        point_id=point_id,
                        recorded_at=start + timedelta(seconds=20),
                        org_value=values[-1],
                        rt_value=values[-1],
                    )
                )
        async with sessions() as session, session.begin():
            await context(session)
            live = await timeseries.load_realtime(session, "DISPLAYTEST")
            assert next(p for p in live if p["point_id"] == digital)["display_bits"] == 4
            history = await timeseries.load_history(
                session,
                dev_number="DISPLAYTEST",
                point_ids=[digital, analog],
                from_ts=start,
                to_ts=start + timedelta(minutes=5),
                sample_interval_s=300,
                offset=0,
                limit=10,
            )
            values = {p["point_id"]: p["rt_value"] for p in history}
            assert values == {digital: 3, analog: 15}
        async with sessions() as session:
            await api.update_point(
                "DISPLAYTEST",
                digital,
                PointUpdateRequest(display_bits=None),
                actor,
                session,
                notices,
            )
        assert (await current())[digital]["display_bits"] is None
        print(
            json.dumps(
                {
                    "passed": True,
                    "checks": [
                        "create",
                        "persist_and_reload",
                        "partial_update_validation",
                        "transaction_rollback",
                        "csv_roundtrip",
                        "delete_metadata",
                        "realtime_metadata",
                        "digital_last_sample_and_analog_average",
                        "clear_display",
                    ],
                }
            )
        )
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
