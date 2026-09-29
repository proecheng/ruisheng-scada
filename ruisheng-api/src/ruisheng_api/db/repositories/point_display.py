"""Point presentation metadata in the existing device static key/value store.

Callers authorize the device before reads and hold its row lock during writes.
Keeping this metadata separate preserves register decoding and sampled history.
"""

from __future__ import annotations

from ruisheng_shared.models.devices import DevicePoint, DeviceStaticData
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

DISPLAY_KEY_PREFIX = "point_display_bits:"
MAX_DISPLAY_BITS = 16
VALID_WIDTHS = {str(i) for i in range(1, MAX_DISPLAY_BITS + 1)}


async def hydrate_display(
    session: AsyncSession, dev_number: str, points: list[DevicePoint]
) -> None:
    if not points:
        return
    keys = [f"{DISPLAY_KEY_PREFIX}{p.id}" for p in points]
    result = await session.execute(
        select(DeviceStaticData.base_msg_name, DeviceStaticData.base_msg_value)
        .where(DeviceStaticData.dev_number == dev_number, DeviceStaticData.base_msg_name.in_(keys))
        .order_by(DeviceStaticData.id)
    )
    widths = {key: int(value) for key, value in result if value in VALID_WIDTHS}
    for point in points:
        point.display_bits = widths.get(f"{DISPLAY_KEY_PREFIX}{point.id}")  # type: ignore[attr-defined]


async def save_display(session: AsyncSession, point: DevicePoint, width: int | None) -> None:
    if width is not None and (type(width) is not int or not 1 <= width <= MAX_DISPLAY_BITS):
        raise ValueError("display width must be 1..16 or null")
    key = f"{DISPLAY_KEY_PREFIX}{point.id}"
    await session.execute(
        delete(DeviceStaticData).where(
            DeviceStaticData.dev_number == point.dev_number, DeviceStaticData.base_msg_name == key
        )
    )
    if width is not None:
        session.add(
            DeviceStaticData(
                dev_number=point.dev_number, base_msg_name=key, base_msg_value=str(width)
            )
        )
    point.display_bits = width  # type: ignore[attr-defined]
    await session.flush()
