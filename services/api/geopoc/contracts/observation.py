"""Canonical observation contract: the Version 3.0 nested schema (V3 section 4) with the Version 2.0 rules
that must survive (see docs/architecture/observation-schema.md for the field-by-field reconciliation).

Null means genuinely unknown. Nothing here derives a value: in particular no geographic coordinate is ever
computed from a pixel coordinate.
"""

import math
import re
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, BeforeValidator, ConfigDict, Field, model_validator

from geopoc.contracts.flags import normalise_flag
from geopoc.contracts.review import ReviewStatus

SCHEMA_VERSION = "3.0"

Pipeline = Literal["ground_image", "overhead_raster", "video"]
ClaimType = Literal["observed", "inferred", "metadata_derived", "human_entered"]

# The only ways a geographic geometry may come about. Neither applies to a ground photograph or a video.
DERIVATION_METHODS = {"raster_affine_transform", "human_entered"}
PIPELINES_WITHOUT_GEOREFERENCING = {"ground_image", "video"}

CRS_ID_RE = re.compile(r"^[A-Z][A-Z0-9_]*:[A-Za-z0-9_.\-]+$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
CATEGORY_RE = re.compile(r"^[a-z0-9_]+(:[a-z0-9_ .\-]+)?$")
UNCLASSIFIED = "unclassified"


def _utc(value: datetime) -> datetime:
    """V2 rule retained: timestamps are UTC in storage. A timestamp without an offset is ambiguous and refused."""
    if value.tzinfo is None:
        raise ValueError("timestamp must state its UTC offset")
    return value.astimezone(UTC)


UtcDatetime = Annotated[datetime, AfterValidator(_utc)]
Flag = Annotated[str, BeforeValidator(normalise_flag)]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PixelRegion(Strict):
    """A region of an image, with the convention that makes the numbers unambiguous (V2 rule retained).

    Coordinates are in the pixel grid of the stored original exactly as decoded, without applying any EXIF
    orientation. The origin is the top-left corner of the top-left pixel; x grows right, y grows down; values
    are continuous, so pixel (0, 0) covers [0, 1) x [0, 1).
    """

    type: Literal["bbox_xyxy", "polygon"]
    coordinates: list[Any]
    coordinate_space: Literal["source_image_pixels"]
    origin: Literal["top_left"]
    pixel_reference: Literal["corner"]
    image_width: int = Field(gt=0)
    image_height: int = Field(gt=0)

    @model_validator(mode="after")
    def _check_geometry(self) -> "PixelRegion":
        def number(value: Any) -> float:
            if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
                raise ValueError("pixel coordinates must be finite numbers")
            return float(value)

        def inside(x: float, y: float) -> None:
            if not (0 <= x <= self.image_width and 0 <= y <= self.image_height):
                raise ValueError("pixel coordinates lie outside the image")

        if self.type == "bbox_xyxy":
            if len(self.coordinates) != 4:
                raise ValueError("bbox_xyxy needs exactly [x_min, y_min, x_max, y_max]")
            x_min, y_min, x_max, y_max = (number(v) for v in self.coordinates)
            if not (x_min < x_max and y_min < y_max):
                raise ValueError("bbox_xyxy needs x_min < x_max and y_min < y_max")
            inside(x_min, y_min)
            inside(x_max, y_max)
        else:
            if len(self.coordinates) < 3:
                raise ValueError("a polygon needs at least three points")
            for point in self.coordinates:
                if not isinstance(point, list | tuple) or len(point) != 2:
                    raise ValueError("each polygon point must be [x, y]")
                inside(number(point[0]), number(point[1]))
        return self


class Evidence(Strict):
    asset_id: uuid.UUID
    region_px: PixelRegion | None = None
    frame_index: int | None = Field(default=None, ge=0)
    timestamp_ms: int | None = Field(default=None, ge=0)
    source_excerpt_or_metadata_key: str | None = None


class ModelRef(Strict):
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    run_id: uuid.UUID


class Claim(Strict):
    category: str
    description: str = Field(min_length=1, description="Evidence-based text. Replaces the V2 `summary` field.")
    claim_type: ClaimType

    @model_validator(mode="after")
    def _check_category(self) -> "Claim":
        if not CATEGORY_RE.match(self.category):
            raise ValueError("category must be a taxonomy id such as 'coco:person', or 'unclassified'")
        return self


class Score(Strict):
    raw_value: float | None
    semantics: str = Field(min_length=1, description="What the number means for this model.")
    calibrated: bool
    # V2 said "number 0-1". Not every model scores in 0-1, so the range is stated per model instead.
    value_range: tuple[float, float] | None = None

    @model_validator(mode="after")
    def _check(self) -> "Score":
        if self.raw_value is not None:
            if not math.isfinite(self.raw_value):
                raise ValueError("score must be a finite number or null")
            if self.value_range and not (self.value_range[0] <= self.raw_value <= self.value_range[1]):
                raise ValueError("score lies outside the range stated for this model")
        if self.calibrated and self.raw_value is None:
            raise ValueError("a calibrated score needs a value")
        return self


class Uncertainty(Strict):
    value: float = Field(ge=0)
    unit: str = Field(min_length=1)


class Geospatial(Strict):
    geometry: dict[str, Any] | None = None
    crs_id: str | None = None
    coordinate_units: str | None = None
    derivation_method: str | None = None
    uncertainty: Uncertainty | None = None

    @model_validator(mode="after")
    def _all_or_nothing(self) -> "Geospatial":
        if self.geometry is None:
            if any(
                v is not None for v in (self.crs_id, self.coordinate_units, self.derivation_method, self.uncertainty)
            ):
                raise ValueError("without a geometry, CRS, units, method and uncertainty must all be null")
            return self
        # GEO-001: a coordinate is only meaningful with its reference system, units and derivation.
        if not (self.crs_id and self.coordinate_units and self.derivation_method):
            raise ValueError("a geometry needs crs_id, coordinate_units and derivation_method")
        if not CRS_ID_RE.match(self.crs_id):
            raise ValueError("crs_id must be a complete authority code such as EPSG:32643")
        if self.derivation_method not in DERIVATION_METHODS:
            raise ValueError(f"derivation_method must be one of {sorted(DERIVATION_METHODS)}")
        if "type" not in self.geometry or "coordinates" not in self.geometry:
            raise ValueError("geometry must be a GeoJSON geometry")
        return self


class Measurement(Strict):
    name: str = Field(min_length=1)
    value: float | None
    unit: str | None
    method: str | None
    validity: Literal["valid", "estimated", "unavailable"]

    @model_validator(mode="after")
    def _check(self) -> "Measurement":
        if self.validity == "unavailable":
            if self.value is not None:
                raise ValueError("an unavailable measurement has no value")
        elif self.value is None or not math.isfinite(self.value) or not self.unit or not self.method:
            raise ValueError("a measurement needs a finite value, a unit and a method")
        return self


class Review(Strict):
    status: ReviewStatus
    reviewer_note: str | None = None  # V2 rule retained: a human note, kept separate from model output
    reviewed_at: UtcDatetime | None = None
    reviewed_by: str | None = None  # V3 section 5: "record who/what verified it"

    @model_validator(mode="after")
    def _check(self) -> "Review":
        concluded = self.status in (ReviewStatus.VERIFIED, ReviewStatus.REJECTED)
        if concluded and not (self.reviewed_at and self.reviewed_by):
            raise ValueError(f"a {self.status.value} observation must record who concluded it and when")
        if self.status == ReviewStatus.REJECTED and not (self.reviewer_note and self.reviewer_note.strip()):
            raise ValueError("a rejected observation must record the reason")
        if self.status == ReviewStatus.PROPOSED and (self.reviewed_at or self.reviewed_by):
            raise ValueError("a proposed observation has not been reviewed")
        return self


class Provenance(Strict):
    source_checksum: str
    ingested_at: UtcDatetime
    transformations: list[dict[str, Any]] = []
    created_at: UtcDatetime

    @model_validator(mode="after")
    def _check(self) -> "Provenance":
        if not SHA256_RE.match(self.source_checksum):
            raise ValueError("source_checksum must be a lower-case SHA-256")
        return self


class Observation(Strict):
    schema_version: Literal["3.0"] = SCHEMA_VERSION
    observation_id: uuid.UUID
    project_id: uuid.UUID
    asset_id: uuid.UUID
    pipeline: Pipeline
    model: ModelRef | None
    claim: Claim
    score: Score
    # V2 rule retained: at least one source reference (also EVD-001).
    evidence: list[Evidence] = Field(min_length=1)
    geospatial: Geospatial
    measurements: list[Measurement] = []
    quality_flags: list[Flag] = []
    review: Review
    provenance: Provenance

    @model_validator(mode="after")
    def _cross_checks(self) -> "Observation":
        # V2 rule retained: model identity is required for model-generated results.
        if self.claim.claim_type != "human_entered" and self.model is None:
            raise ValueError("model name, version and run are required unless the claim is human_entered")

        if not any(e.asset_id == self.asset_id for e in self.evidence):
            raise ValueError("at least one evidence entry must reference the observation's source asset")

        # A ground photograph or a video has no supported georeferencing method: pixels never become coordinates.
        if self.pipeline in PIPELINES_WITHOUT_GEOREFERENCING and self.geospatial.geometry is not None:
            raise ValueError(f"a {self.pipeline} observation cannot carry geographic coordinates")
        if self.geospatial.geometry is not None and self.geospatial.derivation_method == "human_entered":
            if self.claim.claim_type != "human_entered":
                raise ValueError("a human-entered geometry requires a human_entered claim")

        located = self.geospatial.geometry is not None
        if not located and "NOT_GEOREFERENCED" not in self.quality_flags:
            raise ValueError("an observation without a geometry must carry the NOT_GEOREFERENCED flag")
        if located and "NOT_GEOREFERENCED" in self.quality_flags:
            raise ValueError("NOT_GEOREFERENCED contradicts the geometry")

        model_made = self.claim.claim_type == "inferred"
        if model_made and self.pipeline == "ground_image":
            if not any(e.region_px is not None for e in self.evidence):
                raise ValueError("a model finding in an image must point to an image region")
        if self.pipeline == "video":
            timed = any(e.frame_index is not None or e.timestamp_ms is not None for e in self.evidence)
            if not timed and "TIMESTAMP_UNKNOWN" not in self.quality_flags:
                raise ValueError("a video observation needs a frame or timestamp, or the TIMESTAMP_UNKNOWN flag")

        if self.claim.category == UNCLASSIFIED and "UNCLASSIFIED" not in self.quality_flags:
            raise ValueError("an unclassified observation must carry the UNCLASSIFIED flag")  # V2 rule retained
        if not self.score.calibrated and model_made and "UNCALIBRATED_SCORE" not in self.quality_flags:
            raise ValueError("an uncalibrated model score must carry the UNCALIBRATED_SCORE flag")

        if len(set(self.quality_flags)) != len(self.quality_flags):
            raise ValueError("quality flags must not repeat")
        return self
