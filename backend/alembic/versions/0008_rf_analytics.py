"""Indexed, rebuildable historical RF analytics projection."""
from alembic import op
import sqlalchemy as sa
revision='0008_rf_analytics'
down_revision='0007_integration_readiness'
branch_labels=None
depends_on=None


def upgrade():
    op.create_table('rf_analytics_points',
        sa.Column('id',sa.Integer(),primary_key=True),
        sa.Column('source_sample_id',sa.Integer(),sa.ForeignKey('telemetry_samples.id',ondelete='CASCADE'),nullable=False),
        sa.Column('device_id',sa.Integer(),sa.ForeignKey('devices.id',ondelete='CASCADE'),nullable=False),
        sa.Column('site_id',sa.Integer(),sa.ForeignKey('sites.id',ondelete='SET NULL')),
        sa.Column('link_id',sa.Integer(),sa.ForeignKey('network_links.id',ondelete='SET NULL')),
        sa.Column('series_key',sa.String(40),nullable=False),sa.Column('vendor',sa.String(20),nullable=False),
        sa.Column('source_profile',sa.String(64),nullable=False),sa.Column('mapping_digest',sa.String(64),nullable=False),
        sa.Column('sampled_at',sa.DateTime(timezone=True),nullable=False),sa.Column('projected_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('valid',sa.Boolean(),nullable=False),sa.Column('radio_state',sa.String(16)),
        sa.Column('signal_dbm',sa.Float()),
        sa.Column('remote_signal_dbm',sa.Float()),
        sa.Column('snr_db',sa.Float()),
        sa.Column('noise_floor_dbm',sa.Float()),
        sa.Column('modulation_bits',sa.Float()),
        sa.Column('mcs_index',sa.Float()),
        sa.Column('capacity_mbps',sa.Float()),
        sa.Column('tx_throughput_mbps',sa.Float()),
        sa.Column('rx_throughput_mbps',sa.Float()),
        sa.Column('tx_phy_mbps',sa.Float()),
        sa.Column('rx_phy_mbps',sa.Float()),
        sa.Column('utilization_percent',sa.Float()),
        sa.Column('quality_percent',sa.Float()),
        sa.Column('temperature_c',sa.Float()),
        sa.Column('uptime_seconds',sa.Float()),
        sa.Column('frequency_mhz',sa.Float()),
        sa.Column('bandwidth_mhz',sa.Float()),
        sa.Column('availability_percent',sa.Float()),
        sa.Column('tx_errors',sa.Numeric(20,0)),
        sa.Column('rx_errors',sa.Numeric(20,0)),
        sa.Column('retries',sa.Numeric(20,0)),
        sa.Column('disconnects',sa.Numeric(20,0)),
        sa.UniqueConstraint('source_sample_id','series_key',name='uq_rf_analytics_source_series'))
    for name,columns in [
        ('ix_rf_analytics_device_series_time',['device_id','series_key','mapping_digest','sampled_at']),
        ('ix_rf_analytics_time',['sampled_at']),('ix_rf_analytics_site_time',['site_id','sampled_at']),
        ('ix_rf_analytics_link_time',['link_id','sampled_at'])]:
        op.create_index(name,'rf_analytics_points',columns)
    op.create_table('rf_analytics_checkpoints',sa.Column('name',sa.String(40),primary_key=True),
        sa.Column('last_source_id',sa.BigInteger(),nullable=False,server_default='0'),
        sa.Column('updated_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('processed',sa.BigInteger(),nullable=False,server_default='0'),
        sa.Column('skipped',sa.BigInteger(),nullable=False,server_default='0'))


def downgrade():
    op.drop_table('rf_analytics_checkpoints')
    op.drop_table('rf_analytics_points')
