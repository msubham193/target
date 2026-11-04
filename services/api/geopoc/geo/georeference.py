"""Georeferencing recorded by a person for a raster whose own metadata is missing or invalid.

Two different things a person can supply, never confused with each other or with reprojection:

  assign_crs        the raster already has a usable geotransform (its pixels have coordinates) but no usable CRS:
                    the person states which CRS those coordinates are in. No pixel and no coordinate changes.
  set_geotransform  the raster has no usable geotransform: the person enters one (six affine parameters, or a world
                    file) from a documented source, in a CRS that is known or stated with it. A CRS alone never
                    makes a raster mappable.

Nothing is inferred: not from the filename, the project location, the image, or other data. Every proposal is
validated with PROJ and GDAL before it is used, and rejected with the reason when it does not hold up. The
original file is only read, never written; what is recorded is which metadata to use with it.

A valid CRS and geotransform make placement *possible*; they do not show that the placement is right. Results
based on recorded metadata are labelled `user_asserted`, never `trusted`.
"""

import hashlib
import math
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pyproj
from affine import Affine
from geoalchemy2.shape import from_shape, to_shape
from shapely.geometry import Polygon, mapping
from sqlalchemy import select
from sqlalchemy.orm import Session

from geopoc.db.models import Asset, Blob, GeorefAssertion, RasterMetadata, WorkspaceUser, utcnow
from geopoc.geo import orientation, tiles
from geopoc.geo import transform as transforms
from geopoc.geo.crs import CrsResolutionError, normalise_identifier, resolve
from geopoc.ingestion.raster import footprint_checked
from geopoc.storage import store

Operation = Literal["assign_crs", "set_geotransform", "revert_to_file"]
CheckStatus = Literal["ok", "warning", "failed"]
IDENTITY = (0.0, 1.0, 0.0, 0.0, 0.0, 1.0)


class GeoreferenceError(Exception):
    def __init__(self, status: int, code: str, title: str, detail: str | None = None, checks=None) -> None:
        super().__init__(title)
        self.status, self.code, self.title, self.detail = status, code, title, detail
        self.checks: list[Check] = checks or []


@dataclass
class Check:
    code: str
    status: CheckStatus
    message: str


@dataclass
class Effective:
    """The georeferencing used for a raster now: the file's own, or a recorded version."""

    version: int
    source: Literal["file", "recorded"]
    assertion_id: uuid.UUID | None
    crs_id: str | None
    crs_name: str | None
    crs_wkt: str | None
    crs_usable: bool
    geotransform: list[float] | None
    geotransform_usable: bool
    georeferenced: bool
    footprint: Polygon | None
    bounds_native: dict[str, float] | None
    pixel_size: list[float] | None
    pixel_units: str | None

    def snapshot(self) -> dict[str, Any]:
        """A JSON record of this state, for the history."""
        return {
            "version": self.version,
            "source": self.source,
            "crs_id": self.crs_id,
            "crs_name": self.crs_name,
            "crs_usable": self.crs_usable,
            "geotransform": self.geotransform,
            "geotransform_usable": self.geotransform_usable,
            "georeferenced": self.georeferenced,
            "footprint_4326": mapping(self.footprint) if self.footprint is not None else None,
            "bounds_native": self.bounds_native,
            "pixel_size": self.pixel_size,
            "pixel_units": self.pixel_units,
        }


@dataclass
class Proposal:
    operation: Operation
    crs_id: str | None = None
    geotransform: list[float] | None = None
    world_file: str | None = None
    geotransform_source: str | None = None
    reason: str = ""
    # Which registered datum transformation to use when several apply (an id from the CRS assessment).
    transformation_operation: str | None = None
    # The person accepts the accuracy of the datum shift that would otherwise be refused (worse than 10 m, or not
    # stated). What is acknowledged is taken from the refusal itself, so it names exactly that operation and accuracy.
    acknowledge_transformation: bool = False


