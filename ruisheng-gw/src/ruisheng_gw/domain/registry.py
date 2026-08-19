"""In-memory device + point registry with atomic alarm-rule refresh.

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
    min_val: float | None
    max_val: float | None
    alarm_level: int


@dataclass(frozen=True)
class AlarmSpec:
    id: int
    reg_bit: int | None
    alarm_name: str
    alarm_type: str
    limit_value: float
    alarm_msg: str | None
    waring_flag: bool
    relation_point_id: int | None
    relation_reg_bit: int | None
    relation_alarm_type: str | None
    relation_limit_value: float | None


@dataclass(frozen=True)
class PointEntry:
    point: Point
    threshold: ThresholdSpec = ThresholdSpec(None, None, 1)
    alarms: tuple[AlarmSpec, ...] = ()

    @property
    def register_span(self) -> int:
        if self.point.value_type == "双字":
            return 2
        return 1


@dataclass
class RegistryEntry:
    device: Device
    update_interval_decisec: int
    transport_type: str = "tcp"  # 'tcp' | 'serial'
    serial_port: str | None = None  # e.g. "COM3"; None for TCP
    dev_ip: str | None = None
    modbus_addr: int = 1  # ModBus slave address 1-247
    config_version: int = 0
    points: dict[int, PointEntry] = field(default_factory=dict)
    poll_cursor: int = 0


class Registry:
    def __init__(self) -> None:
        self._entries: dict[str, RegistryEntry] = {}

    @classmethod
    def build(
        cls,
        *,
        device_rows: list[dict[str, Any]],
        point_rows: list[dict[str, Any]],
        alarm_rows: list[dict[str, Any]] | None = None,
    ) -> Registry:
        reg = cls()
        for dr in device_rows:
            dev = Device(
                dev_number=dr["dev_number"],
                usr_group=dr["usr_group"],
                dev_ser_number=dr.get("dev_ser_number", ""),
                iccid=dr.get("iccid"),
            )
            reg._entries[dr["dev_number"]] = RegistryEntry(
                device=dev,
                update_interval_decisec=dr["update_interval_decisec"],
                transport_type=dr.get("transport_type", "tcp"),
                serial_port=dr.get("serial_port"),
                dev_ip=dr.get("dev_ip"),
                modbus_addr=dr.get("modbus_addr", 1),
                config_version=int(dr.get("update_flag", 0)),
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
                point_number=pr.get("point_number", 0),
                fun_code=pr.get("fun_code", 3),
                dev_addr=pr.get("dev_addr", 1),
                r_bit=pr.get("r_bit"),
                value_type=pr.get("value_type", "字"),
            )
            entry.points[pr["id"]] = PointEntry(
                point=point,
                threshold=ThresholdSpec(
                    min_val=pr.get("min_val"),
                    max_val=pr.get("max_val"),
                    alarm_level=pr.get("alarm_level", 1),
                ),
            )
        alarms_by_point: dict[int, list[AlarmSpec]] = {}
        for row in alarm_rows or []:
            alarms_by_point.setdefault(row["point_id"], []).append(
                AlarmSpec(
                    id=row["id"],
                    reg_bit=row.get("reg_bit"),
                    alarm_name=row["alarm_name"],
                    alarm_type=row["alarm_type"],
                    limit_value=float(row["limit_value"]),
                    alarm_msg=row.get("alarm_msg"),
                    waring_flag=bool(row.get("waring_flag", False)),
                    relation_point_id=row.get("relation_point_id"),
                    relation_reg_bit=row.get("relation_reg_bit"),
                    relation_alarm_type=row.get("relation_alarm_type"),
                    relation_limit_value=(
                        float(row["relation_limit_value"])
                        if row.get("relation_limit_value") is not None
                        else None
                    ),
                )
            )
        for entry in reg._entries.values():
            for point_id, point_entry in tuple(entry.points.items()):
                entry.points[point_id] = PointEntry(
                    point=point_entry.point,
                    threshold=point_entry.threshold,
                    alarms=tuple(alarms_by_point.get(point_id, ())),
                )
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
                            "       transport_type, serial_port, dev_ip, modbus_addr, "
                            "       dev_ser_number, iccid, update_flag "
                            "FROM devices "
                            "WHERE usr_group IS NOT NULL "
                            "  AND deleted_at IS NULL "
                            "  AND is_enabled IS TRUE"
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
                            "SELECT id, dev_number, point_number, fun_code, dev_addr, "
                            "       r_bit, value_type, point_ratio, point_offset, "
                            "       user_ratio, user_point_offset, "
                            "       min_value AS min_val, max_value AS max_val "
                            "FROM device_points"
                        )
                    )
                )
                .mappings()
                .all()
            )
            a_rows = (
                (
                    await conn.execute(
                        text(
                            "SELECT id, point_id, reg_bit, alarm_name, alarm_type, limit_value, "
                            "       alarm_msg, waring_flag, relation_point_id, "
                            "       relation_reg_bit, relation_alarm_type, relation_limit_value "
                            "FROM device_waring_cfgs "
                            "WHERE enable IS TRUE"
                        )
                    )
                )
                .mappings()
                .all()
            )
        return cls.build(
            device_rows=[dict(r) for r in d_rows],
            point_rows=[dict(r) for r in p_rows],
            alarm_rows=[dict(r) for r in a_rows],
        )

    def get(self, dev_number: str) -> RegistryEntry | None:
        return self._entries.get(dev_number)

    def replace_alarm_rules(
        self,
        *,
        dev_numbers: set[str],
        alarm_rows: list[dict[str, Any]],
    ) -> None:
        alarms_by_point: dict[int, list[AlarmSpec]] = {}
        for row in alarm_rows:
            alarms_by_point.setdefault(int(row["point_id"]), []).append(
                AlarmSpec(
                    id=int(row["id"]),
                    reg_bit=row.get("reg_bit"),
                    alarm_name=str(row["alarm_name"]),
                    alarm_type=str(row["alarm_type"]),
                    limit_value=float(row["limit_value"]),
                    alarm_msg=row.get("alarm_msg"),
                    waring_flag=bool(row.get("waring_flag", False)),
                    relation_point_id=row.get("relation_point_id"),
                    relation_reg_bit=row.get("relation_reg_bit"),
                    relation_alarm_type=row.get("relation_alarm_type"),
                    relation_limit_value=(
                        float(row["relation_limit_value"])
                        if row.get("relation_limit_value") is not None
                        else None
                    ),
                )
            )
        for dev_number in dev_numbers:
            entry = self._entries.get(dev_number)
            if entry is None:
                continue
            entry.points = {
                point_id: PointEntry(
                    point=point_entry.point,
                    threshold=point_entry.threshold,
                    alarms=tuple(alarms_by_point.get(point_id, ())),
                )
                for point_id, point_entry in entry.points.items()
            }

    async def reload_alarm_rules(
        self,
        engine: Any,
        dev_numbers: set[str],
    ) -> set[str]:
        from sqlalchemy import text  # noqa: PLC0415

        requested = {dev for dev in dev_numbers if dev in self._entries}
        if not requested:
            return set()
        async with engine.begin() as conn:
            params = {"devices": sorted(requested)}
            version_rows = (
                (
                    await conn.execute(
                        text(
                            "SELECT dev_number, update_flag FROM devices "
                            "WHERE dev_number = ANY(CAST(:devices AS text[])) "
                            "AND deleted_at IS NULL AND is_enabled IS TRUE"
                        ),
                        params,
                    )
                )
                .mappings()
                .all()
            )
            refreshed = {str(row["dev_number"]) for row in version_rows}
            if not refreshed:
                return set()
            alarm_rows = (
                (
                    await conn.execute(
                        text(
                            "SELECT id, dev_number, point_id, reg_bit, alarm_name, alarm_type, "
                            "limit_value, alarm_msg, waring_flag, relation_point_id, "
                            "relation_reg_bit, relation_alarm_type, relation_limit_value "
                            "FROM device_waring_cfgs WHERE enable IS TRUE "
                            "AND dev_number = ANY(CAST(:devices AS text[]))"
                        ),
                        params,
                    )
                )
                .mappings()
                .all()
            )
            self.replace_alarm_rules(
                dev_numbers=refreshed,
                alarm_rows=[dict(row) for row in alarm_rows],
            )
            for row in version_rows:
                self._entries[str(row["dev_number"])].config_version = int(row["update_flag"])
        return refreshed

    async def reload_alarm_rules_if_changed(self, engine: Any) -> set[str]:
        """Poll immutable per-device versions without consuming shared state."""
        from sqlalchemy import text  # noqa: PLC0415

        async with engine.begin() as conn:
            version_rows = (
                (
                    await conn.execute(
                        text(
                            "SELECT dev_number, update_flag FROM devices "
                            "WHERE deleted_at IS NULL AND is_enabled IS TRUE"
                        )
                    )
                )
                .mappings()
                .all()
            )
        changed = {
            str(row["dev_number"])
            for row in version_rows
            if (entry := self._entries.get(str(row["dev_number"]))) is not None
            and entry.config_version != int(row["update_flag"])
        }
        return await self.reload_alarm_rules(engine, changed)

    def needs_config_reload(self, dev_number: str, version: int) -> bool:
        entry = self._entries.get(dev_number)
        return entry is not None and version > entry.config_version

    def entries(self) -> ValuesView[RegistryEntry]:
        return self._entries.values()

    def devices_for_serial_port(self, port: str) -> list[RegistryEntry]:
        """Return all entries whose transport is serial and serial_port matches."""
        return [
            e
            for e in self._entries.values()
            if e.transport_type == "serial" and e.serial_port == port
        ]

    def tcp_device_for_modbus_addr(self, addr: int) -> RegistryEntry | None:
        """Return the unique TCP device for a ModBus address, or None if absent/ambiguous."""
        matches = [
            e for e in self._entries.values() if e.transport_type == "tcp" and e.modbus_addr == addr
        ]
        if len(matches) != 1:
            return None
        return matches[0]

    def tcp_device_for_dev_ser_number(self, dev_ser_number: str) -> RegistryEntry | None:
        """Return the unique TCP device for a device serial number."""
        matches = [
            e
            for e in self._entries.values()
            if e.transport_type == "tcp" and e.device.dev_ser_number == dev_ser_number
        ]
        if len(matches) != 1:
            return None
        return matches[0]
