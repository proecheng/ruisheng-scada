"""devices 仓储。"""

from __future__ import annotations

from datetime import UTC

from ruisheng_shared.device_status import SERIAL_LOSS_LIMIT, SERIAL_MIN_GRACE_SECONDS
from ruisheng_shared.errors.codes import BizError, ErrCode
from ruisheng_shared.models.devices import Device, DeviceTemplate
from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession


async def list_devices(
    session: AsyncSession,
    *,
    offset: int,
    limit: int,
    online_only: bool = False,
    q: str | None = None,
) -> tuple[list[Device], int]:
    base = select(Device).where(Device.deleted_at.is_(None))
    if online_only:
        base = base.where(Device.is_online.is_(True))
        age = func.extract("epoch", func.now() - Device.last_back_at)
        base = base.where(
            or_(
                Device.transport_type != "serial",
                and_(
                    Device.is_enabled.is_(True),
                    Device.loss_count < SERIAL_LOSS_LIMIT,
                    age >= 0,
                    age
                    < func.greatest(
                        SERIAL_MIN_GRACE_SECONDS,
                        SERIAL_LOSS_LIMIT * Device.update_interval_decisec / 10.0,
                    ),
                ),
            )
        )
    if q:
        like = f"%{q}%"
        base = base.where(or_(Device.dev_number.ilike(like), Device.dev_name.ilike(like)))
    total = (await session.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    stmt = base.order_by(Device.id.desc()).offset(offset).limit(limit)
    rows = list((await session.execute(stmt)).scalars())
    return rows, int(total)


async def get_by_dev_number(
    session: AsyncSession, dev_number: str, *, for_update: bool = False
) -> Device | None:
    stmt = select(Device).where(Device.dev_number == dev_number, Device.deleted_at.is_(None))
    if for_update:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_serial_endpoint(
    session: AsyncSession,
    *,
    serial_port: str,
    modbus_addr: int,
) -> Device | None:
    stmt = select(Device).where(
        Device.deleted_at.is_(None),
        Device.transport_type == "serial",
        Device.serial_port == serial_port,
        Device.modbus_addr == modbus_addr,
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def create_device(session: AsyncSession, **fields: object) -> Device:
    d = Device(**fields)
    session.add(d)
    await _flush_device(session)
    return d


async def update_device_fields(
    session: AsyncSession, device: Device, updates: dict[str, object]
) -> Device:
    if updates.get("transport_type", device.transport_type) == "serial":
        updates = {**updates, "is_online": False, "last_back_at": None}
    elif updates.get("is_enabled") is False:
        updates = {**updates, "is_online": False}
    for k, v in updates.items():
        setattr(device, k, v)
    await _flush_device(session)
    return device


async def soft_delete(session: AsyncSession, device: Device) -> None:
    from datetime import datetime

    device.deleted_at = datetime.now(UTC)
    device.is_online = False
    await session.flush()


async def _flush_device(session: AsyncSession) -> None:
    try:
        await session.flush()
    except IntegrityError as exc:
        # asyncpg exposes constraint metadata on the chained original exception.
        cause: BaseException | None = exc.orig
        messages = {
            "uq_devices_serial_port_modbus_addr": "serial_port and modbus_addr already in use",
            "uq_devices_dev_number": "dev_number already exists",
            "uq_devices_ser_iccid": "device identity already exists",
        }
        while cause is not None:
            diagnostic = getattr(cause, "diag", None)
            name = getattr(cause, "constraint_name", None) or getattr(
                diagnostic, "constraint_name", None
            )
            if name in messages:
                raise BizError(ErrCode.BAD_PARAM, messages[name]) from exc
            cause = cause.__cause__
        raise


async def list_templates(session: AsyncSession) -> list[DeviceTemplate]:
    stmt = select(DeviceTemplate).order_by(DeviceTemplate.name)
    return list((await session.execute(stmt)).scalars())


async def get_template(session: AsyncSession, template_id: int) -> DeviceTemplate | None:
    stmt = select(DeviceTemplate).where(DeviceTemplate.id == template_id)
    return (await session.execute(stmt)).scalar_one_or_none()


async def create_template(session: AsyncSession, **fields: object) -> DeviceTemplate:
    t = DeviceTemplate(**fields)
    session.add(t)
    await session.flush()
    return t


async def update_template_fields(
    session: AsyncSession, template: DeviceTemplate, updates: dict[str, object]
) -> DeviceTemplate:
    for k, v in updates.items():
        setattr(template, k, v)
    await session.flush()
    return template


async def delete_template(session: AsyncSession, template: DeviceTemplate) -> None:
    await session.delete(template)
    await session.flush()