@dataclass
class Outcome:
    checks: list[Check] = field(default_factory=list)
    result: Effective | None = None
    method: str = ""
    crs: pyproj.CRS | None = None
    corners: list[dict[str, Any]] = field(default_factory=list)
    transformation: dict[str, Any] | None = None
    # Set when no transformation could be used, so the caller can show the options.
    transformation_plan: transforms.Plan | None = None
    # The acknowledgement that allowed the datum shift, if one was needed. `apply` stamps who and when.
    acknowledgement: transforms.Acknowledgement | None = None

    @property
    def ok(self) -> bool:
        return self.result is not None and not any(c.status == "failed" for c in self.checks)

    def add(self, code: str, status: CheckStatus, message: str) -> None:
        self.checks.append(Check(code, status, message))


# --- geotransform helpers -------------------------------------------------------------------------------------------


def geotransform_problem(values: list[float] | None) -> str | None:
    """Why a GDAL-order geotransform cannot give map positions, or None if it can."""
    if values is None:
        return "There is no geotransform."
    if len(values) != 6 or not all(isinstance(v, int | float) and math.isfinite(v) for v in values):
        return "A geotransform needs six finite numbers."
    if tuple(float(v) for v in values) == IDENTITY:
        return "The geotransform is the identity: it gives pixel numbers, not map coordinates."
    transform = Affine.from_gdal(*values)
    if transform.determinant == 0:
        return "The geotransform is degenerate (its pixels have no area)."
    if math.hypot(transform.a, transform.d) == 0 or math.hypot(transform.b, transform.e) == 0:
        return "The geotransform has a zero pixel size."
    return None


def parse_world_file(text: str) -> list[float]:
    """GDAL-order geotransform from a world file (.tfw/.wld).

    A world file lists A, D, B, E, C, F: the pixel size and rotation terms, then the position of the *centre* of the
    top-left pixel. GDAL's origin is that pixel's *corner*, so half a pixel is subtracted along both axes.
    """
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    if len(lines) != 6:
        raise ValueError("A world file has exactly six numbers, one per line.")
    try:
        a, d, b, e, c, f = (float(line) for line in lines)
    except ValueError as exc:
        raise ValueError("Every line of a world file must be a number.") from exc
    return [c - a / 2 - b / 2, a, b, f - d / 2 - e / 2, d, e]


def _pixel_size(values: list[float]) -> list[float]:
    transform = Affine.from_gdal(*values)
    return [math.hypot(transform.a, transform.d), math.hypot(transform.b, transform.e)]


def _crs_usable(crs: pyproj.CRS | None) -> bool:
    return crs is not None and (crs.is_geographic or crs.is_projected) and not crs.is_compound


# --- reading the current state ---------------------------------------------------------------------------------------


def _file_state(raster: RasterMetadata) -> Effective:
    crs = None
    if raster.crs_status == "valid" and raster.crs_wkt:
        try:
            crs = pyproj.CRS.from_wkt(raster.crs_wkt)
        except pyproj.exceptions.CRSError:
            crs = None
    gt_ok = geotransform_problem(raster.geotransform) is None and raster.pixel_size_x is not None
    return Effective(
        version=raster.georef_version,
        source="file",
        assertion_id=raster.current_assertion_id,
        crs_id=raster.crs_identifier,
        crs_name=raster.crs_name,
        crs_wkt=raster.crs_wkt if crs is not None else None,
        crs_usable=_crs_usable(crs),
        geotransform=list(raster.geotransform) if raster.geotransform else None,
        geotransform_usable=gt_ok,
        georeferenced=raster.georeferenced,
        footprint=to_shape(raster.footprint_4326) if raster.footprint_4326 is not None else None,
        bounds_native=raster.bounds_native,
        pixel_size=[raster.pixel_size_x, raster.pixel_size_y] if raster.pixel_size_x is not None else None,
        pixel_units=raster.pixel_units,
    )


