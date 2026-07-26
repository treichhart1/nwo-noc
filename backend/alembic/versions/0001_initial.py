"""Initial schema."""
from alembic import op
import sqlalchemy as sa

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.String(80), nullable=False, unique=True),
        sa.Column("email", sa.String(255), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role", sa.String(30), nullable=False, server_default="admin"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table("integrations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(80), nullable=False, unique=True),
        sa.Column("status", sa.String(40), nullable=False, server_default="not_configured"),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_item_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table("devices",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source", sa.String(30), nullable=False),
        sa.Column("external_id", sa.String(120), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("hostname", sa.String(255), nullable=True),
        sa.Column("model", sa.String(120), nullable=True),
        sa.Column("model_name", sa.String(255), nullable=True),
        sa.Column("vendor", sa.String(120), nullable=True),
        sa.Column("category", sa.String(80), nullable=True),
        sa.Column("device_type", sa.String(80), nullable=True),
        sa.Column("role", sa.String(80), nullable=True),
        sa.Column("status", sa.String(80), nullable=True),
        sa.Column("authorized", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("ip_address", sa.String(120), nullable=True),
        sa.Column("mac_address", sa.String(120), nullable=True),
        sa.Column("firmware_version", sa.String(120), nullable=True),
        sa.Column("site_id", sa.String(120), nullable=True),
        sa.Column("site_name", sa.String(255), nullable=True),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column("signal", sa.Float(), nullable=True),
        sa.Column("cpu", sa.Float(), nullable=True),
        sa.Column("ram", sa.Float(), nullable=True),
        sa.Column("frequency", sa.Float(), nullable=True),
        sa.Column("channel_width", sa.Float(), nullable=True),
        sa.Column("uplink_capacity", sa.BigInteger(), nullable=True),
        sa.Column("downlink_capacity", sa.BigInteger(), nullable=True),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=True),
        sa.Column("raw_data", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("source", "external_id", name="uq_devices_source_external_id"),
    )
    op.create_index("ix_devices_status", "devices", ["status"])
    op.create_index("ix_devices_site_name", "devices", ["site_name"])
    op.create_table("audit_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("action", sa.String(120), nullable=False),
        sa.Column("entity_type", sa.String(80), nullable=True),
        sa.Column("entity_id", sa.String(120), nullable=True),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("ip_address", sa.String(120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

def downgrade():
    op.drop_table("audit_logs")
    op.drop_index("ix_devices_site_name", table_name="devices")
    op.drop_index("ix_devices_status", table_name="devices")
    op.drop_table("devices")
    op.drop_table("integrations")
    op.drop_table("users")
