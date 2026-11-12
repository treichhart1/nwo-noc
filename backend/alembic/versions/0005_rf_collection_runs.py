"""Create sanitized RF collection run history.

Revision ID: 0005_rf_collection_runs
Revises: 0004_live_monitoring
"""
from alembic import op
import sqlalchemy as sa

revision = "0005_rf_collection_runs"
down_revision = "0004_live_monitoring"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "rf_collection_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("received", sa.Integer(), nullable=False),
        sa.Column("staged", sa.Integer(), nullable=False),
        sa.Column("skipped", sa.Integer(), nullable=False),
    )
    op.create_index("ix_rf_collection_runs_started_at", "rf_collection_runs", ["started_at"])


def downgrade():
    op.drop_index("ix_rf_collection_runs_started_at", table_name="rf_collection_runs")
    op.drop_table("rf_collection_runs")
