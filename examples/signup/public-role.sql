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
