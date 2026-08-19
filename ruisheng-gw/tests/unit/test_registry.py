"""Registry: in-memory device + point map loaded from DB at startup."""

from __future__ import annotations

from ruisheng_gw.domain.device import DeviceState
from ruisheng_gw.domain.registry import Registry


def test_build_from_rows() -> None:
    device_rows = [
        {
            "dev_number": "DEV-001",
            "usr_group": "ug_A",
            "update_interval_decisec": 10,
            "dev_ser_number": "SN-001",
            "iccid": "ICCID-1",
        },
        {"dev_number": "DEV-002", "usr_group": "ug_B", "update_interval_decisec": 50},
    ]
    point_rows = [
        {
            "id": 10,
            "dev_number": "DEV-001",
            "point_ratio": 1.0,
            "point_offset": 0.0,
            "user_ratio": 1.0,
            "user_point_offset": 0.0,
            "min_val": 0.0,
            "max_val": 100.0,
            "alarm_level": 1,
        },
    ]
    alarm_rows = [
        {
            "id": 7,
            "point_id": 10,
            "reg_bit": 3,
            "alarm_name": "high",
            "alarm_type": ">",
            "limit_value": 80.0,
            "alarm_msg": "too high",
            "waring_flag": False,
            "relation_point_id": 11,
            "relation_reg_bit": 2,
            "relation_alarm_type": "=",
            "relation_limit_value": 1.0,
        }
    ]
    reg = Registry.build(
        device_rows=device_rows,
        point_rows=point_rows,
        alarm_rows=alarm_rows,
    )
    e1 = reg.get("DEV-001")
    assert e1 is not None
    assert e1.device.usr_group == "ug_A"
    assert e1.device.dev_ser_number == "SN-001"
    assert e1.device.iccid == "ICCID-1"
    assert e1.device.state is DeviceState.UNREGISTERED
    assert len(e1.points) == 1
    assert e1.points[10].alarms[0].id == 7
    assert e1.points[10].alarms[0].reg_bit == 3
    assert e1.points[10].alarms[0].alarm_name == "high"
    assert e1.points[10].alarms[0].relation_point_id == 11
    assert e1.points[10].alarms[0].relation_reg_bit == 2


def test_get_returns_none_for_unknown() -> None:
    reg = Registry.build(device_rows=[], point_rows=[])
    assert reg.get("DEV-XXX") is None


def test_iter_all_devices() -> None:
    device_rows = [
        {"dev_number": "A", "usr_group": "ug", "update_interval_decisec": 10},
        {"dev_number": "B", "usr_group": "ug", "update_interval_decisec": 10},
    ]
    reg = Registry.build(device_rows=device_rows, point_rows=[])
    assert sorted(e.device.dev_number for e in reg.entries()) == ["A", "B"]


def test_devices_for_serial_port_returns_matching() -> None:
    device_rows = [
        {
            "dev_number": "SER-001",
            "usr_group": "ug",
            "update_interval_decisec": 10,
            "transport_type": "serial",
            "serial_port": "COM3",
            "modbus_addr": 1,
        },
        {
            "dev_number": "SER-002",
            "usr_group": "ug",
            "update_interval_decisec": 10,
            "transport_type": "serial",
            "serial_port": "COM3",
            "modbus_addr": 2,
        },
        {
            "dev_number": "TCP-001",
            "usr_group": "ug",
            "update_interval_decisec": 10,
            "transport_type": "tcp",
            "serial_port": None,
            "modbus_addr": 1,
        },
    ]
    reg = Registry.build(device_rows=device_rows, point_rows=[])
    result = reg.devices_for_serial_port("COM3")
    dev_numbers = {r.device.dev_number for r in result}
    addrs = {r.modbus_addr for r in result}
    assert dev_numbers == {"SER-001", "SER-002"}
    assert addrs == {1, 2}


def test_devices_for_serial_port_empty_when_no_match() -> None:
    device_rows = [
        {
            "dev_number": "TCP-001",
            "usr_group": "ug",
            "update_interval_decisec": 10,
            "transport_type": "tcp",
            "serial_port": None,
            "modbus_addr": 1,
        },
    ]
    reg = Registry.build(device_rows=device_rows, point_rows=[])
    assert reg.devices_for_serial_port("COM3") == []


def test_build_preserves_transport_fields() -> None:
    device_rows = [
        {
            "dev_number": "SER-001",
            "usr_group": "ug",
            "update_interval_decisec": 10,
            "transport_type": "serial",
            "serial_port": "COM3",
            "modbus_addr": 5,
        },
    ]
    reg = Registry.build(device_rows=device_rows, point_rows=[])
    entry = reg.get("SER-001")
    assert entry is not None
    assert entry.transport_type == "serial"
    assert entry.serial_port == "COM3"
    assert entry.modbus_addr == 5


