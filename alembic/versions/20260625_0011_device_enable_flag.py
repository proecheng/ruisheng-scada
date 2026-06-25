"""devices: add business enable flag

Revision ID: 0011_device_enable_flag
Revises: 0010_user_emails_user_name
Create Date: 2026-06-25
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0011_device_enable_flag"
down_revision = "0010_user_emails_user_name"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "devices",
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.create_index("idx_devices_enabled", "devices", ["is_enabled"], unique=False)


def downgrade() -> None:
    op.drop_index("idx_devices_enabled", table_name="devices")
    op.drop_column("devices", "is_enabled")
