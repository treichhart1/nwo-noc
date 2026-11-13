"""Read-only integration health and durable imported object identities."""
from alembic import op
import sqlalchemy as sa
revision='0007_integration_readiness'
down_revision='0006_rf_query_index'
branch_labels=None
depends_on=None


def upgrade():
    op.add_column("monitor_checks",sa.Column("source",sa.String(40),server_default="tcp",nullable=False))
    for name in ('last_attempt_at','last_success_at'):
        op.add_column('integrations',sa.Column(name,sa.DateTime(timezone=True),nullable=True))
    for name in ('last_received_count','last_skipped_count'):
        op.add_column('integrations',sa.Column(name,sa.Integer(),server_default='0',nullable=False))
    op.execute('UPDATE integrations SET last_success_at=last_sync_at WHERE status=\'connected\'')
    op.create_table('integration_bindings',sa.Column('id',sa.Integer(),primary_key=True),
        sa.Column('integration',sa.String(80),sa.ForeignKey('integrations.name'),nullable=False),
        sa.Column('kind',sa.String(20),nullable=False),sa.Column('external_id',sa.String(120),nullable=False),
        sa.Column('device_id',sa.Integer(),sa.ForeignKey('devices.id',ondelete='CASCADE')),
        sa.Column('site_id',sa.Integer(),sa.ForeignKey('sites.id',ondelete='CASCADE')),
        sa.Column('link_id',sa.Integer(),sa.ForeignKey('network_links.id',ondelete='CASCADE')),
        sa.Column('sector_id',sa.Integer(),sa.ForeignKey('sectors.id',ondelete='CASCADE')),
        sa.Column('observed_at',sa.DateTime(timezone=True)),sa.Column('received_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('digest',sa.String(64),nullable=False),sa.Column('values',sa.JSON(),nullable=False),
        sa.UniqueConstraint('integration','kind','external_id',name='uq_integration_binding'))


def downgrade():
    op.drop_column("monitor_checks","source")
    op.drop_table('integration_bindings')
    for name in ('last_skipped_count','last_received_count','last_success_at','last_attempt_at'):
        op.drop_column('integrations',name)
