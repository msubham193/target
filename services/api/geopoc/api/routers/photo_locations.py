"""Where ground photographs are, as recorded by a person.

A photograph's EXIF GPS position is held as an unverified candidate; it becomes a location only when a person confirms
it, types coordinates, or chooses a point on the map. Every change is a new append-only version with who made it and
when. The stored original is never written, and nothing here touches the photograph's observations.
"""

import hashlib
import uuid
from pathlib import Path

from fastapi import APIRouter
from PIL import Image, UnidentifiedImageError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from geopoc.api.deps import ProfileDep, RequestIdDep, SessionDep, record_audit
from geopoc.api.errors import ProblemError, problem_responses
from geopoc.api.schemas import (
    CameraViewOut,
    PhotoLocationApply,
    PhotoLocationCurrent,
    PhotoLocationExif,
    PhotoLocationItem,
    PhotoLocationOut,
    PhotoLocationProblem,
    PhotoLocationRow,
    PhotoLocationsOut,
    ReadExifOut,
)
from geopoc.db.models import Asset, PhotoLocation, Project
from geopoc.geo import georeference, photolocation
from geopoc.geo.photolocation import LocationError
from geopoc.ingestion import camera_view
from geopoc.ingestion.gps import PROBLEMS, GpsReading, read_gps

router = APIRouter(prefix="/api/v1", tags=["photo-locations"], responses=problem_responses(422))

LIST_NOTE = (
    "Only confirmed locations are drawn. An EXIF position is a candidate until a person confirms it, and a photograph "
    "without a confirmed location is never placed on the map."
)


def _photo(session: Session, asset_id: uuid.UUID) -> Asset:
    asset = session.get(Asset, asset_id)
    if asset is None:
        raise ProblemError(404, "ASSET_NOT_FOUND", "Asset not found")
    if asset.modality != "ground_image":
        raise ProblemError(409, "NOT_A_PHOTOGRAPH", "Only ground photographs have a photo location")
    return asset


def _problems(codes: list[str]) -> list[PhotoLocationProblem]:
    return [PhotoLocationProblem(code=c, message=PROBLEMS.get(c, c)) for c in codes]


def _row(row: PhotoLocation) -> PhotoLocationRow:
    return PhotoLocationRow(
        version_no=row.version_no,
        status=row.status,
        source=row.source,
        latitude=row.latitude,
        longitude=row.longitude,
        crs_id=row.crs_id,
        candidate_version_no=row.candidate_version_no,
        reason=row.reason,
        actor_label=row.actor_label,
        created_at=row.created_at,
    )


def _exif_state(asset: Asset, rows: list[PhotoLocation]) -> PhotoLocationExif:
    candidate = photolocation.latest_candidate(rows)
    if candidate is not None:
        return PhotoLocationExif(
            state="candidate",
            candidate_version_no=candidate.version_no,
            latitude=candidate.latitude,
            longitude=candidate.longitude,
            datum=(candidate.details or {}).get("datum"),
            problems=[],
        )
    metadata = asset.extracted_metadata or {}
    gps = metadata.get("exif_gps")
    if gps is None:
        # Ingested before GPS positions were read: the file may still hold one.
        present = bool((metadata.get("exif") or {}).get("gps_tags_present"))
        return PhotoLocationExif(
            state="not_read" if present else "none",
            candidate_version_no=None,
            latitude=None,
            longitude=None,
            datum=None,
            problems=[],
        )
    state = {"candidate": "candidate", "unusable": "unusable"}.get(gps.get("state"), "none")
    # A candidate that was read at upload but has no row would be a fault; report it as unread rather than invent one.
    state = "not_read" if state == "candidate" else state
    return PhotoLocationExif(
        state=state,
        candidate_version_no=None,
        latitude=None,
        longitude=None,
        datum=None,
        problems=_problems(list(gps.get("problems") or [])),
    )


