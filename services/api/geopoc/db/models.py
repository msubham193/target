import uuid
from datetime import UTC, datetime
from typing import Any

from geoalchemy2 import Geometry, WKBElement
from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, Integer, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class WorkspaceUser(Base):
    """A named local profile used for audit attribution. Not an authentication account."""

    __tablename__ = "workspace_user"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    display_name: Mapped[str] = mapped_column(Text, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Project(Base):
    __tablename__ = "project"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    # Display copy in EPSG:4326. User-entered context only: never evidence of where an asset was captured.
    aoi_geom: Mapped[WKBElement | None] = mapped_column(
        Geometry(geometry_type="GEOMETRY", srid=4326, spatial_index=False)
    )
    # The AOI exactly as entered (geometry + crs_id) plus the transformation record.
    aoi_source: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    source_preference: Mapped[str] = mapped_column(Text, default="local_only")
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspace_user.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    creator: Mapped[WorkspaceUser] = relationship(lazy="joined")


class Blob(Base):
    """One stored original file, identified by its content. Written once, never modified."""

    __tablename__ = "blob"

    sha256: Mapped[str] = mapped_column(Text, primary_key=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    storage_path: Mapped[str] = mapped_column(Text)  # relative to DATA_ROOT
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Asset(Base):
    """One upload into a project. Several assets may share a blob; copies are not independent evidence."""

    __tablename__ = "asset"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("project.id"))
    blob_sha256: Mapped[str] = mapped_column(ForeignKey("blob.sha256"))
    original_filename: Mapped[str] = mapped_column(Text)
    modality: Mapped[str] = mapped_column(Text)
    detected_format: Mapped[str] = mapped_column(Text)
    media_type: Mapped[str] = mapped_column(Text)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(Text)
    duplicate_of_asset_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("asset.id"))
    origin: Mapped[str] = mapped_column(Text, default="upload")
    extracted_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict)
    # field name -> reason it is unknown. Unknown values are recorded, never guessed.
    unknowns: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    quality_flags: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    acquisition_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    uploaded_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspace_user.id"))
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    uploader: Mapped[WorkspaceUser] = relationship(lazy="joined")
    raster: Mapped["RasterMetadata | None"] = relationship(lazy="joined", uselist=False)
    video: Mapped["VideoMetadata | None"] = relationship(lazy="joined", uselist=False)


class RasterMetadata(Base):
    __tablename__ = "raster_metadata"

    asset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("asset.id"), primary_key=True)
    driver: Mapped[str] = mapped_column(Text)
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    band_count: Mapped[int] = mapped_column(Integer)
    dtypes: Mapped[list[str]] = mapped_column(ARRAY(Text))
    nodata: Mapped[list[Any]] = mapped_column(JSONB)
    crs_status: Mapped[str] = mapped_column(Text)  # valid | missing | invalid
    crs_identifier: Mapped[str | None] = mapped_column(Text)
    crs_name: Mapped[str | None] = mapped_column(Text)
    crs_wkt: Mapped[str | None] = mapped_column(Text)
    georeferenced: Mapped[bool] = mapped_column(Boolean)
    geotransform: Mapped[list[float] | None] = mapped_column(ARRAY(Float))  # GDAL order, verbatim
    pixel_size_x: Mapped[float | None] = mapped_column(Float)
    pixel_size_y: Mapped[float | None] = mapped_column(Float)
    pixel_units: Mapped[str | None] = mapped_column(Text)
    bounds_native: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # Only present when the raster is validly georeferenced.
    footprint_4326: Mapped[WKBElement | None] = mapped_column(
        Geometry(geometry_type="GEOMETRY", srid=4326, spatial_index=False)
    )
    # Everything above is what the file declares and never changes (a trigger refuses it). These two point to the
    # georeferencing version in use: 0 / None means the file's own metadata.
    georef_version: Mapped[int] = mapped_column(Integer, default=0)
    current_assertion_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("georef_assertion.id"))
    current_assertion: Mapped["GeorefAssertion | None"] = relationship(
        lazy="joined", foreign_keys=[current_assertion_id]
    )


