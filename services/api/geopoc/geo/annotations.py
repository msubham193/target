"""Map annotations: marks a person draws on the map (points, lines, polygons) with a note.

An annotation belongs to a project and to nothing else. It is not an observation (nothing was detected), not a
photograph location (no photograph is involved), and it makes no claim beyond what its note says. Each change is a new
append-only version in `map_annotation`; the latest version is the current state and a `deleted` version hides it from
the normal views while the history stays.

Geometry is accepted only as GeoJSON in EPSG:4326, [longitude, latitude] in degrees, and is checked here so that a
refusal can say what is wrong; the database repeats the structural rules in a CHECK constraint as the last line.

Length and area are computed from the stored vertices when asked for and are never stored: they are geodesic figures on
the WGS 84 ellipsoid for the shape as drawn, with the limits stated in `LIMITATIONS`.
"""

import math
import uuid
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from geoalchemy2.shape import from_shape, to_shape
from shapely.geometry import LineString, Point, Polygon, mapping
from shapely.validation import explain_validity
from sqlalchemy import and_, func, select, text
from sqlalchemy.orm import Session

from geopoc.db.models import MapAnnotation, WorkspaceUser
from geopoc.geo import measure

KINDS = {"point": "Point", "line": "LineString", "polygon": "Polygon"}
SUPPORTED_CRS = "EPSG:4326"
MAX_VERTICES = 1000
MAX_NOTE = 2000
DECIMALS = 8  # about a millimetre; the same rounding as photograph locations

NOTE = (
    "An annotation is a mark a person drew on the map, with a note. It is not a finding, a detection or a photograph "
    "location, and the software cannot check that it is correct. The author is the local profile that made the change; "
    "profiles are not authenticated."
)
LIMITATIONS = [
    "Computed from the vertices exactly as drawn, so it is only as accurate as the drawing: not a survey measurement.",
    "Geodesic on the WGS 84 ellipsoid between consecutive vertices. The map draws each edge as a straight line on "
    "screen, which differs from the geodesic edge on long segments.",
    "A polygon's area is the area of its outline only; it says nothing about what is inside.",
]
METHOD = "geodesic on the WGS 84 ellipsoid (PROJ geodesic, Karney)"

_CRS_ALIASES = {"EPSG:4326", "URN:OGC:DEF:CRS:EPSG::4326"}
_NULL_ISLAND = (0.0, 0.0)


class AnnotationError(Exception):
    def __init__(self, status: int, code: str, title: str, detail: str | None = None) -> None:
        super().__init__(title)
        self.status, self.code, self.title, self.detail = status, code, title, detail


def _refuse(code: str, title: str, detail: str | None = None) -> AnnotationError:
    return AnnotationError(422, code, title, detail)


# --- validation -----------------------------------------------------------------------------------------------------


def check_crs(crs_id: str | None) -> str:
    """The only accepted system is WGS 84 longitude/latitude. Anything else is refused, never guessed or converted."""
    if crs_id is None or crs_id.strip().upper() not in _CRS_ALIASES:
        raise _refuse(
            "ANNOTATION_CRS_UNSUPPORTED",
            "Only EPSG:4326 coordinates are accepted",
            f"Received {crs_id!r}. Give positions as [longitude, latitude] in degrees (WGS 84); convert other "
            "coordinate systems first.",
        )
    return SUPPORTED_CRS


def check_note(note: str) -> str:
    if "\x00" in note:
        raise _refuse("ANNOTATION_NOTE_INVALID", "The note contains characters that cannot be stored")
    if len(note) > MAX_NOTE:
        raise _refuse("ANNOTATION_NOTE_TOO_LONG", f"A note can have at most {MAX_NOTE} characters")
    return note