def effective(raster: RasterMetadata) -> Effective:
    """The georeferencing in use: the latest recorded version, or the file's own when none (or after a revert)."""
    assertion = raster.current_assertion
    if assertion is None or assertion.operation == "revert_to_file":
        return _file_state(raster)
    r = assertion.result
    return Effective(
        version=raster.georef_version,
        source="recorded",
        assertion_id=assertion.id,
        crs_id=r.get("crs_id"),
        crs_name=r.get("crs_name"),
        crs_wkt=assertion.crs_wkt if r.get("crs_usable") else None,
        crs_usable=bool(r.get("crs_usable")),
        geotransform=r.get("geotransform"),
        geotransform_usable=bool(r.get("geotransform_usable")),
        georeferenced=assertion.georeferenced,
        footprint=to_shape(assertion.footprint_4326) if assertion.footprint_4326 is not None else None,
        bounds_native=r.get("bounds_native"),
        pixel_size=r.get("pixel_size"),
        pixel_units=r.get("pixel_units"),
    )


def unplaced_reason(raster: RasterMetadata, state: Effective) -> str | None:
    """Why the raster is not on the map, in words, or None when it is."""
    if state.georeferenced:
        return None
    if not state.crs_usable and not state.geotransform_usable:
        return "It has neither a usable CRS nor a usable geotransform, so its pixels have no map position."
    if not state.crs_usable:
        missing = "declares no CRS" if raster.crs_status == "missing" else "declares a CRS that cannot be used"
        return (
            f"Its pixels have coordinates (a geotransform), but the file {missing}, so it is not known what those "
            "coordinates mean."
        )
    if not state.geotransform_usable:
        return "It has a CRS, but no usable geotransform, so its pixels have no coordinates in that CRS."
    return "Its coordinates fall outside the valid range of its CRS, so the georeferencing is not trusted."


@dataclass
class Options:
    assign_crs: bool
    assign_crs_reason: str
    set_geotransform: bool
    set_geotransform_reason: str
    crs_required_with_geotransform: bool
    revert_to_file: bool


def options(raster: RasterMetadata, state: Effective) -> Options:
    file_ok = raster.georeferenced
    if file_ok:
        why = "The file's own CRS and geotransform are valid. They are not overridden."
        return Options(False, why, False, why, False, False)
    assign = state.geotransform_usable
    assign_reason = (
        "States which CRS the raster's existing coordinates are in."
        if assign
        else "Not available: the raster has no usable geotransform, so a CRS alone cannot place it. Enter a "
        "geotransform from a documented source instead."
    )
    return Options(
        assign_crs=assign,
        assign_crs_reason=assign_reason,
        set_geotransform=True,
        set_geotransform_reason="Enters the six affine parameters (or a world file) from a documented source.",
        crs_required_with_geotransform=not state.crs_usable,
        revert_to_file=state.source == "recorded",
    )


# --- validation -----------------------------------------------------------------------------------------------------


def _resolve_crs(identifier: str, outcome: Outcome) -> pyproj.CRS | None:
    try:
        normalised = normalise_identifier(identifier)
        crs = resolve(normalised)
    except CrsResolutionError as exc:
        outcome.add("CRS_RESOLVES", "failed", str(exc))
        return None
    if not _crs_usable(crs):
        outcome.add("CRS_RESOLVES", "failed", f"{normalised} is not a 2D geographic or projected CRS.")
        return None
    outcome.add("CRS_RESOLVES", "ok", f"{normalised} ({crs.name}) is in the local PROJ registry.")
    if crs.is_deprecated:
        outcome.add("CRS_DEPRECATED", "warning", f"{normalised} is marked deprecated in the registry.")
    return crs


def _orientation(crs: pyproj.CRS, transform: Affine, width: int, height: int, outcome: "Outcome") -> None:
    """Warn when the picture is mirrored on the ground: its rows run north where a map's run south.

    This is measured on the ground at the raster's centre, not read from the sign of the pixel height. A positive pixel
    height is normal for a CRS whose y axis points south (EPSG:2046), and a negative one is a mirror for it; the sign
    alone cannot tell. If the centre is outside the CRS's domain the footprint check reports that, and this says
    nothing."""
    cx, cy = transform @ (width / 2, height / 2)
    basis = orientation.local_basis(crs, np.array([cx]), np.array([cy]))
    if basis is None:
        return
    ground = orientation.geometry(crs, list(transform.to_gdal()), basis.ex[0], basis.ey[0])
    if ground.handedness == "mirrored":
        outcome.add(
            "ORIENTATION",
            "warning",
            "On the ground the picture's rows run north, where a map's rows run south: it is mirrored. This is "
            "unusual; "
            "check the geotransform.",
        )


