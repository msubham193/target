import uuid
from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints

from geopoc.contracts.observation import Observation


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


# --- workspace --------------------------------------------------------------------------------------------------------


class ProfileCreate(StrictModel):
    display_name: DisplayName


class ProfileOut(BaseModel):
    id: uuid.UUID
    display_name: str
    created_at: datetime


class ProfileList(BaseModel):
    items: list[ProfileOut]
    notice: str


# --- projects ---------------------------------------------------------------------------------------------------------


class AoiInput(StrictModel):
    geometry: dict[str, Any] = Field(
        description="GeoJSON Point, Polygon or MultiPolygon. Coordinates are x, y in the stated CRS "
        "(easting or longitude first)."
    )
    crs_id: CrsId = Field(description="Complete CRS identifier as an authority code, e.g. EPSG:4326.")
    transformation_operation: str | None = Field(
        default=None,
        max_length=100,
        description="The datum transformation to WGS 84, where several registered operations apply to this area. "
        "Never needed for WGS 84 or a projection of it.",
    )
    acknowledge_accuracy: bool = Field(
        default=False,
        description="Acknowledge the accuracy of `transformation_operation` (or of the only applicable shift) when it "
        "is worse than 10 m or not stated. It is recorded with the AOI, with who acknowledged it and when. It "
        "applies to this AOI only and only to the operation and accuracy that are refused without it.",
    )


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


class ProjectSummary(BaseModel):
    project_id: uuid.UUID
    assets_by_modality: dict[str, int]
    assets_total: int
    jobs_by_status: dict[str, int]
    observations_by_review_status: dict[str, int]
    observations_total: int
    last_activity_at: datetime = Field(description="Latest of: project change, upload, job creation.")


class ProjectPage(BaseModel):
    items: list[ProjectOut]
    next_cursor: str | None


# --- assets -----------------------------------------------------------------------------------------------------------

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


class PreviewInfo(BaseModel):
    status: Literal["none", "pending", "ready", "failed"]
    variants: list[str] = []
    job_id: uuid.UUID | None = None
    error_code: str | None = None
    error_message: str | None = None


class GeoreferencingSummary(BaseModel):
    """The georeferencing used for a raster now. `raster` above is what the file itself declares."""

    version: int
    source: Literal["file", "recorded"]
    placed: bool
    crs_id: str | None
    crs_name: str | None
    unplaced_reason: str | None


class AssetOut(BaseModel):
    preview: PreviewInfo
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
    raster: RasterOut | None = Field(description="What the file declares, exactly as ingested. Never changed.")
    georeferencing: GeoreferencingSummary | None = None
    video: VideoOut | None
    map_sheet: "MapSheetInfo | None" = Field(
        default=None, description="Present when the asset was taken in as a map sheet."
    )


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
    sidecar_extensions: list[str] = Field(
        default_factory=list,
        description="Sidecar metadata files accepted with this kind of input (rasters only). Reviewed, never applied "
        "on their own.",
    )
    sidecar_max_bytes: int | None = None


class IngestionRules(BaseModel):
    rules: list[IngestionRule]
    notes: list[str]


# --- jobs, runs, models -----------------------------------------------------------------------------------------------

JobStatus = Literal[
    "draft",
    "queued",
    "validating",
    "processing",
    "needs_review",
    "completed",
    "completed_with_warnings",
    "failed",
    "cancelled",
]
JobKind = Literal["preview", "analyze"]


class ModelChoice(StrictModel):
    name: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    version: Annotated[str, StringConstraints(min_length=1, max_length=200)]


class VideoSampling(StrictModel):
    """How a video is sampled. The server applies further limits (its configured minimum interval and frame ceiling)."""

    interval_s: float = Field(gt=0, le=3600, allow_inf_nan=False, description="Seconds between sampled frames.")
    max_frames: int = Field(
        ge=1, le=300, description="The most frames this analysis may take. Never truncates silently."
    )
    start_s: float = Field(default=0.0, ge=0, allow_inf_nan=False, description="Media time of the first sample.")
    end_s: float | None = Field(
        default=None, gt=0, allow_inf_nan=False, description="Media time after which no sample is taken."
    )


class JobCreate(StrictModel):
    kind: JobKind
    asset_id: uuid.UUID
    pipeline: ModalityName | None = Field(default=None, description="Required for an analysis job.")
    model: ModelChoice | None = Field(default=None, description="Required for an analysis job.")
    sampling: VideoSampling | None = Field(
        default=None, description="Required for a video analysis, refused otherwise."
    )
    submit: bool = Field(default=True, description="False saves a draft that is started later.")


class JobError(BaseModel):
    code: str
    message: str | None


class JobEventOut(BaseModel):
    at: datetime
    level: str
    stage: str | None
    message: str


