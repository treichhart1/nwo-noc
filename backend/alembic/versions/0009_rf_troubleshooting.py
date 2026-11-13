"""Evidence-based human RF troubleshooting workflow; no source changes."""
from alembic import op
import sqlalchemy as sa
revision='0009_rf_troubleshooting'
down_revision='0008_rf_analytics'
branch_labels=None
depends_on=None


def upgrade():
    op.create_table('rf_recommendations',
        sa.Column('id',sa.Integer(),primary_key=True),
        sa.Column('problem_key',sa.String(160),nullable=False),
        sa.Column('device_id',sa.Integer(),sa.ForeignKey('devices.id',ondelete='CASCADE'),nullable=False),
        sa.Column('rule',sa.String(60),nullable=False),sa.Column('status',sa.String(24),nullable=False),
        sa.Column('evidence_state',sa.String(16),nullable=False),sa.Column('episode',sa.Integer(),nullable=False),
        sa.Column('evidence',sa.JSON(),nullable=False),sa.Column('resolution',sa.Text()),
        sa.Column('first_seen_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('episode_started_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('updated_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('recovery_observed_at',sa.DateTime(timezone=True)),
        sa.UniqueConstraint('problem_key',name='uq_rf_recommendation_problem'))
    op.create_index('ix_rf_recommendations_status_updated','rf_recommendations',['status','updated_at'])
    op.create_table('rf_recommendation_notes',sa.Column('id',sa.Integer(),primary_key=True),
        sa.Column('recommendation_id',sa.Integer(),sa.ForeignKey('rf_recommendations.id',ondelete='CASCADE'),nullable=False),
        sa.Column('user_id',sa.Integer(),sa.ForeignKey('users.id',ondelete='SET NULL')),
        sa.Column('episode',sa.Integer(),nullable=False),sa.Column('body',sa.Text(),nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False))
    op.create_index('ix_rf_recommendation_notes_recommendation_id','rf_recommendation_notes',['recommendation_id'])


def downgrade():
    op.drop_table('rf_recommendation_notes')
    op.drop_table('rf_recommendations')
