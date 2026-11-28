"""Email extensions of the canonical NOC96 outbox; no public table grants."""

from alembic import op

revision = "0035_unified_communications"
down_revision = "0034_customer_retention"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "\nCREATE TABLE notification_email_providers (\n\tname VARCHAR(20) NOT NULL, \n\tconfig JSON NOT NULL, \n\trevision INTEGER NOT NULL, \n\tlive_authorized BOOLEAN NOT NULL, \n\tauthorization_reference VARCHAR(120), \n\twebhook_public_key TEXT, \n\thealth VARCHAR(40) NOT NULL, \n\tlast_submission_at TIMESTAMP WITH TIME ZONE, \n\tlast_callback_at TIMESTAMP WITH TIME ZONE, \n\tPRIMARY KEY (name)\n)\n\n"
    )
    op.execute(
        "\nCREATE TABLE notification_email_routing (\n\tid SERIAL NOT NULL, \n\tconfig JSON NOT NULL, \n\trevision INTEGER NOT NULL, \n\tPRIMARY KEY (id)\n)\n\n"
    )
    op.execute(
        "\nCREATE TABLE notification_email_suppressions (\n\tcontact_hash VARCHAR(64) NOT NULL, \n\treason VARCHAR(30) NOT NULL, \n\tevidence VARCHAR(120) NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (contact_hash)\n)\n\n"
    )
    op.execute(
        "\nCREATE TABLE notification_email_templates (\n\tid SERIAL NOT NULL, \n\tkey VARCHAR(60) NOT NULL, \n\trevision INTEGER NOT NULL, \n\tconfig JSON NOT NULL, \n\tcreated_by INTEGER NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_email_template_revision UNIQUE (key, revision), \n\tFOREIGN KEY(created_by) REFERENCES users (id)\n)\n\n"
    )
    op.execute(
        "\nCREATE TABLE notification_email_preferences (\n\tid SERIAL NOT NULL, \n\tsubject_key VARCHAR(60) NOT NULL, \n\tcustomer_id INTEGER, \n\tapplication_id INTEGER, \n\tpurpose VARCHAR(30) NOT NULL, \n\tcontact_hash VARCHAR(64) NOT NULL, \n\tgranted BOOLEAN NOT NULL, \n\treference VARCHAR(120) NOT NULL, \n\tverified_by INTEGER NOT NULL, \n\trevision INTEGER NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_email_subject_purpose UNIQUE (subject_key, purpose), \n\tFOREIGN KEY(customer_id) REFERENCES customer_accounts (id), \n\tFOREIGN KEY(application_id) REFERENCES signup_applications (id), \n\tFOREIGN KEY(verified_by) REFERENCES users (id)\n)\n\n"
    )
    op.execute(
        "CREATE INDEX ix_notification_email_preferences_contact_hash ON notification_email_preferences (contact_hash)"
    )
    op.execute(
        "\nCREATE TABLE notification_email_preference_events (\n\tid SERIAL NOT NULL, \n\tpreference_id INTEGER NOT NULL, \n\trevision INTEGER NOT NULL, \n\tgranted BOOLEAN NOT NULL, \n\tcontact_hash VARCHAR(64) NOT NULL, \n\treference VARCHAR(120) NOT NULL, \n\tactor_id INTEGER NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(preference_id) REFERENCES notification_email_preferences (id), \n\tFOREIGN KEY(actor_id) REFERENCES users (id)\n)\n\n"
    )
    op.execute(
        "CREATE INDEX ix_notification_email_preference_events_preference_id ON notification_email_preference_events (preference_id)"
    )
    op.execute(
        '\nCREATE TABLE notification_email_envelopes (\n\tdelivery_id INTEGER NOT NULL, \n\tprovider VARCHAR(20) NOT NULL, \n\tsource JSON NOT NULL, \n\ttemplate_id INTEGER NOT NULL, \n\tpreference_id INTEGER NOT NULL, \n\tcontact_hash VARCHAR(64) NOT NULL, \n\tciphertext TEXT NOT NULL, \n\t"authorization" JSON, \n\tcorrelation VARCHAR(64), \n\tPRIMARY KEY (delivery_id), \n\tFOREIGN KEY(delivery_id) REFERENCES notification_deliveries (id), \n\tFOREIGN KEY(provider) REFERENCES notification_email_providers (name), \n\tFOREIGN KEY(template_id) REFERENCES notification_email_templates (id), \n\tFOREIGN KEY(preference_id) REFERENCES notification_email_preferences (id), \n\tUNIQUE (correlation)\n)\n\n'
    )
    op.execute(
        "CREATE INDEX ix_notification_email_envelopes_contact_hash ON notification_email_envelopes (contact_hash)"
    )
    op.execute(
        "\nCREATE TABLE notification_email_events (\n\tkey VARCHAR(64) NOT NULL, \n\tdelivery_id INTEGER NOT NULL, \n\tkind VARCHAR(30) NOT NULL, \n\tbody_hash VARCHAR(64) NOT NULL, \n\tprovider_message_id VARCHAR(255), \n\treceived_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (key), \n\tFOREIGN KEY(delivery_id) REFERENCES notification_deliveries (id)\n)\n\n"
    )
    op.execute(
        "CREATE INDEX ix_notification_email_events_delivery_id ON notification_email_events (delivery_id)"
    )
    op.execute(
        "ALTER TABLE notification_attempts ADD COLUMN email_provider VARCHAR(20) REFERENCES notification_email_providers(name)"
    )
    op.execute(
        "CREATE INDEX ix_notification_attempts_email_provider ON notification_attempts (email_provider)"
    )
    op.execute(
        "CREATE TRIGGER notification_email_templates_immutable BEFORE UPDATE OR DELETE ON notification_email_templates FOR EACH ROW EXECUTE FUNCTION public.noc_retention_immutable()"
    )
    op.execute(
        "CREATE TRIGGER notification_email_preference_events_immutable BEFORE UPDATE OR DELETE ON notification_email_preference_events FOR EACH ROW EXECUTE FUNCTION public.noc_retention_immutable()"
    )
    op.execute(
        "CREATE TRIGGER notification_email_events_immutable BEFORE UPDATE OR DELETE ON notification_email_events FOR EACH ROW EXECUTE FUNCTION public.noc_retention_immutable()"
    )

    op.create_check_constraint(
        "ck_email_preference_subject",
        "notification_email_preferences",
        "(customer_id IS NULL) <> (application_id IS NULL)",
    )


def downgrade():
    op.execute("DROP INDEX ix_notification_attempts_email_provider")
    op.execute("ALTER TABLE notification_attempts DROP COLUMN email_provider")
    op.execute("DROP TABLE notification_email_events")
    op.execute("DROP TABLE notification_email_envelopes")
    op.execute("DROP TABLE notification_email_preference_events")
    op.execute("DROP TABLE notification_email_preferences")
    op.execute("DROP TABLE notification_email_templates")
    op.execute("DROP TABLE notification_email_suppressions")
    op.execute("DROP TABLE notification_email_routing")
    op.execute("DROP TABLE notification_email_providers")