class JobOut(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID | None
    asset_id: uuid.UUID | None
    kind: str
    status: JobStatus
    stage: str | None
    progress: float | None = Field(description="0-1, or null when progress cannot be measured.")
    cancel_requested: bool
    attempt: int
    max_attempts: int
    payload: dict[str, Any]
    result: dict[str, Any] | None
    error: JobError | None
    has_partial_outputs: bool
    retry_of_job_id: uuid.UUID | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class JobDetail(JobOut):
    events: list[JobEventOut]


class RecentJob(JobOut):
    project_name: str | None
    asset_filename: str | None


class RecentJobPage(BaseModel):
    items: list[RecentJob]


class JobPage(BaseModel):
    items: list[JobOut]
    next_cursor: str | None


class RunOut(BaseModel):
    id: uuid.UUID
    job_id: uuid.UUID
    project_id: uuid.UUID
    asset_id: uuid.UUID
    pipeline: str
    model_name: str
    model_version: str
    weights_sha256: str | None
    settings: dict[str, Any]
    device: str | None
    library_versions: dict[str, Any]
    summary: dict[str, Any]
    started_at: datetime
    finished_at: datetime


class ModelOut(BaseModel):
    name: str
    version: str
    pipeline: str
    task: str
    runtime: str
    licence: str
    score_semantics: str
    score_calibrated: bool
    settings: dict[str, Any]
    weights_sha256: str | None
    weights_size_bytes: int | None
    available: bool
    reason_unavailable: str | None
    custom: bool = Field(False, description="Uploaded through the application rather than listed in the manifest")
    original_filename: str | None = None
    added_at: str | None = None


# --- CRS --------------------------------------------------------------------------------------------------------------


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


# --- health -----------------------------------------------------------------------------------------------------------


class HealthOut(BaseModel):
    status: Literal["ok", "unavailable"]
    database: dict[str, Any]
    storage: dict[str, Any]
    worker: dict[str, Any]
    versions: dict[str, str]
    online: dict[str, Any]
    bind_host: str
    # The PROJ environment: network off, the two PROJ copies and whether they agree (see geopoc.geo.projenv).
    proj: dict[str, Any] | None = None


class ModelHealthOut(BaseModel):
    manifest_found: bool
    models: list[ModelOut]
    errors: list[str]
    worker: dict[str, Any]
    llm_runtime: dict[str, Any]


# --- observations and review (Phase 4) --------------------------------------------------------------------------------

ReviewStatusName = Literal["proposed", "needs_review", "verified", "inconclusive", "disputed", "rejected"]
ReviewActionName = Literal["confirm", "reject", "mark_inconclusive", "reopen", "relabel", "note"]
ReviewNote = Annotated[str, StringConstraints(max_length=2000), AfterValidator(clean_text)]
CategoryText = Annotated[str, StringConstraints(max_length=200), AfterValidator(clean_text)]


class AllowedActionOut(BaseModel):
    action: ReviewActionName
    target_status: ReviewStatusName | None
    note_required: bool


class ObservationCurrent(BaseModel):
    """The observation's state now. The model's own output is in `observation` and never changes."""

    status: ReviewStatusName
    category: str
    relabelled: bool
    revision: int
    last_changed_at: datetime | None
    last_changed_by: str | None
    last_note: str | None


class ObservationOut(BaseModel):
    observation: Observation
    current: ObservationCurrent
    asset_filename: str
    open_conflict_ids: list[uuid.UUID]
    possible_duplicate_group_ids: list[uuid.UUID]
    allowed_actions: list[AllowedActionOut]


class RevisionOut(BaseModel):
    revision_no: int
    action: str
    status_before: ReviewStatusName
    status_after: ReviewStatusName
    category_before: str
    category_after: str
    note: str | None
    actor_kind: Literal["reviewer", "system"]
    actor_label: str
    details: dict[str, Any]
    created_at: datetime


class ConflictMemberOut(BaseModel):
    observation_id: uuid.UUID
    asset_id: uuid.UUID
    asset_filename: str
    value: dict[str, Any]
    current_status: ReviewStatusName
    current_category: str


class ConflictOut(BaseModel):
    id: uuid.UUID
    attribute: str
    status: Literal["open", "resolved_documented"]
    rule: str
    details: dict[str, Any]
    created_by_run_id: uuid.UUID | None
    created_at: datetime
    resolved_at: datetime | None
    resolution_note: str | None
    members: list[ConflictMemberOut]


class LinkGroupOut(BaseModel):
    id: uuid.UUID
    kind: str
    method: str
    status: str
    rationale: str
    observation_ids: list[uuid.UUID]


class ObservationDetail(ObservationOut):
    revisions: list[RevisionOut]
    conflicts: list[ConflictOut]
    links: list[LinkGroupOut]


class ReviewRequest(StrictModel):
    action: ReviewActionName
    expected_revision: int = Field(ge=0, description="The revision you reviewed. A mismatch is refused (409).")
    note: ReviewNote | None = None
    category: CategoryText | None = Field(default=None, description="New category; only for `relabel`.")


# --- map layers (Phase 4) ---------------------------------------------------------------------------------------------


class RasterLayerOut(BaseModel):
    """A raster that can be placed on the map because its own CRS and geotransform are valid."""

    asset_id: uuid.UUID
    title: str
    crs_id: str | None
    crs_name: str | None
    placement: Literal["trusted", "user_asserted"] = Field(
        description="trusted: from the file's own metadata. user_asserted: from georeferencing recorded by a person."
    )
    georef_version: int = Field(description="0 for the file's own metadata; otherwise the recorded version used.")
    band_count: int
    dtypes: list[str]
    bounds_4326: list[float] = Field(description="[west, south, east, north] of the footprint, degrees")
    footprint_4326: dict[str, Any]
    tile_url: str | None
    display_unavailable_reason: str | None
    min_zoom: int
    max_zoom: int
    native_zoom: int
    bands_shown: list[int]
    display_stretch: Any
    pixel_size: list[float]
    pixel_units: str | None
    footprint_transformation: dict[str, Any] | None
    transformation: "LayerTransformationOut | None" = None
    map_sheet: "MapSheetLayerOut | None" = None


class ExcludedAssetOut(BaseModel):
    asset_id: uuid.UUID
    title: str
    modality: str
    reason: Literal[
        "NOT_GEOREFERENCED",
        "CRS_MISSING",
        "CRS_INVALID",
        "NO_GEOTRANSFORM",
        "INVALID_GEOREFERENCING",
        "TRANSFORMATION_BLOCKED",
        "ANTIMERIDIAN_CROSSING_UNSUPPORTED",
    ]
    detail: str
    georeferencing_can_be_recorded: bool = False
    map_sheet: "MapSheetLayerOut | None" = None


class AoiLayerOut(BaseModel):
    geometry_4326: dict[str, Any]
    entered_crs_id: str | None
    note: str


class ObservationLayerSummary(BaseModel):
    with_location: int
    without_location: int
    note: str


class BasemapOut(BaseModel):
    configured: bool
    detail: str


class LayersOut(BaseModel):
    rasters: list[RasterLayerOut]
    excluded: list[ExcludedAssetOut]
    aoi: AoiLayerOut | None
    observations: ObservationLayerSummary
    basemap: BasemapOut


# --- coordinate transformations: classes, accuracy and the CRS assessment (OSM/ESM Phase 1) ---------------------------

TransformationClass = Literal["exact", "approximate", "ballpark", "unavailable"]
TransformationPlanStatus = Literal["exact", "approximate", "selection_required", "unavailable"]
AssessmentStatusCode = Literal[
    "georeferenced",
    "crs_identified",
    "crs_missing",
    "crs_ambiguous",
    "transformation_available",
    "transformation_unavailable",
    "georeferencing_required",
    "validation_failed",
]


class TransformGridOut(BaseModel):
    name: str
    available: bool = Field(description="False when the grid file is not installed. Nothing is downloaded.")


class TransformationOperationOut(BaseModel):
    """One coordinate operation PROJ knows between two CRSs, and whether it may be used."""

    id: str = Field(
        description="Stable identifier to send back as `operation`: a registry code such as EPSG:1247, "
        "`datum-unchanged` (no datum change), `ballpark`, or `sha:...` for an unregistered operation."
    )
    name: str
    classification: TransformationClass = Field(
        description="exact: no datum change. approximate: registered shift with a documented accuracy. "
        "ballpark: a rough "
        "fallback, blocked by default. unavailable: needs a grid that is not installed."
    )
    accuracy_m: float | None = Field(
        description="Documented horizontal accuracy of the datum shift in metres. Null if none."
    )
    authority_code: str | None
    area_of_use: str | None
    grids: list[TransformGridOut]
    summary: str
    usable: bool
    recommended: bool = Field(
        description="Only a suggestion (the smallest documented accuracy) when a choice is needed."
    )
    selected: bool
    requires_acknowledgement: bool = Field(
        default=False,
        description="True when the operation may only be used after a person acknowledges it: its documented accuracy "
        "is worse than the threshold, or is not stated.",
    )
    acknowledgement_reason: Literal["accuracy_above_threshold", "accuracy_not_stated"] | None = None
    acknowledgement_threshold_m: float = 10.0


class TransformationPlanOut(BaseModel):
    source_crs: str
    target_crs: str
    status: TransformationPlanStatus
    status_message: str
    recommended_id: str | None
    selected_id: str | None
    operations: list[TransformationOperationOut]
    missing_grids: list[str]
    messages: list[str]


class TransformationResolutionOut(BaseModel):
    operation_id: str
    classification: TransformationClass
    accuracy_m: float | None
    chosen_by: Literal["selection", "only_option"]
    requires_acknowledgement: bool = False
    acknowledged: bool = False


class TransformationBlockOut(BaseModel):
    code: str
    title: str
    detail: str


class AssessedTransformationOut(TransformationPlanOut):
    purpose: Literal["coordinates", "tiles"]
    purpose_label: str
    resolution: TransformationResolutionOut | None = Field(description="The operation that would be used now, if any.")
    blocked: TransformationBlockOut | None = Field(description="Why no operation can be used yet, if so.")


class CrsDatumOut(BaseModel):
    name: str | None
    is_ensemble: bool
    ensemble_members: list[str]


class CrsEllipsoidOut(BaseModel):
    name: str
    semi_major_m: float
    inverse_flattening: float | None
    semi_minor_m: float


class CrsPrimeMeridianOut(BaseModel):
    name: str
    longitude_degrees: float


class CrsProjectionParameterOut(BaseModel):
    name: str
    value: float
    unit: str


class CrsProjectionOut(BaseModel):
    method: str
    name: str
    parameters: list[CrsProjectionParameterOut]


class CrsAxisDetailOut(BaseModel):
    name: str
    abbreviation: str
    direction: str
    unit: str
    unit_to_si: float | None


class CrsDetailsOut(BaseModel):
    identifier: str | None = Field(description="Registry code, only when the definition matches exactly one entry.")
    name: str
    kind: str
    registry_matches: list[str]
    datum: CrsDatumOut
    ellipsoid: CrsEllipsoidOut | None
    prime_meridian: CrsPrimeMeridianOut | None
    projection: CrsProjectionOut | None
    axes: list[CrsAxisDetailOut]
    coordinate_units: list[str]
    area_of_use: CrsAreaOfUse | None
    deprecated: bool
    towgs84: list[float]


class AssessmentStatusOut(BaseModel):
    code: AssessmentStatusCode
    label: str
    active: bool
    message: str


class AssessmentRasterOut(BaseModel):
    width: int
    height: int
    band_count: int
    pixel_size: list[float] | None
    pixel_units: str | None
    geotransform: list[float] | None
    bounds_native: dict[str, Any] | None
    nodata: list[Any]


class PlanningAreaOut(BaseModel):
    west: float
    south: float
    east: float
    north: float


class CrsAssessmentOut(BaseModel):
    asset_id: uuid.UUID
    georeferencing_source: Literal["file", "recorded"]
    georeferencing_version: int
    crs: CrsDetailsOut | None
    raster: AssessmentRasterOut
    statuses: list[AssessmentStatusOut]
    transformations: list[AssessedTransformationOut]
    planning_area: PlanningAreaOut | None = Field(
        description="Roughly where the raster is. Only used to list the operations that apply there."
    )
    selected_operation_id: str | None
    summary: str
    accuracy_notes: list[str]
    class_explanations: dict[str, str]
    limitations: list[str]
    versions: dict[str, str]


class CrsOperationsOut(BaseModel):
    plan: TransformationPlanOut
    area_used: PlanningAreaOut | None
    accuracy_notes: list[str]
    class_explanations: dict[str, str]
    versions: dict[str, str]


class LayerTransformationOut(BaseModel):
    """How a map layer is carried to longitude/latitude, in one place for the layer list."""

    status: TransformationPlanStatus
    classification: TransformationClass | None
    operation_id: str | None
    accuracy_m: float | None
    chosen_by: Literal["selection", "only_option", "recorded"] | None
    selection_required: bool
    message: str
    candidates: list[TransformationOperationOut]
    missing_grids: list[str]
    requires_acknowledgement: bool = False
    acknowledged: bool = False


class CrsTransformOut(BaseModel):
    source_crs_id: str
    target_crs_id: str
    x: float
    y: float
    target_axes: list[CrsAxis] = Field(description="The CRS's axes in the authority's own order.")
    xy_axes: list[CrsAxis] = Field(
        default=[], description="The same axes in the order x, y are given here: x is not always east."
    )
    order_note: str
    transformation: dict[str, Any]
    warnings: list[str]
    operation: TransformationOperationOut | None = Field(
        default=None, description="The coordinate operation used, with its class and documented accuracy."
    )


# --- georeferencing recorded by a person (Phase 4 follow-up) ----------------------------------------------------------

GeoreferenceOperation = Literal["assign_crs", "set_geotransform", "revert_to_file"]


class GeoreferencingRequest(StrictModel):
    operation: GeoreferenceOperation = Field(
        description="assign_crs: state the CRS of the existing coordinates. set_geotransform: enter the geotransform "
        "(and the CRS if none is usable). revert_to_file: use the file's own metadata again."
    )
    crs_id: CrsId | None = None
    geotransform: list[float] | None = Field(
        default=None,
        min_length=6,
        max_length=6,
        description="GDAL order: x origin, pixel width, row rotation, "
        "y origin, column rotation, pixel height. The origin is the top-left corner of the top-left pixel.",
    )
    world_file: Annotated[str, StringConstraints(max_length=500), AfterValidator(clean_text)] | None = None
    geotransform_source: Annotated[str, StringConstraints(max_length=500), AfterValidator(clean_text)] | None = None
    reason: Annotated[str, StringConstraints(max_length=2000), AfterValidator(clean_text)] = ""
    transformation_operation: (
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)] | None
    ) = Field(
        default=None,
        description="Which registered datum transformation to use when several apply (an id from the CRS assessment). "
        "Not needed when only one applies.",
    )
    acknowledge_transformation: bool = Field(
        default=False,
        description="Acknowledge the accuracy of the datum shift that was refused with "
        "TRANSFORMATION_ACKNOWLEDGEMENT_REQUIRED (worse than 10 m, or not stated). Recorded with this version, "
        "with who acknowledged it and when. It covers that operation and accuracy only.",
    )


