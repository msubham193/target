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
Tag = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50), AfterValidator(clean_text)
]
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
