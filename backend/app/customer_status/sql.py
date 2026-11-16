SQL = r"""
CREATE SCHEMA noc_public_status;
REVOKE ALL ON SCHEMA noc_public_status FROM PUBLIC;
CREATE VIEW noc_public_status.configuration WITH(security_barrier=true) AS SELECT enabled,baseline_status,baseline_approved_at FROM public.customer_status_policy WHERE id=1;
CREATE VIEW noc_public_status.announcements WITH(security_barrier=true) AS
SELECT p.identifier,CASE WHEN p.maintenance_id IS NULL THEN 'incident' ELSE 'maintenance' END AS kind,
jsonb_build_object('title',r.payload::jsonb->'title','description',r.payload::jsonb->'description','status',r.payload::jsonb->'status','areas',r.payload::jsonb->'areas','starts_at',r.payload::jsonb->'starts_at','ends_at',r.payload::jsonb->'ends_at','estimated_restoration_at',r.payload::jsonb->'estimated_restoration_at') AS payload,r.published_at AS updated_at
FROM public.customer_status_publications p JOIN public.customer_status_revisions r ON r.id=p.published_revision_id AND r.publication_id=p.id
WHERE r.state IN ('published','updated','resolved') AND r.approved_by IS NOT NULL AND r.approved_at IS NOT NULL AND r.published_at IS NOT NULL AND NOT r.withdraw;
CREATE FUNCTION public.customer_status_frozen_revision() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.publication_id<>OLD.publication_id OR NEW.number<>OLD.number OR NEW.base_version<>OLD.base_version OR NEW.source_digest<>OLD.source_digest OR NEW.payload::jsonb<>OLD.payload::jsonb OR NEW.recovery_id IS DISTINCT FROM OLD.recovery_id OR NEW.withdraw<>OLD.withdraw OR NEW.created_by<>OLD.created_by OR NEW.created_at<>OLD.created_at THEN RAISE EXCEPTION 'Public revision content is immutable; create a new revision'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER customer_status_frozen_revision BEFORE UPDATE ON public.customer_status_revisions FOR EACH ROW EXECUTE FUNCTION public.customer_status_frozen_revision();
CREATE TRIGGER customer_status_revision_retention BEFORE DELETE ON public.customer_status_revisions FOR EACH ROW EXECUTE FUNCTION field_append_only();
CREATE TRIGGER customer_status_event_retention BEFORE UPDATE OR DELETE ON public.customer_status_events FOR EACH ROW EXECUTE FUNCTION field_append_only();
CREATE TRIGGER customer_status_recovery_retention BEFORE UPDATE OR DELETE ON public.customer_status_recoveries FOR EACH ROW EXECUTE FUNCTION field_append_only();
DO $$ BEGIN IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='noc_app') THEN GRANT USAGE ON SCHEMA noc_public_status TO noc_app; GRANT SELECT ON ALL TABLES IN SCHEMA noc_public_status TO noc_app; END IF; END $$;
"""
DOWN = "DROP SCHEMA noc_public_status CASCADE; DROP FUNCTION public.customer_status_frozen_revision() CASCADE;"
