"""WISP foundation tables

Revision ID: 0002_wisp_foundation
Revises: 0001_initial
"""
from alembic import op
import sqlalchemy as sa

revision = "0002_wisp_foundation"
down_revision = "0001_initial"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("sites",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False, unique=True),
        sa.Column("site_type", sa.String(40), nullable=False, server_default="tower"),
        sa.Column("status", sa.String(30), nullable=False, server_default="active"),
        sa.Column("address", sa.String(255)), sa.Column("city", sa.String(120)), sa.Column("state", sa.String(20)), sa.Column("postal_code", sa.String(20)),
        sa.Column("latitude", sa.Float()), sa.Column("longitude", sa.Float()), sa.Column("tower_height_ft", sa.Float()),
        sa.Column("structure_owner", sa.String(255)), sa.Column("site_contact", sa.String(255)), sa.Column("access_instructions", sa.Text()),
        sa.Column("utility_provider", sa.String(255)), sa.Column("ups_present", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("generator_present", sa.Boolean(), nullable=False, server_default=sa.false()), sa.Column("customer_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("notes", sa.Text()), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False)
    )
    op.create_index("ix_sites_name", "sites", ["name"])
    op.add_column("devices", sa.Column("wisp_site_id", sa.Integer(), nullable=True))
    op.add_column("devices", sa.Column("parent_device_id", sa.Integer(), nullable=True))
    op.add_column("devices", sa.Column("management_vlan", sa.Integer(), nullable=True))
    op.add_column("devices", sa.Column("asset_tag", sa.String(120), nullable=True))
    op.add_column("devices", sa.Column("serial_number", sa.String(160), nullable=True))
    op.add_column("devices", sa.Column("install_height_ft", sa.Float(), nullable=True))
    op.add_column("devices", sa.Column("azimuth", sa.Float(), nullable=True))
    op.create_foreign_key("fk_devices_wisp_site", "devices", "sites", ["wisp_site_id"], ["id"], ondelete="SET NULL")
    op.create_foreign_key("fk_devices_parent", "devices", "devices", ["parent_device_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_devices_wisp_site_id", "devices", ["wisp_site_id"])
    op.create_table("network_links",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("name", sa.String(255), nullable=False, unique=True),
        sa.Column("link_type", sa.String(50), nullable=False, server_default="wireless"), sa.Column("status", sa.String(30), nullable=False, server_default="active"),
        sa.Column("a_site_id", sa.Integer(), sa.ForeignKey("sites.id"), nullable=False), sa.Column("z_site_id", sa.Integer(), sa.ForeignKey("sites.id"), nullable=False),
        sa.Column("a_device_id", sa.Integer(), sa.ForeignKey("devices.id")), sa.Column("z_device_id", sa.Integer(), sa.ForeignKey("devices.id")),
        sa.Column("vendor", sa.String(120)), sa.Column("model", sa.String(160)), sa.Column("frequency_ghz", sa.Float()), sa.Column("channel_width_mhz", sa.Float()),
        sa.Column("licensed", sa.Boolean(), nullable=False, server_default=sa.false()), sa.Column("license_call_sign", sa.String(80)), sa.Column("capacity_mbps", sa.Float()),
        sa.Column("rx_signal_a", sa.Float()), sa.Column("rx_signal_z", sa.Float()), sa.Column("notes", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False)
    )
    op.create_index("ix_network_links_a_site_id", "network_links", ["a_site_id"])
    op.create_index("ix_network_links_z_site_id", "network_links", ["z_site_id"])
    op.create_table("sectors",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("name", sa.String(255), nullable=False, unique=True),
        sa.Column("site_id", sa.Integer(), sa.ForeignKey("sites.id"), nullable=False), sa.Column("device_id", sa.Integer(), sa.ForeignKey("devices.id")),
        sa.Column("status", sa.String(30), nullable=False, server_default="active"), sa.Column("technology", sa.String(80)), sa.Column("frequency_mhz", sa.Float()),
        sa.Column("channel_width_mhz", sa.Float()), sa.Column("azimuth", sa.Float()), sa.Column("beamwidth", sa.Float()),
        sa.Column("customer_count", sa.Integer(), nullable=False, server_default="0"), sa.Column("capacity_mbps", sa.Float()), sa.Column("notes", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False)
    )
    op.create_index("ix_sectors_site_id", "sectors", ["site_id"])
    # Seed real site records from existing UISP site names.
    op.execute("""
        INSERT INTO sites (name, site_type, status, customer_count, ups_present, generator_present, created_at, updated_at)
        SELECT DISTINCT site_name, 'tower', 'active', 0, false, false, now(), now()
        FROM devices WHERE site_name IS NOT NULL AND trim(site_name) <> ''
        ON CONFLICT (name) DO NOTHING
    """)
    op.execute("UPDATE devices d SET wisp_site_id=s.id FROM sites s WHERE d.site_name=s.name AND d.wisp_site_id IS NULL")

def downgrade():
    op.drop_table("sectors")
    op.drop_table("network_links")
    op.drop_index("ix_devices_wisp_site_id", table_name="devices")
    op.drop_constraint("fk_devices_parent", "devices", type_="foreignkey")
    op.drop_constraint("fk_devices_wisp_site", "devices", type_="foreignkey")
    for c in ["azimuth","install_height_ft","serial_number","asset_tag","management_vlan","parent_device_id","wisp_site_id"]:
        op.drop_column("devices", c)
    op.drop_table("sites")
