"""Where a ground photograph is, as far as a person has said so.

A photograph's location history is append-only (`photo_location`). Its current state is the latest row:

- no rows, or `cleared`: no location;
- `candidate`: coordinates read from the file's EXIF GPS block. Unverified metadata, never drawn as a location;
- `confirmed`: a person stands behind the position (they confirmed the EXIF candidate, typed coordinates, or chose a
  point on the map).

A confirmed position says where the photograph is recorded as having been taken or placed. It is not the position of
anything seen in the picture, and it does not georeference the pixels or the model's boxes.
"""

import math
import uuid
from collections import defaultdict
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from geopoc.db.models import Asset, ObservationRecord, PhotoLocation, WorkspaceUser
from geopoc.ingestion.gps import GpsReading

Action = Literal["confirm_exif", "set_position", "clear"]
NOTE = (
    "A confirmed location records where this photograph was taken or placed, as stated by a person. It is not the "
    "position of anything seen in the picture; findings in the photograph remain positions in the image only. The "
    "software cannot check that a position is correct."
)
NULL_ISLAND = "0, 0 is almost always a missing value, so it is refused as a photograph location."


class LocationError(Exception):
    def __init__(self, status: int, code: str, title: str, detail: str | None = None) -> None:
        super().__init__(title)
        self.status, self.code, self.title, self.detail = status, code, title, detail


def check_position(latitude: float | None, longitude: float | None) -> tuple[float, float]:
    """Latitude and longitude in decimal degrees (WGS 84), or a LocationError saying what is wrong."""
    if latitude is None or longitude is None:
        raise LocationError(422, "LOCATION_INCOMPLETE", "Enter both a latitude and a longitude")
    if not (math.isfinite(latitude) and math.isfinite(longitude)):
        raise LocationError(422, "LOCATION_NOT_FINITE", "Latitude and longitude must be finite numbers")
    if not -90 <= latitude <= 90:
        raise LocationError(422, "LOCATION_LATITUDE_RANGE", "Latitude must be between -90 and 90 degrees")
    if not -180 <= longitude <= 180:
        raise LocationError(422, "LOCATION_LONGITUDE_RANGE", "Longitude must be between -180 and 180 degrees")
    latitude, longitude = round(latitude, 8) + 0.0, round(longitude, 8) + 0.0
    if latitude == 0 and longitude == 0:
        raise LocationError(422, "LOCATION_NULL_ISLAND", "That position is not accepted", NULL_ISLAND)
    return latitude, longitude


def history(session: Session, asset_id: uuid.UUID) -> list[PhotoLocation]:
    return list(
        session.scalars(
            select(PhotoLocation).where(PhotoLocation.asset_id == asset_id).order_by(PhotoLocation.version_no)
        )
    )


def current(rows: list[PhotoLocation]) -> PhotoLocation | None:
    """The latest row, or None. A `cleared` row is returned: the caller treats it as no location."""
    return rows[-1] if rows else None


def latest_candidate(rows: list[PhotoLocation]) -> PhotoLocation | None:
    return next((r for r in reversed(rows) if r.status == "candidate"), None)


def is_located(row: PhotoLocation | None) -> bool:
    return row is not None and row.status == "confirmed"


def _append(
    session: Session,
    asset: Asset,
    rows: list[PhotoLocation],
    *,
    status: str,
    source: str,
    latitude: float | None,
    longitude: float | None,
    actor: WorkspaceUser,
    request_id: str | None,
    reason: str | None = None,
    candidate_version_no: int | None = None,
    details: dict | None = None,
) -> PhotoLocation:
    row = PhotoLocation(
        project_id=asset.project_id,
        asset_id=asset.id,
        version_no=(rows[-1].version_no if rows else 0) + 1,
        status=status,
        source=source,
        latitude=latitude,
        longitude=longitude,
        candidate_version_no=candidate_version_no,
        reason=reason,
        details=details or {},
        source_checksum=asset.blob_sha256,
        actor_id=actor.id,
        actor_label=actor.display_name,
        request_id=request_id,
    )
    session.add(row)
    session.flush()
    return row


def lock(session: Session, asset: Asset) -> list[PhotoLocation]:
    """Serialise changes to one photograph's location, and return its history."""
    session.execute(select(Asset.id).where(Asset.id == asset.id).with_for_update())
    return history(session, asset.id)


