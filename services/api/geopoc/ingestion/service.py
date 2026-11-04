"""Turns a fully received, signature-checked upload into a stored asset with metadata and an audit record."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from geoalchemy2.shape import from_shape
from sqlalchemy import select
from sqlalchemy.orm import Session

from geopoc.api.deps import record_audit
from geopoc.config import get_settings
from geopoc.db.models import Asset, Blob, Project, RasterMetadata, VideoMetadata, WorkspaceUser
from geopoc.geo import displaycopy, photolocation
from geopoc.ingestion import mapsheet
from geopoc.ingestion.image import inspect_image
from geopoc.ingestion.raster import inspect_raster
from geopoc.ingestion.rules import Inspection
from geopoc.ingestion.video import inspect_video
from geopoc.jobs import queue
from geopoc.storage import store

INSPECTORS = {
    "ground_image": inspect_image,
    "overhead_raster": inspect_raster,
    "video": inspect_video,
}

# Flags that are normal facts about an asset, not validation warnings.
INFORMATIONAL_FLAGS = {"NOT_GEOREFERENCED", "TIMESTAMP_UNKNOWN", "DUPLICATE_ASSET", *mapsheet.INFORMATIONAL_FLAGS}


@dataclass
class IngestResult:
    asset: Asset
    duplicate_of_asset_id: object | None
    stored_original_reused: bool


def ingest(
    session: Session,
    *,
    project: Project,
    profile: WorkspaceUser,
    filename: str,
    modality: str,
    family: str,
    temp_path: Path,
    sha256: str,
    size_bytes: int,
    request_id: str,
    inspector: Callable[..., Inspection] | None = None,
    intake: dict[str, Any] | None = None,
) -> IngestResult:
    """Validate readability, extract metadata, store the original once, and persist the asset.

    `inspector` replaces the modality's usual inspector (map-sheet intake), and `intake` is added to the audit record.
    Raises UnreadableAsset or ToolingUnavailable before anything is stored. The caller owns `temp_path`.
    """
    inspection: Inspection = (inspector or INSPECTORS[modality])(temp_path, family, get_settings())

    blob = session.get(Blob, sha256)
    reused = blob is not None
    duplicate_of = None
    restore_missing = False
    if reused:
        stored = store.absolute_path(blob.storage_path)
        if not stored.is_file():
            # The checksum proves this upload is the same content, so it can put the lost original back.
            restore_missing = True
        elif stored.stat().st_size != blob.size_bytes:
            raise store.StorageError("The stored original for this content is damaged.")
        # A duplicate is linked to the first asset in this project with the same content.
        duplicate_of = session.scalars(
            select(Asset.id)
            .where(Asset.project_id == project.id, Asset.blob_sha256 == sha256)
            .order_by(Asset.ingested_at, Asset.id)
            .limit(1)
        ).first()
    else:
        session.add(Blob(sha256=sha256, size_bytes=size_bytes, storage_path=store.relative_path(sha256)))
        session.flush()  # the asset row references it

    if duplicate_of is not None:
        inspection.flag("DUPLICATE_ASSET")
    warnings = [flag for flag in inspection.quality_flags if flag not in INFORMATIONAL_FLAGS]

    asset = Asset(
        project_id=project.id,
        blob_sha256=sha256,
        original_filename=filename,
        modality=modality,
        detected_format=inspection.detected_format,
        media_type=inspection.media_type,
        size_bytes=size_bytes,
        status="validated_with_warnings" if warnings else "validated",
        duplicate_of_asset_id=duplicate_of,
        extracted_metadata=inspection.metadata,
        unknowns=inspection.unknowns,
        quality_flags=inspection.quality_flags,
        acquisition_time=inspection.acquisition_time,
        uploaded_by=profile.id,
    )
    session.add(asset)
    session.flush()

    if inspection.raster is not None:
        fields = dict(inspection.raster)
        footprint = fields.pop("footprint")
        session.add(
            RasterMetadata(
                asset_id=asset.id,
                footprint_4326=from_shape(footprint, srid=4326) if footprint is not None else None,
                **fields,
            )
        )
    if inspection.video is not None:
        session.add(VideoMetadata(asset_id=asset.id, **inspection.video))
    if inspection.photo_gps is not None:
        # An EXIF position is stored as an unverified candidate, attributed to the uploader. It is not a location.
        photolocation.record_candidate(session, asset, inspection.photo_gps, actor=profile, request_id=request_id)

    record_audit(
        session,
        actor=profile,
        action="asset.upload",
        entity_type="asset",
        entity_id=asset.id,
        request_id=request_id,
        details={
            "project_id": str(project.id),
            "filename": filename,
            "modality": modality,
            "sha256": sha256,
            "size_bytes": size_bytes,
            "status": asset.status,
            "duplicate_of_asset_id": str(duplicate_of) if duplicate_of else None,
            "stored_original_reused": reused,
            "stored_original_restored": restore_missing,
            **({"exif_gps": inspection.photo_gps.state} if inspection.photo_gps is not None else {}),
            **({"map_sheet": intake} if intake is not None else {}),
        },
    )
    # The preview is made in the background; the upload does not wait for it.
    if not inspection.no_preview:
        queue.enqueue(session, kind="preview", project_id=project.id, asset_id=asset.id, created_by=profile.id)
    if inspection.raster is not None:
        # Band statistics (Phase 3b), and a verified display copy for large rasters without overviews.
        queue.enqueue(session, kind="statistics", project_id=project.id, asset_id=asset.id, created_by=profile.id)
        if displaycopy.needs_copy(
            inspection.raster["width"],
            inspection.raster["height"],
            inspection.metadata.get("overview_levels"),
            get_settings().display_copy_min_side,
        ):
            queue.enqueue(session, kind="display_copy", project_id=project.id, asset_id=asset.id, created_by=profile.id)
    session.flush()

    if not reused or restore_missing:
        store.commit_original(temp_path, sha256)
    try:
        session.commit()
    except Exception:
        session.rollback()
        if not reused:
            store.remove_original(sha256)  # do not leave a file no database row refers to
        raise
    session.refresh(asset)
    return IngestResult(asset=asset, duplicate_of_asset_id=duplicate_of, stored_original_reused=reused)