class GeorefAssertion(Base):
    """One version of a raster's georeferencing recorded by a person, with its validation. Append-only.

    It never changes the stored file: the original keeps its bytes and its own (missing or invalid) metadata, and
    this record says which CRS and geotransform are used for it, who said so, why, and what was checked.
    """

    __tablename__ = "georef_assertion"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    asset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("asset.id"))
    version_no: Mapped[int] = mapped_column(Integer)
    operation: Mapped[str] = mapped_column(Text)  # assign_crs | set_geotransform | revert_to_file
    method: Mapped[str] = mapped_column(Text)
    crs_id: Mapped[str | None] = mapped_column(Text)
    crs_name: Mapped[str | None] = mapped_column(Text)
    crs_wkt: Mapped[str | None] = mapped_column(Text)
    geotransform: Mapped[list[float] | None] = mapped_column(ARRAY(Float))  # GDAL order
    geotransform_source: Mapped[str | None] = mapped_column(Text)
    world_file: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    previous: Mapped[dict[str, Any]] = mapped_column(JSONB)
    result: Mapped[dict[str, Any]] = mapped_column(JSONB)
    validation: Mapped[dict[str, Any]] = mapped_column(JSONB)
    georeferenced: Mapped[bool] = mapped_column(Boolean)
    footprint_4326: Mapped[WKBElement | None] = mapped_column(
        Geometry(geometry_type="GEOMETRY", srid=4326, spatial_index=False)
    )
    source_checksum: Mapped[str] = mapped_column(Text)
    actor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspace_user.id"))
    actor_label: Mapped[str] = mapped_column(Text)
    request_id: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PhotoLocation(Base):
    """One step in the location history of a ground photograph. Append-only; the latest row is the current state.

    A `candidate` is a position read from the photo's EXIF GPS block: unverified metadata, not a location. A
    `confirmed` row is a position a person stands behind (they confirmed the candidate, typed coordinates, or chose a
    point on the map). `cleared` means no location. The photo's original bytes and its observations are never touched.
    """

    __tablename__ = "photo_location"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("project.id"))
    asset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("asset.id"))
    version_no: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(Text)  # candidate | confirmed | cleared
    source: Mapped[str] = mapped_column(Text)  # exif | manual_entry | map_selection | none
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    crs_id: Mapped[str] = mapped_column(Text, default="EPSG:4326")
    candidate_version_no: Mapped[int | None] = mapped_column(Integer)
    reason: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    source_checksum: Mapped[str] = mapped_column(Text)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("workspace_user.id"))
    actor_label: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    request_id: Mapped[str | None] = mapped_column(Text)


class MapAnnotation(Base):
    """One version of a mark a person drew on the map: a point, line or polygon with a note. Append-only.

    It belongs to a project and refers to nothing else (no photograph, observation, raster or job), so it cannot be
    mistaken for a finding or a photograph location. `annotation_id` is stable across versions; the latest version is
    the current state and a `deleted` version hides it from the normal views while the history stays.
    """

    __tablename__ = "map_annotation"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    annotation_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("project.id"))
    version_no: Mapped[int] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(Text)  # create | update | delete
    status: Mapped[str] = mapped_column(Text)  # active | deleted
    kind: Mapped[str] = mapped_column(Text)  # point | line | polygon
    geom: Mapped[WKBElement] = mapped_column(Geometry(geometry_type="GEOMETRY", srid=4326, spatial_index=False))
    crs_id: Mapped[str] = mapped_column(Text, default="EPSG:4326")
    note: Mapped[str] = mapped_column(Text, default="")
    reason: Mapped[str | None] = mapped_column(Text)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("workspace_user.id"))
    actor_label: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    request_id: Mapped[str | None] = mapped_column(Text)


class Sidecar(Base):
    """A world file, .prj or .aux.xml received for one raster. Stored unmodified; evidence to review. Append-only.

    Its content is read again from the stored bytes (checksum verified) whenever it is reviewed or used, so what a
    person confirms is always what the file says.
    """

    __tablename__ = "sidecar"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("project.id"))
    asset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("asset.id"))
    kind: Mapped[str] = mapped_column(Text)  # world_file | prj | aux_xml
    original_filename: Mapped[str] = mapped_column(Text)
    blob_sha256: Mapped[str] = mapped_column(ForeignKey("blob.sha256"))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    pairing: Mapped[str] = mapped_column(Text)  # filename_match | explicit
    upload_status: Mapped[str] = mapped_column(Text)  # parsed | malformed | unsupported, at upload time
    parser_version: Mapped[str] = mapped_column(Text)
    uploaded_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspace_user.id"))
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    request_id: Mapped[str | None] = mapped_column(Text)

    uploader: Mapped[WorkspaceUser] = relationship(lazy="joined")


class AssetLineage(Base):
    """How a derived asset was computed from its source assets. Append-only."""

    __tablename__ = "asset_lineage"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    child_asset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("asset.id"))
    parent_asset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("asset.id"))
    relation: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(Text)
    tool: Mapped[str] = mapped_column(Text)
    tool_version: Mapped[str] = mapped_column(Text)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    parent_checksum: Mapped[str] = mapped_column(Text)
    parent_georef_version: Mapped[int] = mapped_column(Integer)
    created_by_job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("job.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class VideoMetadata(Base):
    __tablename__ = "video_metadata"

    asset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("asset.id"), primary_key=True)
    container: Mapped[str] = mapped_column(Text)
    video_codec: Mapped[str | None] = mapped_column(Text)
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    duration_ms: Mapped[int | None] = mapped_column(BigInteger)
    avg_frame_rate: Mapped[str | None] = mapped_column(Text)
    frame_rate: Mapped[float | None] = mapped_column(Float)
    time_base: Mapped[str | None] = mapped_column(Text)
    frame_count: Mapped[int | None] = mapped_column(BigInteger)
    creation_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    has_audio: Mapped[bool] = mapped_column(Boolean)


