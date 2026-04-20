"""devices 仓储。"""

from __future__ import annotations

from datetime import UTC

from ruisheng_shared.models.devices import Device
from sqlalchemy import func, or_, select
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
    if q:
        like = f"%{q}%"
        base = base.where(or_(Device.dev_number.ilike(like), Device.dev_name.ilike(like)))
    total = (await session.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    stmt = base.order_by(Device.id.desc()).offset(offset).limit(limit)
    rows = list((await session.execute(stmt)).scalars())
    return rows, int(total)


async def get_by_dev_number(session: AsyncSession, dev_number: str) -> Device | None:
    stmt = select(Device).where(Device.dev_number == dev_number, Device.deleted_at.is_(None))
    return (await session.execute(stmt)).scalar_one_or_none()


async def create_device(session: AsyncSession, **fields: object) -> Device:
    d = Device(**fields)
    session.add(d)
    await session.flush()
    return d


async def update_device_fields(
    session: AsyncSession, device: Device, updates: dict[str, object]
) -> Device:
    for k, v in updates.items():
        setattr(device, k, v)
    await session.flush()
    return device


async def soft_delete(session: AsyncSession, device: Device) -> None:
    from datetime import datetime

    device.deleted_at = datetime.now(UTC)
    await session.flush()
