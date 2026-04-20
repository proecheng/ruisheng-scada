from __future__ import annotations

from ruisheng_shared.models.devices import DevicePoint
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


async def list_points(session: AsyncSession, dev_number: str) -> list[DevicePoint]:
    stmt = select(DevicePoint).where(DevicePoint.dev_number == dev_number).order_by(DevicePoint.id)
    return list((await session.execute(stmt)).scalars())


async def get_point(session: AsyncSession, point_id: int) -> DevicePoint | None:
    return (
        await session.execute(select(DevicePoint).where(DevicePoint.id == point_id))
    ).scalar_one_or_none()


async def create_point(session: AsyncSession, *, dev_number: str, **fields: object) -> DevicePoint:
    p = DevicePoint(dev_number=dev_number, **fields)
    session.add(p)
    await session.flush()
    return p


async def update_point(
    session: AsyncSession, point: DevicePoint, updates: dict[str, object]
) -> DevicePoint:
    for k, v in updates.items():
        setattr(point, k, v)
    await session.flush()
    return point


async def delete_point(session: AsyncSession, point: DevicePoint) -> None:
    await session.delete(point)
    await session.flush()
