-- Operator-reviewed example only. Run on an isolated rehearsal database first.
-- Create the LOGIN using a separately injected secret; never commit passwords.
CREATE ROLE noc_signup NOLOGIN;
GRANT CONNECT ON DATABASE nwo_noc TO noc_signup;
GRANT USAGE ON SCHEMA public TO noc_signup;
GRANT SELECT ON signup_plans TO noc_signup;
GRANT SELECT (id, reference, submission_key, digest, lookup_hash, state, redacted_at), INSERT ON signup_applications TO noc_signup;
GRANT SELECT (id), INSERT ON signup_events TO noc_signup;
GRANT SELECT, INSERT, UPDATE ON signup_public_budgets TO noc_signup;
GRANT SELECT, INSERT ON signup_captcha_uses TO noc_signup;
GRANT USAGE, SELECT ON SEQUENCE signup_applications_id_seq, signup_events_id_seq TO noc_signup;
-- Do not grant customer, topology, users, quotes, coverage, documents or work-order tables.
-- Audit existing PUBLIC grants and schema CREATE rights before enabling any public ingress.

-- Quote access is capability-based and cannot read private quote/application tables.
GRANT EXECUTE ON FUNCTION public.noc_signup_quote_read(text), public.noc_signup_quote_respond(text,text) TO noc_signup;
-- Cancellation intake uses fixed SECURITY DEFINER functions only; no customer/case grants.
GRANT EXECUTE ON FUNCTION public.noc_cancel_config(), public.noc_cancel_replay(text,text), public.noc_cancel_submit(text,text,text,text,text,text,date,text,integer) TO noc_signup;
