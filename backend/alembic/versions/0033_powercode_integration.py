"""Reviewed Powercode contracts, package mapping and durable provisioning evidence."""

from alembic import op
import sqlalchemy as sa

revision = "0033_powercode_integration"
down_revision = "0032_signup_acquisition"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "customer_services", sa.Column("package_id", sa.String(120), nullable=True)
    )
    op.add_column(
        "customer_services",
        sa.Column("monthly_recurring", sa.Numeric(12, 2), nullable=True),
    )
    op.add_column(
        "customer_services", sa.Column("currency", sa.String(3), nullable=True)
    )
    op.add_column(
        "customer_services",
        sa.Column(
            "revenue_basis", sa.String(20), nullable=False, server_default="unavailable"
        ),
    )
    op.create_index(
        "ix_customer_services_package_id", "customer_services", ["package_id"]
    )
    op.execute(
        "CREATE TABLE powercode_connection (\n\tid INTEGER NOT NULL, \n\tendpoint VARCHAR(500), \n\tallowed_origin VARCHAR(500), \n\tauth_method VARCHAR(40) NOT NULL, \n\temergency_disabled BOOLEAN NOT NULL, \n\trevision INTEGER NOT NULL, \n\tupdated_by INTEGER NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT ck_powercode_single_connection CHECK (id=1), \n\tFOREIGN KEY(updated_by) REFERENCES users (id)\n);"
    )
    op.execute(
        "CREATE TABLE powercode_packages (\n\texternal_id VARCHAR(120) NOT NULL, \n\tname VARCHAR(160) NOT NULL, \n\ttechnology VARCHAR(20) NOT NULL, \n\taudience VARCHAR(20) NOT NULL, \n\tavailable BOOLEAN NOT NULL, \n\tcurrency VARCHAR(3), \n\tmonthly NUMERIC(12, 2), \n\tinstallation NUMERIC(12, 2), \n\tdownload_mbps NUMERIC(12, 2), \n\tupload_mbps NUMERIC(12, 2), \n\tsource_updated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tlast_synced_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (external_id)\n);"
    )
    op.execute(
        "CREATE TABLE powercode_package_mappings (\n\tplan_id INTEGER NOT NULL, \n\tpackage_id VARCHAR(120) NOT NULL, \n\tpackage_digest VARCHAR(64) NOT NULL, \n\tplan_revision INTEGER NOT NULL, \n\tapproved_by INTEGER NOT NULL, \n\tapproved_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (plan_id), \n\tFOREIGN KEY(plan_id) REFERENCES signup_plans (id), \n\tFOREIGN KEY(package_id) REFERENCES powercode_packages (external_id), \n\tFOREIGN KEY(approved_by) REFERENCES users (id)\n);"
    )
    op.execute(
        "CREATE TABLE powercode_service_history (\n\tid SERIAL NOT NULL, \n\tservice_id INTEGER NOT NULL, \n\tprevious_status VARCHAR(20), \n\tstatus VARCHAR(20) NOT NULL, \n\tsnapshot_id VARCHAR(120) NOT NULL, \n\tsource_updated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\trecorded_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(service_id) REFERENCES customer_services (id)\n);"
    )
    op.execute(
        "CREATE INDEX ix_powercode_service_history_service_id ON powercode_service_history (service_id);"
    )
    op.execute(
        "CREATE TABLE powercode_provisions (\n\tid SERIAL NOT NULL, \n\tapplication_id INTEGER NOT NULL, \n\tstatus VARCHAR(40) NOT NULL, \n\tpreview_digest VARCHAR(64) NOT NULL, \n\tpreview_ciphertext TEXT NOT NULL, \n\tapplication_revision INTEGER NOT NULL, \n\tquote_id INTEGER NOT NULL, \n\tmapping_digest VARCHAR(64) NOT NULL, \n\tattestations JSON NOT NULL, \n\tapproved_by INTEGER, \n\tapproved_at TIMESTAMP WITH TIME ZONE, \n\texternal_account_id VARCHAR(120), \n\texternal_service_id VARCHAR(120), \n\tactivation_approved_by INTEGER, \n\tactivation_approved_at TIMESTAMP WITH TIME ZONE, \n\trevision INTEGER NOT NULL, \n\terror_code VARCHAR(40), \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT ck_powercode_provision_state CHECK (status IN ('pending_review','approved','queued','submitted','account_created','service_assigned','activation_pending','completed','failed','reconciliation_required','cancelled')), \n\tUNIQUE (application_id), \n\tFOREIGN KEY(application_id) REFERENCES signup_applications (id), \n\tFOREIGN KEY(quote_id) REFERENCES signup_quotes (id), \n\tFOREIGN KEY(approved_by) REFERENCES users (id), \n\tFOREIGN KEY(activation_approved_by) REFERENCES users (id)\n);"
    )
    op.execute(
        "CREATE INDEX ix_powercode_provision_queue ON powercode_provisions (status, id);"
    )
    op.execute(
        "CREATE TABLE powercode_attempts (\n\tid SERIAL NOT NULL, \n\tprovision_id INTEGER NOT NULL, \n\toperation_key VARCHAR(36) NOT NULL, \n\toperation VARCHAR(30) NOT NULL, \n\toutcome VARCHAR(40) NOT NULL, \n\texternal_id VARCHAR(120), \n\terror_code VARCHAR(40), \n\tstarted_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tended_at TIMESTAMP WITH TIME ZONE, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(provision_id) REFERENCES powercode_provisions (id), \n\tUNIQUE (operation_key)\n);"
    )
    op.execute(
        "CREATE INDEX ix_powercode_attempts_provision_id ON powercode_attempts (provision_id);"
    )
    op.execute(
        "CREATE TABLE powercode_provision_events (\n\tid SERIAL NOT NULL, \n\tprovision_id INTEGER NOT NULL, \n\tactor_id INTEGER, \n\taction VARCHAR(40) NOT NULL, \n\tstatus VARCHAR(40) NOT NULL, \n\tevidence JSON NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(provision_id) REFERENCES powercode_provisions (id), \n\tFOREIGN KEY(actor_id) REFERENCES users (id)\n);"
    )
    op.execute(
        "CREATE INDEX ix_powercode_provision_events_provision_id ON powercode_provision_events (provision_id);"
    )
    op.execute(
        "\nCREATE FUNCTION public.powercode_history_guard() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public AS $f$\nBEGIN RAISE EXCEPTION 'Powercode evidence is append only' USING ERRCODE='23514'; END $f$;\nCREATE TRIGGER powercode_status_immutable BEFORE UPDATE OR DELETE ON public.powercode_service_history FOR EACH ROW EXECUTE FUNCTION public.powercode_history_guard();\nCREATE TRIGGER powercode_event_immutable BEFORE UPDATE OR DELETE ON public.powercode_provision_events FOR EACH ROW EXECUTE FUNCTION public.powercode_history_guard();\nCREATE FUNCTION public.powercode_attempt_guard() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public AS $f$\nBEGIN\nIF TG_OP='DELETE' OR OLD.ended_at IS NOT NULL THEN RAISE EXCEPTION 'Final provider attempt evidence is immutable' USING ERRCODE='23514'; END IF;\nIF (NEW.provision_id,NEW.operation_key,NEW.operation,NEW.started_at) IS DISTINCT FROM (OLD.provision_id,OLD.operation_key,OLD.operation,OLD.started_at) THEN RAISE EXCEPTION 'Provider attempt identity immutable' USING ERRCODE='23514'; END IF;\nRETURN NEW; END $f$;\nCREATE TRIGGER powercode_attempt_immutable BEFORE UPDATE OR DELETE ON public.powercode_attempts FOR EACH ROW EXECUTE FUNCTION public.powercode_attempt_guard();\nCREATE FUNCTION public.powercode_preview_guard() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public AS $f$\nBEGIN\nIF TG_OP='DELETE' THEN RAISE EXCEPTION 'Provisioning history retained' USING ERRCODE='23514'; END IF;\nIF (NEW.application_id,NEW.preview_digest,NEW.preview_ciphertext,NEW.application_revision,NEW.quote_id,NEW.mapping_digest,NEW.attestations::jsonb) IS DISTINCT FROM (OLD.application_id,OLD.preview_digest,OLD.preview_ciphertext,OLD.application_revision,OLD.quote_id,OLD.mapping_digest,OLD.attestations::jsonb) THEN RAISE EXCEPTION 'Reviewed provisioning preview is immutable' USING ERRCODE='23514'; END IF;\nIF (OLD.external_account_id IS NOT NULL AND OLD.external_account_id IS DISTINCT FROM NEW.external_account_id) OR (OLD.external_service_id IS NOT NULL AND OLD.external_service_id IS DISTINCT FROM NEW.external_service_id) THEN RAISE EXCEPTION 'Verified external identities cannot be overwritten' USING ERRCODE='23514'; END IF;\nRETURN NEW; END $f$;\nCREATE TRIGGER powercode_preview_immutable BEFORE UPDATE OR DELETE ON public.powercode_provisions FOR EACH ROW EXECUTE FUNCTION public.powercode_preview_guard();\nREVOKE ALL ON FUNCTION public.powercode_history_guard(),public.powercode_attempt_guard(),public.powercode_preview_guard() FROM PUBLIC;\nDO $r$ DECLARE r text; t text; BEGIN\nFOREACH r IN ARRAY ARRAY['noc_signup','noc_status','noc_slack_callback','noc_sms_callback'] LOOP\n IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname=r) THEN\n  FOREACH t IN ARRAY ARRAY['powercode_connection','powercode_packages','powercode_package_mappings','powercode_service_history','powercode_provisions','powercode_attempts','powercode_provision_events'] LOOP\n   EXECUTE format('REVOKE ALL ON TABLE public.%I FROM %I',t,r);\n  END LOOP;\n END IF;\nEND LOOP; END $r$;\n"
    )


def downgrade():
    raise RuntimeError(
        "Reviewed backup recovery required; historical provisioning evidence must not be dropped"
    )
