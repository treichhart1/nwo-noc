from datetime import date
from typing import Literal
from pydantic import Field, model_validator
from app.field.contracts import Mutation
from app.customers.contracts import SafeModel


class Parameters(SafeModel):
    frequency_mhz: float | None = Field(None, ge=100, le=100000, allow_inf_nan=False)
    channel_width_mhz: float | None = Field(None, gt=0, le=4000, allow_inf_nan=False)
    tx_power_dbm: float | None = Field(None, ge=-50, le=60, allow_inf_nan=False)
    tx_gain_dbi: float | None = Field(None, ge=-10, le=70, allow_inf_nan=False)
    tx_loss_db: float | None = Field(None, ge=0, le=100, allow_inf_nan=False)
    receiver_sensitivity_dbm: float | None = Field(
        None, ge=-160, le=-10, allow_inf_nan=False
    )
    polarization: Literal["horizontal", "vertical", "dual", "circular"] | None = None


class ProfileInput(Mutation):
    name: str = Field(min_length=2, max_length=120)
    manufacturer: str = Field(min_length=2, max_length=100)
    model: str = Field(min_length=2, max_length=100)
    technology: Literal["wireless", "microwave", "60ghz"]
    parameters: Parameters
    evidence: str = Field(min_length=10, max_length=2000)


class Point(SafeModel):
    latitude: float = Field(ge=-85, le=85, allow_inf_nan=False)
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)
    height_m: float = Field(ge=0, le=1000, allow_inf_nan=False)
    # Explicit surveyed endpoint ground elevation can fill raster endpoint gaps only.
    ground_m: float | None = Field(None, ge=-500, le=9000, allow_inf_nan=False)
    evidence: str = Field(min_length=10, max_length=1000)


class Emitter(Point):
    profile_id: int = Field(ge=1)
    site_id: int | None = Field(None, ge=1)
    sector_id: int | None = Field(None, ge=1)
    device_id: int | None = Field(None, ge=1)
    asset_id: int | None = Field(None, ge=1)
    azimuth_deg: float | None = Field(None, ge=0, lt=360, allow_inf_nan=False)
    horizontal_beamwidth_deg: float | None = Field(
        None, gt=0, le=360, allow_inf_nan=False
    )
    mechanical_downtilt_deg: float | None = Field(
        None, ge=-90, le=90, allow_inf_nan=False
    )
    electrical_downtilt_deg: float | None = Field(
        None, ge=-90, le=90, allow_inf_nan=False
    )
    vertical_beamwidth_deg: float | None = Field(
        None, gt=0, le=180, allow_inf_nan=False
    )
    tower_height_m: float | None = Field(None, ge=0, le=1000, allow_inf_nan=False)

    @model_validator(mode="after")
    def mount(self):
        if self.tower_height_m is not None and self.height_m > self.tower_height_m:
            raise ValueError("Mount height exceeds documented tower height")
        return self


class ScenarioInput(Mutation):
    name: str = Field(min_length=2, max_length=120)
    kind: Literal["link", "coverage"]
    dataset_ids: list[int] = Field(default_factory=list, max_length=8)
    emitters: list[Emitter] = Field(min_length=1, max_length=16)
    target: Point | None = None
    bbox: list[float] | None = Field(None, min_length=4, max_length=4)
    grid_size: int = Field(8, ge=2, le=32)
    receiver_height_m: float = Field(2, ge=0, le=1000, allow_inf_nan=False)
    rx_gain_dbi: float | None = Field(None, ge=-10, le=70, allow_inf_nan=False)
    rx_loss_db: float | None = Field(None, ge=0, le=100, allow_inf_nan=False)
    rx_polarization: Literal["horizontal", "vertical", "dual", "circular"] | None = None
    required_fade_margin_db: float = Field(10, ge=0, le=60, allow_inf_nan=False)
    refraction_k: float = Field(4 / 3, ge=0.5, le=3, allow_inf_nan=False)
    fresnel_fraction: float = Field(0.6, ge=0, le=1, allow_inf_nan=False)
    profile_samples: int = Field(65, ge=3, le=513)
    model: Literal["fspl", "dominant_knife_edge"] = "fspl"
    atmospheric_loss_db_per_km: float | None = Field(
        None, ge=0, le=100, allow_inf_nan=False
    )
    qualification_enabled: bool = False

    @model_validator(mode="after")
    def shape(self):
        import math

        if len(set(self.dataset_ids)) != len(self.dataset_ids) or any(
            x < 1 for x in self.dataset_ids
        ):
            raise ValueError("Unique dataset IDs required")
        if self.kind == "link" and (
            self.target is None or len(self.emitters) != 1 or self.bbox is not None
        ):
            raise ValueError("Link requires one emitter and target")
        if self.kind == "coverage":
            b = self.bbox
            if (
                self.target
                or b is None
                or not all(math.isfinite(x) for x in b)
                or not (-180 <= b[0] < b[2] <= 180 and -85 <= b[1] < b[3] <= 85)
                or b[2] - b[0] > 1
                or b[3] - b[1] > 1
            ):
                raise ValueError(
                    "Local WGS84 west/south/east/north bbox required, maximum one degree"
                )
        rays = (self.grid_size**2 if self.kind == "coverage" else 1) * len(
            self.emitters
        )
        if rays > 4096 or rays * self.profile_samples > 150000:
            raise ValueError(
                "Planning resource budget exceeded; reduce grid, sectors or samples"
            )
        return self


class DatasetInput(Mutation):
    name: str = Field(min_length=2, max_length=120)
    kind: Literal["ground", "surface"]
    units: Literal["metres", "international_feet", "us_survey_feet"]
    vertical_datum: str = Field(min_length=3, max_length=100)
    attribution: str = Field(min_length=10, max_length=1000)
    acquired_on: date
    expected_bbox: list[float] = Field(min_length=4, max_length=4)

    @model_validator(mode="after")
    def bounds(self):
        import math

        a, b, c, d = self.expected_bbox
        if not all(math.isfinite(x) for x in self.expected_bbox) or not (
            -180 <= a < c <= 180 and -85 <= b < d <= 85
        ):
            raise ValueError("Finite WGS84 bounds required")
        if self.acquired_on > date.today():
            raise ValueError("Acquisition date cannot be future")
        return self


class CalibrationInput(Mutation):
    alignment_verified: Literal[True]
    receiver_device_id: int | None = Field(None, ge=1)
    measured_latitude: float = Field(ge=-85, le=85, allow_inf_nan=False)
    measured_longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)
    job_id: int = Field(ge=1)
    point_index: int = Field(ge=0, le=1023)
    emitter_index: int = Field(ge=0, le=15)
    rf_point_id: int | None = Field(None, ge=1)
    survey_id: int | None = Field(None, ge=1)
    observed_dbm: float | None = Field(None, ge=-160, le=20, allow_inf_nan=False)
    evidence: str = Field(min_length=20, max_length=2000)

    @model_validator(mode="after")
    def source(self):
        if (self.rf_point_id is None) == (self.survey_id is None):
            raise ValueError(
                "Exactly one measured RF or installation survey source required"
            )
        return self