class GeoreferencingApply(GeoreferencingRequest):
    expected_version: int = Field(ge=0, description="The version you reviewed. A mismatch is refused (409).")
    confirm: bool = Field(description="Must be true: the change was reviewed before it is recorded.")


class GeoreferenceCheck(BaseModel):
    code: str
    status: Literal["ok", "warning", "failed"]
    message: str


class GeoreferenceState(BaseModel):
    version: int
    source: Literal["file", "recorded"]
    crs_id: str | None
    crs_name: str | None
    crs_usable: bool
    geotransform: list[float] | None
    geotransform_usable: bool
    georeferenced: bool
    footprint_4326: dict[str, Any] | None
    bounds_native: dict[str, Any] | None
    pixel_size: list[float] | None
    pixel_units: str | None


class GeoreferenceValidation(BaseModel):
    valid: bool
    checks: list[GeoreferenceCheck]
    proposed: GeoreferenceState | None
    corners: list[dict[str, Any]] = Field(description="Pixel -> native coordinates -> longitude, latitude.")
    method: str
    explanation: str
    transformation_plan: TransformationPlanOut | None = Field(
        default=None,
        description="Set when the proposal was refused because no datum transformation could be used yet: the "
        "operations to choose between (send one as `transformation_operation`), or why none can be used.",
    )


class GeoreferenceVersionOut(BaseModel):
    version_no: int
    operation: GeoreferenceOperation
    method: str
    crs_id: str | None
    crs_name: str | None
    geotransform: list[float] | None
    geotransform_source: str | None
    world_file: str | None
    reason: str
    previous: dict[str, Any]
    result: dict[str, Any]
    validation: dict[str, Any]
    georeferenced: bool
    source_checksum: str
    actor_label: str
    created_at: datetime


class GeoreferenceFileMetadata(BaseModel):
    crs_status: Literal["valid", "missing", "invalid"]
    crs_identifier: str | None
    crs_name: str | None
    geotransform: list[float] | None
    geotransform_problem: str | None
    georeferenced: bool
    ground_control_points: int
    unknowns: dict[str, Any]


class GeoreferenceOptions(BaseModel):
    assign_crs: bool
    assign_crs_reason: str
    set_geotransform: bool
    set_geotransform_reason: str
    crs_required_with_geotransform: bool
    revert_to_file: bool


class GeoreferencingOut(BaseModel):
    asset_id: uuid.UUID
    source_checksum: str
    width: int
    height: int
    file: GeoreferenceFileMetadata
    current: GeoreferenceState
    unplaced_reason: str | None
    options: GeoreferenceOptions
    history: list[GeoreferenceVersionOut]
    note: str


# --- sidecar metadata (Phase 3b follow-up) ----------------------------------------------------------------------------