def record_candidate(
    session: Session, asset: Asset, reading: GpsReading, *, actor: WorkspaceUser, request_id: str | None
) -> PhotoLocation | None:
    """Store a usable EXIF reading as a candidate. Never replaces a confirmed position, and never repeats itself."""
    if reading.state != "candidate" or reading.latitude is None or reading.longitude is None:
        return None
    rows = lock(session, asset)
    if is_located(current(rows)):
        raise LocationError(
            409,
            "LOCATION_ALREADY_CONFIRMED",
            "This photograph already has a confirmed location",
            "Clear the location first if you want to read the file's GPS position again.",
        )
    previous = latest_candidate(rows)
    if previous and (previous.latitude, previous.longitude) == (reading.latitude, reading.longitude):
        raise LocationError(409, "EXIF_CANDIDATE_EXISTS", "This GPS position was already read from the file")
    return _append(
        session,
        asset,
        rows,
        status="candidate",
        source="exif",
        latitude=reading.latitude,
        longitude=reading.longitude,
        actor=actor,
        request_id=request_id,
        details=reading.details,
    )


@dataclass
class Change:
    row: PhotoLocation
    action: str


def apply(
    session: Session,
    asset: Asset,
    action: Action,
    *,
    latitude: float | None,
    longitude: float | None,
    method: str | None,
    reason: str | None,
    expected_version: int,
    actor: WorkspaceUser,
    request_id: str | None,
) -> Change:
    """Record a person's decision as a new row. Validated here; nothing is stored unless it is accepted."""
    rows = lock(session, asset)
    now = current(rows)
    if expected_version != (now.version_no if now else 0):
        raise LocationError(
            409,
            "LOCATION_VERSION_CONFLICT",
            "The location changed since you reviewed it",
            f"You reviewed version {expected_version}; the current version is {now.version_no if now else 0}.",
        )

    if action == "confirm_exif":
        candidate = latest_candidate(rows)
        if candidate is None:
            raise LocationError(
                409,
                "NO_EXIF_CANDIDATE",
                "There is no EXIF position to confirm",
                "The photograph carries no usable GPS position. Enter one or choose it on the map instead.",
            )
        if is_located(now) and (now.latitude, now.longitude) == (candidate.latitude, candidate.longitude):
            raise LocationError(409, "LOCATION_UNCHANGED", "The photograph already has this confirmed position")
        row = _append(
            session,
            asset,
            rows,
            status="confirmed",
            source="exif",
            latitude=candidate.latitude,
            longitude=candidate.longitude,
            actor=actor,
            request_id=request_id,
            reason=reason,
            candidate_version_no=candidate.version_no,
            details={"confirmed_from": "exif_candidate", "datum": candidate.details.get("datum")},
        )
        return Change(row, "confirm_exif")

    if action == "set_position":
        if method not in ("manual_entry", "map_selection"):
            raise LocationError(
                422, "LOCATION_METHOD_REQUIRED", "Say whether the position was typed or chosen on the map"
            )
        lat, lon = check_position(latitude, longitude)
        if is_located(now) and (now.latitude, now.longitude) == (lat, lon):
            raise LocationError(409, "LOCATION_UNCHANGED", "The photograph already has this confirmed position")
        row = _append(
            session,
            asset,
            rows,
            status="confirmed",
            source=method,
            latitude=lat,
            longitude=lon,
            actor=actor,
            request_id=request_id,
            reason=reason,
            details={"entered_as": "decimal degrees, WGS 84 (EPSG:4326)"},
        )
        return Change(row, "set_position")

    # clear
    if now is None or now.status == "cleared":
        raise LocationError(409, "LOCATION_NOTHING_TO_CLEAR", "This photograph has no location to clear")
    row = _append(
        session,
        asset,
        rows,
        status="cleared",
        source="none",
        latitude=None,
        longitude=None,
        actor=actor,
        request_id=request_id,
        reason=reason,
        details={"cleared": {"status": now.status, "source": now.source, "version_no": now.version_no}},
    )
    return Change(row, "clear")


def latest_by_asset(session: Session, project_id: uuid.UUID) -> dict[uuid.UUID, PhotoLocation]:
    """The current row of every photograph in a project that has any history."""
    latest: dict[uuid.UUID, PhotoLocation] = {}
    for row in session.scalars(
        select(PhotoLocation).where(PhotoLocation.project_id == project_id).order_by(PhotoLocation.version_no)
    ):
        latest[row.asset_id] = row  # later versions overwrite earlier ones
    return latest


