from sqlalchemy import (
    Column as C,
    Integer as I,
    String as S,
    Boolean as B,
    DateTime as D,
    JSON,
    ForeignKey as FK,
    UniqueConstraint as U,
    CheckConstraint as CK,
    Index,
)
from app.core.database import Base
from app.field.models import now, uid


class Policy(Base):
    __tablename__ = "customer_status_policy"
    id = C(I, primary_key=True)
    enabled = C(B, nullable=False, default=False)
    baseline_status = C(S(40), nullable=False, default="investigating")
    baseline_approved_at = C(D(timezone=True))
    stability_seconds = C(I, nullable=False, default=300)
    minimum_samples = C(I, nullable=False, default=3)
    maximum_gap_seconds = C(I, nullable=False, default=120)
    revision = C(I, nullable=False, default=1)
    updated_by = C(I, FK("users.id"), nullable=False)


class Area(Base):
    __tablename__ = "customer_status_areas"
    id = C(I, primary_key=True)
    identifier = C(S(36), unique=True, nullable=False, default=uid)
    label = C(S(100), nullable=False)
    public_details = C(JSON, nullable=False, default=dict, server_default="{}")
    active = C(B, nullable=False, default=True)
    revision = C(I, nullable=False, default=1)
    created_by = C(I, FK("users.id"), nullable=False)


class Publication(Base):
    __tablename__ = "customer_status_publications"
    __table_args__ = (
        CK(
            "(incident_id IS NULL) <> (maintenance_id IS NULL)", name="ck_status_source"
        ),
    )
    id = C(I, primary_key=True)
    identifier = C(S(36), unique=True, nullable=False, default=uid)
    incident_id = C(I, FK("incidents.id"), unique=True)
    maintenance_id = C(I, FK("notification_maintenance.id"), unique=True)
    work_order_id = C(I, FK("field_work_orders.id"))
    schedule_id = C(I, FK("maintenance_schedules.id"))
    service_affecting = C(B, nullable=False, default=False)
    published_revision_id = C(
        I,
        FK(
            "customer_status_revisions.id",
            use_alter=True,
            name="fk_status_current_revision",
        ),
    )
    version = C(I, nullable=False, default=1)
    created_by = C(I, FK("users.id"), nullable=False)
    created_at = C(D(timezone=True), nullable=False, default=now)


class Recovery(Base):
    __tablename__ = "customer_status_recoveries"
    id = C(I, primary_key=True)
    publication_id = C(
        I, FK("customer_status_publications.id"), nullable=False, index=True
    )
    source_digest = C(S(64), nullable=False)
    policy_revision = C(I, nullable=False)
    evidence = C(JSON, nullable=False)
    verified = C(B, nullable=False)
    created_at = C(D(timezone=True), nullable=False, default=now)
    created_by = C(I, FK("users.id"), nullable=False)


class Revision(Base):
    __tablename__ = "customer_status_revisions"
    __table_args__ = (
        U("publication_id", "number", name="uq_status_revision"),
        Index("ix_status_revision_state_time", "state", "created_at"),
    )
    id = C(I, primary_key=True)
    publication_id = C(
        I, FK("customer_status_publications.id"), nullable=False, index=True
    )
    number = C(I, nullable=False)
    base_version = C(I, nullable=False)
    source_digest = C(S(64), nullable=False)
    payload = C(JSON, nullable=False)
    recovery_id = C(I, FK("customer_status_recoveries.id"))
    withdraw = C(B, nullable=False, default=False)
    state = C(S(30), nullable=False, default="draft")
    created_by = C(I, FK("users.id"), nullable=False)
    approved_by = C(I, FK("users.id"))
    approved_at = C(D(timezone=True))
    published_at = C(D(timezone=True))
    created_at = C(D(timezone=True), nullable=False, default=now)


class Event(Base):
    __tablename__ = "customer_status_events"
    id = C(I, primary_key=True)
    publication_id = C(I, FK("customer_status_publications.id"), index=True)
    revision_id = C(I, FK("customer_status_revisions.id"))
    action = C(S(50), nullable=False)
    reason = C(S(1000), nullable=False)
    actor_id = C(I, FK("users.id"), nullable=False)
    created_at = C(D(timezone=True), nullable=False, default=now)


class Message(Base):
    __tablename__ = "customer_status_messages"
    id = C(I, primary_key=True)
    publication_id = C(
        I, FK("customer_status_publications.id"), nullable=False, index=True
    )
    plan_id = C(I, FK("notification_plans.id"), nullable=False, unique=True)
    recovery_id = C(I, FK("customer_status_recoveries.id"))
    created_by = C(I, FK("users.id"), nullable=False)
    created_at = C(D(timezone=True), nullable=False, default=now)


class PublicBudget(Base):
    __tablename__ = "customer_status_public_budgets"
    key = C(S(64), primary_key=True)
    minute = C(D(timezone=True), primary_key=True)
    count = C(I, nullable=False)
