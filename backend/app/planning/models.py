from sqlalchemy import (
    Column as C,
    Integer as I,
    String as S,
    Float as F,
    DateTime as D,
    JSON,
    ForeignKey as FK,
    CheckConstraint as CK,
    UniqueConstraint as U,
    Index,
    Boolean as B,
)
from app.core.database import Base
from app.field.models import now, uid


class Dataset(Base):
    __tablename__ = "planning_datasets"
    __table_args__ = (
        CK("kind IN ('ground','surface')", name="ck_planning_dataset_kind"),
        CK(
            "state IN ('queued','processing','ready','failed','retired')",
            name="ck_planning_dataset_state",
        ),
        Index("ix_planning_bounds", "west", "east", "south", "north"),
    )
    id = C(I, primary_key=True)
    identifier = C(S(36), unique=True, nullable=False, default=uid)
    name = C(S(120), nullable=False)
    sha256 = C(S(64), unique=True, nullable=False)
    storage_key = C(S(100), nullable=False)
    format = C(S(10), nullable=False)
    kind = C(S(20), nullable=False)
    units = C(S(20), nullable=False)
    vertical_datum = C(S(100), nullable=False)
    attribution = C(S(1000), nullable=False)
    acquired_on = C(S(10), nullable=False)
    state = C(S(20), nullable=False, default="queued", index=True)
    bytes = C(I, nullable=False)
    metadata_json = C(JSON, nullable=False, default=dict)
    west = C(F)
    east = C(F)
    south = C(F)
    north = C(F)
    created_by = C(I, FK("users.id"), nullable=False)
    created_at = C(D(timezone=True), nullable=False, default=now)


class Tile(Base):
    __tablename__ = "planning_tiles"
    __table_args__ = (
        U("dataset_id", "column", "row", name="uq_planning_tile"),
        Index("ix_planning_tile_bounds", "west", "east", "south", "north"),
    )
    id = C(I, primary_key=True)
    dataset_id = C(I, FK("planning_datasets.id"), nullable=False, index=True)
    column = C(I, nullable=False)
    row = C(I, nullable=False)
    west = C(F, nullable=False)
    east = C(F, nullable=False)
    south = C(F, nullable=False)
    north = C(F, nullable=False)


class Profile(Base):
    __tablename__ = "planning_profiles"
    id = C(I, primary_key=True)
    name = C(S(120), nullable=False, unique=True)
    manufacturer = C(S(100), nullable=False)
    model = C(S(100), nullable=False)
    technology = C(S(30), nullable=False)
    parameters = C(JSON, nullable=False)
    evidence = C(S(2000), nullable=False)
    created_by = C(I, FK("users.id"), nullable=False)
    created_at = C(D(timezone=True), nullable=False, default=now)


class Scenario(Base):
    __tablename__ = "planning_scenarios"
    id = C(I, primary_key=True)
    name = C(S(120), nullable=False)
    inputs = C(JSON, nullable=False)
    fingerprint = C(S(64), nullable=False, index=True)
    created_by = C(I, FK("users.id"), nullable=False)
    created_at = C(D(timezone=True), nullable=False, default=now)


class Job(Base):
    __tablename__ = "planning_jobs"
    __table_args__ = (
        CK(
            "state IN ('queued','running','completed','failed','cancelled')",
            name="ck_planning_job_state",
        ),
        CK("progress>=0 AND progress<=100", name="ck_planning_job_progress"),
        Index("ix_planning_queue", "state", "lease_until"),
    )
    id = C(I, primary_key=True)
    kind = C(S(20), nullable=False)
    dataset_id = C(I, FK("planning_datasets.id"), nullable=True)
    scenario_id = C(I, FK("planning_scenarios.id"), nullable=True)
    state = C(S(20), nullable=False, default="queued")
    progress = C(I, nullable=False, default=0)
    attempts = C(I, nullable=False, default=0)
    lease_token = C(S(36))
    lease_until = C(D(timezone=True))
    cancel_requested = C(B, nullable=False, default=False)
    result = C(JSON, nullable=False, default=dict)
    error_code = C(S(80))
    created_by = C(I, FK("users.id"), nullable=False)
    created_at = C(D(timezone=True), nullable=False, default=now)
    finished_at = C(D(timezone=True))


class Calibration(Base):
    __tablename__ = "planning_calibrations"
    __table_args__ = (
        U(
            "job_id",
            "point_index",
            "emitter_index",
            "rf_point_id",
            name="uq_planning_calibration",
        ),
        U(
            "job_id",
            "point_index",
            "emitter_index",
            "survey_id",
            name="uq_planning_survey_calibration",
        ),
        CK(
            "num_nonnulls(rf_point_id,survey_id)=1",
            name="ck_planning_calibration_source",
        ),
    )
    id = C(I, primary_key=True)
    job_id = C(I, FK("planning_jobs.id"), nullable=False, index=True)
    point_index = C(I, nullable=False)
    emitter_index = C(I, nullable=False)
    rf_point_id = C(I, nullable=True)
    source_snapshot = C(JSON, nullable=False, default=dict)
    survey_id = C(I, FK("signup_surveys.id"), nullable=True)
    predicted_dbm = C(F, nullable=False)
    observed_dbm = C(F, nullable=False)
    error_db = C(F, nullable=False)
    evidence = C(S(2000), nullable=False)
    created_by = C(I, FK("users.id"), nullable=False)
    created_at = C(D(timezone=True), nullable=False, default=now)