def _units_plausibility(crs: pyproj.CRS, pixel: list[float], outcome: Outcome) -> None:
    unit = (crs.axis_info[0].unit_name or "").lower()
    smallest, largest = min(pixel), max(pixel)
    if crs.is_geographic and largest > 1:
        outcome.add(
            "UNITS_PLAUSIBLE",
            "warning",
            f"Each pixel would be {largest:g} {unit} across. Coordinates in metres given a geographic CRS often look "
            "like this; check that the CRS matches the units of the geotransform.",
        )
    elif crs.is_projected and smallest < 0.01:
        outcome.add(
            "UNITS_PLAUSIBLE",
            "warning",
            f"Each pixel would be {smallest:g} {unit} across. Coordinates in degrees given a projected CRS often look "
            "like this; check that the CRS matches the units of the geotransform.",
        )
    else:
        outcome.add("UNITS_PLAUSIBLE", "ok", f"Pixel size {pixel[0]:g} × {pixel[1]:g} {unit}.")


def validate(
    asset: Asset,
    raster: RasterMetadata,
    proposal: Proposal,
    original: Path | None,
    render_check: bool = True,
) -> Outcome:
    """Check a proposed change without storing anything. The outcome lists every check and, if valid, the result."""
    outcome = Outcome()
    state = effective(raster)
    if proposal.reason.strip():
        outcome.add("REASON_RECORDED", "ok", "A reason is recorded.")
    else:
        outcome.add("REASON_RECORDED", "failed", "Enter the reason for this change; it is kept in the history.")

    if proposal.operation == "revert_to_file":
        if state.source != "recorded":
            outcome.add("REVERT_APPLICABLE", "failed", "The raster already uses the metadata in its file.")
            return outcome
        outcome.method = "file_metadata_restored"
        restored = _file_state(raster)
        restored.source, restored.version = "file", raster.georef_version + 1
        outcome.add("REVERT_APPLICABLE", "ok", "The file's own metadata will be used again.")
        outcome.result = restored
        return outcome

    # --- what geotransform and CRS the proposal implies ---
    if proposal.operation == "assign_crs":
        outcome.method = "registry_crs_selected"
        if not proposal.crs_id:
            outcome.add("CRS_RESOLVES", "failed", "Choose the CRS of the raster's coordinates.")
            return outcome
        if proposal.geotransform is not None or proposal.world_file:
            outcome.add(
                "OPERATION_CONSISTENT",
                "failed",
                "Assigning a CRS keeps the raster's existing coordinates. To enter coordinates, use the geotransform "
                "operation.",
            )
            return outcome
        if not state.geotransform_usable:
            outcome.add(
                "GEOTRANSFORM_AVAILABLE",
                "failed",
                "The raster has no usable geotransform, so its pixels have no coordinates to which a CRS could be "
                f"assigned ({geotransform_problem(state.geotransform)}). Assigning a CRS alone cannot place it.",
            )
            return outcome
        values = list(state.geotransform)  # type: ignore[arg-type]
        outcome.add("GEOTRANSFORM_AVAILABLE", "ok", "The raster's existing geotransform is used unchanged.")
        crs = _resolve_crs(proposal.crs_id, outcome)
        if crs is not None and raster.crs_status == "valid" and raster.crs_wkt:
            outcome.add(
                "FILE_CRS_REPLACED",
                "warning",
                f"The file declares {raster.crs_identifier or raster.crs_name}, but its coordinates do not fit it. "
                "The file is unchanged; the CRS you choose is used instead and recorded as yours.",
            )
        if crs is not None and state.crs_usable and state.crs_wkt and pyproj.CRS.from_wkt(state.crs_wkt) == crs:
            outcome.add("CHANGE_PRESENT", "failed", "That CRS is already the one in use.")
            return outcome
    else:  # set_geotransform
        if proposal.world_file and proposal.geotransform is not None:
            outcome.add("OPERATION_CONSISTENT", "failed", "Give either the six parameters or a world file, not both.")
            return outcome
        if proposal.world_file:
            outcome.method = "world_file_entered"
            try:
                values = parse_world_file(proposal.world_file)
            except ValueError as exc:
                outcome.add("GEOTRANSFORM_VALID", "failed", f"The world file could not be read: {exc}")
                return outcome
            outcome.add(
                "WORLD_FILE_READ",
                "ok",
                "Read as A, D, B, E, C, F; the top-left pixel centre was converted to GDAL's top-left corner.",
            )
        elif proposal.geotransform is not None:
            outcome.method = "geotransform_entered"
            values = [float(v) for v in proposal.geotransform]
        else:
            outcome.add("GEOTRANSFORM_AVAILABLE", "failed", "Enter the geotransform, or the contents of a world file.")
            return outcome
        if not (proposal.geotransform_source or "").strip():
            outcome.add(
                "SOURCE_RECORDED",
                "failed",
                "Say where these numbers come from (for example the supplier's world file or metadata sheet).",
            )
        else:
            outcome.add("SOURCE_RECORDED", "ok", "The source of the numbers is recorded.")
        if proposal.crs_id:
            crs = _resolve_crs(proposal.crs_id, outcome)
        elif state.crs_usable and state.crs_wkt:
            crs = pyproj.CRS.from_wkt(state.crs_wkt)
            outcome.add("CRS_RESOLVES", "ok", f"The CRS in use ({state.crs_id or state.crs_name}) is kept.")
        else:
            outcome.add(
                "CRS_RESOLVES",
                "failed",
                "The raster has no usable CRS. Choose the CRS these coordinates are in; a geotransform has no "
                "meaning without one.",
            )
            crs = None
        if (
            state.geotransform
            and list(map(float, state.geotransform)) == values
            and crs is not None
            and (state.crs_wkt and pyproj.CRS.from_wkt(state.crs_wkt) == crs)
        ):
            outcome.add("CHANGE_PRESENT", "failed", "That geotransform and CRS are already in use.")
            return outcome

    problem = geotransform_problem(values)
    if problem:
        outcome.add("GEOTRANSFORM_VALID", "failed", problem)
        return outcome
    transform = Affine.from_gdal(*values)
    rotated = transform.b != 0 or transform.d != 0
    outcome.add(
        "GEOTRANSFORM_VALID",
        "warning" if rotated else "ok",
        "The geotransform is rotated or sheared; the full affine transform is used, not a north-up box."
        if rotated
        else "Six finite affine parameters, non-zero pixel size, north-up.",
    )
    if crs is None:
        return outcome
    pixel = _pixel_size(values)
    _units_plausibility(crs, pixel, outcome)
    _orientation(crs, transform, raster.width, raster.height, outcome)

    # --- where the pixels would be ---
    width, height = raster.width, raster.height
    checked = footprint_checked(transform, width, height, crs, operation_id=proposal.transformation_operation)
    if (
        checked.error is not None
        and checked.error.code == transforms.ACK_REQUIRED_CODE
        and proposal.acknowledge_transformation
    ):
        # Draw it again with an acknowledgement made from the refusal itself: that operation, those CRSs, that accuracy.
        refused = checked.error.extra
        checked = footprint_checked(
            transform,
            width,
            height,
            crs,
            operation_id=proposal.transformation_operation,
            acknowledgement=transforms.Acknowledgement(
                operation_id=refused["operation_id"],
                source_crs=refused["source_crs"],
                target_crs=refused["target_crs"],
                accuracy_m=refused["accuracy_m"],
                scope="stored",
            ),
        )
    outline, transformation = checked.polygon, checked.record
    if outline is None and checked.error is not None:
        # The coordinates are fine; the datum transformation is the problem. Say which, and what to do about it.
        code = (
            "TRANSFORMATION_ACKNOWLEDGEMENT_REQUIRED"
            if checked.error.code == transforms.ACK_REQUIRED_CODE
            else "TRANSFORMATION_AVAILABLE"
        )
        outcome.add(code, "failed", f"{checked.error.title}. {checked.error.detail}")
        outcome.transformation_plan = checked.error.plan
        return outcome
    if outline is None:
        outcome.add(
            "COORDINATES_FIT_CRS",
            "failed",
            f"With this geotransform the raster's coordinates fall outside the valid range of {crs.name}, or cannot "
            "be transformed to longitude and latitude. It is not placed.",
        )
        return outcome
    outcome.add(
        "COORDINATES_FIT_CRS", "ok", "Every point of the raster's outline transforms to valid longitude and latitude."
    )
    outcome.transformation = transformation
    operation = checked.operation
    accuracy = (transformation or {}).get("accuracy_m")
    outcome.acknowledgement = checked.acknowledgement
    if checked.crosses_antimeridian:
        outcome.add(
            "CROSSES_ANTIMERIDIAN",
            "warning",
            "The raster crosses longitude 180°. Its footprint is stored split into two parts at the antimeridian. "
            "Measurement, areas of interest, terrain, pixel lookup and tiles are not supported for it.",
        )
    if operation is not None and operation.klass == "approximate":
        outcome.add(
            "TRANSFORMATION_AVAILABLE",
            "warning",
            f"Placed with the registered datum shift {operation.name} ({operation.id}); its documented accuracy is "
            f"about {accuracy:g} m, so positions are approximate by that much or more.",
        )
    elif operation is not None and operation.klass == "ballpark":
        outcome.add("TRANSFORMATION_AVAILABLE", "failed", "A ballpark transformation cannot be used.")
        return outcome
    else:
        outcome.add(
            "TRANSFORMATION_AVAILABLE",
            "ok" if accuracy is not None else "warning",
            f"Transformed to EPSG:4326 with PROJ {pyproj.proj_version_str}"
            + (f"; stated accuracy {accuracy} m." if accuracy is not None else "; PROJ states no accuracy for it."),
        )
    area = crs.area_of_use
    centre = outline.centroid
    if area is not None:
        inside = (
            area.west <= centre.x <= area.east if area.west <= area.east else not (area.east < centre.x < area.west)
        ) and (area.south <= centre.y <= area.north)
        outcome.add(
            "WITHIN_AREA_OF_USE",
            "ok" if inside else "warning",
            f"The raster's centre is inside the area of use of {crs.name}."
            if inside
            else f"The raster's centre ({centre.y:.5f}, {centre.x:.5f}) is outside the area of use of {crs.name} "
            f"({area.name}). Check the CRS: a wrong CRS can still give valid-looking coordinates.",
        )
    # The corners are converted with the very operation that placed the outline above, not PROJ's default.
    assert operation is not None and operation.transformer is not None
    to_lonlat = operation.transformer
    for label, (col, row) in (
        ("top-left corner", (0, 0)),
        ("top-right corner", (width, 0)),
        ("bottom-right corner", (width, height)),
        ("bottom-left corner", (0, height)),
        ("centre", (width / 2, height / 2)),
    ):
        x, y = transform @ (col, row)
        lon, lat = to_lonlat.transform(x, y)
        outcome.corners.append({"point": label, "pixel": [col, row], "native": [x, y], "lon_lat": [lon, lat]})
    xs = [c["native"][0] for c in outcome.corners[:4]]
    ys = [c["native"][1] for c in outcome.corners[:4]]

    # --- can it actually be drawn? A tile is rendered from the original with the proposed metadata ---
    if render_check and original is not None:
        z = min(tiles.MAX_ZOOM, tiles.native_zoom(_metres(pixel[0], crs, centre.y), centre.y))
        tx, ty = _tile_of(centre.x, centre.y, z)
        try:
            # Tiles are drawn through the same registered datum shift that placed the outline.
            display = transforms.resolve(
                crs,
                tiles.WEB_MERCATOR,
                outline.bounds,
                operation.id if operation is not None else None,
                acknowledgement=checked.acknowledgement,
            )
            png = tiles.render_tile(
                original, (1,), (), z, tx, ty, georef=(crs.to_wkt(), tuple(values)), operation=display.pipeline
            )
        except Exception as exc:  # noqa: BLE001 - any failure here means "cannot be drawn", reported as such
            outcome.add("TILE_RENDERS", "failed", f"A map tile could not be produced: {type(exc).__name__}: {exc}")
            return outcome
        if png is None:
            # Possible for a raster smaller than one tile pixel even at the highest zoom: shown, not hidden.
            outcome.add(
                "TILE_RENDERS",
                "warning",
                f"A map tile at zoom {z} was rendered, but the raster covers none of its pixels; it may be too "
                "small to see on the map.",
            )
        else:
            outcome.add(
                "TILE_RENDERS", "ok", f"A map tile at zoom {z} was rendered from the original with this metadata."
            )

    outcome.crs = crs
    authority = crs.to_authority(min_confidence=100)
    outcome.result = Effective(
        version=raster.georef_version + 1,
        source="recorded",
        assertion_id=None,
        crs_id=f"{authority[0]}:{authority[1]}" if authority else None,
        crs_name=crs.name,
        crs_wkt=crs.to_wkt(),
        crs_usable=True,
        geotransform=values,
        geotransform_usable=True,
        georeferenced=True,
        footprint=outline,
        bounds_native={"left": min(xs), "bottom": min(ys), "right": max(xs), "top": max(ys)},
        pixel_size=pixel,
        pixel_units=crs.axis_info[0].unit_name,
    )
    return outcome


