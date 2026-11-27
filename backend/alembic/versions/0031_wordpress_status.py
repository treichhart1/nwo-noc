"""Approved public service areas and privacy-filtered v2 projection."""

from alembic import op
import sqlalchemy as sa

revision = "0031_wordpress_status"
down_revision = "0030_native_slack"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "customer_status_areas",
        sa.Column("public_details", sa.JSON(), nullable=False, server_default="{}"),
    )
    op.execute("""
CREATE OR REPLACE VIEW noc_public_status.configuration WITH(security_barrier=true) AS
SELECT enabled,baseline_status,baseline_approved_at,
(SELECT max(r.published_at) FROM public.customer_status_publications p JOIN public.customer_status_revisions r ON r.id=p.published_revision_id AND r.publication_id=p.id WHERE r.approved_by IS NOT NULL AND r.approved_at IS NOT NULL AND r.published_at IS NOT NULL) AS last_public_update
FROM public.customer_status_policy WHERE id=1;
CREATE OR REPLACE VIEW noc_public_status.announcements WITH(security_barrier=true) AS
SELECT p.identifier,CASE WHEN p.maintenance_id IS NULL THEN 'incident' ELSE 'maintenance' END AS kind,
jsonb_build_object('title',r.payload::jsonb->'title','description',r.payload::jsonb->'description','status',r.payload::jsonb->'status','areas',r.payload::jsonb->'areas','starts_at',r.payload::jsonb->'starts_at','ends_at',r.payload::jsonb->'ends_at','estimated_restoration_at',r.payload::jsonb->'estimated_restoration_at','impact',COALESCE(r.payload::jsonb->'impact','\"general\"'::jsonb),'area_details',COALESCE(r.payload::jsonb->'area_details','[]'::jsonb)) AS payload,r.published_at AS updated_at
FROM public.customer_status_publications p JOIN public.customer_status_revisions r ON r.id=p.published_revision_id AND r.publication_id=p.id
WHERE r.state IN ('published','updated','resolved') AND r.approved_by IS NOT NULL AND r.approved_at IS NOT NULL AND r.published_at IS NOT NULL AND NOT r.withdraw;
CREATE VIEW noc_public_status.areas WITH(security_barrier=true) AS
SELECT identifier,label,jsonb_build_object('county',public_details::jsonb->'county','technology',COALESCE(public_details::jsonb->'technology','\"unknown\"'::jsonb),'group_label',public_details::jsonb->'group_label','description',public_details::jsonb->'description') AS details
FROM public.customer_status_areas WHERE active;
DO $$ BEGIN
IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='noc_app') THEN GRANT SELECT ON noc_public_status.areas TO noc_app; END IF;
IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='noc_status_public') THEN GRANT SELECT ON noc_public_status.areas TO noc_status_public; END IF;
END $$;
""")


def downgrade():
    raise RuntimeError(
        "Preserve approved public history; recover a reviewed matching backup"
    )
