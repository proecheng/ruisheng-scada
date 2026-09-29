"""Add serial read profiles and allow soft-deleted address reuse.

Revision ID: 0013_serial_polling_profile
Revises: 0012_alarm_notification_runtime
Create Date: 2026-09-07
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0013_serial_polling_profile"
down_revision = "0012_alarm_notification_runtime"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "devices",
        sa.Column("read_profile", sa.String(20), nullable=False, server_default="point_groups"),
    )
    op.create_check_constraint(
        "read_profile", "devices", "read_profile IN ('point_groups', 'zero_origin_38')"
    )
    op.create_check_constraint(
        "read_profile_transport",
        "devices",
        "read_profile != 'zero_origin_38' OR transport_type = 'serial'",
    )
    op.drop_index("uq_devices_serial_port_modbus_addr", table_name="devices")
    op.create_index(
        "uq_devices_serial_port_modbus_addr",
        "devices",
        ["serial_port", "modbus_addr"],
        unique=True,
        postgresql_where=sa.text("transport_type = 'serial' AND deleted_at IS NULL"),
    )


def downgrade() -> None:
    # Refuse unsafe downgrades before changing schema; never remove retained history.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM devices WHERE transport_type = 'serial'
                GROUP BY serial_port, modbus_addr HAVING COUNT(*) > 1
            ) THEN
                RAISE EXCEPTION 'Cannot downgrade: reused serial addresses conflict with the old index';
            END IF;
        END $$;
        """
    )
    op.drop_index("uq_devices_serial_port_modbus_addr", table_name="devices")
    op.create_index(
        "uq_devices_serial_port_modbus_addr",
        "devices",
        ["serial_port", "modbus_addr"],
        unique=True,
        postgresql_where=sa.text("transport_type = 'serial'"),
    )
    op.drop_constraint("read_profile_transport", "devices", type_="check")
    op.drop_constraint("read_profile", "devices", type_="check")
    op.drop_column("devices", "read_profile")
