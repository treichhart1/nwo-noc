"""WISP operations, incidents, maintenance and telemetry."""
from alembic import op
import sqlalchemy as sa

revision = "0003_operations"
down_revision = "0002_wisp_foundation"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("incidents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("severity", sa.String(30), nullable=False, server_default="warning"),
        sa.Column("status", sa.String(30), nullable=False, server_default="open"),
        sa.Column("site_id", sa.Integer(), sa.ForeignKey("sites.id", ondelete="SET NULL")),
        sa.Column("device_id", sa.Integer(), sa.ForeignKey("devices.id", ondelete="SET NULL")),
        sa.Column("link_id", sa.Integer(), sa.ForeignKey("network_links.id", ondelete="SET NULL")),
        sa.Column("customer_impact", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("summary", sa.Text()), sa.Column("root_cause", sa.Text()), sa.Column("resolution", sa.Text()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True)), sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))
    for col in ["title","severity","status","site_id","device_id","link_id"]:
        op.create_index(f"ix_incidents_{col}", "incidents", [col])
    op.create_table("maintenance_windows",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("title", sa.String(255), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="scheduled"),
        sa.Column("site_id", sa.Integer(), sa.ForeignKey("sites.id", ondelete="SET NULL")),
        sa.Column("link_id", sa.Integer(), sa.ForeignKey("network_links.id", ondelete="SET NULL")),
        sa.Column("description", sa.Text()), sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expected_customer_impact", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))
    for col in ["status","site_id","link_id","starts_at"]:
        op.create_index(f"ix_maintenance_windows_{col}", "maintenance_windows", [col])
    op.create_table("telemetry_samples",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("device_id", sa.Integer(), sa.ForeignKey("devices.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sampled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cpu_percent", sa.Float()), sa.Column("ram_percent", sa.Float()),
        sa.Column("rx_mbps", sa.Float()), sa.Column("tx_mbps", sa.Float()),
        sa.Column("latency_ms", sa.Float()), sa.Column("packet_loss_percent", sa.Float()),
        sa.Column("signal_dbm", sa.Float()), sa.Column("temperature_c", sa.Float()),
        sa.Column("raw_data", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")))
    op.create_index("ix_telemetry_samples_device_id", "telemetry_samples", ["device_id"])
    op.create_index("ix_telemetry_samples_sampled_at", "telemetry_samples", ["sampled_at"])

def downgrade():
    op.drop_table("telemetry_samples")
    op.drop_table("maintenance_windows")
    op.drop_table("incidents")
