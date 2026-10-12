import uuid
from datetime import UTC, datetime
from typing import Any

from geoalchemy2 import Geometry, WKBElement
from sqlalchemy import DateTime, ForeignKey, Text
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
