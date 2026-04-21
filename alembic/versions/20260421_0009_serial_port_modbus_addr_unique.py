"""devices: add partial unique index on (serial_port, modbus_addr) for serial transport

Revision ID: 0009_serial_port_unique
Revises: 0008_transport_serial
Create Date: 2026-04-21

仅对 transport_type = 'serial' 的设备生效。
TCP 设备 serial_port IS NULL，不受此约束影响。
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0009_serial_port_unique"
down_revision = "0008_transport_serial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "uq_devices_serial_port_modbus_addr",
        "devices",
        ["serial_port", "modbus_addr"],
        unique=True,
        postgresql_where=sa.text("transport_type = 'serial'"),
    )


def downgrade() -> None:
    op.drop_index("uq_devices_serial_port_modbus_addr", table_name="devices")
