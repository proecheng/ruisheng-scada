from __future__ import annotations

from ruisheng_shared.models.devices import DevicePoint
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .point_display import hydrate_display, save_display


async def list_points(session: AsyncSession, dev_number: str) -> list[DevicePoint]:
    stmt = select(DevicePoint).where(DevicePoint.dev_number == dev_number).order_by(DevicePoint.id)
    points = list((await session.execute(stmt)).scalars())
    await hydrate_display(session, dev_number, points)
    return points


async def get_point(session: AsyncSession, point_id: int) -> DevicePoint | None:
    point = (
        await session.execute(select(DevicePoint).where(DevicePoint.id == point_id))
    ).scalar_one_or_none()
    if point is not None:
        await hydrate_display(session, point.dev_number, [point])
    return point


async def create_point(session: AsyncSession, *, dev_number: str, **fields: object) -> DevicePoint:
    width = fields.pop("display_bits", None)
    p = DevicePoint(dev_number=dev_number, **fields)
    session.add(p)
    await session.flush()
    if width is not None:
        await save_display(session, p, int(str(width)))
    return p


async def create_points(
    session: AsyncSession, *, dev_number: str, rows: list[dict[str, object]]
) -> list[DevicePoint]:
    points = [
        DevicePoint(dev_number=dev_number, **{k: v for k, v in row.items() if k != "display_bits"})
        for row in rows
    ]
    session.add_all(points)
    await session.flush()
    for point, row in zip(points, rows, strict=True):
        width = row.get("display_bits")
        if width is not None:
            await save_display(session, point, int(str(width)))
    return points


async def update_point(
    session: AsyncSession, point: DevicePoint, updates: dict[str, object]
) -> DevicePoint:
    for k, v in updates.items():
        if k == "display_bits":
            await save_display(session, point, None if v is None else int(str(v)))
            continue
        setattr(point, k, v)
    await session.flush()
    return point


async def delete_point(session: AsyncSession, point: DevicePoint) -> None:
    await save_display(session, point, None)
    await session.delete(point)
    await session.flush()