class SidecarComparison(BaseModel):
    field: str
    sidecar: Any
    file: Any = Field(description="What the raster file declares; never changed.")
    in_use: Any = Field(description="What is in use now (the file's value or a recorded version).")
    relation_file: Literal["same", "differs", "none", "unresolved"]
    relation_in_use: Literal["same", "differs", "none", "unresolved"]
    applied: bool | None = Field(description="False for values shown for information only (band metadata).")


class SidecarOut(BaseModel):
    id: uuid.UUID
    kind: Literal["world_file", "prj", "aux_xml"]
    kind_label: str
    filename: str
    sha256: str
    size_bytes: int
    pairing: Literal["filename_match", "explicit"]
    uploaded_by: str
    uploaded_at: datetime
    current: bool = Field(description="The latest sidecar of its kind for this raster; older ones are not used.")
    status: Literal["parsed", "malformed", "unsupported"]
    summary: str
    values: dict[str, Any]
    problems: list[str]
    ignored: list[str] = Field(description="Present in the file but not used.")
    comparisons: list[SidecarComparison]
    role: str


class SidecarChoice(BaseModel):
    sidecar_id: uuid.UUID
    filename: str
    value: str | None


class SidecarProposalOut(BaseModel):
    operation: GeoreferenceOperation
    crs_id: str | None
    geotransform: list[float] | None
    world_file: str | None
    geotransform_source: str | None
    sources: dict[str, Any]


class SidecarReviewOut(BaseModel):
    asset_id: uuid.UUID
    expected_version: int = Field(description="The georeferencing version this review is based on.")
    sidecar_ids: list[uuid.UUID] = Field(description="The sidecars this review covers; send them back to record it.")
    state: Literal["none", "file_valid", "proposal", "conflict", "insufficient", "in_use"]
    title: str
    explanation: list[str]
    conflicts: list[str]
    choices: dict[str, list[SidecarChoice]]
    proposal: SidecarProposalOut | None
    sidecars: list[SidecarOut]
    accepted_extensions: list[str]
    note: str


class SidecarUploadOut(BaseModel):
    sidecar: SidecarOut
    asset_id: uuid.UUID
    raster_filename: str
    duplicate: bool
    message: str


class SidecarRequest(BaseModel):
    reason: str = Field(default="", max_length=2000)
    geotransform_from: uuid.UUID | None = Field(default=None, description="Which sidecar, when two disagree.")
    crs_from: uuid.UUID | None = Field(default=None, description="Which sidecar, when two disagree.")


class SidecarApply(SidecarRequest):
    expected_version: int = Field(ge=0)
    sidecar_ids: list[uuid.UUID] = Field(description="The sidecars that were reviewed.")
    confirm: bool = False


class SidecarValidation(BaseModel):
    review: SidecarReviewOut
    validation: GeoreferenceValidation | None


# --- non-AI raster tools (Phase 3b) -----------------------------------------------------------------------------------


class JobBrief(BaseModel):
    id: uuid.UUID
    status: str
    error_code: str | None
    error_message: str | None
    created_at: datetime


class RasterToolsOut(BaseModel):
    asset_id: uuid.UUID
    width: int
    height: int
    band_count: int
    dtypes: list[str]
    bands: list[dict[str, Any]] = Field(description="What the file declares about each band. Never inferred.")
    statistics: dict[str, Any] | None
    statistics_job: JobBrief | None
    display_copy: dict[str, Any] | None
    display_copy_needed: bool
    display_copy_job: JobBrief | None
    display_source: Literal["original", "display_copy"]
    derived_from: list[dict[str, Any]] = Field(description="Lineage, for a derived raster; empty for originals.")
    note: str


class DisplayOut(BaseModel):
    bands: list[int]
    mode: str
    ranges: list[list[float] | None]
    description: str
    note: str


class PixelBandValue(BaseModel):
    band: int
    raw: float | int | None
    nodata: bool
    scaled: float | None
    units: str | None
    description: str | None


class PixelOut(BaseModel):
    col: int
    row: int
    values: list[PixelBandValue]
    dtypes: list[str]
    native: dict[str, Any] | None
    lon_lat: list[float] | None
    lon_lat_note: str | None = Field(
        default=None, description="Why there is no longitude/latitude (a datum transformation that may not be used)."
    )
    transformation: dict[str, Any] | None = Field(
        default=None, description="The coordinate operation that produced lon_lat, with its class and accuracy."
    )
    georeferencing_source: Literal["file", "recorded"] | None
    note: str


class MeasureRequest(StrictModel):
    kind: Literal["distance", "area"]
    space: Literal["lonlat", "pixel"]
    asset_id: uuid.UUID | None = Field(default=None, description="Required for pixel space.")
    points: list[list[float]] = Field(min_length=2, max_length=1000)
    transformation_operation: str | None = Field(
        default=None,
        max_length=100,
        description="The datum transformation chosen for the longitude/latitude of pixel points, where several apply. "
        "The measurement itself is on the raster's own ellipsoid and does not need one.",
    )


class MeasureOut(BaseModel):
    kind: Literal["distance", "area"]
    validity: Literal["valid", "unavailable"]
    value: float | None
    unit: str | None
    method: str
    perimeter_m: float | None
    planar: dict[str, Any] | None
    pixel_value: float | None
    pixel_unit: str | None
    flags: list[str]
    note: str
    points_lonlat: list[list[float]] | None
    points_lonlat_note: str | None = Field(
        default=None, description="Why the points have no longitude/latitude (a transformation that may not be used)."
    )
    transformation: dict[str, Any] | None = Field(
        default=None, description="The coordinate operation that produced points_lonlat."
    )


class ComparisonRequest(StrictModel):
    asset_a: uuid.UUID
    asset_b: uuid.UUID
    band_a: int = Field(default=1, ge=1)
    band_b: int = Field(default=1, ge=1)
    reason: Annotated[str, StringConstraints(max_length=1000), AfterValidator(clean_text)] | None = None


class AlignmentOut(BaseModel):
    aligned: bool
    reasons: list[str]


class ComparisonOut(BaseModel):
    job: JobBrief
    asset_a: uuid.UUID
    asset_b: uuid.UUID
    band_a: int
    band_b: int
    derived_asset_id: uuid.UUID | None
    summary: dict[str, Any] | None


# --- photo locations (demo phase 1) -----------------------------------------------------------------------------------


class PhotoLocationRow(BaseModel):
    """One step of a photograph's location history, exactly as recorded."""

    version_no: int
    status: Literal["candidate", "confirmed", "cleared"]
    source: Literal["exif", "manual_entry", "map_selection", "none"]
    latitude: float | None
    longitude: float | None
    crs_id: Literal["EPSG:4326"]
    candidate_version_no: int | None
    reason: str | None
    actor_label: str
    created_at: datetime


class PhotoLocationCurrent(BaseModel):
    state: Literal["none", "confirmed"] = Field(
        description="none: no confirmed location (an unverified EXIF position, if any, is under `exif`). confirmed: "
        "a person stands behind the position."
    )
    version_no: int = Field(description="0 when nothing has been recorded. Send it back as `expected_version`.")
    source: Literal["exif", "manual_entry", "map_selection"] | None
    latitude: float | None
    longitude: float | None
    crs_id: Literal["EPSG:4326"] | None
    reason: str | None
    actor_label: str | None
    changed_at: datetime | None


class PhotoLocationProblem(BaseModel):
    code: str
    message: str


class PhotoLocationExif(BaseModel):
    state: Literal["none", "candidate", "unusable", "not_read"] = Field(
        description="none: no GPS tags. candidate: a usable position was read. unusable: GPS tags exist but cannot "
        "be used (see `problems`). not_read: GPS tags exist but this photograph was ingested before they were read."
    )
    candidate_version_no: int | None
    latitude: float | None
    longitude: float | None
    datum: str | None
    problems: list[PhotoLocationProblem]


