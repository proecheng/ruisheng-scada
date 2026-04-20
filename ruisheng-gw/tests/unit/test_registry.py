"""Registry: in-memory device + point map loaded from DB at startup."""

from __future__ import annotations

from ruisheng_gw.domain.device import DeviceState
from ruisheng_gw.domain.registry import Registry


def test_build_from_rows() -> None:
    device_rows = [
        {"dev_number": "DEV-001", "usr_group": "ug_A", "update_interval_decisec": 10},
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
    reg = Registry.build(device_rows=device_rows, point_rows=point_rows)
    e1 = reg.get("DEV-001")
    assert e1 is not None
    assert e1.device.usr_group == "ug_A"
    assert e1.device.state is DeviceState.UNREGISTERED
    assert len(e1.points) == 1


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
