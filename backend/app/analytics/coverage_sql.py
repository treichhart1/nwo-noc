"""Frozen by the additive Alembic revision; late recorded samples invalidate caches."""

SQL = r"""
CREATE VIEW noc_analytics.coverage WITH(security_barrier=true) AS SELECT bucket,complete,source_points,refreshed_at FROM public.analytics_hour_coverage;
CREATE FUNCTION public.analytics_invalidate_hour() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE stamp timestamptz; old_stamp timestamptz; hour_key integer; keys integer[];
BEGIN
 IF TG_TABLE_NAME='telemetry_samples' OR TG_TABLE_NAME='rf_analytics_points' THEN stamp=NEW.sampled_at; IF TG_OP='UPDATE' THEN old_stamp=OLD.sampled_at; END IF;
 ELSIF TG_TABLE_NAME='tower_health_samples' THEN stamp=NEW.observed_at; IF TG_OP='UPDATE' THEN old_stamp=OLD.observed_at; END IF;
 ELSE stamp=NEW.checked_at; IF TG_OP='UPDATE' THEN old_stamp=OLD.checked_at; END IF;
 END IF;
 keys=ARRAY[(floor(extract(epoch from stamp)/3600))::integer,(floor(extract(epoch from coalesce(old_stamp,stamp))/3600))::integer];
 FOR hour_key IN SELECT DISTINCT unnest(keys) ORDER BY 1 LOOP
  PERFORM pg_advisory_xact_lock_shared(108,hour_key);
 END LOOP;
 UPDATE public.analytics_hour_coverage SET complete=false WHERE bucket=date_bin(interval '1 hour',stamp,'1970-01-01'::timestamptz) OR (old_stamp IS NOT NULL AND bucket=date_bin(interval '1 hour',old_stamp,'1970-01-01'::timestamptz));
 RETURN NEW;
END $$;
CREATE TRIGGER analytics_telemetry_insert AFTER INSERT OR UPDATE ON public.telemetry_samples FOR EACH ROW EXECUTE FUNCTION public.analytics_invalidate_hour();
CREATE TRIGGER analytics_rf_insert AFTER INSERT OR UPDATE ON public.rf_analytics_points FOR EACH ROW EXECUTE FUNCTION public.analytics_invalidate_hour();
CREATE TRIGGER analytics_tower_insert AFTER INSERT OR UPDATE ON public.tower_health_samples FOR EACH ROW EXECUTE FUNCTION public.analytics_invalidate_hour();
CREATE TRIGGER analytics_monitor_insert AFTER INSERT OR UPDATE ON public.monitor_checks FOR EACH ROW EXECUTE FUNCTION public.analytics_invalidate_hour();
"""
DOWN = """DROP TRIGGER analytics_telemetry_insert ON public.telemetry_samples; DROP TRIGGER analytics_rf_insert ON public.rf_analytics_points; DROP TRIGGER analytics_tower_insert ON public.tower_health_samples; DROP TRIGGER analytics_monitor_insert ON public.monitor_checks; DROP FUNCTION public.analytics_invalidate_hour();"""
