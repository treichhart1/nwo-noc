"""Explicit prospect notification subjects and restricted customer quote responses."""

from alembic import op
import sqlalchemy as sa

revision = "0032_signup_acquisition"
down_revision = "0031_wordpress_status"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "equipment_locations", sa.Column("application_id", sa.Integer(), nullable=True)
    )
    op.create_foreign_key(
        "fk_equipment_location_application",
        "equipment_locations",
        "signup_applications",
        ["application_id"],
        ["id"],
    )
    op.create_index(
        "ix_equipment_locations_application_id",
        "equipment_locations",
        ["application_id"],
    )
    # Extend the existing authoritative guard without rewriting migration0019
    # or replacing later technician/lifecycle corrections made in migration0020.
    op.execute("""
DO $patch$ DECLARE definition text; old_rule text := 'IF NEW.kind=''customer_premises'' AND NEW.service_id IS NULL THEN RAISE EXCEPTION ''Reviewed customer reference required'' USING ERRCODE=''23514''; END IF;';
BEGIN
SELECT pg_get_functiondef('public.equipment_context_guard()'::regprocedure) INTO definition;
IF strpos(definition,old_rule)=0 THEN RAISE EXCEPTION 'Inventory guard changed; explicit migration review required'; END IF;
definition:=replace(definition,old_rule,'IF NEW.kind=''customer_premises'' AND (NEW.service_id IS NULL)=(NEW.application_id IS NULL) THEN RAISE EXCEPTION ''Exactly one reviewed customer service or application required'' USING ERRCODE=''23514''; END IF; IF NEW.application_id IS NOT NULL AND (NEW.kind<>''customer_premises'' OR NOT EXISTS(SELECT 1 FROM public.signup_applications a WHERE a.id=NEW.application_id AND a.proposed_site_id=NEW.site_id AND a.redacted_at IS NULL AND a.state IN (''customer_accepted'',''installation_pending'',''installation_scheduled'',''installed'',''activation_pending'',''completed''))) THEN RAISE EXCEPTION ''Accepted application premises required'' USING ERRCODE=''23514''; END IF;');
definition:=replace(definition,'(NEW.kind,NEW.site_id,NEW.service_id) IS DISTINCT FROM (OLD.kind,OLD.site_id,OLD.service_id)','(NEW.kind,NEW.site_id,NEW.service_id,NEW.application_id) IS DISTINCT FROM (OLD.kind,OLD.site_id,OLD.service_id,OLD.application_id)');
EXECUTE definition;
END $patch$;
""")
    op.alter_column("notification_recipients", "customer_id", nullable=True)
    op.add_column(
        "notification_recipients",
        sa.Column("application_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_notification_prospect_application",
        "notification_recipients",
        "signup_applications",
        ["application_id"],
        ["id"],
    )
    op.create_unique_constraint(
        "uq_notification_prospect",
        "notification_recipients",
        ["plan_id", "application_id"],
    )
    op.create_index(
        "ix_notification_recipient_contact",
        "notification_recipients",
        ["contact_hash", "id"],
    )
    op.create_check_constraint(
        "ck_notification_recipient_subject",
        "notification_recipients",
        "(customer_id IS NULL) <> (application_id IS NULL)",
    )
    op.create_table(
        "notification_contact_suppressions",
        sa.Column("contact_hash", sa.String(64), primary_key=True),
        sa.Column("reason", sa.String(40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.add_column(
        "signup_quotes", sa.Column("access_hash", sa.String(64), nullable=True)
    )
    op.add_column(
        "signup_quotes", sa.Column("access_ciphertext", sa.Text(), nullable=True)
    )
    op.create_unique_constraint(
        "uq_signup_quote_access_hash", "signup_quotes", ["access_hash"]
    )
    op.create_table(
        "signup_quote_responses",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "quote_id",
            sa.Integer(),
            sa.ForeignKey("signup_quotes.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("decision", sa.String(20), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.execute("""
CREATE OR REPLACE FUNCTION public.signup_quote_guard() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
IF TG_OP='DELETE' OR (to_jsonb(NEW)-ARRAY['status','approved_by','issued_at','acceptance_ciphertext','access_hash','access_ciphertext']) IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['status','approved_by','issued_at','acceptance_ciphertext','access_hash','access_ciphertext']) THEN RAISE EXCEPTION 'Quote contents immutable' USING ERRCODE='23514'; END IF; RETURN NEW; END $$;
CREATE TRIGGER signup_quote_response_immutable BEFORE UPDATE OR DELETE ON public.signup_quote_responses FOR EACH ROW EXECUTE FUNCTION public.field_append_only();
CREATE FUNCTION public.noc_signup_quote_read(p_hash text) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v record;
BEGIN
IF p_hash !~ '^[a-f0-9]{64}$' THEN RETURN jsonb_build_object('available',false); END IF;
SELECT q.* INTO v FROM public.signup_quotes q JOIN public.signup_applications a ON a.id=q.application_id
WHERE q.access_hash=p_hash AND q.approved_by IS NOT NULL AND q.issued_at IS NOT NULL AND q.status IN ('issued','accepted','rejected') AND q.expires_at>clock_timestamp() AND a.redacted_at IS NULL AND a.wrapped_key IS NOT NULL
AND q.number=(SELECT max(x.number) FROM public.signup_quotes x WHERE x.application_id=a.id)
AND (a.state='quote_sent' OR q.status IN ('accepted','rejected'));
IF NOT FOUND THEN RETURN jsonb_build_object('available',false); END IF;
RETURN jsonb_build_object('available',true,'number',v.number,'expires_at',v.expires_at,'status',v.status,'terms',jsonb_build_object('plan_name',v.snapshot::jsonb->'plan_name','currency',v.snapshot::jsonb->'currency','monthly',v.snapshot::jsonb->'monthly','installation',v.snapshot::jsonb->'installation','equipment',v.snapshot::jsonb->'equipment','construction',v.snapshot::jsonb->'construction','discount',COALESCE(v.snapshot::jsonb->'discount','0'::jsonb),'optional_services',v.snapshot::jsonb->'optional_services'));
END $$;
CREATE FUNCTION public.noc_signup_quote_respond(p_hash text,p_decision text) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v record; a record; prior record; proof jsonb; stamp timestamptz;
BEGIN
IF p_hash !~ '^[a-f0-9]{64}$' OR p_decision NOT IN ('accepted','rejected') THEN RETURN jsonb_build_object('available',false); END IF;
IF NOT EXISTS(SELECT 1 FROM public.signup_quotes q WHERE q.access_hash=p_hash) THEN RETURN jsonb_build_object('available',false); END IF;
PERFORM pg_advisory_xact_lock(1000100);PERFORM pg_advisory_xact_lock(1000101);
SELECT q.* INTO v FROM public.signup_quotes q WHERE q.access_hash=p_hash FOR UPDATE;
IF NOT FOUND THEN RETURN jsonb_build_object('available',false); END IF;
SELECT x.* INTO a FROM public.signup_applications x WHERE x.id=v.application_id FOR UPDATE;
stamp:=clock_timestamp();
IF a.redacted_at IS NOT NULL OR a.wrapped_key IS NULL OR v.approved_by IS NULL OR v.issued_at IS NULL OR v.expires_at<=stamp OR v.number<>(SELECT max(q.number) FROM public.signup_quotes q WHERE q.application_id=a.id) THEN RETURN jsonb_build_object('available',false); END IF;
SELECT r.* INTO prior FROM public.signup_quote_responses r WHERE r.quote_id=v.id;
IF FOUND THEN
IF prior.decision<>p_decision THEN RETURN jsonb_build_object('available',false,'reason','response_already_recorded'); END IF;
RETURN jsonb_build_object('available',true,'decision',prior.decision,'recorded_at',prior.created_at);
END IF;
IF v.status<>'issued' OR a.state<>'quote_sent' THEN RETURN jsonb_build_object('available',false); END IF;
proof:=jsonb_build_object('method','explicit_bearer_token_and_csrf_and_turnstile','quote_digest',encode(sha256(convert_to(v.snapshot::text,'UTF8')),'hex'),'quote_number',v.number,'decision',p_decision,'at',stamp);
INSERT INTO public.signup_quote_responses(quote_id,decision,evidence,created_at) VALUES(v.id,p_decision,proof,stamp);
UPDATE public.signup_quotes SET status=p_decision WHERE id=v.id;
UPDATE public.signup_applications SET state=CASE WHEN p_decision='accepted' THEN 'customer_accepted' ELSE 'under_review' END,revision=revision+1,updated_at=stamp WHERE id=a.id;
INSERT INTO public.signup_events(application_id,actor_id,action,reason_ciphertext,data,created_at) VALUES(a.id,NULL,'customer_quote_response',NULL,jsonb_build_object('quote_id',v.id,'decision',p_decision),stamp);
RETURN jsonb_build_object('available',true,'decision',p_decision,'recorded_at',stamp);
END $$;
REVOKE ALL ON FUNCTION public.noc_signup_quote_read(text),public.noc_signup_quote_respond(text,text) FROM PUBLIC;
DO $$ BEGIN
IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='noc_app') THEN
GRANT EXECUTE ON FUNCTION public.noc_signup_quote_read(text),public.noc_signup_quote_respond(text,text) TO noc_app;
GRANT SELECT,INSERT,UPDATE,DELETE ON public.notification_contact_suppressions,public.signup_quote_responses TO noc_app;
GRANT USAGE,SELECT ON SEQUENCE public.signup_quote_responses_id_seq TO noc_app;
END IF;
IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='noc_signup') THEN GRANT EXECUTE ON FUNCTION public.noc_signup_quote_read(text),public.noc_signup_quote_respond(text,text) TO noc_signup; END IF;
END $$;
""")


def downgrade():
    raise RuntimeError(
        "Preserve prospect consent and immutable quote responses; use reviewed backup recovery"
    )