def _metres(size: float, crs: pyproj.CRS, latitude: float) -> float:
    unit = (crs.axis_info[0].unit_name or "").lower()
    if "degree" in unit:
        return size * 111_320 * max(0.01, math.cos(math.radians(latitude)))
    factor = crs.axis_info[0].unit_conversion_factor or 1.0
    return size * factor


def _tile_of(lon: float, lat: float, z: int) -> tuple[int, int]:
    x_m, y_m = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True).transform(lon, lat)
    size = 2 * tiles.ORIGIN / 2**z
    return (
        min(2**z - 1, max(0, int((x_m + tiles.ORIGIN) / size))),
        min(2**z - 1, max(0, int((tiles.ORIGIN - y_m) / size))),
    )


def original_path(session: Session, asset: Asset) -> Path | None:
    blob = session.get(Blob, asset.blob_sha256)
    path = store.absolute_path(blob.storage_path) if blob else None
    return path if path is not None and path.is_file() else None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


# --- applying ---------------------------------------------------------------------------------------------------------


def apply(
    session: Session,
    asset_id: uuid.UUID,
    proposal: Proposal,
    *,
    expected_version: int,
    actor: WorkspaceUser,
    request_id: str | None,
    method: str | None = None,
    provenance: list[dict[str, Any]] | None = None,
    extra_checks: list[Check] | None = None,
    control_points: dict[str, Any] | None = None,
) -> tuple[GeorefAssertion, Outcome]:
    """Validate again and store the new version. The caller commits. Nothing is stored unless every check passes.

    `method`, `provenance` and `extra_checks` are given when the values come from sidecar files: the method names
    that origin, the provenance lists the files used (with their checksums), and the extra checks (for example the
    sidecar's own checksum) are recorded and must pass like the others. `control_points` is the evidence for values
    fitted from points a person supplied (see `geopoc.geo.controlpoints`); it is stored with the version.
    """
    raster = session.scalars(
        select(RasterMetadata).where(RasterMetadata.asset_id == asset_id).with_for_update(of=RasterMetadata)
    ).first()
    asset = session.get(Asset, asset_id)
    if asset is None or raster is None or asset.modality != "overhead_raster":
        raise GeoreferenceError(404 if asset is None else 409, "NOT_A_RASTER", "Only rasters can be georeferenced")
    if raster.georef_version != expected_version:
        raise GeoreferenceError(
            409,
            "GEOREFERENCE_VERSION_CONFLICT",
            "The raster's georeferencing was changed by someone else",
            f"You reviewed version {expected_version}; it is now at version {raster.georef_version}. Reload it, "
            "check the change, and try again.",
        )
    if raster.georeferenced and proposal.operation != "revert_to_file":
        raise GeoreferenceError(
            409,
            "ALREADY_GEOREFERENCED",
            "This raster's own georeferencing is valid",
            "The CRS and geotransform in the file are used. They are not overridden.",
        )
    original = original_path(session, asset)
    if original is None:
        raise GeoreferenceError(500, "STORAGE_ERROR", "The stored original is missing")
    checksum = _sha256(original)
    if checksum != asset.blob_sha256:
        raise GeoreferenceError(
            409, "ORIGINAL_CHANGED", "The stored original no longer matches its checksum", "Nothing was recorded."
        )
    previous = effective(raster)
    outcome = validate(asset, raster, proposal, original)
    outcome.add(
        "ORIGINAL_UNCHANGED", "ok", f"The original's SHA-256 matches the one recorded at upload ({checksum[:16]}…)."
    )
    outcome.checks.extend(extra_checks or [])
    if outcome.acknowledgement is not None and outcome.transformation is not None:
        # Stored with this version and so as permanent as it: who accepted which operation at which accuracy, and when.
        outcome.transformation["acknowledgement"] = {
            "operation_id": outcome.acknowledgement.operation_id,
            "source_crs": outcome.acknowledgement.source_crs,
            "target_crs": outcome.acknowledgement.target_crs,
            "accuracy_m": outcome.acknowledgement.accuracy_m,
            "threshold_m": transforms.ACK_ACCURACY_M,
            "acknowledged_by": actor.display_name,
            "acknowledged_by_id": str(actor.id),
            "acknowledged_at": utcnow().isoformat(),
        }
    if method is not None and outcome.method:
        outcome.method = method
    if not outcome.ok:
        raise GeoreferenceError(
            422,
            "GEOREFERENCE_INVALID",
            "The proposed georeferencing did not pass validation",
            "Nothing was recorded; the raster keeps its current state.",
            outcome.checks,
        )
    result = outcome.result
    assert result is not None
    crs_id = (
        None if proposal.operation == "revert_to_file" else result.crs_id or normalise_identifier(proposal.crs_id or "")
    )
    assertion = GeorefAssertion(
        id=uuid.uuid4(),
        asset_id=asset.id,
        version_no=raster.georef_version + 1,
        operation=proposal.operation,
        method=outcome.method,
        crs_id=crs_id,
        crs_name=result.crs_name,
        crs_wkt=result.crs_wkt,
        geotransform=result.geotransform if proposal.operation != "revert_to_file" else None,
        geotransform_source=(proposal.geotransform_source or "").strip() or None,
        world_file=proposal.world_file,
        reason=proposal.reason.strip(),
        previous=previous.snapshot(),
        result={**result.snapshot(), "crs_id": crs_id if proposal.operation != "revert_to_file" else result.crs_id},
        validation={
            "checks": [asdict(c) for c in outcome.checks],
            "corners": outcome.corners,
            "transformation": outcome.transformation,
            "derived_artifacts": [],
            "tiles": "rendered on request from the unchanged original with this version; cached per version",
            **({"sidecars": provenance} if provenance is not None else {}),
            **({"control_points": control_points} if control_points is not None else {}),
        },
        georeferenced=result.georeferenced,
        footprint_4326=from_shape(result.footprint, srid=4326) if result.footprint is not None else None,
        source_checksum=checksum,
        actor_id=actor.id,
        actor_label=actor.display_name,
        request_id=request_id,
        created_at=utcnow(),
    )
    session.add(assertion)
    session.flush()
    raster.georef_version = assertion.version_no
    raster.current_assertion_id = assertion.id
    session.flush()
    session.refresh(raster)
    return assertion, outcome
