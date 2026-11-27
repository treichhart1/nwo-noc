"""Add native provider metadata to canonical notification records; no second queue."""

from alembic import op
import sqlalchemy as sa

revision = "0029_multi_provider_sms"
down_revision = "0028_customer_status"
branch_labels = None
depends_on = None

ATTEMPT_COLUMNS = [
    ("provider_name", sa.String(20)),
    ("claim_token", sa.String(36)),
    ("provider_message_id", sa.String(100)),
    ("raw_status", sa.String(40)),
    ("provider_error_code", sa.String(40)),
    ("acceptance", sa.String(30)),
    ("lease_expires_at", sa.DateTime(timezone=True)),
    ("estimated_segments", sa.Integer()),
    ("estimated_cost_microunits", sa.BigInteger()),
    ("reported_cost_microunits", sa.BigInteger()),
    ("currency", sa.String(3)),
    ("destination_hash", sa.String(64)),
    ("content_hash", sa.String(64)),
    ("config_revision", sa.Integer()),
    ("updated_at", sa.DateTime(timezone=True)),
]


def upgrade():
    op.create_table(
        "notification_sms_providers",
        sa.Column("name", sa.String(20), primary_key=True),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("live_authorized", sa.Boolean(), nullable=False),
        sa.Column("authorization_reference", sa.String(120)),
        sa.Column("webhook_ciphertext", sa.Text()),
        sa.Column("last_submission_at", sa.DateTime(timezone=True)),
        sa.Column("last_callback_at", sa.DateTime(timezone=True)),
        sa.Column("health", sa.String(40), nullable=False),
    )
    op.create_table(
        "notification_sms_routing",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
    )
    op.create_table(
        "notification_sms_events",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column(
            "provider",
            sa.String(20),
            sa.ForeignKey("notification_sms_providers.name"),
            nullable=False,
        ),
        sa.Column("message_id", sa.String(100), nullable=False),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("raw_status", sa.String(40)),
        sa.Column("provider_error_code", sa.String(40)),
        sa.Column("reported_cost_microunits", sa.BigInteger()),
        sa.Column("body_hash", sa.String(64), nullable=False),
        sa.Column("contact_hash", sa.String(64)),
        sa.Column("ciphertext", sa.Text()),
        sa.Column(
            "delivery_id", sa.Integer(), sa.ForeignKey("notification_deliveries.id")
        ),
        sa.Column("reviewed_by", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
    )
    for col in ("provider", "message_id", "contact_hash", "delivery_id", "received_at"):
        op.create_index(
            "ix_notification_sms_events_" + col, "notification_sms_events", [col]
        )
    op.create_table(
        "notification_sms_callback_budget",
        sa.Column("provider", sa.String(20), primary_key=True),
        sa.Column("minute", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("count", sa.Integer(), nullable=False),
    )
    op.add_column(
        "notification_deliveries",
        sa.Column(
            "sms_provider",
            sa.String(20),
            sa.ForeignKey("notification_sms_providers.name"),
            nullable=True,
        ),
    )
    op.add_column(
        "notification_deliveries",
        sa.Column("sms_authorization", sa.JSON(), nullable=True),
    )
    for name, kind in ATTEMPT_COLUMNS:
        op.add_column(
            "notification_attempts",
            sa.Column(
                name,
                kind,
                *(
                    [sa.ForeignKey("notification_sms_providers.name")]
                    if name == "provider_name"
                    else []
                ),
                nullable=True,
            ),
        )
    op.create_index(
        "ix_sms_attempt_provider_created",
        "notification_attempts",
        ["provider_name", "created_at"],
    )
    op.create_index(
        "ix_sms_claim_expiry",
        "notification_attempts",
        ["acceptance", "lease_expires_at"],
    )
    op.create_unique_constraint(
        "uq_sms_provider_receipt",
        "notification_attempts",
        ["provider_name", "provider_message_id"],
    )
    op.create_unique_constraint(None, "notification_attempts", ["claim_token"])
    for col in ("provider_name", "provider_message_id"):
        op.create_index(
            "ix_notification_attempts_" + col, "notification_attempts", [col]
        )

    op.execute("""
    CREATE FUNCTION public.noc_sms_enforce_opt_out(p_phone text, p_at timestamptz)
    RETURNS void LANGUAGE plpgsql SECURITY DEFINER
    SET search_path = pg_catalog, public AS $$
    BEGIN
      IF p_phone !~ '^\\+[1-9][0-9]{7,14}$' OR p_at IS NULL THEN
        RAISE EXCEPTION 'Invalid SMS opt-out input';
      END IF;
      UPDATE public.customer_accounts SET sms_opt_out=true, opt_out_at=p_at WHERE phone=p_phone;
      INSERT INTO public.notification_suppressions(customer_id,reason,created_at)
        SELECT id,'opt_out',p_at FROM public.customer_accounts WHERE phone=p_phone
        ON CONFLICT (customer_id) DO NOTHING;
      UPDATE public.notification_deliveries SET state='suppressed',last_error='verified_opt_out',completed_at=p_at
        WHERE channel='sms' AND state IN ('queued','retry') AND recipient_id IN
        (SELECT r.id FROM public.notification_recipients r JOIN public.customer_accounts c
          ON c.id=r.customer_id WHERE c.phone=p_phone);
      RETURN;
    END $$;
    REVOKE ALL ON FUNCTION public.noc_sms_enforce_opt_out(text,timestamptz) FROM PUBLIC;
    DO $$ BEGIN
      IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname='noc_app') THEN
        GRANT EXECUTE ON FUNCTION public.noc_sms_enforce_opt_out(text,timestamptz) TO noc_app;
      END IF;
    END $$;
    """)


def downgrade():
    # This migration contains consent/replay and submission history. A schema
    # rollback must never silently erase it. Restore a matching backup instead.
    raise RuntimeError(
        "SMS provider history is retained; use reviewed matching-release backup recovery, not destructive downgrade"
    )
