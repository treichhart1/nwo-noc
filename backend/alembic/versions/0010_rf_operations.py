"""Shared incident investigations and observed RF alert acknowledgments."""
from alembic import op
import sqlalchemy as sa
revision='0010_rf_operations'
down_revision='0009_rf_troubleshooting'
branch_labels=None
depends_on=None

def upgrade():
    op.add_column('incidents',sa.Column('assigned_to_user_id',sa.Integer(),sa.ForeignKey('users.id',name='fk_incident_assignee',ondelete='SET NULL')))
    op.create_index('ix_incidents_assigned_to_user_id','incidents',['assigned_to_user_id'])
    op.add_column('incidents',sa.Column('findings',sa.Text()))
    op.add_column('incidents',sa.Column('closed_at',sa.DateTime(timezone=True)))
    op.add_column('incidents',sa.Column('revision',sa.Integer(),nullable=False,server_default='1'))
    op.create_index('ix_incidents_status_started_id','incidents',['status','started_at','id'])
    op.create_table('incident_notes',sa.Column('id',sa.Integer(),primary_key=True),
        sa.Column('incident_id',sa.Integer(),sa.ForeignKey('incidents.id',ondelete='CASCADE'),nullable=False),
        sa.Column('user_id',sa.Integer(),sa.ForeignKey('users.id',ondelete='SET NULL')),
        sa.Column('body',sa.Text(),nullable=False),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False))
    op.create_index('ix_incident_notes_incident_id','incident_notes',['incident_id'])
    op.create_table('rf_alert_reviews',sa.Column('alert_key',sa.String(120),primary_key=True),
        sa.Column('observed_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('acknowledged_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('user_id',sa.Integer(),sa.ForeignKey('users.id',ondelete='SET NULL')),
        sa.Column('incident_id',sa.Integer(),sa.ForeignKey('incidents.id',ondelete='SET NULL')))
    op.create_index('ix_rf_alert_reviews_incident_id','rf_alert_reviews',['incident_id'])
    op.create_index('ix_audit_entity_cursor','audit_logs',['entity_type','entity_id','id'])


def downgrade():
    op.drop_index('ix_audit_entity_cursor',table_name='audit_logs')
    op.drop_table('rf_alert_reviews');op.drop_table('incident_notes')
    op.drop_index('ix_incidents_status_started_id',table_name='incidents')
    op.drop_index('ix_incidents_assigned_to_user_id',table_name='incidents')
    op.drop_constraint('fk_incident_assignee','incidents',type_='foreignkey')
    for col in ['revision','closed_at','findings','assigned_to_user_id']:op.drop_column('incidents',col)