class Job(Base):
    """A unit of background work. The table is the queue: there is no broker."""

    __tablename__ = "job"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("project.id"))
    asset_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("asset.id"))
    kind: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    stage: Mapped[str | None] = mapped_column(Text)
    progress: Mapped[float | None] = mapped_column(Float)  # null when not measurable
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error_code: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    has_partial_outputs: Mapped[bool] = mapped_column(Boolean, default=False)
    worker_id: Mapped[str | None] = mapped_column(Text)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    retry_of_job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("job.id"))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("workspace_user.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class JobEvent(Base):
    """Readable log of what happened to a job. Append-only."""

    __tablename__ = "job_event"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("job.id"))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    level: Mapped[str] = mapped_column(Text, default="info")
    stage: Mapped[str | None] = mapped_column(Text)
    message: Mapped[str] = mapped_column(Text)


class WorkerHeartbeat(Base):
    """One row per worker process, so the API can say whether a worker is alive and what it can run."""

    __tablename__ = "worker_heartbeat"

    worker_id: Mapped[str] = mapped_column(Text, primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    capabilities: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class Artifact(Base):
    """A file derived from an asset (currently previews). Never the original, and regenerable."""

    __tablename__ = "artifact"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    asset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("asset.id"))
    kind: Mapped[str] = mapped_column(Text)
    variant: Mapped[str] = mapped_column(Text)
    storage_path: Mapped[str] = mapped_column(Text)  # relative to DATA_ROOT
    media_type: Mapped[str] = mapped_column(Text)
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(Text)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_by_job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("job.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PipelineRun(Base):
    """One execution of an analysis pipeline on one asset, with everything needed to trace its outputs."""

    __tablename__ = "pipeline_run"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("job.id"))
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("project.id"))
    asset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("asset.id"))
    pipeline: Mapped[str] = mapped_column(Text)
    model_name: Mapped[str] = mapped_column(Text)
    model_version: Mapped[str] = mapped_column(Text)
    weights_sha256: Mapped[str | None] = mapped_column(Text)
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    device: Mapped[str | None] = mapped_column(Text)
    library_versions: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    summary: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ObservationRecord(Base):
    """A validated observation as the pipeline produced it. `record` is the canonical document and is immutable."""

    __tablename__ = "observation"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("project.id"))
    asset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("asset.id"))
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("pipeline_run.id"))
    pipeline: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(Text)
    review_status: Mapped[str] = mapped_column(Text)
    score_raw: Mapped[float | None] = mapped_column(Float)
    quality_flags: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    record: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # Current values after review. `review_status` is the current state; `category` and `record` stay as the
    # model produced them (a database trigger refuses changes to them).
    current_category: Mapped[str] = mapped_column(Text)
    revision: Mapped[int] = mapped_column(Integer, default=0)


class ObservationRevision(Base):
    """One change to an observation's review state, category or notes. Append-only (R-018, REV-001)."""

    __tablename__ = "observation_revision"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    observation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("observation.id"))
    revision_no: Mapped[int] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(Text)
    status_before: Mapped[str] = mapped_column(Text)
    status_after: Mapped[str] = mapped_column(Text)
    category_before: Mapped[str] = mapped_column(Text)
    category_after: Mapped[str] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)
    actor_kind: Mapped[str] = mapped_column(Text)  # reviewer | system
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("workspace_user.id"))
    actor_label: Mapped[str] = mapped_column(Text)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    request_id: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Conflict(Base):
    """Supported observations that disagree. Members are kept; resolution is documented, never averaged."""

    __tablename__ = "conflict"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("project.id"))
    attribute: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default="open")
    rule: Mapped[str] = mapped_column(Text)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_by_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("pipeline_run.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution_note: Mapped[str | None] = mapped_column(Text)


class ConflictMember(Base):
    __tablename__ = "conflict_member"

    conflict_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("conflict.id"), primary_key=True)
    observation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("observation.id"), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class LinkGroup(Base):
    """Observations that may describe the same thing. A candidate link, not a merge and not corroboration."""

    __tablename__ = "link_group"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("project.id"))
    kind: Mapped[str] = mapped_column(Text)
    method: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default="candidate")
    rationale: Mapped[str] = mapped_column(Text)
    created_by_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("pipeline_run.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class LinkMember(Base):
    __tablename__ = "link_member"

    link_group_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("link_group.id"), primary_key=True)
    observation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("observation.id"), primary_key=True)


class AuditEvent(Base):
    """Append-only."""

    __tablename__ = "audit_event"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("workspace_user.id"))
    action: Mapped[str] = mapped_column(Text)
    entity_type: Mapped[str] = mapped_column(Text)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    request_id: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class CrsDefinition(Base):
    """Explicitly registered custom CRS definitions. Empty until one is approved (U-006, U-007)."""

    __tablename__ = "crs_definition"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    identifier: Mapped[str] = mapped_column(Text, unique=True)
    kind: Mapped[str] = mapped_column(Text)
    wkt2: Mapped[str | None] = mapped_column(Text)
    units: Mapped[str | None] = mapped_column(Text)
    axis_order: Mapped[str | None] = mapped_column(Text)
    area_of_use: Mapped[str | None] = mapped_column(Text)
    source_reference: Mapped[str | None] = mapped_column(Text)
    approved_by: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default="unclaimed")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
