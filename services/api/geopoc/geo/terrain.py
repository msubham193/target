"""Reading a height grid from a stored raster for 3D viewing, and saying honestly what it is.

Nothing here invents terrain or elevation. A raster is offered as terrain only when it is georeferenced (a CRS and a
plain
north-up geotransform) and a band is, or is confirmed by a person to be, elevation. RGB imagery, 8-bit pictures and
multi-band imagery are never treated as elevation. Elevation units are the file's own; when the file declares none, the
person must state them for that view, and the answer says so.

The grid is a decimation of the original: every value is a real source pixel (nearest pixel, no averaging, no
smoothing, no
filling of holes). Cells with no data are NaN and are drawn as holes. The original is only opened for reading.

Horizontal scale: a projected CRS gives metres (or feet, converted) exactly. A geographic CRS has no single metre
size for a
degree, so the geodesic size of a cell at the centre latitude is used and the view says it is approximate.
"""

import math
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pyproj
import rasterio
from affine import Affine
from rasterio.enums import Resampling
from rasterio.errors import NotGeoreferencedWarning, RasterioError

from geopoc.config import Settings
from geopoc.db.models import Asset
from geopoc.geo import antimeridian, convert, georeference
from geopoc.geo.transform import TransformationError

ELEVATION_WORDS = ("elevation", "height", "altitude", "dem", "dsm", "dtm", "terrain", "surface model")
UNIT_ALIASES = {
    "m": "metre",
    "metre": "metre",
    "metres": "metre",
    "meter": "metre",
    "meters": "metre",
    "ft": "foot",
    "foot": "foot",
    "feet": "foot",
    "us survey foot": "us_survey_foot",
    "us survey feet": "us_survey_foot",
    "ftus": "us_survey_foot",
    "us-ft": "us_survey_foot",
}
UNIT_TO_METRE = {"metre": 1.0, "foot": 0.3048, "us_survey_foot": 1200 / 3937}
UNIT_LABEL = {"metre": "m", "foot": "ft", "us_survey_foot": "ft (US survey)"}
NUMERIC_ELEVATION_DTYPES = ("int16", "uint16", "int32", "uint32", "float32", "float64")
IMAGERY_COLOUR = (
    "red",
    "green",
    "blue",
    "alpha",
    "palette",
    "cyan",
    "magenta",
    "yellow",
    "hue",
    "saturation",
    "lightness",
)
# Values that stand in for "no data" in many files. They are reported, never silently treated as no data.
SENTINELS = (-9999.0, -32768.0, -3.4028234663852886e38, 3.4028234663852886e38)


