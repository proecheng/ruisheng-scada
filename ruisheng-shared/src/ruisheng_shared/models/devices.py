"""设备相关 5 张表。对应 spec §4.2。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Double,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, SoftDeleteMixin, TimestampMixin


class Device(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "devices"
    __table_args__ = (
        UniqueConstraint("dev_number", name="dev_number"),  # → uq_devices_dev_number
        UniqueConstraint("dev_ser_number", "iccid", name="ser_iccid"),  # → uq_devices_ser_iccid
        CheckConstraint(
            "update_interval_decisec BETWEEN 10 AND 1000",
            name="poll_interval",  # → ck_devices_poll_interval
        ),
        CheckConstraint(
            "modbus_addr BETWEEN 1 AND 247",
            name="modbus_addr",  # → ck_devices_modbus_addr
        ),
        CheckConstraint(
            "baud_rate IN (9600, 19200, 38400, 57600, 115200)",
            name="baud_rate",  # → ck_devices_baud_rate
        ),
        CheckConstraint(
            "transport_type IN ('tcp', 'serial')",
            name="transport_type",  # → ck_devices_transport_type
        ),
        CheckConstraint(
            "(transport_type = 'serial' AND serial_port IS NOT NULL)"
            " OR (transport_type = 'tcp' AND serial_port IS NULL)",
            name="serial_port_consistency",  # → ck_devices_serial_port_consistency
        ),
        Index("idx_devices_tenant", "usr_group"),
        Index("idx_devices_admin", "administrators"),
        Index(
            "idx_devices_online",
            "is_online",
            postgresql_where="deleted_at IS NULL",
        ),
        Index(
            "uq_devices_serial_port_modbus_addr",
            "serial_port",
            "modbus_addr",
            unique=True,
            postgresql_where=text("transport_type = 'serial'"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    dev_number: Mapped[str] = mapped_column(String(50), nullable=False)
    dev_ser_number: Mapped[str] = mapped_column(String(50), nullable=False)
    iccid: Mapped[str | None] = mapped_column(String(50))
    dev_name: Mapped[str | None] = mapped_column(String(100))
    dev_type: Mapped[str | None] = mapped_column(String(50))
    transport_type: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        default="tcp",
        server_default="tcp",
    )
    serial_port: Mapped[str | None] = mapped_column(String(50))
    modbus_addr: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    baud_rate: Mapped[int | None] = mapped_column(Integer)
    group_company: Mapped[str | None] = mapped_column(String(100))
    company: Mapped[str | None] = mapped_column(String(100))
    department: Mapped[str | None] = mapped_column(String(100))
    administrators: Mapped[str | None] = mapped_column(String(50), ForeignKey("users.user_name"))
    dev_ip: Mapped[str | None] = mapped_column(INET)
    code_file: Mapped[str | None] = mapped_column(String(255))
    code_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    update_interval_decisec: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    last_call_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_back_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    loss_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_online: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_state: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    update_flag: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    usr_group: Mapped[str] = mapped_column(
        String(50), ForeignKey("wx_groups.usr_group"), nullable=False
    )


class DevicePoint(Base, TimestampMixin):
    __tablename__ = "device_points"
    __table_args__ = (
        CheckConstraint(
            "point_number BETWEEN 0 AND 65535", name="point_number"
        ),  # → ck_device_points_point_number
        CheckConstraint("fun_code IN (1,2,3,4)", name="fun_code"),  # → ck_device_points_fun_code
        Index("idx_points_dev", "dev_number"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    dev_number: Mapped[str] = mapped_column(
        String(50), ForeignKey("devices.dev_number", ondelete="CASCADE"), nullable=False
    )
    point_name: Mapped[str] = mapped_column(String(100), nullable=False)
    user_point_name: Mapped[str | None] = mapped_column(String(100))
    point_number: Mapped[int] = mapped_column(Integer, nullable=False)
    fun_code: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    dev_addr: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    r_bit: Mapped[int | None] = mapped_column(SmallInteger)
    value_type: Mapped[str] = mapped_column(String(20), nullable=False)
    point_unit: Mapped[str | None] = mapped_column(String(20))
    point_ratio: Mapped[float] = mapped_column(Double, default=1.0)
    point_offset: Mapped[float] = mapped_column(Double, default=0.0)
    user_ratio: Mapped[float] = mapped_column(Double, default=1.0)
    user_point_offset: Mapped[float] = mapped_column(Double, default=0.0)
    min_value: Mapped[float | None] = mapped_column(Double)
    max_value: Mapped[float | None] = mapped_column(Double)
    show: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)


class DeviceStaticData(Base, TimestampMixin):
    __tablename__ = "device_static_data"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    dev_number: Mapped[str] = mapped_column(
        String(50), ForeignKey("devices.dev_number", ondelete="CASCADE"), nullable=False
    )
    base_msg_name: Mapped[str] = mapped_column(String(100), nullable=False)
    base_msg_value: Mapped[str | None] = mapped_column(String(255))


class SimCard(Base, TimestampMixin):
    __tablename__ = "sim_cards"

    iccid: Mapped[str] = mapped_column(String(50), primary_key=True)
    msisdn: Mapped[str | None] = mapped_column(String(20))
    card_type: Mapped[str | None] = mapped_column(String(50))
    card_status: Mapped[int] = mapped_column(SmallInteger, default=0)
    service_months: Mapped[int | None] = mapped_column(Integer)
    data_amount: Mapped[float | None] = mapped_column(Double)
    total_data_amount: Mapped[float | None] = mapped_column(Double)
    open_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    active_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cost: Mapped[float | None] = mapped_column(Double)
    month_data: Mapped[float | None] = mapped_column(Double)
    remark: Mapped[str | None] = mapped_column(String(255))
    usr_remark: Mapped[str | None] = mapped_column(String(255))


class DeviceTemplate(Base, TimestampMixin):
    """设备模板（Q-B10 保留占位）。"""

    __tablename__ = "device_templates"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    dev_type: Mapped[str | None] = mapped_column(String(50))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