def _current(rows: list[PhotoLocation]) -> PhotoLocationCurrent:
    """The confirmed location, or none. An EXIF candidate is reported under `exif`, never as the location.

    `version_no` is still the latest row (candidate or not), because that is what a change must be based on.
    """
    now = photolocation.current(rows)
    if now is None or now.status != "confirmed":
        return PhotoLocationCurrent(
            state="none",
            version_no=now.version_no if now else 0,
            source=None,
            latitude=None,
            longitude=None,
            crs_id=None,
            reason=now.reason if now and now.status == "cleared" else None,
            actor_label=now.actor_label if now and now.status == "cleared" else None,
            changed_at=now.created_at if now and now.status == "cleared" else None,
        )
    return PhotoLocationCurrent(
        state=now.status,
        version_no=now.version_no,
        source=now.source,
        latitude=now.latitude,
        longitude=now.longitude,
        crs_id=now.crs_id,
        reason=now.reason,
        actor_label=now.actor_label,
        changed_at=now.created_at,
    )


def location_out(session: Session, asset: Asset) -> PhotoLocationOut:
    rows = photolocation.history(session, asset.id)
    return PhotoLocationOut(
        asset_id=asset.id,
        source_checksum=asset.blob_sha256,
        current=_current(rows),
        exif=_exif_state(asset, rows),
        history=[_row(r) for r in rows],
        note=photolocation.NOTE,
    )


def _check_original(path: Path, asset: Asset) -> None:
    """The stored original is read only when its bytes still match the checksum recorded at upload."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    if digest.hexdigest() != asset.blob_sha256:
        raise ProblemError(
            409, "ORIGINAL_CHECKSUM_MISMATCH", "The stored original does not match its checksum", "It was not read."
        )


def _problem(exc: LocationError) -> ProblemError:
    return ProblemError(exc.status, exc.code, exc.title, exc.detail)


@router.get("/assets/{asset_id}/location", response_model=PhotoLocationOut, responses=problem_responses(404, 409))
def get_location(asset_id: uuid.UUID, session: SessionDep):
    """The photograph's current location state, any EXIF candidate, and the full history."""
    return location_out(session, _photo(session, asset_id))


@router.post(
    "/assets/{asset_id}/location",
    response_model=PhotoLocationOut,
    status_code=201,
    responses=problem_responses(400, 401, 404, 409, 422),
)
def change_location(
    asset_id: uuid.UUID, body: PhotoLocationApply, session: SessionDep, profile: ProfileDep, request_id: RequestIdDep
):
    """Record a reviewed decision as a new version: confirm the EXIF position, enter or choose one, or clear it."""
    asset = _photo(session, asset_id)
    if not body.confirm:
        raise ProblemError(422, "CONFIRMATION_REQUIRED", "Review the position and confirm it", "Nothing was recorded.")
    try:
        change = photolocation.apply(
            session,
            asset,
            body.action,
            latitude=body.latitude,
            longitude=body.longitude,
            method=body.method,
            reason=body.reason,
            expected_version=body.expected_version,
            actor=profile,
            request_id=request_id,
        )
    except LocationError as exc:
        session.rollback()
        if exc.status == 422:
            # A refused position is worth an audit line; nothing about it is stored.
            record_audit(
                session,
                actor=profile,
                action="photo_location.refused",
                entity_type="asset",
                entity_id=asset.id,
                request_id=request_id,
                details={"action": body.action, "code": exc.code},
            )
            session.commit()
        raise _problem(exc) from exc
    except IntegrityError as exc:  # two requests raced past the lock
        session.rollback()
        raise ProblemError(409, "LOCATION_VERSION_CONFLICT", "The location changed since you reviewed it") from exc
    row = change.row
    record_audit(
        session,
        actor=profile,
        action=f"photo_location.{change.action}",
        entity_type="asset",
        entity_id=asset.id,
        request_id=request_id,
        details={"version": row.version_no, "status": row.status, "source": row.source},
    )
    session.commit()
    return location_out(session, asset)


