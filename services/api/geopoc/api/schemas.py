import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints


def clean_text(value: str) -> str:
    """Reject text PostgreSQL cannot store."""
    if "\x00" in value:
        raise ValueError("must not contain NUL characters")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("must be valid Unicode text") from exc
    return value


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


Name = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200), AfterValidator(clean_text)
]
Description = Annotated[str, StringConstraints(max_length=5000), AfterValidator(clean_text)]
Tag = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50), AfterValidator(clean_text)]
Tags = Annotated[list[Tag], Field(max_length=20), AfterValidator(_dedupe)]
DisplayName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80), AfterValidator(clean_text)
]
CrsId = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
SourcePreference = Literal["local_only", "allow_online"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- workspace ---------------------------------------------------------------------------------------


class ProfileCreate(StrictModel):
    display_name: DisplayName


class ProfileOut(BaseModel):
    id: uuid.UUID
    display_name: str
    created_at: datetime


class ProfileList(BaseModel):
    items: list[ProfileOut]
    notice: str


# --- projects ----------------------------------------------------------------------------------------


class AoiInput(StrictModel):
    geometry: dict[str, Any] = Field(
        description="GeoJSON Point, Polygon or MultiPolygon. Coordinates are x, y in the stated CRS "
        "(easting or longitude first)."
    )
    crs_id: CrsId = Field(description="Complete CRS identifier as an authority code, e.g. EPSG:4326.")


class ProjectCreate(StrictModel):
    name: Name
    description: Description | None = None
    tags: Tags = []
    aoi: AoiInput | None = None
    source_preference: SourcePreference = "local_only"


class ProjectUpdate(StrictModel):
    name: Name | None = None
    description: Description | None = None
    tags: Tags | None = None
    aoi: AoiInput | None = Field(default=None, description="Send null to remove the AOI.")
    source_preference: SourcePreference | None = None
    archived: bool | None = None


class AoiOut(BaseModel):
    geometry_4326: dict[str, Any]
    source: dict[str, Any]
    warnings: list[str]


class ProjectOut(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    tags: list[str]
    aoi: AoiOut | None
    source_preference: SourcePreference
    created_by: ProfileOut
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None


class ProjectPage(BaseModel):
    items: list[ProjectOut]
    next_cursor: str | None


# --- assets ------------------------------------------------------------------------------------------

ModalityName = Literal["ground_image", "overhead_raster", "video"]
AssetStatus = Literal["validated", "validated_with_warnings"]


class RasterOut(BaseModel):
    driver: str
    width: int
    height: int
    band_count: int
    dtypes: list[str]
    nodata: list[Any]
    crs_status: Literal["valid", "missing", "invalid"]
    crs_identifier: str | None
    crs_name: str | None
    crs_wkt: str | None
    georeferenced: bool = Field(
        description="True only with a valid CRS, a usable geotransform and coordinates that fit the CRS."
    )
    geotransform: list[float] | None
    pixel_size_x: float | None
    pixel_size_y: float | None
    pixel_units: str | None
    bounds_native: dict[str, Any] | None
    footprint_4326: dict[str, Any] | None = Field(
        description="GeoJSON outline (longitude, latitude). Null unless the raster is validly georeferenced."
    )


class VideoOut(BaseModel):
    container: str
    video_codec: str | None
    width: int
    height: int
    duration_ms: int | None
    avg_frame_rate: str | None
    frame_rate: float | None
    time_base: str | None
    frame_count: int | None
    creation_time: datetime | None
    has_audio: bool


class AssetOut(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    original_filename: str
    modality: ModalityName
    detected_format: str
    media_type: str
    size_bytes: int
    sha256: str
    status: AssetStatus
    duplicate_of_asset_id: uuid.UUID | None
    quality_flags: list[str]
    unknowns: dict[str, Any] = Field(description="Field name -> why its value is unknown. Never guessed.")
    metadata: dict[str, Any]
    acquisition_time: datetime | None
    uploaded_by: ProfileOut
    ingested_at: datetime
    raster: RasterOut | None
    video: VideoOut | None


class AssetUploadOut(BaseModel):
    asset: AssetOut
    duplicate: bool = Field(description="True when this project already holds an asset with identical content.")
    duplicate_of_asset_id: uuid.UUID | None
    stored_original_reused: bool = Field(
        description="True when identical content was already stored and no second copy was written."
    )
    message: str


class AssetPage(BaseModel):
    items: list[AssetOut]
    next_cursor: str | None


class IngestionRule(BaseModel):
    modality: ModalityName
    label: str
    extensions: list[str]
    max_bytes: int


class IngestionRules(BaseModel):
    rules: list[IngestionRule]
    notes: list[str]


# --- CRS ---------------------------------------------------------------------------------------------


class CrsAxis(BaseModel):
    abbreviation: str
    direction: str
    unit: str


class CrsAreaOfUse(BaseModel):
    name: str | None
    west: float
    south: float
    east: float
    north: float


class CrsOut(BaseModel):
    identifier: str
    name: str
    kind: str
    units: list[str]
    axis_order: list[CrsAxis]
    area_of_use: CrsAreaOfUse | None
    deprecated: bool


class CrsSearchResult(BaseModel):
    items: list[CrsOut]
    registry: str


# --- health ------------------------------------------------------------------------------------------


class HealthOut(BaseModel):
    status: Literal["ok", "unavailable"]
    database: dict[str, Any]
    storage: dict[str, Any]
    worker: dict[str, Any]
    versions: dict[str, str]
    online: dict[str, Any]
    bind_host: str


class ModelHealthOut(BaseModel):
    manifest_found: bool
    models: list[dict[str, Any]]
    errors: list[str]
    llm_runtime: dict[str, Any]
