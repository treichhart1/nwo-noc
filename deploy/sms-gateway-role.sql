-- OPTIONAL: approved DBA only, after migration0029 on the selected isolated DB.
-- No login/password, network binding or live integration is created here.
BEGIN;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname='noc_sms_gateway_access') THEN
    CREATE ROLE noc_sms_gateway_access NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
  END IF;
END $$;
GRANT USAGE ON SCHEMA public TO noc_sms_gateway_access;
GRANT SELECT ON public.notification_sms_providers, public.notification_sms_events TO noc_sms_gateway_access;
GRANT UPDATE(last_callback_at) ON public.notification_sms_providers TO noc_sms_gateway_access;
GRANT INSERT ON public.notification_sms_events TO noc_sms_gateway_access;
GRANT SELECT,INSERT,UPDATE,DELETE ON public.notification_sms_callback_budget TO noc_sms_gateway_access;
GRANT SELECT(id,provider_name,provider_message_id,destination_hash,delivery_id,raw_status,outcome,updated_at,reported_cost_microunits,provider_error_code)
  ON public.notification_attempts TO noc_sms_gateway_access;
GRANT UPDATE(raw_status,outcome,updated_at,reported_cost_microunits,provider_error_code)
  ON public.notification_attempts TO noc_sms_gateway_access;
GRANT SELECT(id,state,provider_status,provider_status_at,completed_at)
  ON public.notification_deliveries TO noc_sms_gateway_access;
GRANT UPDATE(state,provider_status,provider_status_at,completed_at)
  ON public.notification_deliveries TO noc_sms_gateway_access;
GRANT INSERT ON public.audit_logs TO noc_sms_gateway_access;
-- SQLAlchemy INSERT RETURNING requires SELECT on the returned identifier only.
GRANT SELECT(id) ON public.audit_logs TO noc_sms_gateway_access;
GRANT USAGE ON SEQUENCE public.audit_logs_id_seq TO noc_sms_gateway_access;
GRANT EXECUTE ON FUNCTION public.noc_sms_enforce_opt_out(text,timestamptz) TO noc_sms_gateway_access;
-- Deliberately no private user/customer/credential table reads, consent updates,
-- acceptance updates, provider activation, queue INSERT or event DELETE grants.
COMMIT;