class TerrainError(Exception):
    """The raster cannot be shown as terrain (or the request is wrong). `status` and `code` are stable."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code = status, code


def normalise_unit(text: str | None) -> str | None:
    return UNIT_ALIASES.get((text or "").strip().lower()) if text else None


@dataclass
class BandAssessment:
    band: int
    dtype: str
    description: str | None
    colour: str | None
    declared_units: str | None
    unit: str | None  # normalised, from the file
    role: str  # elevation_declared | possible | not_elevation
    reasons: list[str] = field(default_factory=list)


@dataclass
class Assessment:
    status: str  # usable | needs_confirmation | not_elevation | unusable
    reasons: list[str]
    bands: list[BandAssessment]
    unusable_reason: str | None

    @property
    def candidates(self) -> list[BandAssessment]:
        return [b for b in self.bands if b.role != "not_elevation"]


def _band_infos(asset: Asset) -> list[dict[str, Any]]:
    raster = asset.raster
    declared = (asset.extracted_metadata or {}).get("bands")
    if declared:
        return [{**b, "dtype": raster.dtypes[b["band"] - 1]} for b in declared]
    return [
        {"band": i + 1, "dtype": d, "description": None, "color_interpretation": None, "units": None}
        for i, d in enumerate(raster.dtypes)
    ]


def _unusable_reason(asset: Asset, state: georeference.Effective, settings: Settings) -> str | None:
    raster = asset.raster
    if not state.georeferenced or not state.crs_wkt or not state.geotransform:
        return georeference.unplaced_reason(raster, state) or "It has no usable georeferencing."
    if raster.width * raster.height > settings.terrain_max_source_pixels:
        return (
            f"It has {raster.width * raster.height:,} pixels, over the {settings.terrain_max_source_pixels:,} that "
            f"can be "
            "read for a 3D view. Use a smaller extract of it."
        )
    a, b, _, d, e, _ = _six(state.geotransform)
    if abs(b) > 1e-12 or abs(d) > 1e-12:
        return "Its geotransform is rotated or sheared, which the 3D view does not support."
    if not a > 0 or not e < 0:
        return "Its rows do not run north to south (or its columns west to east), which the 3D view does not support."
    try:
        crs = pyproj.CRS.from_wkt(state.crs_wkt)
        _horizontal_unit(crs)
    except (pyproj.exceptions.CRSError, TerrainError) as exc:
        return str(exc)
    return None


def _six(gt: list[float]) -> tuple[float, float, float, float, float, float]:
    # GDAL order (c, a, b, f, d, e) -> (a, b, c, d, e, f) of an affine transform
    transform = Affine.from_gdal(*gt)
    return transform.a, transform.b, transform.c, transform.d, transform.e, transform.f


def assess(asset: Asset, settings: Settings) -> Assessment:
    """Whether this raster can be shown as terrain, from what the file declares. Reads no pixels."""
    state = georeference.effective(asset.raster)
    bands: list[BandAssessment] = []
    count = asset.raster.band_count
    for info in _band_infos(asset):
        dtype = str(info["dtype"])
        description = info.get("description")
        colour = (info.get("color_interpretation") or "").lower() or None
        units = info.get("units")
        unit = normalise_unit(units)
        words = f"{description or ''}".lower()
        says_elevation = any(w in words for w in ELEVATION_WORDS)
        reasons: list[str] = []
        if colour in IMAGERY_COLOUR:
            role = "not_elevation"
            reasons.append(f"The file marks this band as {colour}: it is image colour, not elevation.")
        elif says_elevation or unit is not None:
            role = "elevation_declared"
            reasons.append(
                "The file declares this band as elevation"
                + (f" (description {description!r})" if says_elevation else "")
                + (f" in {UNIT_LABEL[unit]}" if unit else " but states no unit")
                + "."
            )
            if dtype not in NUMERIC_ELEVATION_DTYPES and dtype != "uint8":
                role, reasons = "not_elevation", [f"Its data type {dtype} is not one elevation is stored in."]
        elif count == 1 and dtype in NUMERIC_ELEVATION_DTYPES:
            role = "possible"
            reasons.append(
                "A single numeric band. The file does not say it is elevation, so it is only treated as elevation if "
                "a person confirms that."
            )
        elif dtype == "uint8":
            role = "not_elevation"
            reasons.append("An 8-bit band holds picture brightness, not elevation.")
        else:
            role = "not_elevation"
            reasons.append(
                "One of several bands and not declared as elevation. Imagery bands are never treated as elevation."
            )
        bands.append(BandAssessment(info["band"], dtype, description, colour, units, unit, role, reasons))

    unusable = _unusable_reason(asset, state, settings)
    candidates = [b for b in bands if b.role != "not_elevation"]
    if unusable:
        status, reasons = "unusable", [unusable]
    elif not candidates:
        status, reasons = (
            "not_elevation",
            ["No band of this raster is elevation data."] + [r for b in bands for r in b.reasons[:1]],
        )
    elif any(b.role == "elevation_declared" and b.unit for b in candidates):
        status, reasons = "usable", ["A band is declared as elevation with a stated unit."]
    else:
        status, reasons = (
            "needs_confirmation",
            [
                "A band may be elevation but the file does not declare it, or does not state the unit. "
                "A person must state this for the view."
            ],
        )
    return Assessment(status, reasons, bands, unusable)


def _horizontal_unit(crs: pyproj.CRS) -> tuple[str, float]:
    """('degree', 1) for a geographic CRS, or ('linear', metres per CRS unit) for a projected one."""
    axis = crs.axis_info[0]
    if crs.is_geographic:
        if "degree" not in (axis.unit_name or "").lower():
            raise TerrainError(422, "TERRAIN_UNSUPPORTED_CRS", f"Its CRS uses {axis.unit_name!r} units, not degrees.")
        return "degree", 1.0
    if crs.is_projected:
        factor = axis.unit_conversion_factor
        if not factor or factor <= 0 or "degree" in (axis.unit_name or "").lower():
            raise TerrainError(
                422, "TERRAIN_UNSUPPORTED_CRS", f"Its CRS horizontal unit {axis.unit_name!r} is not a length."
            )
        return "linear", float(factor)
    raise TerrainError(422, "TERRAIN_UNSUPPORTED_CRS", "Its CRS is neither geographic nor projected.")


@dataclass
class Grid:
    values: np.ndarray  # float32, shape (rows, cols), north row first, NaN = no data
    meta: dict[str, Any]


def build_grid(
    path: Path,
    asset: Asset,
    settings: Settings,
    *,
    band: int | None,
    unit: str | None,
    confirm_elevation: bool,
    max_side: int,
    operation_id: str | None = None,
) -> Grid:
    """The decimated height grid and everything a viewer needs to place and describe it. Raises TerrainError."""
    assessment = assess(asset, settings)
    if assessment.status == "unusable":
        raise TerrainError(422, "TERRAIN_UNUSABLE", assessment.unusable_reason or "This raster cannot be shown in 3D.")
    if not assessment.candidates:
        raise TerrainError(422, "NOT_ELEVATION", "No band of this raster is elevation data. " + assessment.reasons[-1])
    if not 16 <= max_side <= settings.terrain_max_grid_side:
        raise TerrainError(
            422, "TERRAIN_GRID_SIZE", f"The grid side must be between 16 and {settings.terrain_max_grid_side}."
        )
    chosen = next((b for b in assessment.candidates if band is None or b.band == band), None)
    if chosen is None:
        raise TerrainError(
            422,
            "BAND_NOT_ELEVATION",
            f"Band {band} is not an elevation candidate. Candidates: {[b.band for b in assessment.candidates]}.",
        )
    if unit is not None and unit not in UNIT_TO_METRE:
        raise TerrainError(422, "UNIT_UNKNOWN", f"The unit must be one of {sorted(UNIT_TO_METRE)}.")
    if chosen.role == "possible" and not confirm_elevation:
        raise TerrainError(
            409,
            "ELEVATION_NOT_CONFIRMED",
            f"The file does not say band {chosen.band} is elevation. Confirm it is, for this view, to draw it as "
            f"terrain.",
        )
    if chosen.unit and unit and unit != chosen.unit:
        raise TerrainError(
            422,
            "UNIT_CONFLICT",
            f"The file declares the unit {chosen.unit!r}; a different unit cannot be stated over it.",
        )
    if not chosen.unit and not unit:
        raise TerrainError(
            409,
            "ELEVATION_UNIT_UNDECLARED",
            "The file declares no elevation unit. State the unit (metre or foot) for this view; the view will say it "
            "was stated by a person.",
        )
    effective_unit = chosen.unit or unit
    unit_source = "declared" if chosen.unit else "stated_for_this_view"

    state = georeference.effective(asset.raster)
    crs = pyproj.CRS.from_wkt(state.crs_wkt)
    kind, factor = _horizontal_unit(crs)
    a, _, c, _, e, f = _six(state.geotransform)
    width, height = asset.raster.width, asset.raster.height
    step = max(1, math.ceil(max(width, height) / max_side))
    cols, rows = math.ceil(width / step), math.ceil(height / step)

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", NotGeoreferencedWarning)
            with rasterio.open(path) as src:
                data = src.read(chosen.band, out_shape=(rows, cols), resampling=Resampling.nearest, masked=True)
                nodata = src.nodata
                scale = src.scales[chosen.band - 1] if src.scales else 1.0
                offset = src.offsets[chosen.band - 1] if src.offsets else 0.0
    except (RasterioError, OSError, ValueError) as exc:
        raise TerrainError(500, "TERRAIN_READ_FAILED", "The stored raster could not be read.") from exc

    mask = np.ma.getmaskarray(data)
    values = np.ma.getdata(data).astype("float64")
    invalid = mask | ~np.isfinite(values)
    scaled = bool((scale not in (None, 1.0)) or (offset not in (None, 0.0)))
    if scaled:
        values = values * (scale or 1.0) + (offset or 0.0)
    values[invalid] = np.nan
    valid = ~np.isnan(values)
    valid_count = int(valid.sum())
    if valid_count == 0:
        raise TerrainError(
            422, "TERRAIN_NO_VALID_DATA", "Every cell of this band is no-data, so there is no terrain to show."
        )

    warnings_out: list[str] = []
    if nodata is None:
        hits = {s: int(np.count_nonzero(values[valid] == s)) for s in SENTINELS}
        for sentinel, count in hits.items():
            if count:
                warnings_out.append(
                    f"{count} cell(s) hold the value {sentinel:g}, which is often a no-data marker, but the file "
                    f"declares no "
                    "no-data value. They are drawn as real elevations."
                )
    if np.nanmax(values) == np.nanmin(values):
        warnings_out.append("Every valid cell has the same value, so the surface is flat.")

    # Horizontal size of one grid cell.
    dx_native, dy_native = abs(a) * step, abs(e) * step
    x_min, y_max = c, f
    x_max, y_min = x_min + dx_native * cols, y_max - dy_native * rows
    if kind == "degree":
        lon_c, lat_c = (x_min + x_max) / 2, (y_min + y_max) / 2
        # Metres per cell: a size estimate on the WGS 84 ellipsoid. It is not a position, so it needs no transformation.
        geod = pyproj.Geod(ellps="WGS84")
        dx_m = geod.inv(lon_c, lat_c, lon_c + dx_native, lat_c)[2]
        dy_m = geod.inv(lon_c, lat_c, lon_c, lat_c + dy_native)[2]
        horizontal = {
            "method": "geodesic metres per cell at the centre latitude, applied across the whole area",
            "approximate": True,
        }
    else:
        dx_m, dy_m = dx_native * factor, dy_native * factor
        horizontal = {"method": f"exact: the CRS unit is {factor:g} m", "approximate": False}
    # Where the surface is on the earth, in WGS 84. The same planner decides as for every other conversion. If no
    # transformation may be used the position is left out and says why; the surface is still drawn in its own frame.
    if antimeridian.geometry_crosses(state.footprint):
        antimeridian.refuse("Building a 3D surface")
    state_source = state.source
    selection = convert.selection_for(asset.raster, state_source, operation_id)
    transformation_id = selection.operation_id
    xs = [x_min, x_max, x_min, x_max, (x_min + x_max) / 2]
    ys = [y_min, y_min, y_max, y_max, (y_min + y_max) / 2]
    bbox: list[float] | None
    lon_lat_centre: list[float] | None
    try:
        placed = convert.to_geographic(
            crs, xs, ys, operation_id=transformation_id, acknowledgement=selection.acknowledgement
        )
        lons, lats = [float(v) for v in placed.x], [float(v) for v in placed.y]
        bbox = [min(lons[:4]), min(lats[:4]), max(lons[:4]), max(lats[:4])]
        lon_lat_centre = [lons[4], lats[4]]
        positioning: dict[str, Any] = {"state": "positioned", "transformation": placed.provenance}
    except TransformationError as exc:
        bbox = lon_lat_centre = None
        positioning = {
            "state": "not_positioned",
            "code": exc.code,
            "reason": convert.unavailable_reason(exc),
            "candidates": [o.id for o in exc.plan.usable] if exc.plan else [],
        }

    vertical_name = None
    try:
        for sub in crs.sub_crs_list or []:
            if sub.is_vertical:
                vertical_name = sub.name
    except Exception:  # a CRS without sub-CRSs
        vertical_name = None

    grid = values.astype("float32")
    to_m = UNIT_TO_METRE[effective_unit]
    finite = grid[~np.isnan(grid)]
    meta: dict[str, Any] = {
        "band": chosen.band,
        "source_size": [width, height],
        "grid": {
            "width": cols,
            "height": rows,
            "decimation": step,
            "method": "nearest source pixel: every value is a real source pixel; nothing is averaged, smoothed or "
            "filled",
        },
        "crs_id": state.crs_id,
        "crs_name": state.crs_name,
        "georeferencing_source": state.source,
        "horizontal_unit": "degree" if kind == "degree" else "linear",
        "cell_size_native": [dx_native, dy_native],
        "cell_size_m": [dx_m, dy_m],
        "extent_native": {"x_min": x_min, "y_min": y_min, "x_max": x_max, "y_max": y_max},
        "extent_m": [dx_m * cols, dy_m * rows],
        "bbox_4326": bbox,
        "centre_4326": lon_lat_centre,
        "positioning": positioning,
        "horizontal": horizontal,
        "elevation": {
            "unit": effective_unit,
            "unit_label": UNIT_LABEL[effective_unit],
            "unit_source": unit_source,
            "to_metre": to_m,
            "role_source": "declared" if chosen.role == "elevation_declared" else "confirmed_for_this_view",
            "declared_scale_offset_applied": scaled,
            "nodata_value": None if nodata is None else float(nodata),
            "valid_cells": valid_count,
            "nodata_cells": int(grid.size - valid_count),
            "min": float(finite.min()),
            "max": float(finite.max()),
            "mean": float(finite.mean()),
        },
        "vertical_datum": {"state": "stated" if vertical_name else "not_stated", "name": vertical_name},
        "warnings": warnings_out,
    }
    return Grid(grid, meta)