def _number(value: Any, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise _refuse("ANNOTATION_COORDINATE_INVALID", f"{what} must be a number")
    if not math.isfinite(value):
        raise _refuse("ANNOTATION_COORDINATE_INVALID", f"{what} must be a finite number")
    return float(value)


def _position(raw: Any) -> tuple[float, float]:
    if not isinstance(raw, list | tuple) or len(raw) != 2:
        raise _refuse(
            "ANNOTATION_COORDINATE_INVALID",
            "Each position must be [longitude, latitude]",
            "Elevation is not accepted; give exactly two numbers.",
        )
    lon, lat = _number(raw[0], "Longitude"), _number(raw[1], "Latitude")
    if not -180 <= lon <= 180:
        raise _refuse(
            "ANNOTATION_LONGITUDE_RANGE",
            "Longitude must be between -180 and 180 degrees",
            "Positions are [longitude, latitude].",
        )
    if not -90 <= lat <= 90:
        swapped = -90 <= lon <= 90 and -180 <= lat <= 180
        raise _refuse(
            "ANNOTATION_LATITUDE_RANGE",
            "Latitude must be between -90 and 90 degrees",
            "Positions are [longitude, latitude]" + ("; the two may be the wrong way round." if swapped else "."),
        )
    lon, lat = round(lon, DECIMALS) + 0.0, round(lat, DECIMALS) + 0.0
    if (lon, lat) == _NULL_ISLAND:
        raise _refuse(
            "ANNOTATION_NULL_ISLAND",
            "That position is not accepted",
            "0, 0 is almost always a missing value, so it is refused.",
        )
    return lon, lat


def _positions(raw: Any, *, minimum: int, maximum: int, what: str) -> list[tuple[float, float]]:
    if not isinstance(raw, list | tuple):
        raise _refuse("ANNOTATION_GEOMETRY_INVALID", f"{what} must be a list of positions")
    if len(raw) < minimum:
        raise _refuse("ANNOTATION_TOO_FEW_POINTS", f"{what} needs at least {minimum} positions")
    if len(raw) > maximum:
        raise _refuse("ANNOTATION_TOO_MANY_POINTS", f"{what} can have at most {MAX_VERTICES} vertices")
    points = [_position(p) for p in raw]
    for a, b in zip(points, points[1:], strict=False):
        if abs(b[0] - a[0]) > 180:
            raise _refuse(
                "ANNOTATION_ANTIMERIDIAN",
                "Shapes crossing the 180° meridian are not supported",
                "Draw it as two separate annotations, one on each side.",
            )
    return points


@dataclass
class Shape:
    kind: str
    geometry: dict[str, Any]  # GeoJSON, rounded, exactly what is stored
    shapely: Point | LineString | Polygon


def parse(kind: str, geometry: Any, crs_id: str | None = SUPPORTED_CRS) -> Shape:
    """A validated, rounded shape, or an AnnotationError (422) saying what is wrong. Nothing is repaired or guessed."""
    check_crs(crs_id)
    if kind not in KINDS:
        raise _refuse("ANNOTATION_KIND_INVALID", "The kind must be point, line or polygon")
    if not isinstance(geometry, dict):
        raise _refuse("ANNOTATION_GEOMETRY_INVALID", "The geometry must be a GeoJSON geometry object")
    declared = geometry.get("crs")
    if declared is not None:
        name = ((declared.get("properties") or {}).get("name") or "") if isinstance(declared, dict) else ""
        if str(name).strip().upper() not in _CRS_ALIASES | {"URN:OGC:DEF:CRS:OGC:1.3:CRS84"}:
            raise _refuse(
                "ANNOTATION_CRS_UNSUPPORTED", "Only EPSG:4326 coordinates are accepted", f"Received {name!r}."
            )
    if geometry.get("type") != KINDS[kind]:
        raise _refuse(
            "ANNOTATION_KIND_MISMATCH",
            f"A {kind} annotation needs a GeoJSON {KINDS[kind]}",
            f"Received {geometry.get('type')!r}. Multi-part shapes and collections are not supported.",
        )
    coordinates = geometry.get("coordinates")

    if kind == "point":
        lon, lat = _position(coordinates)
        shape: Point | LineString | Polygon = Point(lon, lat)
    elif kind == "line":
        points = _positions(coordinates, minimum=2, maximum=MAX_VERTICES, what="A line")
        if len(set(points)) < 2:
            raise _refuse("ANNOTATION_GEOMETRY_INVALID", "A line needs at least two different positions")
        shape = LineString(points)
    else:
        if not isinstance(coordinates, list | tuple) or not coordinates:
            raise _refuse("ANNOTATION_GEOMETRY_INVALID", "A polygon needs an outline")
        if len(coordinates) > 1:
            raise _refuse(
                "ANNOTATION_HOLES_UNSUPPORTED",
                "Polygons with holes are not supported",
                "Draw the outline only, or annotate the inner area separately.",
            )
        ring = _positions(coordinates[0], minimum=4, maximum=MAX_VERTICES + 1, what="A polygon outline")
        if ring[0] != ring[-1]:
            raise _refuse("ANNOTATION_RING_NOT_CLOSED", "The outline must end where it starts")
        shape = Polygon(ring)
    if not shape.is_valid:
        raise _refuse(
            "ANNOTATION_GEOMETRY_INVALID",
            "The shape is not valid",
            explain_validity(shape) + ". Nothing was saved.",
        )
    if kind == "polygon" and shape.area == 0:
        raise _refuse("ANNOTATION_GEOMETRY_INVALID", "The outline encloses no area")
    return Shape(kind=kind, geometry=_geojson(shape), shapely=shape)


def _geojson(shape: Point | LineString | Polygon) -> dict[str, Any]:
    raw = mapping(shape)

    def listify(value: Any) -> Any:
        return [listify(v) for v in value] if isinstance(value, tuple | list) else value

    return {"type": raw["type"], "coordinates": listify(raw["coordinates"])}


# --- measurement ----------------------------------------------------------------------------------------------------


@dataclass
class Measurement:
    length_m: float | None
    area_m2: float | None
    perimeter_m: float | None
    method: str
    limitations: list[str]


def measurement_of(geometry: dict[str, Any]) -> Measurement | None:
    """Length of a line, or area and perimeter of a polygon, from the stored vertices. None for a point."""
    kind = geometry["type"]
    if kind == "LineString":
        result = measure.measure_lonlat("distance", [list(p) for p in geometry["coordinates"]])
        return Measurement(result.value, None, None, METHOD, LIMITATIONS[:2])
    if kind == "Polygon":
        ring = [list(p) for p in geometry["coordinates"][0][:-1]]
        result = measure.measure_lonlat("area", ring)
        return Measurement(None, result.value, result.perimeter_m, METHOD, LIMITATIONS)
    return None


# --- storage --------------------------------------------------------------------------------------------------------


def geometry_of(row: MapAnnotation) -> dict[str, Any]:
    return _geojson(to_shape(row.geom))


def latest_rows(session: Session, project_id: uuid.UUID) -> list[MapAnnotation]:
    """The current (latest) version of every annotation in the project, oldest annotation first."""
    newest = (
        select(MapAnnotation.annotation_id, func.max(MapAnnotation.version_no).label("version_no"))
        .where(MapAnnotation.project_id == project_id)
        .group_by(MapAnnotation.annotation_id)
        .subquery()
    )
    return list(
        session.scalars(
            select(MapAnnotation).join(
                newest,
                and_(
                    MapAnnotation.annotation_id == newest.c.annotation_id,
                    MapAnnotation.version_no == newest.c.version_no,
                ),
            )
        )
    )


def first_versions(session: Session, project_id: uuid.UUID) -> dict[uuid.UUID, MapAnnotation]:
    return {
        r.annotation_id: r
        for r in session.scalars(
            select(MapAnnotation).where(MapAnnotation.project_id == project_id, MapAnnotation.version_no == 1)
        )
    }


def table_exists(session: Session) -> bool:
    """False for a database that has not been migrated for annotations (revision 0008 or older)."""
    return session.execute(text("SELECT to_regclass('map_annotation')")).scalar_one() is not None


def all_versions(session: Session, project_id: uuid.UUID) -> dict[uuid.UUID, list[MapAnnotation]]:
    """Every version of every annotation of this project, each list oldest first. Other projects never appear."""
    grouped: dict[uuid.UUID, list[MapAnnotation]] = defaultdict(list)
    for row in session.scalars(
        select(MapAnnotation)
        .where(MapAnnotation.project_id == project_id)
        .order_by(MapAnnotation.annotation_id, MapAnnotation.version_no)
    ):
        grouped[row.annotation_id].append(row)
    return grouped


def history(session: Session, project_id: uuid.UUID, annotation_id: uuid.UUID) -> list[MapAnnotation]:
    """Every version of one annotation of this project. Empty when it does not exist in this project."""
    return list(
        session.scalars(
            select(MapAnnotation)
            .where(MapAnnotation.project_id == project_id, MapAnnotation.annotation_id == annotation_id)
            .order_by(MapAnnotation.version_no)
        )
    )


def _append(
    session: Session,
    *,
    project_id: uuid.UUID,
    annotation_id: uuid.UUID,
    version_no: int,
    action: str,
    status: str,
    kind: str,
    shape: Shape | None,
    previous: MapAnnotation | None,
    note: str,
    reason: str | None,
    actor: WorkspaceUser,
    request_id: str | None,
) -> MapAnnotation:
    geom = from_shape(shape.shapely, srid=4326) if shape is not None else previous.geom  # type: ignore[union-attr]
    row = MapAnnotation(
        project_id=project_id,
        annotation_id=annotation_id,
        version_no=version_no,
        action=action,
        status=status,
        kind=kind,
        geom=geom,
        crs_id=SUPPORTED_CRS,
        note=note,
        reason=reason,
        actor_id=actor.id,
        actor_label=actor.display_name,
        request_id=request_id,
    )
    session.add(row)
    session.flush()
    return row


def create(
    session: Session,
    project_id: uuid.UUID,
    *,
    kind: str,
    geometry: Any,
    crs_id: str | None,
    note: str,
    actor: WorkspaceUser,
    request_id: str | None,
) -> MapAnnotation:
    shape = parse(kind, geometry, crs_id)
    return _append(
        session,
        project_id=project_id,
        annotation_id=uuid.uuid4(),
        version_no=1,
        action="create",
        status="active",
        kind=kind,
        shape=shape,
        previous=None,
        note=check_note(note),
        reason=None,
        actor=actor,
        request_id=request_id,
    )


def _current(rows: list[MapAnnotation], expected_version: int) -> MapAnnotation:
    if not rows:
        raise AnnotationError(404, "ANNOTATION_NOT_FOUND", "Annotation not found")
    latest = rows[-1]
    if latest.version_no != expected_version:
        raise AnnotationError(
            409,
            "ANNOTATION_VERSION_CONFLICT",
            "The annotation changed since you opened it",
            f"You edited version {expected_version}; the current version is {latest.version_no}. Nothing was saved.",
        )
    if latest.status == "deleted":
        raise AnnotationError(409, "ANNOTATION_DELETED", "This annotation was deleted", "Nothing was changed.")
    return latest


def update(
    session: Session,
    rows: list[MapAnnotation],
    *,
    expected_version: int,
    geometry: Any | None,
    crs_id: str | None,
    note: str | None,
    reason: str | None,
    actor: WorkspaceUser,
    request_id: str | None,
) -> MapAnnotation:
    latest = _current(rows, expected_version)
    if geometry is None and note is None:
        raise _refuse("ANNOTATION_NOTHING_TO_CHANGE", "Give a new shape or a new note")
    shape = parse(latest.kind, geometry, crs_id) if geometry is not None else None
    new_note = check_note(note) if note is not None else latest.note
    if (shape is None or shape.geometry == geometry_of(latest)) and new_note == latest.note:
        raise _refuse("ANNOTATION_NOTHING_TO_CHANGE", "Nothing differs from the current version")
    return _append(
        session,
        project_id=latest.project_id,
        annotation_id=latest.annotation_id,
        version_no=latest.version_no + 1,
        action="update",
        status="active",
        kind=latest.kind,
        shape=shape,
        previous=latest,
        note=new_note,
        reason=reason,
        actor=actor,
        request_id=request_id,
    )


def delete(
    session: Session,
    rows: list[MapAnnotation],
    *,
    expected_version: int,
    reason: str | None,
    actor: WorkspaceUser,
    request_id: str | None,
) -> MapAnnotation:
    """Soft delete: a new `deleted` version carrying the last shape and note. Earlier versions are untouched."""
    latest = _current(rows, expected_version)
    return _append(
        session,
        project_id=latest.project_id,
        annotation_id=latest.annotation_id,
        version_no=latest.version_no + 1,
        action="delete",
        status="deleted",
        kind=latest.kind,
        shape=None,
        previous=latest,
        note=latest.note,
        reason=reason,
        actor=actor,
        request_id=request_id,
    )
