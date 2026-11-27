"""Add native Slack metadata without a second notification outbox."""

from alembic import op
import sqlalchemy as sa

revision = "0030_native_slack"
down_revision = "0029_multi_provider_sms"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "notification_slack_integration",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("live_authorized", sa.Boolean(), nullable=False),
        sa.Column("authorization_reference", sa.String(120)),
        sa.Column("signing_ciphertext", sa.Text()),
        sa.Column("health", sa.String(40), nullable=False),
        sa.Column("last_submission_at", sa.DateTime(timezone=True)),
        sa.Column("last_callback_at", sa.DateTime(timezone=True)),
        sa.Column("backoff_until", sa.DateTime(timezone=True)),
    )
    for name in ["slack_authorization", "slack_context"]:
        op.add_column(
            "notification_deliveries", sa.Column(name, sa.JSON(), nullable=True)
        )
    for name, typ in [
        ("slack_workspace_id", sa.String(40)),
        ("slack_mode", sa.String(20)),
        ("slack_channel_id", sa.String(40)),
        ("slack_ts", sa.String(40)),
        ("slack_thread_ts", sa.String(40)),
        ("slack_revision", sa.Integer()),
    ]:
        op.add_column("notification_attempts", sa.Column(name, typ, nullable=True))
    op.create_unique_constraint(
        "uq_slack_receipt",
        "notification_attempts",
        ["slack_workspace_id", "slack_channel_id", "slack_ts"],
    )
    op.create_index(
        "ix_slack_attempt_created",
        "notification_attempts",
        ["slack_mode", "created_at"],
    )
    op.create_table(
        "notification_slack_callback_budget",
        sa.Column("minute", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("count", sa.Integer(), nullable=False),
    )
    op.create_index(
        "ix_notification_attempts_slack_mode", "notification_attempts", ["slack_mode"]
    )
    op.create_table(
        "notification_slack_threads",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("workspace", sa.String(40), nullable=False),
        sa.Column("channel", sa.String(40), nullable=False),
        sa.Column("topic", sa.String(80), nullable=False),
        sa.Column("timestamp", sa.String(40)),
        sa.Column(
            "pending_delivery_id",
            sa.Integer(),
            sa.ForeignKey("notification_deliveries.id"),
        ),
    )
    op.create_index(
        "ix_notification_slack_threads_channel",
        "notification_slack_threads",
        ["channel"],
    )
    op.create_table(
        "notification_slack_users",
        sa.Column("slack_id", sa.String(40), primary_key=True),
        sa.Column("workspace", sa.String(40), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("channels", sa.JSON(), nullable=False),
    )
    op.create_table(
        "notification_slack_interactions",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column(
            "delivery_id",
            sa.Integer(),
            sa.ForeignKey("notification_deliveries.id"),
            nullable=False,
        ),
        sa.Column(
            "slack_user_id",
            sa.String(40),
            sa.ForeignKey("notification_slack_users.slack_id"),
            nullable=False,
        ),
        sa.Column("workspace", sa.String(40), nullable=False),
        sa.Column("channel", sa.String(40), nullable=False),
        sa.Column("message_ts", sa.String(40), nullable=False),
        sa.Column("action", sa.String(30), nullable=False),
        sa.Column("body_hash", sa.String(64), nullable=False),
        sa.Column("state", sa.String(30), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_slack_interaction_pending",
        "notification_slack_interactions",
        ["state", "received_at"],
    )
    op.create_table(
        "notification_slack_sources",
        sa.Column(
            "plan_id",
            sa.Integer(),
            sa.ForeignKey("notification_plans.id"),
            primary_key=True,
        ),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("identifier", sa.String(80), nullable=False),
        sa.Column("site_id", sa.Integer(), sa.ForeignKey("sites.id")),
        sa.Column("incident_id", sa.Integer(), sa.ForeignKey("incidents.id")),
        sa.Column("work_order_id", sa.Integer(), sa.ForeignKey("field_work_orders.id")),
    )
    op.create_index(
        "ix_notification_slack_sources_kind", "notification_slack_sources", ["kind"]
    )
    op.execute("""
CREATE FUNCTION public.noc_slack_interaction_allowed(p_workspace text,p_user text,p_channel text,p_delivery integer)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 SELECT EXISTS(SELECT 1 FROM public.notification_slack_users m
 JOIN public.users u ON u.id=m.user_id
 JOIN public.notification_deliveries d ON d.id=p_delivery
 JOIN public.notification_slack_sources s ON s.plan_id=d.plan_id
 LEFT JOIN public.incidents i ON i.id=s.incident_id
 LEFT JOIN public.field_work_orders w ON w.id=s.work_order_id
 WHERE m.workspace=p_workspace AND m.slack_id=p_user AND m.active AND u.is_active
 AND d.channel='slack' AND d.recipient_key=p_channel AND d.slack_authorization IS NOT NULL
 AND m.channels::jsonb ? p_channel AND s.incident_id IS NOT NULL
 AND (u.role IN ('admin','operator') OR (u.role='technician' AND
 (i.assigned_to_user_id=u.id OR w.technician_id=u.id OR EXISTS(
 SELECT 1 FROM public.field_site_grants g WHERE g.technician_id=u.id AND g.site_id=COALESCE(i.site_id,w.site_id,s.site_id) AND g.active)))))
$$;
REVOKE ALL ON FUNCTION public.noc_slack_interaction_allowed(text,text,text,integer) FROM PUBLIC;
DO $$ BEGIN IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='noc_app') THEN
 GRANT EXECUTE ON FUNCTION public.noc_slack_interaction_allowed(text,text,text,integer) TO noc_app;
END IF; END $$;
""")


def downgrade():
    raise RuntimeError(
        "Retain Slack acceptance, replay and audit history; use reviewed matching-backup recovery"
    )
