-- OPTIONAL reviewed DBA step after migration0030. No login, secret or activation.
BEGIN;
DO $$ BEGIN IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='noc_slack_gateway_access') THEN CREATE ROLE noc_slack_gateway_access NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS; END IF; END $$;
GRANT USAGE ON SCHEMA public TO noc_slack_gateway_access;
GRANT SELECT ON public.notification_slack_integration,public.notification_slack_interactions TO noc_slack_gateway_access;
GRANT UPDATE(last_callback_at) ON public.notification_slack_integration TO noc_slack_gateway_access;
GRANT SELECT(id,channel,recipient_key,provider_receipt,slack_authorization) ON public.notification_deliveries TO noc_slack_gateway_access;
GRANT INSERT ON public.notification_slack_interactions TO noc_slack_gateway_access;
GRANT SELECT,INSERT,UPDATE,DELETE ON public.notification_slack_callback_budget TO noc_slack_gateway_access;
GRANT EXECUTE ON FUNCTION public.noc_slack_interaction_allowed(text,text,text,integer) TO noc_slack_gateway_access;
-- No credentials, private users/customers, incident writes, queue writes or activation.
COMMIT;
