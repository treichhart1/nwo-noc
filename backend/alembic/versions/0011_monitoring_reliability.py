"""Durable monitoring jobs, fenced attempts, receipts, alerts and worker health."""

from alembic import op
import sqlalchemy as s

revision = "0011_monitoring_reliability"
down_revision = "0010_rf_operations"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "monitoring_jobs",
        s.Column("key", s.String(100), primary_key=True),
        s.Column(
            "device_id",
            s.Integer(),
            s.ForeignKey("devices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        s.Column("kind", s.String(20), nullable=False),
        s.Column("enabled", s.Boolean(), nullable=False),
        s.Column("interval_seconds", s.Integer(), nullable=False),
        s.Column("timeout_seconds", s.Integer(), nullable=False),
        s.Column("lease_seconds", s.Integer(), nullable=False),
        s.Column("fixture", s.JSON(), nullable=False),
        s.Column("state", s.String(20), nullable=False),
        s.Column("next_due_at", s.DateTime(timezone=True), nullable=False),
        s.Column("slot_at", s.DateTime(timezone=True)),
        s.Column("lease_until", s.DateTime(timezone=True)),
        s.Column("lease_token", s.String(36)),
        s.Column("failures", s.Integer(), nullable=False),
        s.Column("successes", s.Integer(), nullable=False),
        s.Column("last_success_at", s.DateTime(timezone=True)),
        s.Column("last_valid_sample_at", s.DateTime(timezone=True)),
        s.Column("last_error", s.String(40)),
        s.Column("updated_at", s.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_monitoring_jobs_due", "monitoring_jobs", ["enabled", "next_due_at"]
    )
    op.create_table(
        "monitoring_attempts",
        s.Column("token", s.String(36), primary_key=True),
        s.Column(
            "job_key",
            s.String(100),
            s.ForeignKey("monitoring_jobs.key", ondelete="CASCADE"),
            nullable=False,
        ),
        s.Column("worker_id", s.String(80), nullable=False),
        s.Column("started_at", s.DateTime(timezone=True), nullable=False),
        s.Column("finished_at", s.DateTime(timezone=True)),
        s.Column("result", s.String(40), nullable=False),
        s.Column("duration_ms", s.Float()),
    )
    op.create_index(
        "ix_monitoring_attempts_job_started",
        "monitoring_attempts",
        ["job_key", "started_at"],
    )
    op.create_table(
        "monitoring_receipts",
        s.Column("id", s.Integer(), primary_key=True),
        s.Column(
            "job_key",
            s.String(100),
            s.ForeignKey("monitoring_jobs.key", ondelete="CASCADE"),
            nullable=False,
        ),
        s.Column("slot_at", s.DateTime(timezone=True), nullable=False),
        s.Column(
            "sample_id",
            s.Integer(),
            s.ForeignKey("telemetry_samples.id", ondelete="CASCADE"),
            nullable=False,
        ),
        s.Column("accepted_at", s.DateTime(timezone=True), nullable=False),
        s.Column("replayed", s.Boolean(), nullable=False),
        s.UniqueConstraint("job_key", "slot_at", name="uq_monitoring_receipt_slot"),
    )
    op.create_table(
        "monitoring_alerts",
        s.Column("id", s.Integer(), primary_key=True),
        s.Column(
            "device_id",
            s.Integer(),
            s.ForeignKey("devices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        s.Column("series", s.String(40), nullable=False),
        s.Column("rule", s.String(40), nullable=False),
        s.Column("category", s.String(20), nullable=False),
        s.Column("severity", s.String(20), nullable=False),
        s.Column("state", s.String(20), nullable=False),
        s.Column("episode", s.Integer(), nullable=False),
        s.Column("first_seen_at", s.DateTime(timezone=True), nullable=False),
        s.Column("updated_at", s.DateTime(timezone=True), nullable=False),
        s.Column("cleared_at", s.DateTime(timezone=True)),
        s.Column("evidence", s.JSON(), nullable=False),
        s.Column(
            "incident_id",
            s.Integer(),
            s.ForeignKey("incidents.id", ondelete="SET NULL"),
        ),
        s.UniqueConstraint(
            "device_id", "series", "rule", name="uq_monitoring_alert_rule"
        ),
    )
    op.create_index("ix_monitoring_alerts_state", "monitoring_alerts", ["state"])
    op.create_table(
        "monitoring_workers",
        s.Column("id", s.String(80), primary_key=True),
        s.Column("started_at", s.DateTime(timezone=True), nullable=False),
        s.Column("heartbeat_at", s.DateTime(timezone=True), nullable=False),
        s.Column("redis_state", s.String(20), nullable=False),
        s.Column("phase", s.String(30), nullable=False),
        s.Column("spool_pending", s.Integer(), nullable=False),
    )


def downgrade():
    for name in (
        "monitoring_workers",
        "monitoring_alerts",
        "monitoring_receipts",
        "monitoring_attempts",
        "monitoring_jobs",
    ):
        op.drop_table(name)
