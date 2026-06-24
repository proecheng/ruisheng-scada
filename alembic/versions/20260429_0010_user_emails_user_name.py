"""user_emails: add owning user_name

Revision ID: 0010_user_emails_user_name
Revises: 0009_serial_port_unique
Create Date: 2026-04-29
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0010_user_emails_user_name"
down_revision = "0009_serial_port_unique"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("user_emails", sa.Column("user_name", sa.String(length=50), nullable=True))
    op.create_foreign_key(
        op.f("fk_user_emails_user_name_users"),
        "user_emails",
        "users",
        ["user_name"],
        ["user_name"],
        ondelete="CASCADE",
    )
    op.create_index("idx_user_emails_user_name", "user_emails", ["user_name"], unique=False)
    op.execute(
        """
        WITH unique_phone_owner AS (
            SELECT phone_number, MIN(user_name) AS user_name
            FROM user_phone_numbers
            GROUP BY phone_number
            HAVING COUNT(DISTINCT user_name) = 1
        )
        UPDATE user_emails AS ue
        SET user_name = upo.user_name
        FROM unique_phone_owner AS upo
        WHERE ue.phone_number = upo.phone_number
          AND ue.user_name IS NULL
        """
    )


def downgrade() -> None:
    op.drop_index("idx_user_emails_user_name", table_name="user_emails")
    op.drop_constraint(op.f("fk_user_emails_user_name_users"), "user_emails", type_="foreignkey")
    op.drop_column("user_emails", "user_name")