class PhotoLocationOut(BaseModel):
    asset_id: uuid.UUID
    source_checksum: str
    current: PhotoLocationCurrent
    exif: PhotoLocationExif
    history: list[PhotoLocationRow]
    note: str


class PhotoLocationApply(StrictModel):
    action: Literal["confirm_exif", "set_position", "clear"]
    latitude: float | None = Field(default=None, allow_inf_nan=False, description="Decimal degrees, WGS 84.")
    longitude: float | None = Field(default=None, allow_inf_nan=False, description="Decimal degrees, WGS 84.")
    method: Literal["manual_entry", "map_selection"] | None = Field(
        default=None, description="Required for `set_position`: typed, or chosen on the map."
    )
    reason: Annotated[str, StringConstraints(max_length=1000), AfterValidator(clean_text)] | None = None
    expected_version: int = Field(ge=0, description="The version you reviewed. A mismatch is refused (409).")
    confirm: bool = Field(description="Must be true: the position was reviewed before it is recorded.")


class ReadExifOut(BaseModel):
    outcome: Literal["candidate_recorded", "no_gps", "unusable"]
    problems: list[PhotoLocationProblem]
    location: PhotoLocationOut


class PhotoLocationItem(BaseModel):
    asset_id: uuid.UUID
    filename: str
    state: Literal["none", "candidate", "confirmed"]
    source: Literal["exif", "manual_entry", "map_selection"] | None
    latitude: float | None = Field(description="Present only for a confirmed location.")
    longitude: float | None
    version_no: int
    confirmed_by: str | None
    confirmed_at: datetime | None
    observations: int = Field(description="Observations stored for this photograph (all runs).")


class PhotoLocationsOut(BaseModel):
    items: list[PhotoLocationItem]
    confirmed: int
    candidates: int
    without_location: int
    note: str


class CameraViewOut(BaseModel):
    """Which way the camera faced and how wide its view was, as the photograph's file states it. Read, never stored."""

    asset_id: uuid.UUID
    heading_deg: float | None = Field(
        description="Direction the camera faced, in degrees clockwise from north, as the file records it (EXIF GPS "
        "image direction). Unverified: a phone compass can be off by tens of degrees."
    )
    heading_reference: Literal["true_north", "magnetic_north", "not_stated"] | None = Field(
        description="Which north the direction is measured from, as the file says. None when there is no direction."
    )
    focal_length_35mm_mm: float | None = Field(
        description="The lens's 35 mm equivalent focal length, from which the width of view can be estimated."
    )
    focal_length_mm: float | None = Field(description="The lens's actual focal length, for reference only.")
    problems: list[PhotoLocationProblem]
    note: str


# --- map annotations -------------------------------------------------------------------------------------------------


class AnnotationCreate(StrictModel):
    kind: Literal["point", "line", "polygon"]
    geometry: dict[str, Any] = Field(
        description="GeoJSON Point, LineString or Polygon (one outline, no holes). Positions are [longitude, latitude] "
        "in degrees; a polygon outline must end where it starts."
    )
    crs_id: str = Field(
        default="EPSG:4326", max_length=64, description="Only EPSG:4326 is accepted; anything else is refused."
    )
    note: Annotated[str, StringConstraints(max_length=2000), AfterValidator(clean_text)] = ""


class AnnotationUpdate(StrictModel):
    expected_version: int = Field(ge=1, description="The version you were editing. A mismatch is refused (409).")
    geometry: dict[str, Any] | None = Field(
        default=None, description="A new shape of the same kind. Omit to keep the current one."
    )
    crs_id: str = Field(default="EPSG:4326", max_length=64)
    note: Annotated[str, StringConstraints(max_length=2000), AfterValidator(clean_text)] | None = Field(
        default=None, description="A new note. Omit to keep the current one; an empty string clears it."
    )
    reason: Annotated[str, StringConstraints(max_length=1000), AfterValidator(clean_text)] | None = None


class AnnotationMeasurement(BaseModel):
    length_m: float | None = Field(description="Line length in metres (lines only).")
    area_m2: float | None = Field(description="Enclosed area in square metres (polygons only).")
    perimeter_m: float | None = Field(description="Outline length in metres (polygons only).")
    method: str
    limitations: list[str]


class AnnotationOut(BaseModel):
    feature_type: Literal["user_annotation"] = "user_annotation"
    annotation_id: uuid.UUID
    project_id: uuid.UUID
    kind: Literal["point", "line", "polygon"]
    status: Literal["active", "deleted"]
    version_no: int
    geometry: dict[str, Any]
    crs_id: str
    note: str
    measurement: AnnotationMeasurement | None = Field(description="Null for a point.")
    created_by: str = Field(description="The local profile that drew it. Attribution only; not authentication.")
    created_at: datetime
    updated_by: str
    updated_at: datetime
    deleted_by: str | None = None
    deleted_at: datetime | None = None


class AnnotationsOut(BaseModel):
    items: list[AnnotationOut]
    active: int
    deleted: int = Field(description="Deleted annotations in the project, whether or not they are listed.")
    include_deleted: bool
    note: str


class AnnotationVersionOut(BaseModel):
    version_no: int
    action: Literal["create", "update", "delete"]
    status: Literal["active", "deleted"]
    geometry: dict[str, Any]
    note: str
    reason: str | None
    actor_label: str
    created_at: datetime


class AnnotationHistoryOut(BaseModel):
    annotation_id: uuid.UUID
    project_id: uuid.UUID
    kind: Literal["point", "line", "polygon"]
    versions: list[AnnotationVersionOut]
    note: str


# --- video-frame analysis -------------------------------------------------------------------------------------------


class VideoSamplingPlan(BaseModel):
    sampling: VideoSampling
    planned_frames: int | None = Field(description="Frames the policy asks for. Null when it cannot be worked out yet.")
    wanted_ms: list[int] = Field(description="The media times (ms) the policy asks for, before frames are matched.")
    duration_ms: int | None
    basis: str
    min_interval_s: float
    max_frames_ceiling: int
    policy: str
    warnings: list[str]


class VideoFrameOut(BaseModel):
    frame_index: int = Field(description="0-based position in presentation order.")
    timestamp_ms: int = Field(description="Presentation time in ms from the first frame.")
    sample_target_ms: int = Field(description="The first wanted time this frame stands for.")
    also_stands_for_ms: list[int]
    width: int
    height: int
    sha256: str
    size_bytes: int
    finding_count: int
    image_url: str


class VideoFrameFailureOut(BaseModel):
    frame_index: int
    timestamp_ms: int
    code: str
    message: str


class VideoFramesOut(BaseModel):
    run_id: uuid.UUID
    asset_id: uuid.UUID
    source_sha256: str
    model_name: str
    model_version: str
    sampling: VideoSampling
    frames: list[VideoFrameOut]
    failed: list[VideoFrameFailureOut]
    findings_total: int
    frames_in_file: int | None
    policy: str
    frame_numbering: str
    coordinates: str
    note: str


# --- 3D terrain view ------------------------------------------------------------------------------------------------


class TerrainBandOut(BaseModel):
    band: int
    dtype: str
    description: str | None
    colour_interpretation: str | None
    declared_unit: str | None = Field(description="The unit the file declares, normalised (metre, foot), or null.")
    role: Literal["elevation_declared", "possible", "not_elevation"]
    reasons: list[str]


class TerrainRasterOut(BaseModel):
    asset_id: uuid.UUID
    filename: str
    width: int
    height: int
    crs_id: str | None
    status: Literal["usable", "needs_confirmation", "not_elevation", "unusable"]
    reasons: list[str]
    bands: list[TerrainBandOut]


