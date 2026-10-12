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
