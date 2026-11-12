"""Composite index for bounded per-device RF telemetry history.

Revision ID: 0006_rf_query_index
Revises: 0005_rf_collection_runs
"""
from alembic import op

revision = "0006_rf_query_index"
down_revision = "0005_rf_collection_runs"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index(
        "ix_telemetry_samples_device_sampled_id",
        "telemetry_samples",
        ["device_id", "sampled_at", "id"],
    )


def downgrade():
    op.drop_index("ix_telemetry_samples_device_sampled_id", table_name="telemetry_samples")
