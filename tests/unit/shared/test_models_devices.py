"""Spec §4.2 devices / device_points / device_static_data / sim_cards / device_templates"""

from ruisheng_shared.models.devices import (
    Device,
    DevicePoint,
    DeviceStaticData,
    DeviceTemplate,
    SimCard,
)


def test_devices_tablename() -> None:
    assert Device.__tablename__ == "devices"


def test_devices_columns() -> None:
    cols = {c.name for c in Device.__table__.columns}
    assert cols >= {
        "id",
        "dev_number",
        "dev_ser_number",
        "iccid",
        "dev_name",
        "dev_type",
        "modbus_addr",
        "baud_rate",
        "group_company",
        "company",
        "department",
        "administrators",
        "dev_ip",
        "code_file",
        "code_updated_at",
        "update_interval_decisec",
        "last_call_at",
        "last_back_at",
        "loss_count",
        "is_online",
        "is_enabled",
        "last_state",
        "update_flag",
        "usr_group",
        "created_at",
        "updated_at",
        "deleted_at",
    }


def test_devices_primary_key() -> None:
    pk = [c.name for c in Device.__table__.primary_key.columns]
    assert pk == ["id"]


def test_devices_constraints() -> None:
    names = {c.name for c in Device.__table__.constraints if c.name}
    assert "ck_devices_poll_interval" in names
    assert "ck_devices_modbus_addr" in names
    assert "ck_devices_baud_rate" in names
    assert "uq_devices_ser_iccid" in names
    assert "uq_devices_dev_number" in names


def test_devices_indexes() -> None:
    idx_names = {ix.name for ix in Device.__table__.indexes}
    assert "idx_devices_tenant" in idx_names
    assert "idx_devices_admin" in idx_names
    assert "idx_devices_online" in idx_names


def test_points_tablename() -> None:
    assert DevicePoint.__tablename__ == "device_points"


def test_points_columns() -> None:
    cols = {c.name for c in DevicePoint.__table__.columns}
    assert cols >= {
        "id",
        "dev_number",
        "point_name",
        "user_point_name",
        "point_number",
        "fun_code",
        "dev_addr",
        "r_bit",
        "value_type",
        "point_unit",
        "point_ratio",
        "point_offset",
        "user_ratio",
        "user_point_offset",
        "min_value",
        "max_value",
        "show",
        "created_at",
        "updated_at",
    }


def test_points_constraints() -> None:
    names = {c.name for c in DevicePoint.__table__.constraints if c.name}
    assert "ck_device_points_point_number" in names
    assert "ck_device_points_fun_code" in names


def test_points_index() -> None:
    idx_names = {ix.name for ix in DevicePoint.__table__.indexes}
    assert "idx_points_dev" in idx_names


def test_static_tablename() -> None:
    assert DeviceStaticData.__tablename__ == "device_static_data"


def test_static_columns() -> None:
    cols = {c.name for c in DeviceStaticData.__table__.columns}
    assert cols >= {
        "id",
        "dev_number",
        "base_msg_name",
        "base_msg_value",
        "created_at",
        "updated_at",
    }


def test_sim_tablename() -> None:
    assert SimCard.__tablename__ == "sim_cards"


def test_sim_primary_key() -> None:
    pk = [c.name for c in SimCard.__table__.primary_key.columns]
    assert pk == ["iccid"]


def test_sim_columns() -> None:
    cols = {c.name for c in SimCard.__table__.columns}
    assert cols >= {
        "iccid",
        "msisdn",
        "card_type",
        "card_status",
        "service_months",
        "data_amount",
        "total_data_amount",
        "open_date",
        "active_date",
        "cost",
        "month_data",
        "remark",
        "usr_remark",
    }


def test_template_tablename() -> None:
    assert DeviceTemplate.__tablename__ == "device_templates"


def test_template_columns() -> None:
    cols = {c.name for c in DeviceTemplate.__table__.columns}
    assert cols >= {"id", "name", "dev_type", "payload", "created_at", "updated_at"}
