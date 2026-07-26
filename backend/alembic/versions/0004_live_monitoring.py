"""Live device monitoring and state-change events."""
from alembic import op
import sqlalchemy as sa

revision = "0004_live_monitoring"
down_revision = "0003_operations"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("devices", sa.Column("monitor_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("devices", sa.Column("monitor_host", sa.String(255), nullable=True))
    op.add_column("devices", sa.Column("monitor_port", sa.Integer(), nullable=False, server_default="443"))
    op.add_column("devices", sa.Column("monitor_interval_seconds", sa.Integer(), nullable=False, server_default="60"))
    op.add_column("devices", sa.Column("monitor_timeout_seconds", sa.Integer(), nullable=False, server_default="3"))
    op.add_column("devices", sa.Column("monitor_status", sa.String(30), nullable=False, server_default="unknown"))
    op.add_column("devices", sa.Column("monitor_latency_ms", sa.Float(), nullable=True))
    op.add_column("devices", sa.Column("monitor_last_checked_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("devices", sa.Column("monitor_last_up_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("devices", sa.Column("monitor_last_down_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("devices", sa.Column("monitor_consecutive_failures", sa.Integer(), nullable=False, server_default="0"))
    op.create_index("ix_devices_monitor_status", "devices", ["monitor_status"])
    op.create_table("monitor_checks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("device_id", sa.Integer(), sa.ForeignKey("devices.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("latency_ms", sa.Float()), sa.Column("error", sa.Text()),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_monitor_checks_device_id", "monitor_checks", ["device_id"])
    op.create_index("ix_monitor_checks_status", "monitor_checks", ["status"])
    op.create_index("ix_monitor_checks_checked_at", "monitor_checks", ["checked_at"])
    op.create_table("monitor_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("device_id", sa.Integer(), sa.ForeignKey("devices.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_type", sa.String(40), nullable=False),
        sa.Column("from_status", sa.String(30)), sa.Column("to_status", sa.String(30), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("acknowledged", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    for col in ["device_id","event_type","acknowledged","created_at"]:
        op.create_index(f"ix_monitor_events_{col}", "monitor_events", [col])

def downgrade():
    op.drop_table("monitor_events")
    op.drop_table("monitor_checks")
    op.drop_index("ix_devices_monitor_status", table_name="devices")
    for c in ["monitor_consecutive_failures","monitor_last_down_at","monitor_last_up_at","monitor_last_checked_at","monitor_latency_ms","monitor_status","monitor_timeout_seconds","monitor_interval_seconds","monitor_port","monitor_host","monitor_enabled"]:
        op.drop_column("devices", c)