class TerrainListOut(BaseModel):
    items: list[TerrainRasterOut]
    usable: int
    needs_confirmation: int
    not_available: int
    max_grid_side: int
    default_grid_side: int
    note: str


class TerrainGridOut(BaseModel):
    width: int
    height: int
    decimation: int = Field(description="Every Nth source pixel was taken.")
    method: str


class TerrainExtentOut(BaseModel):
    x_min: float
    y_min: float
    x_max: float
    y_max: float


class TerrainHorizontalOut(BaseModel):
    method: str
    approximate: bool


class TerrainElevationOut(BaseModel):
    unit: Literal["metre", "foot", "us_survey_foot"]
    unit_label: str
    unit_source: Literal["declared", "stated_for_this_view"]
    to_metre: float
    role_source: Literal["declared", "confirmed_for_this_view"]
    declared_scale_offset_applied: bool
    nodata_value: float | None
    valid_cells: int
    nodata_cells: int
    min: float
    max: float
    mean: float


class TerrainVerticalDatumOut(BaseModel):
    state: Literal["stated", "not_stated"]
    name: str | None


class TerrainOut(BaseModel):
    asset_id: uuid.UUID
    filename: str
    source_checksum: str
    band: int
    source_size: list[int]
    grid: TerrainGridOut
    crs_id: str | None
    crs_name: str | None
    georeferencing_source: str
    horizontal_unit: Literal["degree", "linear"]
    cell_size_native: list[float]
    cell_size_m: list[float]
    extent_native: TerrainExtentOut
    extent_m: list[float]
    bbox_4326: list[float] | None
    centre_4326: list[float] | None
    positioning: dict[str, Any] = Field(
        description="How bbox_4326 and centre_4326 were obtained, with the transformation used, or why they are "
        "absent. "
        "The surface itself is drawn in the raster's own horizontal frame and does not depend on it."
    )
    horizontal: TerrainHorizontalOut
    elevation: TerrainElevationOut
    vertical_datum: TerrainVerticalDatumOut
    warnings: list[str]
    disclosures: list[str]
    grid_url: str
    grid_format: str


# --- map sheets -------------------------------------------------------------------------------------------------------

MapSeries = Literal["osm", "esm", "unspecified"]
MapSheetIntake = Literal["georeferenced_raster", "scanned_raster", "scanned_image", "pdf_document"]
GeoreferencingBasis = Literal["file_metadata", "sidecar", "world_file", "control_points", "recorded", "none"]


class MapSheetDeclarationOut(BaseModel):
    series: MapSeries
    series_label: str
    basis: str = Field(description="Always declared_by_uploader: the series is attribution, never evidence of a CRS.")
    sheet_label: str | None
    scale_denominator: int | None
    edition: str | None
    declared_by: str | None
    note: str


class MapSheetInfo(BaseModel):
    """What is known about a map sheet: the uploader's declaration, how it arrived, and how it is positioned."""

    declaration: MapSheetDeclarationOut
    intake: MapSheetIntake
    format_family: str
    georeferencing_basis: GeoreferencingBasis
    georeferencing_basis_label: str
    placed: bool
    processing: list[dict[str, Any]] = Field(description="What was done with the upload, in order. Read-only record.")


class MapSheetLayerOut(BaseModel):
    """The map-sheet facts shown on a map layer."""

    series: MapSeries
    series_label: str
    sheet_label: str | None
    scale_denominator: int | None
    edition: str | None
    intake: MapSheetIntake
    georeferencing_basis: GeoreferencingBasis
    georeferencing_basis_label: str


class MapSheetSummaryOut(BaseModel):
    asset_id: uuid.UUID
    filename: str
    info: MapSheetInfo
    crs_id: str | None
    crs_name: str | None
    crs_status: Literal["valid", "missing", "invalid"] | None
    width: int | None
    height: int | None
    quality_flags: list[str]
    next_step: str = Field(description="What has to happen before this sheet can be placed (or why it already is).")
    ingested_at: datetime


class MapSheetListOut(BaseModel):
    items: list[MapSheetSummaryOut]
    note: str


class MapSheetUploadOut(BaseModel):
    asset: AssetOut
    map_sheet: MapSheetInfo
    duplicate: bool
    duplicate_of_asset_id: uuid.UUID | None
    stored_original_reused: bool
    message: str


MetadataFieldName = Literal[
    "datum",
    "ellipsoid",
    "prime_meridian",
    "projection",
    "projection_parameters",
    "units",
    "coordinate_grid",
    "extent",
    "transformation_to_wgs84",
    "other",
]
UnresolvedStatus = Literal["missing", "needs_authority", "ambiguous", "declared_unverified"]


class MapSheetFactOut(BaseModel):
    """One property a person needs to know to interpret the sheet, and whether the file actually states it."""

    field: str
    label: str
    state: Literal["stated_by_file", "recorded_by_person", "from_registry", "unknown"]
    value: Any | None
    detail: str


class UnresolvedItemOut(BaseModel):
    field: str
    label: str
    status: UnresolvedStatus
    detail: str
    required: str = Field(description="What would resolve it.")


class FamilyHintOut(BaseModel):
    """A name in the file's CRS that belongs to a family needing an authoritative definition. A name is not a CRS."""

    family: str
    matched: str
    meaning: str


class MapSheetNoteIn(BaseModel):
    field: MetadataFieldName
    value: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
    source: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)] = Field(
        description="Where the value comes from (document, sheet marginalia, supplier). Required."
    )
    status: Literal["unverified", "needs_authority"] = "unverified"


class MapSheetNoteOut(BaseModel):
    id: uuid.UUID
    field: MetadataFieldName
    value: str
    source: str
    status: Literal["unverified", "needs_authority"]
    recorded_by: str | None
    recorded_at: datetime
    applied: bool = Field(description="Always false: a note is a record, never a CRS definition or a georeferencing.")


class MapSheetSidecarOut(BaseModel):
    id: uuid.UUID
    kind: str
    filename: str
    upload_status: str
    uploaded_at: datetime


class MapSheetAssessmentOut(BaseModel):
    """The metadata assessment of a map sheet. Reports what the file states, what it does not, and what is needed."""

    asset_id: uuid.UUID
    filename: str
    info: MapSheetInfo
    facts: list[MapSheetFactOut]
    unresolved: list[UnresolvedItemOut]
    required_information: list[str]
    family_hints: list[FamilyHintOut]
    sidecars: list[MapSheetSidecarOut]
    notes: list[MapSheetNoteOut]
    pdf: dict[str, Any] | None
    crs_assessment: CrsAssessmentOut | None
    series_note: str
    limitations: list[str]


# --- control points ---------------------------------------------------------------------------------------------------


class ControlPointIn(BaseModel):
    label: Annotated[str, StringConstraints(strip_whitespace=True, max_length=80)] = ""
    column: float = Field(description="Pixel column, 0 at the left edge of the picture (GDAL convention).")
    row: float = Field(description="Pixel row, 0 at the top edge of the picture.")
    x: float = Field(description="Easting / longitude in the chosen CRS.")
    y: float = Field(description="Northing / latitude in the chosen CRS.")


