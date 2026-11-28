"""Cancellation evidence and restricted public intake; no billing or service writes."""

from alembic import op
import sqlalchemy as sa

revision = "0034_customer_retention"
down_revision = "0033_powercode_integration"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "\nCREATE TABLE retention_competitors (\n\tid SERIAL NOT NULL, \n\tname VARCHAR(120) NOT NULL, \n\ttechnology VARCHAR(30) NOT NULL, \n\tservice_areas JSON NOT NULL, \n\tactive BOOLEAN NOT NULL, \n\tPRIMARY KEY (id), \n\tUNIQUE (name)\n)\n\n"
    )
    op.execute(
        "\nCREATE TABLE retention_reasons (\n\tcode VARCHAR(50) NOT NULL, \n\tlabel VARCHAR(120) NOT NULL, \n\tvoluntary VARCHAR(20) NOT NULL, \n\tactive BOOLEAN NOT NULL, \n\tPRIMARY KEY (code)\n)\n\n"
    )
    op.execute(
        "\nCREATE TABLE retention_cohorts (\n\tid SERIAL NOT NULL, \n\tas_of DATE NOT NULL, \n\tcomplete BOOLEAN NOT NULL, \n\tmembers JSON NOT NULL, \n\tsource_reference VARCHAR(120) NOT NULL, \n\tapproved_by INTEGER NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tUNIQUE (as_of), \n\tFOREIGN KEY(approved_by) REFERENCES users (id)\n)\n\n"
    )
    op.execute(
        "\nCREATE TABLE retention_imports (\n\tid SERIAL NOT NULL, \n\tdigest VARCHAR(64) NOT NULL, \n\trows INTEGER NOT NULL, \n\tactor_id INTEGER NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tUNIQUE (digest), \n\tFOREIGN KEY(actor_id) REFERENCES users (id)\n)\n\n"
    )
    op.execute(
        "\nCREATE TABLE retention_policies (\n\tid SERIAL NOT NULL, \n\tversion INTEGER NOT NULL, \n\ttext VARCHAR(4000) NOT NULL, \n\tactive BOOLEAN NOT NULL, \n\tretention_days INTEGER NOT NULL, \n\tunresolved_days INTEGER NOT NULL, \n\ttower_churn_threshold INTEGER NOT NULL, \n\tcreated_by INTEGER NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tUNIQUE (version), \n\tFOREIGN KEY(created_by) REFERENCES users (id)\n)\n\n"
    )
    op.execute(
        "\nCREATE TABLE retention_cases (\n\tid SERIAL NOT NULL, \n\treference VARCHAR(45) NOT NULL, \n\tsubmission_key VARCHAR(36) NOT NULL, \n\tdigest VARCHAR(64) NOT NULL, \n\treceipt_hash VARCHAR(64) NOT NULL, \n\twrapped_key TEXT, \n\tpii_ciphertext TEXT, \n\tcustomer_id INTEGER, \n\trequested_date DATE NOT NULL, \n\treported_reason VARCHAR(50) NOT NULL, \n\toperator_reason VARCHAR(50), \n\tpolicy_id INTEGER NOT NULL, \n\tpolicy_ack_at TIMESTAMP WITH TIME ZONE, \n\tequipment_ack BOOLEAN NOT NULL, \n\tstatus VARCHAR(40) NOT NULL, \n\tretention_state VARCHAR(40) NOT NULL, \n\tcompetitor_id INTEGER, \n\tbilling_disposition VARCHAR(30) NOT NULL, \n\trevision INTEGER NOT NULL, \n\tsource VARCHAR(30) NOT NULL, \n\timport_key VARCHAR(64), \n\tverified_by INTEGER, \n\tlegal_hold BOOLEAN NOT NULL, \n\tredacted_at TIMESTAMP WITH TIME ZONE, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tUNIQUE (reference), \n\tUNIQUE (submission_key), \n\tUNIQUE (receipt_hash), \n\tFOREIGN KEY(customer_id) REFERENCES customer_accounts (id), \n\tFOREIGN KEY(reported_reason) REFERENCES retention_reasons (code), \n\tFOREIGN KEY(operator_reason) REFERENCES retention_reasons (code), \n\tFOREIGN KEY(policy_id) REFERENCES retention_policies (id), \n\tFOREIGN KEY(competitor_id) REFERENCES retention_competitors (id), \n\tUNIQUE (import_key), \n\tFOREIGN KEY(verified_by) REFERENCES users (id)\n)\n\n"
    )
    op.execute(
        "CREATE INDEX ix_retention_case_state_age ON retention_cases (status, created_at, id)"
    )
    op.execute(
        "CREATE INDEX ix_retention_cases_customer_id ON retention_cases (customer_id)"
    )
    op.execute(
        "\nCREATE TABLE retention_case_events (\n\tid SERIAL NOT NULL, \n\tcase_id INTEGER NOT NULL, \n\tactor_id INTEGER, \n\taction VARCHAR(40) NOT NULL, \n\tstatus VARCHAR(40) NOT NULL, \n\tevidence JSON NOT NULL, \n\tnotes_ciphertext TEXT, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(case_id) REFERENCES retention_cases (id), \n\tFOREIGN KEY(actor_id) REFERENCES users (id)\n)\n\n"
    )
    op.execute(
        "CREATE INDEX ix_retention_case_events_case_id ON retention_case_events (case_id)"
    )
    op.execute(
        "\nCREATE TABLE retention_observations (\n\tid SERIAL NOT NULL, \n\tcompetitor_id INTEGER NOT NULL, \n\tcase_id INTEGER, \n\toffer JSON NOT NULL, \n\tsource_kind VARCHAR(30) NOT NULL, \n\tsource_reference VARCHAR(200) NOT NULL, \n\tobserved_on DATE NOT NULL, \n\tverified BOOLEAN NOT NULL, \n\tverified_by INTEGER, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(competitor_id) REFERENCES retention_competitors (id), \n\tFOREIGN KEY(case_id) REFERENCES retention_cases (id), \n\tFOREIGN KEY(verified_by) REFERENCES users (id)\n)\n\n"
    )
    op.execute(
        "CREATE INDEX ix_retention_observations_competitor_id ON retention_observations (competitor_id)"
    )
    op.execute(
        "\nCREATE TABLE retention_offers (\n\tid SERIAL NOT NULL, \n\tcase_id INTEGER NOT NULL, \n\tkind VARCHAR(40) NOT NULL, \n\tterms_ciphertext TEXT NOT NULL, \n\tmonthly_cost NUMERIC(12, 2), \n\tone_time_cost NUMERIC(12, 2), \n\tcurrency VARCHAR(3), \n\texpires_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\teligibility JSON NOT NULL, \n\tstatus VARCHAR(30) NOT NULL, \n\tapproved_by INTEGER, \n\tfingerprint VARCHAR(64) NOT NULL, \n\tacceptance_ciphertext TEXT, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(case_id) REFERENCES retention_cases (id), \n\tFOREIGN KEY(approved_by) REFERENCES users (id)\n)\n\n"
    )
    op.execute("CREATE INDEX ix_retention_offers_case_id ON retention_offers (case_id)")
    op.execute(
        "\nCREATE TABLE retention_case_services (\n\tid SERIAL NOT NULL, \n\tcase_id INTEGER NOT NULL, \n\tservice_id INTEGER NOT NULL, \n\tsnapshot JSON NOT NULL, \n\tactual_disconnected_at TIMESTAMP WITH TIME ZONE, \n\tsource_history_id INTEGER, \n\tequipment_return_required BOOLEAN NOT NULL, \n\tequipment_due DATE, \n\tequipment_returned_at TIMESTAMP WITH TIME ZONE, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_retention_case_service UNIQUE (case_id, service_id), \n\tFOREIGN KEY(case_id) REFERENCES retention_cases (id), \n\tFOREIGN KEY(service_id) REFERENCES customer_services (id), \n\tFOREIGN KEY(source_history_id) REFERENCES powercode_service_history (id)\n)\n\n"
    )
    op.execute(
        "CREATE INDEX ix_retention_case_services_case_id ON retention_case_services (case_id)"
    )
    op.execute(
        "CREATE INDEX ix_retention_case_services_service_id ON retention_case_services (service_id)"
    )
    op.add_column(
        "retention_cases",
        sa.Column("reported_disconnected_date", sa.Date(), nullable=True),
    )
    op.create_index(
        "ix_retention_cases_reported_disconnected_date",
        "retention_cases",
        ["reported_disconnected_date"],
    )
    op.execute(
        "CREATE INDEX ix_powercode_history_churn ON powercode_service_history (status,source_updated_at,service_id)"
    )
    op.execute(
        "ALTER TABLE retention_cases ADD CONSTRAINT ck_retention_case_state CHECK (status IN ('pending_verification','verified','retention_review','cancellation_confirmed','awaiting_disconnection','disconnected','withdrawn','closed'))"
    )
    op.execute(
        "ALTER TABLE retention_reasons ADD CONSTRAINT ck_retention_reason_class CHECK (voluntary IN ('voluntary','involuntary','unknown'))"
    )
    op.execute(
        "ALTER TABLE retention_case_services ADD CONSTRAINT ck_retention_disconnection_evidence CHECK ((actual_disconnected_at IS NULL)=(source_history_id IS NULL))"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_retention_active_policy ON retention_policies ((active)) WHERE active"
    )
    op.execute(r"""
CREATE FUNCTION public.noc_retention_immutable() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN RAISE EXCEPTION 'Retention historical evidence is immutable'; END; $$;
CREATE TRIGGER retention_event_immutable BEFORE UPDATE OR DELETE ON public.retention_case_events FOR EACH ROW EXECUTE FUNCTION public.noc_retention_immutable();
CREATE TRIGGER retention_observation_immutable BEFORE UPDATE OR DELETE ON public.retention_observations FOR EACH ROW EXECUTE FUNCTION public.noc_retention_immutable();
CREATE TRIGGER retention_cohort_immutable BEFORE UPDATE OR DELETE ON public.retention_cohorts FOR EACH ROW EXECUTE FUNCTION public.noc_retention_immutable();
CREATE TRIGGER retention_import_immutable BEFORE UPDATE OR DELETE ON public.retention_imports FOR EACH ROW EXECUTE FUNCTION public.noc_retention_immutable();
CREATE FUNCTION public.noc_retention_guard() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Saved retention references cannot be deleted'; END IF;
 IF TG_TABLE_NAME='retention_policies' THEN
   IF (to_jsonb(NEW)-'active') IS DISTINCT FROM (to_jsonb(OLD)-'active') THEN RAISE EXCEPTION 'Create a new policy version'; END IF;
 ELSIF TG_TABLE_NAME='retention_cases' THEN
   IF (NEW.reference,NEW.submission_key,NEW.digest,NEW.receipt_hash,NEW.requested_date,NEW.reported_disconnected_date,NEW.reported_reason,NEW.policy_id,NEW.policy_ack_at,NEW.equipment_ack,NEW.source,NEW.import_key,NEW.created_at) IS DISTINCT FROM (OLD.reference,OLD.submission_key,OLD.digest,OLD.receipt_hash,OLD.requested_date,OLD.reported_disconnected_date,OLD.reported_reason,OLD.policy_id,OLD.policy_ack_at,OLD.equipment_ack,OLD.source,OLD.import_key,OLD.created_at) THEN RAISE EXCEPTION 'Original request evidence is immutable'; END IF;
   IF (NEW.wrapped_key,NEW.pii_ciphertext) IS DISTINCT FROM (OLD.wrapped_key,OLD.pii_ciphertext) AND NOT (
     NEW.wrapped_key IS NULL AND NEW.pii_ciphertext IS NULL AND OLD.redacted_at IS NULL AND NEW.redacted_at IS NOT NULL AND NEW.status IN ('closed','withdrawn') AND NOT OLD.legal_hold AND clock_timestamp() >= OLD.created_at + (SELECT retention_days FROM public.retention_policies WHERE id=OLD.policy_id)*interval '1 day'
   ) THEN RAISE EXCEPTION 'Original encrypted submission cannot be rewritten; only eligible cryptographic redaction is allowed'; END IF;
   IF OLD.customer_id IS NOT NULL AND NEW.customer_id IS DISTINCT FROM OLD.customer_id THEN RAISE EXCEPTION 'Verified customer association is immutable'; END IF;
 ELSIF TG_TABLE_NAME='retention_case_services' THEN
   IF (NEW.case_id,NEW.service_id,NEW.snapshot::jsonb) IS DISTINCT FROM (OLD.case_id,OLD.service_id,OLD.snapshot::jsonb) THEN RAISE EXCEPTION 'Verified service snapshot is immutable'; END IF;
   IF OLD.equipment_returned_at IS NOT NULL AND NEW.equipment_returned_at IS DISTINCT FROM OLD.equipment_returned_at THEN RAISE EXCEPTION 'Observed equipment return is immutable'; END IF;
   IF OLD.source_history_id IS NOT NULL AND (NEW.source_history_id,NEW.actual_disconnected_at) IS DISTINCT FROM (OLD.source_history_id,OLD.actual_disconnected_at) THEN RAISE EXCEPTION 'Observed disconnection evidence is immutable'; END IF;
 ELSIF TG_TABLE_NAME='retention_offers' THEN
   IF (to_jsonb(NEW)-ARRAY['status','approved_by','acceptance_ciphertext']) IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['status','approved_by','acceptance_ciphertext']) OR OLD.acceptance_ciphertext IS NOT NULL THEN RAISE EXCEPTION 'Approved offer evidence is immutable'; END IF;
 END IF;
 RETURN NEW;
END; $$;
CREATE TRIGGER retention_policy_guard BEFORE UPDATE OR DELETE ON public.retention_policies FOR EACH ROW EXECUTE FUNCTION public.noc_retention_guard();
CREATE TRIGGER retention_case_guard BEFORE UPDATE OR DELETE ON public.retention_cases FOR EACH ROW EXECUTE FUNCTION public.noc_retention_guard();
CREATE TRIGGER retention_service_guard BEFORE UPDATE OR DELETE ON public.retention_case_services FOR EACH ROW EXECUTE FUNCTION public.noc_retention_guard();
CREATE TRIGGER retention_offer_guard BEFORE UPDATE OR DELETE ON public.retention_offers FOR EACH ROW EXECUTE FUNCTION public.noc_retention_guard();
CREATE FUNCTION public.noc_cancel_config() RETURNS jsonb LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT jsonb_build_object('policy',(SELECT jsonb_build_object('id',id,'version',version,'text',text) FROM public.retention_policies WHERE active ORDER BY version DESC LIMIT 1),'reasons',COALESCE((SELECT jsonb_agg(jsonb_build_object('code',code,'label',label) ORDER BY code) FROM public.retention_reasons WHERE active),'[]'::jsonb));
$$;
CREATE FUNCTION public.noc_cancel_replay(p_key text,p_digest text) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE c public.retention_cases;
BEGIN
 PERFORM pg_advisory_xact_lock(hashtextextended(p_key,131));
 SELECT * INTO c FROM public.retention_cases WHERE submission_key=p_key;
 IF FOUND THEN
   IF c.digest<>p_digest OR c.redacted_at IS NOT NULL THEN RAISE EXCEPTION 'Submission conflict'; END IF;
   RETURN jsonb_build_object('received',true,'reference',c.reference,'message','Request received for identity verification and review. No service or billing changes have been made.');
 END IF;
 RETURN NULL;
END; $$;
CREATE FUNCTION public.noc_cancel_submit(p_key text,p_digest text,p_ref text,p_receipt text,p_wrapped text,p_pii text,p_requested date,p_reason text,p_policy integer) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE result jsonb; cid integer;
BEGIN
 result=public.noc_cancel_replay(p_key,p_digest);
 IF result IS NOT NULL THEN RETURN result; END IF;
 IF p_key IS NULL OR p_digest IS NULL OR p_receipt IS NULL OR p_wrapped IS NULL OR p_pii IS NULL OR p_requested IS NULL OR p_reason IS NULL OR p_policy IS NULL OR length(p_wrapped)<100 OR length(p_pii)<100 OR p_key !~ '^[a-f0-9-]{36}$' OR p_digest !~ '^[a-f0-9]{64}$' OR p_receipt !~ '^[a-f0-9]{64}$' OR length(p_ref)>45 OR p_ref NOT LIKE 'CAN-%' OR length(p_wrapped)>1000 OR length(p_pii)>24000 OR p_requested<CURRENT_DATE OR p_requested>CURRENT_DATE+366 THEN RAISE EXCEPTION 'Invalid request'; END IF;
 IF NOT EXISTS(SELECT 1 FROM public.retention_policies WHERE id=p_policy AND active) OR NOT EXISTS(SELECT 1 FROM public.retention_reasons WHERE code=p_reason AND active) THEN RAISE EXCEPTION 'Current policy and cancellation reason required'; END IF;
 INSERT INTO public.retention_cases(reference,submission_key,digest,receipt_hash,wrapped_key,pii_ciphertext,requested_date,reported_reason,policy_id,policy_ack_at,equipment_ack,status,retention_state,billing_disposition,revision,source,legal_hold,created_at,updated_at)
 VALUES(p_ref,p_key,p_digest,p_receipt,p_wrapped,p_pii,p_requested,p_reason,p_policy,clock_timestamp(),true,'pending_verification','pending_review','unreviewed',1,'public_request',false,clock_timestamp(),clock_timestamp()) RETURNING id INTO cid;
 INSERT INTO public.retention_case_events(case_id,action,status,evidence,created_at) VALUES(cid,'request_received','pending_verification',json_build_object('account_lookup',false,'service_changes',0),clock_timestamp());
 RETURN jsonb_build_object('received',true,'reference',p_ref,'message','Request received for identity verification and review. No service or billing changes have been made.');
END; $$;
REVOKE ALL ON FUNCTION public.noc_cancel_config(),public.noc_cancel_replay(text,text),public.noc_cancel_submit(text,text,text,text,text,text,date,text,integer),public.noc_retention_guard(),public.noc_retention_immutable() FROM PUBLIC;
DO $$ BEGIN
IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='noc_app') THEN
 GRANT SELECT,INSERT,UPDATE ON public.retention_reasons,public.retention_policies,public.retention_cases,public.retention_case_services,public.retention_competitors,public.retention_offers TO noc_app;
 GRANT SELECT,INSERT ON public.retention_case_events,public.retention_observations,public.retention_cohorts,public.retention_imports TO noc_app;
 GRANT USAGE,SELECT ON SEQUENCE public.retention_policies_id_seq,public.retention_cases_id_seq,public.retention_case_services_id_seq,public.retention_case_events_id_seq,public.retention_competitors_id_seq,public.retention_offers_id_seq,public.retention_observations_id_seq,public.retention_cohorts_id_seq,public.retention_imports_id_seq TO noc_app;
END IF;
IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='noc_signup') THEN
 GRANT EXECUTE ON FUNCTION public.noc_cancel_config(),public.noc_cancel_replay(text,text),public.noc_cancel_submit(text,text,text,text,text,text,date,text,integer) TO noc_signup;
END IF;
END; $$;
""")


def downgrade():
    raise RuntimeError(
        "Restore an independently verified backup; never delete cancellation history automatically"
    )
