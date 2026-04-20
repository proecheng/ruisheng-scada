"""devices: add transport_type + serial_port columns

Revision ID: 0008_transport_serial
Revises: 959079e6cae9
Create Date: 2026-04-20
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0008_transport_serial"
down_revision = "959079e6cae9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "devices",
        sa.Column(
            "transport_type",
            sa.String(length=10),
            nullable=False,
            server_default="tcp",
        ),
    )
    op.create_check_constraint(
        "ck_devices_transport_type",
        "devices",
        "transport_type IN ('tcp', 'serial')",
    )
    op.add_column(
        "devices",
        sa.Column("serial_port", sa.String(length=50), nullable=True),
    )
    op.create_check_constraint(
        "ck_devices_serial_port_consistency",
        "devices",
        "(transport_type = 'serial' AND serial_port IS NOT NULL)"
        " OR (transport_type = 'tcp' AND serial_port IS NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_devices_serial_port_consistency", "devices", type_="check")
    op.drop_constraint("ck_devices_transport_type", "devices", type_="check")
    op.drop_column("devices", "serial_port")
    op.drop_column("devices", "transport_type")