@router.post(
    "/assets/{asset_id}/location/read-exif",
    response_model=ReadExifOut,
    responses=problem_responses(401, 404, 409),
)
def read_exif_position(asset_id: uuid.UUID, session: SessionDep, profile: ProfileDep, request_id: RequestIdDep):
    """Read the GPS position from the stored original again (for photographs ingested before GPS was read)."""
    asset = _photo(session, asset_id)
    path = georeference.original_path(session, asset)
    if path is None:
        raise ProblemError(409, "ORIGINAL_MISSING", "The stored original is missing", "It cannot be read.")
    _check_original(path, asset)
    try:
        with Image.open(path) as image:
            reading = read_gps(image.getexif())
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError):
        reading = GpsReading("unusable", problems=("GPS_UNREADABLE",))

    try:
        recorded = photolocation.record_candidate(session, asset, reading, actor=profile, request_id=request_id)
    except LocationError as exc:
        session.rollback()
        raise _problem(exc) from exc
    outcome = "candidate_recorded" if recorded else ("no_gps" if reading.state == "absent" else "unusable")
    if reading.state == "candidate" and recorded is None:  # pragma: no cover - record_candidate raises instead
        outcome = "unusable"
    record_audit(
        session,
        actor=profile,
        action="photo_location.read_exif",
        entity_type="asset",
        entity_id=asset.id,
        request_id=request_id,
        details={"outcome": outcome, "problems": list(reading.problems)},
    )
    session.commit()
    return ReadExifOut(
        outcome=outcome, problems=_problems(list(reading.problems)), location=location_out(session, asset)
    )


CAMERA_VIEW_NOTE = (
    "The direction and lens figures are what the file states. They are not checked: a phone compass can be off by tens "
    "of degrees, and the file does not say how good the reading was. They are read from the stored original each time "
    "and never stored. Nothing is placed on the map from them; a person decides what they mean."
)


@router.get("/assets/{asset_id}/camera-view", response_model=CameraViewOut, responses=problem_responses(404, 409))
def get_camera_view(asset_id: uuid.UUID, session: SessionDep):
    """Which way the camera faced and its lens figures, read from the photograph's EXIF. Read-only."""
    asset = _photo(session, asset_id)
    path = georeference.original_path(session, asset)
    if path is None:
        raise ProblemError(409, "ORIGINAL_MISSING", "The stored original is missing", "It cannot be read.")
    _check_original(path, asset)
    try:
        with Image.open(path) as image:
            view = camera_view.read_camera_view(image.getexif())
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError):
        view = camera_view.CameraView(problems=("EXIF_UNREADABLE",))
    return CameraViewOut(
        asset_id=asset.id,
        heading_deg=view.heading_deg,
        heading_reference=view.heading_reference,
        focal_length_35mm_mm=view.focal_length_35mm_mm,
        focal_length_mm=view.focal_length_mm,
        problems=[PhotoLocationProblem(code=c, message=camera_view.PROBLEMS[c]) for c in view.problems],
        note=CAMERA_VIEW_NOTE,
    )


@router.get(
    "/projects/{project_id}/photo-locations", response_model=PhotoLocationsOut, responses=problem_responses(404)
)
def list_photo_locations(project_id: uuid.UUID, session: SessionDep):
    """Every ground photograph in the project with its location state. Coordinates are given only when confirmed."""
    if session.get(Project, project_id) is None:
        raise ProblemError(404, "PROJECT_NOT_FOUND", "Project not found")
    photos = (
        session.scalars(
            select(Asset)
            .where(Asset.project_id == project_id, Asset.modality == "ground_image")
            .order_by(Asset.ingested_at, Asset.id)
        )
        .unique()
        .all()
    )
    latest = photolocation.latest_by_asset(session, project_id)
    counts = photolocation.observation_counts(session, [a.id for a in photos])
    items = []
    for asset in photos:
        row = latest.get(asset.id)
        state = "none" if row is None or row.status == "cleared" else row.status
        confirmed = state == "confirmed"
        items.append(
            PhotoLocationItem(
                asset_id=asset.id,
                filename=asset.original_filename,
                state=state,
                source=row.source if row is not None and state != "none" else None,
                latitude=row.latitude if confirmed else None,
                longitude=row.longitude if confirmed else None,
                version_no=row.version_no if row is not None else 0,
                confirmed_by=row.actor_label if confirmed else None,
                confirmed_at=row.created_at if confirmed else None,
                observations=sum(counts.get(asset.id, {}).values()),
            )
        )
    return PhotoLocationsOut(
        items=items,
        confirmed=sum(i.state == "confirmed" for i in items),
        candidates=sum(i.state == "candidate" for i in items),
        without_location=sum(i.state == "none" for i in items),
        note=LIST_NOTE,
    )