class ControlPointsRequest(BaseModel):
    crs_id: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)] = Field(
        description="The registered CRS the control point coordinates are given in. It becomes the sheet's CRS."
    )
    points: list[ControlPointIn] = Field(min_length=3, max_length=200)
    source: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)] = Field(
        description="Where the coordinates come from (printed grid ticks, survey, another georeferenced layer...)."
    )
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
    transformation_operation: str | None = None
    acknowledge_transformation: bool = False  # see GeoreferencingRequest
    reference_uncertainty_m: float | None = Field(
        default=None,
        ge=0,
        le=1_000_000,
        description="How uncertain the coordinates of the reference points are, in metres, if you know. Not assumed "
        "when omitted: the result says it was not stated.",
    )
    source_uncertainty_m: float | None = Field(
        default=None,
        ge=0,
        le=1_000_000,
        description="How uncertain the printed source is on the ground, in metres (for example the graphical accuracy "
        "of the map's scale), if you know. Not assumed when omitted.",
    )


class ControlPointsApply(ControlPointsRequest):
    expected_version: int = Field(ge=0, description="The georeferencing version that was reviewed.")
    confirm: bool = False


class ControlPointResidualOut(BaseModel):
    label: str
    column: float
    row: float
    x: float
    y: float
    fitted_x: float
    fitted_y: float
    residual_map_units: float
    residual_pixels: float
    residual_m: float | None = Field(default=None, description="Distance from the fitted position, on the ground.")
    leverage: float | None = Field(default=None, description="How much this point steers the fit (0 to 1).")
    leave_one_out_m: float | None = Field(
        default=None,
        description="How far the other points put this one from where it was placed. Null with three points, or "
        "when the others cannot fix the sheet without it.",
    )
    leave_one_out_ratio: float | None = Field(
        default=None, description="Leave-one-out error over what the tolerance expects for a left-out point."
    )


class ControlPointFitOut(BaseModel):
    method: str
    point_count: int
    geotransform: list[float]
    rms_pixels: float
    max_pixels: float
    rms_map_units: float
    map_units: str | None
    pixel_size: list[float]
    rotation_degrees: float
    scale_ratio: float = Field(description="Ratio of the larger to the smaller pixel size (1 = square pixels).")
    redundant: bool = Field(
        description="True when there are more points than the six numbers need, so residuals mean something."
    )
    residuals: list[ControlPointResidualOut]
    note: str
    # --- on the ground, in metres (evidence schema 2) ---
    rms_m: float | None = None
    max_m: float | None = None
    rms_m_allowing_for_fit: float | None = Field(
        default=None, description="Null with exactly three points: the fit is exact and has nothing to compare."
    )
    exact_fit: bool = False
    ground_pixel_m: list[float] = []
    ground_resolution_m: float | None = None
    shear_degrees: float | None = None
    shear_effect_m: float | None = None
    handedness: Literal["normal", "mirrored"] | None = None
    rotation_reference: str | None = None
    tolerance: dict[str, Any] | None = Field(
        default=None,
        description="The tolerance used, how it was derived from the ground resolution, and which "
        "uncertainties were stated by the person.",
    )
    leave_one_out: dict[str, Any] | None = None
    corner_uncertainty_m: float | None = None
    corners: list[dict[str, Any]] = []


class ControlPointsValidation(BaseModel):
    valid: bool
    fit: ControlPointFitOut | None
    checks: list[GeoreferenceCheck]
    validation: GeoreferenceValidation | None
    explanation: str


# --- alignment --------------------------------------------------------------------------------------------------------


class AlignmentLayerOut(BaseModel):
    asset_id: uuid.UUID
    title: str
    kind: Literal["map_sheet", "raster"]
    series: MapSeries | None
    crs_id: str | None
    crs_name: str | None
    placement: str
    georeferencing_basis: GeoreferencingBasis
    transformation_class: str | None
    datum_shift_accuracy_m: float | None
    selection_required: bool
    needs_acknowledgement: bool = Field(
        default=False,
        description="True when the layer's only datum shift is worse than 10 m (or has no stated accuracy) and has not "
        "been acknowledged, so the layer is not carried to the map.",
    )
    control_point_rms_m: float | None
    ground_pixel_m: float | None
    bounds_4326: list[float] | None


class OverlayAlignmentOut(BaseModel):
    first: AlignmentLayerOut
    second: AlignmentLayerOut
    overlap: bool
    overlap_bounds_4326: list[float] | None
    overlap_area_km2: float | None
    overlap_fraction_of_first: float | None
    overlap_fraction_of_second: float | None
    resolution_ratio: float | None = Field(
        description="Coarser ground pixel divided by finer. Null when either is unknown."
    )
    findings: list[str]
    cautions: list[str]


# --- Google Earth Engine, GEE-1 (controlled evaluation) ---------------------------------------------------------------


class EarthEngineStatus(BaseModel):
    operating_model: str = Field(description="A: a controlled evaluation by named developers on one host.")
    online_enabled: bool
    earth_engine_enabled: bool
    project_configured: bool
    auth_mode: str
    client_installed: bool
    control_folder_ready: bool
    control_folder_problem: str | None
    kill_switch_engaged: bool
    kill_switch_reason: str | None
    held_by_this_process: bool = Field(description="Whether this process holds the single-instance lock.")
    usage: dict[str, Any] | None = Field(description="Ceilings and today's use from the local log; no call to Google.")
    developers_listed: int
    you_are_listed: bool
    last_verification: dict[str, Any] | None
    live_verified: bool = Field(description="True only if a connection check succeeded in this process.")
    note: str


class DatasetBand(BaseModel):
    name: str
    description: str
    pixel_size_m: int | None
    scale: float | None
    offset: float | None
    units: str | None


class DatasetProbe(BaseModel):
    slug: str
    dataset_id: str
    status: str = Field(description="not_probed until a developer runs the probe; then what the service answered.")
    checked_at: str | None
    extent_start: str | None
    extent_end: str | None
    detail: str
    live: bool


class DatasetEntry(BaseModel):
    slug: str
    id: str
    title: str
    kind: str
    provider: str
    revisit_days: int | None
    documented_start: str
    bands: list[DatasetBand]
    licence: str
    attribution: str
    cloud_handling: str
    notes: list[str]
    verified_on: str = Field(description="When the facts were read on the provider's page. Not a live check.")
    source_url: str
    probe: DatasetProbe


class DatasetList(BaseModel):
    items: list[DatasetEntry]
    note: str


class ConnectionFailure(BaseModel):
    code: str
    message: str


class ConnectionTest(BaseModel):
    ok: bool
    checked_at: str
    auth_mode: str
    credential_kind: str | None
    service_account_email: str | None
    project_configured: bool
    initialised: bool
    computation_ok: bool
    library_version: str | None
    failure: ConnectionFailure | None


# --- satellite catalogue discovery (Phase 1, geopoc.imagery) ----------------------------------------


class ImageryGates(BaseModel):
    online_enabled: bool
    imagery_enabled: bool
    terms_confirmed: bool
    missing: list[str] = Field(description="Environment settings that are still off. A search needs all of them.")


class ImageryCollectionOut(BaseModel):
    id: str
    title: str
    mission: str
    level: str


class ImageryProviderOut(BaseModel):
    id: str
    title: str
    kind: Literal["stac"]
    host: str
    collections: list[ImageryCollectionOut]
    credential_configured: bool
    search_needs_credentials: bool | None = Field(description="None: believed not, but not verified.")
    configuration_problem: str | None
    ready: bool = Field(description="Every application-wide gate is open and the provider is configured.")
    licence: dict[str, str]
    notes: list[str]


class ImageryProvidersOut(BaseModel):
    providers: list[ImageryProviderOut]
    gates: ImageryGates
    note: str


class SceneSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = Field("cdse", min_length=1, max_length=40, pattern=r"^[a-z0-9-]+$")
    collection: str = Field("sentinel-2-l2a", min_length=1, max_length=80, pattern=r"^[a-z0-9-]+$")
    aoi: dict[str, Any] = Field(description="A GeoJSON Polygon in EPSG:4326, [longitude, latitude].")
    start_date: date
    end_date: date = Field(description="Inclusive, UTC calendar days.")
    max_cloud_percent: float | None = Field(None, ge=0, le=100)
    limit: int = Field(20, ge=1, le=100, description="Scenes per page.")
    page_token: str | None = Field(
        None, max_length=4096, description="From a previous answer. Repeat every other field unchanged."
    )
    dry_run: bool = Field(False, description="Check the request and show what would be sent. Sends nothing.")
    confirm_external_transmission: bool = Field(
        False,
        description="Must be true to send. It confirms that a bounding box of the area, the dates and the cloud limit "
        "may leave this machine for the provider named in `provider`.",
    )


class ImageryAssetOut(BaseModel):
    key: str
    title: str | None
    media_type: str | None
    roles: list[str]


class SceneOut(BaseModel):
    provider: str
    collection: str
    scene_id: str
    acquisition_time: str = Field(description="UTC, ISO 8601 with Z.")
    cloud_percent: float | None = Field(description="Share of cloudy pixels in the whole granule, not your area.")
    platform: str | None
    constellation: str | None
    instruments: list[str]
    processing_level: str | None
    processing_baseline: str | None
    mgrs_tile: str | None
    epsg: int | None
    gsd_m: float | None
    relative_orbit: int | None
    geometry: dict[str, Any] | None = Field(description="The footprint in EPSG:4326, as the catalogue gives it.")
    bbox: list[float] | None
    aoi_check: Literal["intersects", "not_verified"]
    assets: list[ImageryAssetOut] = Field(description="Names only. Nothing is downloaded and no address is kept.")


class ImageryTransmission(BaseModel):
    sent: bool
    provider: str
    host: str
    method: str
    path: str
    aoi_sent: dict[str, Any]
    request_body: dict[str, Any]
    credential_header: bool
    not_sent: list[str]


class ImageryFiltering(BaseModel):
    server_returned: int
    outside_aoi: int
    above_cloud_limit: int
    unknown_cloud: int
    malformed: int
    kept: int


class SceneSearchOut(BaseModel):
    status: Literal["dry_run", "searched"]
    provider: str
    collection: str
    transmission: ImageryTransmission
    gates: ImageryGates
    items: list[SceneOut]
    returned: int
    matched: int | None = Field(description="The catalogue's own total, when it says one.")
    next_page_token: str | None
    filtering: ImageryFiltering | None
    retrieved_at: str | None
    licence: dict[str, str]
    notes: list[str]


# --- CDSE S3 authentication check (geopoc.imagery.cdse_s3) -------------------------------------------


class CdseS3StatusOut(BaseModel):
    """Sanitised state of the CDSE S3 integration. Never holds a key, a token or text written by the store."""

    enabled: bool = Field(description="CDSE_S3_ENABLED.")
    configured: bool = Field(
        description="Credentials in a valid format and a valid endpoint are set. Not whether they work."
    )
    authenticated: bool = Field(description="True only when a verification in this response succeeded.")
    status: Literal[
        "DISABLED",
        "MISSING_CREDENTIALS",
        "NOT_VERIFIED",
        "AUTHENTICATED",
        "AUTHENTICATION_FAILED",
        "CONNECTION_FAILED",
    ]
    endpoint: str
    region: str
    reason: str = Field(description="A short stable code for the status, for example `invalid_access_key`.")
    message: str
    online_gate_open: bool = Field(description="GEOPOC_ONLINE_ENABLED. Verification is refused while it is off.")
    checked_at: str | None = Field(default=None, description="When the verification ran; null if none was made.")


class CdseS3VerifyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm_external_request: bool = Field(
        default=False,
        description="Must be true. Verification sends one signed, read-only listing request to the S3 endpoint.",
    )


class CdseS3VerifyOut(CdseS3StatusOut):
    request: dict[str, Any] = Field(description="Exactly what was (or, if refused, would be) sent. No credential.")


# --- derived spectral indices (geopoc.geo.indices, geopoc.geo.bandscan) -------------------------------


class IndexOut(BaseModel):
    key: str
    title: str
    formula: str
    inputs: list[dict[str, str]] = Field(description="The input bands: each has a `role` and the Sentinel-2 `band`.")
    output: str = Field(description="What a run produces.")
    analytical_range: list[float] | None = Field(
        default=None, description="Calculated values outside this inclusive range are masked as nodata, never clamped."
    )
    display: dict[str, Any] | None = Field(default=None, description="Colour ramp, display range and legend stops.")


class IndexesOut(BaseModel):
    indices: list[IndexOut]


class IndexInputBandOut(BaseModel):
    role: str
    band: str
    file_name: str | None = Field(description="The local file's name. Its location is never given.")
    found: bool
    readable: bool
    width: int | None = None
    height: int | None = None
    bands: int | None = None
    dtype: str | None = None
    crs: str | None = None
    pixel_size_m: list[float] | None = None
    scaling: dict[str, Any] | None = Field(default=None, description="Read from the product metadata, per band.")
    problems: list[str]


class IndexSceneOut(BaseModel):
    scene_id: str = Field(description="Opaque identifier of this scene on this server.")
    product_uri: str | None
    tile_id: str | None
    acquisition_start: str | None
    processing_baseline: str | None
    spacecraft: str | None
    resolution_m: int
    bands: list[IndexInputBandOut]
    compatible: bool = Field(description="Both bands were found, are readable and share one grid.")
    problems: list[str]


class IndexInputsOut(BaseModel):
    index: str
    configured: bool = Field(description="A local band folder is set on the server.")
    scenes: list[IndexSceneOut]
    note: str


# --- derived index runs (NDVI jobs, geopoc.derived_index) ---------------------------------------------


class IndexRunCreate(StrictModel):
    scene_id: Annotated[
        str,
        StringConstraints(pattern=r"^[0-9a-f]{16}$"),
        Field(description="The opaque id of a scene from the inputs listing. Paths are never accepted."),
    ]


class IndexRunInput(BaseModel):
    role: str
    band: str
    file_name: str
    sha256: str | None = Field(default=None, description="Known once the job has read the file.")
    scaling: dict[str, Any] | None = None


class IndexRunStatistics(BaseModel):
    min: float | None
    max: float | None
    mean: float | None
    std: float | None
    percentiles: dict[str, float]
    percentile_resolution: float | None = Field(description="Percentiles come from a histogram of this bin width.")


class IndexRunResult(BaseModel):
    derived_asset_id: uuid.UUID = Field(description="The stored GeoTIFF, an ordinary raster asset (origin derived).")
    duplicate_of_asset_id: uuid.UUID | None
    index: str
    scene_id: str
    file_name: str
    output_sha256: str
    size_bytes: int
    width: int
    height: int
    crs: str | None
    resolution_m: float
    nodata: float
    formula: str
    range_mask: list[float] | None = Field(description="Values outside this inclusive range were masked, not clamped.")
    statistics: IndexRunStatistics
    counts: dict[str, int]
    inputs: list[IndexRunInput]
    product: dict[str, Any]
    validation: dict[str, Any]


class IndexRunOut(BaseModel):
    job_id: uuid.UUID
    index: str
    scene_id: str
    status: JobStatus
    stage: str | None
    progress: float | None = Field(description="0-1 from work actually done, or null when not measurable.")
    cancel_requested: bool
    attempt: int
    max_attempts: int
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    error: JobError | None
    inputs: list[IndexRunInput]
    range_mask: list[float] | None
    asset_id: uuid.UUID | None
    result: IndexRunResult | None
    display: dict[str, Any] | None = Field(description="The colour ramp and legend stops for drawing the result.")


class IndexRunsOut(BaseModel):
    runs: list[IndexRunOut]
