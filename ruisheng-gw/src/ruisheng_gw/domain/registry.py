"""In-memory device + point registry loaded from DB at startup.

Plan 1 does NOT reload registry at runtime (no 5s update_flag watcher;
Plan 1.5 scope). Config changes require gw restart.

Load is done via `await Registry.load_from_db(engine)` which delegates
to SQL SELECT on devices + device_points. For testability, the pure
`Registry.build(device_rows, point_rows)` is exposed separately.
"""

from __future__ import annotations

from collections.abc import ValuesView
from dataclasses import dataclass, field
from typing import Any

from ruisheng_gw.domain.device import Device
from ruisheng_gw.domain.point import Point


@dataclass(frozen=True)
class ThresholdSpec:
    """Alarm threshold config per point (used by alarm_simple)."""

    min_val: float | None
    max_val: float | None
    alarm_level: int


@dataclass(frozen=True)
class PointEntry:
    point: Point
    threshold: ThresholdSpec


@dataclass
class RegistryEntry:
    device: Device
    update_interval_decisec: int
    transport_type: str = "tcp"  # 'tcp' | 'serial'
    serial_port: str | None = None  # e.g. "COM3"; None for TCP
    modbus_addr: int = 1  # ModBus slave address 1-247
    points: dict[int, PointEntry] = field(default_factory=dict)


class Registry:
    def __init__(self) -> None:
        self._entries: dict[str, RegistryEntry] = {}

    @classmethod
    def build(
        cls,
        *,
        device_rows: list[dict[str, Any]],
        point_rows: list[dict[str, Any]],
    ) -> Registry:
        reg = cls()
        for dr in device_rows:
            dev = Device(dev_number=dr["dev_number"], usr_group=dr["usr_group"])
            reg._entries[dr["dev_number"]] = RegistryEntry(
                device=dev,
                update_interval_decisec=dr["update_interval_decisec"],
                transport_type=dr.get("transport_type", "tcp"),
                serial_port=dr.get("serial_port"),
                modbus_addr=dr.get("modbus_addr", 1),
            )
        for pr in point_rows:
            entry = reg._entries.get(pr["dev_number"])
            if entry is None:
                continue
            point = Point(
                point_id=pr["id"],
                dev_number=pr["dev_number"],
                point_ratio=pr["point_ratio"],
                point_offset=pr["point_offset"],
                user_ratio=pr["user_ratio"],
                user_point_offset=pr["user_point_offset"],
            )
            threshold = ThresholdSpec(
                min_val=pr.get("min_val"),
                max_val=pr.get("max_val"),
                alarm_level=pr.get("alarm_level", 1),
            )
            entry.points[pr["id"]] = PointEntry(point=point, threshold=threshold)
        return reg

    @classmethod
    async def load_from_db(cls, engine: Any) -> Registry:
        from sqlalchemy import text  # noqa: PLC0415 — lazy import avoids hard dep at module level

        async with engine.begin() as conn:
            d_rows = (
                (
                    await conn.execute(
                        text(
                            "SELECT dev_number, usr_group, update_interval_decisec, "
                            "       transport_type, serial_port, modbus_addr "
                            "FROM devices "
                            "WHERE usr_group IS NOT NULL "
                            "  AND deleted_at IS NULL"
                        )
                    )
                )
                .mappings()
                .all()
            )
            p_rows = (
                (
                    await conn.execute(
                        text(  # noqa: TNL001 (no usr_group col; filtered via devices.usr_group join)
                            "SELECT id, dev_number, point_ratio, point_offset, "
                            "       user_ratio, user_point_offset, "
                            "       min_value AS min_val, max_value AS max_val "
                            "FROM device_points"
                        )
                    )
                )
                .mappings()
                .all()
            )
        return cls.build(
            device_rows=[dict(r) for r in d_rows],
            point_rows=[dict(r) for r in p_rows],
        )

    def get(self, dev_number: str) -> RegistryEntry | None:
        return self._entries.get(dev_number)

    def entries(self) -> ValuesView[RegistryEntry]:
        return self._entries.values()

    def devices_for_serial_port(self, port: str) -> list[RegistryEntry]:
        """Return all entries whose transport is serial and serial_port matches."""
        return [
            e
            for e in self._entries.values()
            if e.transport_type == "serial" and e.serial_port == port
        ]