def test_tcp_device_for_modbus_addr_requires_unique_match() -> None:
    reg = Registry.build(
        device_rows=[
            {
                "dev_number": "TCP-001",
                "usr_group": "ug",
                "update_interval_decisec": 10,
                "transport_type": "tcp",
                "serial_port": None,
                "modbus_addr": 1,
            },
            {
                "dev_number": "TCP-002",
                "usr_group": "ug",
                "update_interval_decisec": 10,
                "transport_type": "tcp",
                "serial_port": None,
                "modbus_addr": 2,
            },
        ],
        point_rows=[],
    )
    assert reg.tcp_device_for_modbus_addr(1).device.dev_number == "TCP-001"  # type: ignore[union-attr]
    assert reg.tcp_device_for_modbus_addr(99) is None


def test_tcp_device_for_dev_ser_number_requires_unique_match() -> None:
    reg = Registry.build(
        device_rows=[
            {
                "dev_number": "TCP-001",
                "dev_ser_number": "SN-001",
                "usr_group": "ug",
                "update_interval_decisec": 10,
                "transport_type": "tcp",
                "serial_port": None,
                "modbus_addr": 1,
            },
            {
                "dev_number": "SER-001",
                "dev_ser_number": "SN-002",
                "usr_group": "ug",
                "update_interval_decisec": 10,
                "transport_type": "serial",
                "serial_port": "COM3",
                "modbus_addr": 1,
            },
        ],
        point_rows=[],
    )
    match = reg.tcp_device_for_dev_ser_number("SN-001")
    assert match is not None
    assert match.device.dev_number == "TCP-001"
    assert reg.tcp_device_for_dev_ser_number("SN-002") is None


def test_replace_alarm_rules_is_visible_without_rebuilding_registry() -> None:
    reg = Registry.build(
        device_rows=[{"dev_number": "D1", "usr_group": "g1", "update_interval_decisec": 10}],
        point_rows=[
            {
                "id": 1,
                "dev_number": "D1",
                "point_ratio": 1.0,
                "point_offset": 0.0,
                "user_ratio": 1.0,
                "user_point_offset": 0.0,
            }
        ],
    )
    original_entry = reg.get("D1")
    reg.replace_alarm_rules(
        dev_numbers={"D1"},
        alarm_rows=[
            {
                "id": 9,
                "point_id": 1,
                "alarm_name": "hot",
                "alarm_type": ">",
                "limit_value": 80,
            }
        ],
    )
    assert reg.get("D1") is original_entry
    assert original_entry is not None
    assert original_entry.points[1].alarms[0].id == 9


async def test_config_version_poll_is_non_consuming_and_clears_deleted_rules() -> None:
    reg = Registry.build(
        device_rows=[
            {
                "dev_number": "D1",
                "usr_group": "g1",
                "update_interval_decisec": 10,
                "update_flag": 1,
            }
        ],
        point_rows=[
            {
                "id": 1,
                "dev_number": "D1",
                "point_ratio": 1.0,
                "point_offset": 0.0,
                "user_ratio": 1.0,
                "user_point_offset": 0.0,
            }
        ],
        alarm_rows=[
            {
                "id": 9,
                "point_id": 1,
                "alarm_name": "old",
                "alarm_type": ">",
                "limit_value": 80,
            }
        ],
    )
    calls: list[tuple[str, dict[str, object] | None]] = []

    class Result:
        def __init__(self, rows):
            self._rows = rows

        def mappings(self):
            return self

        def all(self):
            return self._rows

    class Conn:
        async def execute(self, statement, params=None):
            sql = str(statement)
            calls.append((sql, params))
            if "FROM device_waring_cfgs" in sql:
                return Result([])
            return Result([{"dev_number": "D1", "update_flag": 2}])

    class Begin:
        async def __aenter__(self):
            return Conn()

        async def __aexit__(self, *args):
            return None

    class Engine:
        def begin(self):
            return Begin()

    assert await reg.reload_alarm_rules_if_changed(Engine()) == {"D1"}
    assert reg.get("D1").config_version == 2  # type: ignore[union-attr]
    assert reg.get("D1").points[1].alarms == ()  # type: ignore[union-attr]
    assert not any("UPDATE devices" in statement for statement, _ in calls)
    assert all("usr_group" in statement for statement, _ in calls)
    assert calls[0][1] == {"tenants": ["g1"]}
    assert calls[1][1] == {"dev_numbers": ["D1"], "tenants": ["g1"]}
    assert calls[2][1] == {"dev_numbers": ["D1"], "tenants": ["g1"]}
    assert not reg.needs_config_reload("D1", 2)
    assert not reg.needs_config_reload("D1", 1)
    assert reg.needs_config_reload("D1", 3)
