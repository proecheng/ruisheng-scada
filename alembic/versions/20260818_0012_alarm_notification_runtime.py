"""alarm notification runtime tables and immutable alarm identity

Revision ID: 0012_alarm_notification_runtime
Revises: 0011_device_enable_flag
Create Date: 2026-08-18
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0012_alarm_notification_runtime"
down_revision = "0011_device_enable_flag"
branch_labels = None
depends_on = None

TENANT_TABLES = (
    "alarm_notification_subscriptions",
    "notification_dispatches",
    "notification_deliveries",
    "notification_delivery_attempts",
)


def _enable_rls(table: str) -> None:
    op.execute(sa.text(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY'))
    op.execute(sa.text(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY'))
    op.execute(
        sa.text(
            f'CREATE POLICY tenant_isolation ON "{table}" '
            "USING (usr_group = current_setting('app.tenant_id', true) "
            "OR current_setting('app.role', true) = 'Administrators') "
            "WITH CHECK (usr_group = current_setting('app.tenant_id', true) "
            "OR current_setting('app.role', true) = 'Administrators')"
        )
    )


def upgrade() -> None:
    op.execute("ALTER TABLE user_wx_bindings ADD COLUMN contact_id BIGSERIAL")
    op.create_unique_constraint(
        "uq_user_wx_bindings_contact_id", "user_wx_bindings", ["contact_id"]
    )
    op.add_column("alarm_records", sa.Column("alarm_cfg_id", sa.BigInteger()))
    op.add_column("alarm_records", sa.Column("alarm_type", sa.String(length=4)))
    op.add_column("alarm_records", sa.Column("limit_value", sa.Double()))
    op.create_index(
        "ix_alarm_records_cfg_triggered",
        "alarm_records",
        ["alarm_cfg_id", sa.text("triggered_at DESC")],
        unique=False,
    )
    op.execute(
        """
        CREATE FUNCTION protect_alarm_record_snapshot()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY INVOKER
        SET search_path = public, pg_temp
        AS $$
        BEGIN
          IF TG_OP = 'INSERT' THEN
            IF NEW.alarm_cfg_id IS NULL OR NEW.point_id IS NULL
               OR NEW.alarm_value IS NULL OR NEW.alarm_type IS NULL
               OR NEW.limit_value IS NULL THEN
              RAISE EXCEPTION 'alarm record runtime identity is incomplete'
                USING ERRCODE = '23502';
            END IF;
            RETURN NEW;
          END IF;
          IF NEW.alarm_cfg_id IS DISTINCT FROM OLD.alarm_cfg_id
             OR NEW.dev_number IS DISTINCT FROM OLD.dev_number
             OR NEW.point_id IS DISTINCT FROM OLD.point_id
             OR NEW.alarm_name IS DISTINCT FROM OLD.alarm_name
             OR NEW.alarm_msg IS DISTINCT FROM OLD.alarm_msg
             OR NEW.alarm_value IS DISTINCT FROM OLD.alarm_value
             OR NEW.alarm_type IS DISTINCT FROM OLD.alarm_type
             OR NEW.limit_value IS DISTINCT FROM OLD.limit_value
             OR NEW.triggered_at IS DISTINCT FROM OLD.triggered_at
             OR NEW.usr_group IS DISTINCT FROM OLD.usr_group THEN
            RAISE EXCEPTION 'alarm record snapshot is immutable'
              USING ERRCODE = '23514';
          END IF;
          RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(
        "CREATE TRIGGER trg_alarm_records_protect_snapshot "
        "BEFORE INSERT OR UPDATE ON alarm_records FOR EACH ROW "
        "EXECUTE FUNCTION protect_alarm_record_snapshot()"
    )

    op.create_table(
        "alarm_notification_subscriptions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("alarm_cfg_id", sa.BigInteger(), nullable=False),
        sa.Column("user_name", sa.String(length=50), nullable=False),
        sa.Column("channel", sa.String(length=30), nullable=False),
        sa.Column("usr_group", sa.String(length=50), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "channel IN ('wechat','email','sms_custom_http','voice_custom_http')",
            name=op.f("ck_alarm_notification_subscriptions_channel"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_alarm_notification_subscriptions")),
    )
    op.create_index(
        "uq_alarm_notification_subscriptions_active_target",
        "alarm_notification_subscriptions",
        ["alarm_cfg_id", "user_name", "channel"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "ix_alarm_notification_subscriptions_tenant_cfg",
        "alarm_notification_subscriptions",
        ["usr_group", "alarm_cfg_id"],
    )

    op.create_table(
        "notification_dispatches",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("alarm_id", sa.BigInteger(), nullable=False),
        sa.Column("alarm_triggered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("alarm_cfg_id", sa.BigInteger(), nullable=False),
        sa.Column("usr_group", sa.String(length=50), nullable=False),
        sa.Column("trace_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('materialized','no_subscription')",
            name=op.f("ck_notification_dispatches_status"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notification_dispatches")),
        sa.UniqueConstraint(
            "alarm_id",
            "alarm_triggered_at",
            "alarm_cfg_id",
            name=op.f("uq_notification_dispatches_alarm_identity"),
        ),
    )
    op.create_index(
        "ix_notification_dispatches_tenant_created",
        "notification_dispatches",
        ["usr_group", "created_at"],
    )

    op.create_table(
        "notification_deliveries",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("dispatch_id", sa.BigInteger(), nullable=False),
        sa.Column("usr_group", sa.String(length=50), nullable=False),
        sa.Column("user_name", sa.String(length=50), nullable=False),
        sa.Column("channel", sa.String(length=30), nullable=False),
        sa.Column("contact_ref", sa.String(length=64), nullable=False),
        sa.Column("contact_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("leased_until", sa.DateTime(timezone=True)),
        sa.Column("lease_owner", sa.String(length=100)),
        sa.Column("lease_version", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("last_error_class", sa.String(length=40)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "attempt_count >= 0", name=op.f("ck_notification_deliveries_attempt_count")
        ),
        sa.CheckConstraint(
            "channel IN ('wechat','email','sms_custom_http','voice_custom_http')",
            name=op.f("ck_notification_deliveries_channel"),
        ),
        sa.CheckConstraint(
            "lease_version >= 0", name=op.f("ck_notification_deliveries_lease_version")
        ),
        sa.CheckConstraint(
            "status IN ('pending','retry','leased','sent','failed','skipped')",
            name=op.f("ck_notification_deliveries_status"),
        ),
        sa.ForeignKeyConstraint(
            ["dispatch_id"], ["notification_dispatches.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notification_deliveries")),
        sa.UniqueConstraint(
            "dispatch_id",
            "user_name",
            "channel",
            "contact_ref",
            name=op.f("uq_notification_deliveries_logical_target"),
        ),
    )
    op.create_index(
        "ix_notification_deliveries_ready",
        "notification_deliveries",
        ["next_attempt_at", "leased_until"],
        postgresql_where=sa.text("status IN ('pending','retry','leased')"),
    )
    op.create_index(
        "ix_notification_deliveries_tenant_created",
        "notification_deliveries",
        ["usr_group", "created_at"],
    )

    op.create_table(
        "notification_delivery_attempts",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("delivery_id", sa.BigInteger(), nullable=False),
        sa.Column("usr_group", sa.String(length=50), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("worker_id", sa.String(length=100), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("error_class", sa.String(length=40)),
        sa.Column("http_status", sa.Integer()),
        sa.Column("retry_after_sec", sa.Integer()),
        sa.Column("detail", sa.String(length=200)),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "finished_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "outcome IN ('sent','retry','failed','skipped','stale')",
            name=op.f("ck_notification_delivery_attempts_outcome"),
        ),
        sa.ForeignKeyConstraint(
            ["delivery_id"], ["notification_deliveries.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notification_delivery_attempts")),
        sa.UniqueConstraint(
            "delivery_id",
            "attempt_no",
            name=op.f("uq_notification_delivery_attempts_delivery_attempt"),
        ),
    )
    op.create_index(
        "ix_notification_delivery_attempts_tenant_finished",
        "notification_delivery_attempts",
        ["usr_group", "finished_at"],
    )

    op.execute(
        """
        CREATE FUNCTION protect_notification_audit_delete()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY INVOKER
        SET search_path = public, pg_temp
        AS $$
        BEGIN
          IF current_setting('app.audit_cleanup', true) <> 'on'
             OR current_setting('app.role', true) <> 'Administrators' THEN
            RAISE EXCEPTION 'notification audit rows are append-only'
              USING ERRCODE = '42501';
          END IF;
          RETURN OLD;
        END;
        $$;
        """
    )
    for table in (
        "notification_dispatches",
        "notification_deliveries",
        "notification_delivery_attempts",
    ):
        op.execute(
            f"CREATE TRIGGER trg_{table}_protect_delete BEFORE DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION protect_notification_audit_delete()"
        )

    op.execute(
        """
        CREATE FUNCTION protect_notification_audit_update()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY INVOKER
        SET search_path = public, pg_temp
        AS $$
        BEGIN
          RAISE EXCEPTION 'notification audit rows are immutable'
            USING ERRCODE = '42501';
        END;
        $$;
        """
    )
    for table in ("notification_dispatches", "notification_delivery_attempts"):
        op.execute(
            f"CREATE TRIGGER trg_{table}_protect_update BEFORE UPDATE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION protect_notification_audit_update()"
        )

    op.execute(
        """
        CREATE FUNCTION enforce_notification_tenant_consistency()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY INVOKER
        SET search_path = public, pg_temp
        AS $$
        DECLARE
          expected_tenant varchar(50);
        BEGIN
          IF TG_TABLE_NAME = 'alarm_notification_subscriptions' THEN
            SELECT d.usr_group INTO expected_tenant
              FROM device_waring_cfgs c
              JOIN devices d ON d.dev_number = c.dev_number
             WHERE c.id = NEW.alarm_cfg_id AND d.deleted_at IS NULL;
            IF expected_tenant IS NULL OR expected_tenant <> NEW.usr_group THEN
              RAISE EXCEPTION 'notification_tenant_violation: alarm config tenant mismatch'
                USING ERRCODE = '23514';
            END IF;
            PERFORM 1 FROM users u
             WHERE u.user_name = NEW.user_name
               AND u.usr_group = NEW.usr_group
               AND u.deleted_at IS NULL;
            IF NOT FOUND THEN
              RAISE EXCEPTION 'notification_tenant_violation: recipient tenant mismatch'
                USING ERRCODE = '23514';
            END IF;
          ELSIF TG_TABLE_NAME = 'notification_dispatches' THEN
            SELECT a.usr_group INTO expected_tenant
              FROM alarm_records a
             WHERE a.id = NEW.alarm_id
               AND a.triggered_at = NEW.alarm_triggered_at
               AND a.alarm_cfg_id = NEW.alarm_cfg_id;
            IF expected_tenant IS NULL OR expected_tenant <> NEW.usr_group THEN
              RAISE EXCEPTION 'notification_tenant_violation: alarm identity mismatch'
                USING ERRCODE = '23514';
            END IF;
          ELSIF TG_TABLE_NAME = 'notification_deliveries' THEN
            SELECT p.usr_group INTO expected_tenant
              FROM notification_dispatches p WHERE p.id = NEW.dispatch_id;
            IF expected_tenant IS NULL OR expected_tenant <> NEW.usr_group THEN
              RAISE EXCEPTION 'notification_tenant_violation: dispatch tenant mismatch'
                USING ERRCODE = '23514';
            END IF;
          ELSIF TG_TABLE_NAME = 'notification_delivery_attempts' THEN
            SELECT d.usr_group INTO expected_tenant
              FROM notification_deliveries d WHERE d.id = NEW.delivery_id;
            IF expected_tenant IS NULL OR expected_tenant <> NEW.usr_group THEN
              RAISE EXCEPTION 'notification_tenant_violation: delivery tenant mismatch'
                USING ERRCODE = '23514';
            END IF;
          END IF;
          RETURN NEW;
        END;
        $$;

        """
    )
    op.execute(
        "CREATE TRIGGER trg_alarm_notification_subscriptions_enforce_tenant "
        "BEFORE INSERT OR UPDATE OF alarm_cfg_id, user_name, usr_group "
        "ON alarm_notification_subscriptions FOR EACH ROW "
        "EXECUTE FUNCTION enforce_notification_tenant_consistency()"
    )
    op.execute(
        "CREATE TRIGGER trg_notification_dispatches_enforce_tenant "
        "BEFORE INSERT OR UPDATE OF alarm_id, alarm_triggered_at, alarm_cfg_id, usr_group "
        "ON notification_dispatches FOR EACH ROW "
        "EXECUTE FUNCTION enforce_notification_tenant_consistency()"
    )
    op.execute(
        "CREATE TRIGGER trg_notification_deliveries_enforce_tenant "
        "BEFORE INSERT OR UPDATE OF dispatch_id, usr_group ON notification_deliveries "
        "FOR EACH ROW EXECUTE FUNCTION enforce_notification_tenant_consistency()"
    )
    op.execute(
        "CREATE TRIGGER trg_notification_delivery_attempts_enforce_tenant "
        "BEFORE INSERT OR UPDATE OF delivery_id, usr_group "
        "ON notification_delivery_attempts FOR EACH ROW "
        "EXECUTE FUNCTION enforce_notification_tenant_consistency()"
    )

    for table in TENANT_TABLES:
        _enable_rls(table)

    op.execute(
        """
        CREATE FUNCTION cleanup_notification_audit_rows()
        RETURNS bigint
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = public, pg_temp
        AS $$
        DECLARE
          removed bigint;
        BEGIN
          PERFORM set_config('app.role', 'Administrators', true);
          PERFORM set_config('app.audit_cleanup', 'on', true);
          DELETE FROM notification_dispatches p
           WHERE p.created_at < clock_timestamp() - INTERVAL '180 days'
             AND NOT EXISTS (
               SELECT 1 FROM notification_deliveries d
                WHERE d.dispatch_id = p.id
                  AND (d.status IN ('pending','retry','leased')
                       OR d.updated_at >= clock_timestamp() - INTERVAL '180 days')
             )
             AND NOT EXISTS (
               SELECT 1 FROM notification_delivery_attempts a
               JOIN notification_deliveries d ON d.id = a.delivery_id
                WHERE d.dispatch_id = p.id
                  AND a.finished_at >= clock_timestamp() - INTERVAL '180 days'
             );
          GET DIAGNOSTICS removed = ROW_COUNT;
          RETURN removed;
        END;
        $$;
        """
    )
    op.execute("REVOKE ALL ON FUNCTION cleanup_notification_audit_rows() FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION cleanup_notification_audit_rows() TO ruisheng_api")

    op.execute(
        "CREATE TRIGGER trg_alarm_notification_subscriptions_updated "
        "BEFORE UPDATE ON alarm_notification_subscriptions "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
    )
    op.execute(
        "CREATE TRIGGER trg_notification_deliveries_updated "
        "BEFORE UPDATE ON notification_deliveries "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
    )

    op.execute("GRANT SELECT, INSERT, UPDATE ON alarm_notification_subscriptions TO ruisheng_api")
    op.execute("REVOKE DELETE ON alarm_notification_subscriptions FROM ruisheng_api")
    op.execute("GRANT SELECT, INSERT ON notification_dispatches TO ruisheng_api")
    op.execute("GRANT SELECT, INSERT, UPDATE ON notification_deliveries TO ruisheng_api")
    op.execute("GRANT SELECT, INSERT ON notification_delivery_attempts TO ruisheng_api")
    op.execute(
        "REVOKE UPDATE ON notification_dispatches, notification_delivery_attempts FROM ruisheng_api"
    )
    op.execute(
        "REVOKE DELETE ON notification_dispatches, notification_deliveries, "
        "notification_delivery_attempts FROM ruisheng_api"
    )
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO ruisheng_api")
    op.execute(
        "REVOKE ALL ON alarm_notification_subscriptions, notification_dispatches, "
        "notification_deliveries, notification_delivery_attempts, user_wx_bindings, "
        "user_phone_numbers, user_emails, wx_groups FROM ruisheng_gw"
    )
    op.execute(
        "REVOKE ALL ON SEQUENCE alarm_notification_subscriptions_id_seq, "
        "notification_dispatches_id_seq, notification_deliveries_id_seq, "
        "notification_delivery_attempts_id_seq, user_wx_bindings_contact_id_seq "
        "FROM ruisheng_gw"
    )


def downgrade() -> None:
    op.execute(
        "GRANT SELECT, INSERT, UPDATE ON user_wx_bindings, user_phone_numbers, "
        "user_emails, wx_groups TO ruisheng_gw"
    )
    op.execute("DROP FUNCTION IF EXISTS cleanup_notification_audit_rows()")
    for table in ("notification_dispatches", "notification_delivery_attempts"):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_protect_update ON {table}")
    op.execute("DROP FUNCTION IF EXISTS protect_notification_audit_update()")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_notification_deliveries_updated ON notification_deliveries"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_alarm_notification_subscriptions_updated "
        "ON alarm_notification_subscriptions"
    )
    for table in (
        "notification_delivery_attempts",
        "notification_deliveries",
        "notification_dispatches",
    ):
        op.execute(sa.text(f'DROP TRIGGER IF EXISTS trg_{table}_protect_delete ON "{table}"'))
    op.execute("DROP FUNCTION IF EXISTS protect_notification_audit_delete()")
    for table in reversed(TENANT_TABLES):
        op.execute(sa.text(f'DROP TRIGGER IF EXISTS trg_{table}_enforce_tenant ON "{table}"'))
    op.execute("DROP FUNCTION IF EXISTS enforce_notification_tenant_consistency()")
    for table in reversed(TENANT_TABLES):
        op.execute(sa.text(f'DROP POLICY IF EXISTS tenant_isolation ON "{table}"'))
    op.drop_index(
        "ix_notification_delivery_attempts_tenant_finished",
        table_name="notification_delivery_attempts",
    )
    op.drop_table("notification_delivery_attempts")
    op.drop_index("ix_notification_deliveries_tenant_created", table_name="notification_deliveries")
    op.drop_index("ix_notification_deliveries_ready", table_name="notification_deliveries")
    op.drop_table("notification_deliveries")
    op.drop_index("ix_notification_dispatches_tenant_created", table_name="notification_dispatches")
    op.drop_table("notification_dispatches")
    op.drop_index(
        "ix_alarm_notification_subscriptions_tenant_cfg",
        table_name="alarm_notification_subscriptions",
    )
    op.drop_index(
        "uq_alarm_notification_subscriptions_active_target",
        table_name="alarm_notification_subscriptions",
    )
    op.drop_table("alarm_notification_subscriptions")
    op.drop_index("ix_alarm_records_cfg_triggered", table_name="alarm_records")
    op.execute("DROP TRIGGER IF EXISTS trg_alarm_records_protect_snapshot ON alarm_records")
    op.execute("DROP FUNCTION IF EXISTS protect_alarm_record_snapshot()")
    op.drop_column("alarm_records", "limit_value")
    op.drop_column("alarm_records", "alarm_type")
    op.drop_column("alarm_records", "alarm_cfg_id")
    op.drop_constraint("uq_user_wx_bindings_contact_id", "user_wx_bindings", type_="unique")
    op.drop_column("user_wx_bindings", "contact_id")