def observation_counts(session: Session, asset_ids: list[uuid.UUID]) -> dict[uuid.UUID, dict[str, int]]:
    """Per photograph: how many observations exist, by their current review status."""
    counts: dict[uuid.UUID, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    if asset_ids:
        for asset_id, status in session.execute(
            select(ObservationRecord.asset_id, ObservationRecord.review_status).where(
                ObservationRecord.asset_id.in_(asset_ids)
            )
        ):
            counts[asset_id][status] += 1
    return {k: dict(v) for k, v in counts.items()}


SOURCE_LABELS = {
    "exif": "EXIF GPS position, confirmed by a person",
    "manual_entry": "Coordinates typed by a person",
    "map_selection": "Point chosen on the map by a person",
}
EXPORT_NOTE = (
    "Point features are photographs with a person-confirmed location: where the photograph is recorded as taken or "
    "placed. They are not the positions of findings in the picture. Photographs without a confirmed location "
    "(including those with only an unverified EXIF candidate) are counted, never placed."
)


def geojson_features(session: Session, project_id: uuid.UUID) -> tuple[list[dict], dict]:
    """GeoJSON Point features for the project's photographs with a confirmed location, and a summary of the rest."""
    photos = list(
        session.scalars(
            select(Asset)
            .where(Asset.project_id == project_id, Asset.modality == "ground_image")
            .order_by(Asset.ingested_at, Asset.id)
        )
        .unique()
        .all()
    )
    latest = latest_by_asset(session, project_id)
    counts = observation_counts(session, [a.id for a in photos])
    features = []
    for asset in photos:
        row = latest.get(asset.id)
        if not is_located(row) or row.latitude is None or row.longitude is None:
            continue
        by_status = counts.get(asset.id, {})
        features.append(
            {
                "type": "Feature",
                "id": f"photo:{asset.id}",
                "geometry": {"type": "Point", "coordinates": [row.longitude, row.latitude]},
                "properties": {
                    "feature_type": "photo_location",
                    "asset_id": str(asset.id),
                    "source_file": asset.original_filename,
                    "source_sha256": asset.blob_sha256,
                    "location_status": "confirmed",
                    "location_source": row.source,
                    "location_source_label": SOURCE_LABELS[row.source],
                    "confirmed_by": row.actor_label,
                    "confirmed_at": row.created_at.isoformat(),
                    "location_version": row.version_no,
                    "observations_total": sum(by_status.values()),
                    "observations_by_review_status": by_status,
                },
            }
        )
    summary = {
        "located": len(features),
        "not_located": len(photos) - len(features),
        "unconfirmed_exif_candidates": sum(
            1 for a in photos if (latest.get(a.id) and latest[a.id].status == "candidate")
        ),
        "note": EXPORT_NOTE,
    }
    return features, summary


def export_history(session: Session, project_id: uuid.UUID) -> list[dict]:
    """Every photograph that has any location history, with that history, for the full JSON export."""
    rows = list(
        session.scalars(
            select(PhotoLocation).where(PhotoLocation.project_id == project_id).order_by(PhotoLocation.version_no)
        )
    )
    by_asset: dict[uuid.UUID, list[PhotoLocation]] = defaultdict(list)
    for row in rows:
        by_asset[row.asset_id].append(row)
    names = {
        asset_id: (name, sha)
        for asset_id, name, sha in session.execute(
            select(Asset.id, Asset.original_filename, Asset.blob_sha256).where(Asset.id.in_(list(by_asset)))
        )
    }
    out = []
    for asset_id, history_rows in by_asset.items():
        now = history_rows[-1]
        out.append(
            {
                "asset_id": str(asset_id),
                "source_file": names[asset_id][0],
                "source_sha256": names[asset_id][1],
                "current_state": "none" if now.status == "cleared" else now.status,
                "history": [
                    {
                        "version_no": r.version_no,
                        "status": r.status,
                        "source": r.source,
                        "latitude": r.latitude,
                        "longitude": r.longitude,
                        "crs_id": r.crs_id,
                        "candidate_version_no": r.candidate_version_no,
                        "reason": r.reason,
                        "actor": r.actor_label,
                        "at": r.created_at.isoformat(),
                    }
                    for r in history_rows
                ],
            }
        )
    return out
