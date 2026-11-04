"""Orientation of a georeferenced picture, measured on the ground rather than read from axis names.

Four different things are easy to confuse, and each is kept apart here:

* CRS axis direction: which way the CRS's x and y coordinates increase. Not always east and north. EPSG:2046 has x
  pointing west and y pointing south, EPSG:2193 lists northing first, polar stereographic CRSs use grid directions.
* Raster pixel-axis direction: which way columns and rows run on the ground. Rows normally run south.
* Map rotation: the angle between the picture's own axes and the CRS's grid, for a scan that was not laid square.
* Shear: the two pixel axes not being perpendicular on the ground. That is not a rotation and not a flip; it only
  happens when points are wrong or the sheet is distorted.

All of them are derived from `local_basis`: at a place in the CRS, how many metres east and north one unit of x and one
unit of y move you, found by converting a pair of nearby points to longitude and latitude on the CRS's own datum (no
datum shift is involved, so no transformation policy applies) and measuring the geodesic between them. That works for
any axis order, any direction and any unit, and it needs no assumption about which axis is "north".
"""

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
import pyproj
from pyproj import Transformer
from pyproj.exceptions import ProjError


@dataclass(frozen=True)
class Basis:
    """East and north metres moved by one native unit of x (`ex`) and of y (`ey`), per point. Shape (N, 2)."""

    ex: np.ndarray
    ey: np.ndarray

    def to_metres(self, dx: np.ndarray, dy: np.ndarray) -> np.ndarray:
        """A native displacement as east/north metres, per point. Shape (N, 2)."""
        return dx[:, None] * self.ex + dy[:, None] * self.ey


def local_basis(crs: pyproj.CRS, xs: np.ndarray, ys: np.ndarray) -> Basis | None:
    """The local ground basis at each native point, or None if any point is outside the CRS's domain."""
    xs, ys = np.atleast_1d(np.asarray(xs, dtype=float)), np.atleast_1d(np.asarray(ys, dtype=float))
    geodetic = crs.geodetic_crs or crs
    factor = (crs.axis_info[0].unit_conversion_factor or 1.0) if crs.axis_info else 1.0
    # About ten metres in whatever unit the CRS uses (a degree is 111 km, so a tiny step there).
    step = 1e-4 if crs.is_geographic else 10.0 / factor
    try:
        to_geodetic = Transformer.from_crs(crs, geodetic, always_xy=True)
        lon0, lat0 = to_geodetic.transform(xs, ys)
        lon_x, lat_x = to_geodetic.transform(xs + step, ys)
        lon_y, lat_y = to_geodetic.transform(xs, ys + step)
    except ProjError:
        return None
    arrays = [np.asarray(a, dtype=float) for a in (lon0, lat0, lon_x, lat_x, lon_y, lat_y)]
    if not all(np.isfinite(a).all() for a in arrays):
        return None
    lon0, lat0, lon_x, lat_x, lon_y, lat_y = arrays
    geod = crs.get_geod()

    def vector(lon1: np.ndarray, lat1: np.ndarray) -> np.ndarray:
        azimuth, _back, distance = geod.inv(lon0, lat0, lon1, lat1)
        azimuth, distance = np.radians(np.asarray(azimuth)), np.asarray(distance)
        return np.column_stack([distance * np.sin(azimuth), distance * np.cos(azimuth)]) / step

    return Basis(ex=vector(lon_x, lat_x), ey=vector(lon_y, lat_y))


def _angle(vector: np.ndarray) -> float:
    return math.degrees(math.atan2(vector[1], vector[0]))


def visual_axes(crs: pyproj.CRS) -> list[Any]:
    """The CRS's axes in the order coordinates are given here (x first). PROJ's visualisation order swaps a
    latitude-first pair (north/south, then east/west) and leaves every other order alone, so x is not always east: it is
    west in EPSG:2046 and the easting of a polar grid elsewhere."""
    axes = list(crs.axis_info)
    first = (axes[0].direction or "").lower() if axes else ""
    second = (axes[1].direction or "").lower() if len(axes) >= 2 else ""
    if first in ("north", "south") and second in ("east", "west"):
        axes[0], axes[1] = axes[1], axes[0]
    return axes


def _visual_directions(crs: pyproj.CRS) -> list[str]:
    return [(axis.direction or "").lower() for axis in visual_axes(crs)]


def order_note(crs: pyproj.CRS) -> str:
    """One sentence saying which way x and y run in this CRS, without assuming east and north."""
    axes = visual_axes(crs)
    if len(axes) < 2:
        return "x is the first coordinate and y the second."
    x, y = (axes[0].direction or "").lower(), (axes[1].direction or "").lower()
    if x == "east" and y == "north":
        return "x is easting or longitude and y is northing or latitude, whatever the authority's axis order."
    plain = {"east", "west", "north", "south"}
    along_one_line = {x, y} <= {"north", "south"}  # both axes called "north": a polar grid, defined by grid directions
    if x not in plain or y not in plain or along_one_line:
        return (
            f"This CRS's axes ({axes[0].abbrev}, {axes[1].abbrev}) follow grid directions, not east and north. x is "
            "always given first, whatever the authority's axis order."
        )
    return (
        f"In this CRS x ({axes[0].abbrev}) increases towards the {x} and y ({axes[1].abbrev}) towards the {y}; "
        "x is always given first, whatever the authority's axis order."
    )


def grid_right(crs: pyproj.CRS, ex: np.ndarray, ey: np.ndarray) -> np.ndarray | None:
    """The direction the CRS's grid calls "right" on the ground: its "up" axis turned a quarter-turn clockwise.

    "Up" is the axis the CRS itself says runs north or south (the y axis if it does, otherwise x), pointing the way
    that axis points north. That is the CRS's own convention, so it is right for a south-west grid such as EPSG:2046
    and for a polar grid, where the axes are not aligned with true north by design. None when the CRS gives no
    north/south axis."""
    directions = _visual_directions(crs)
    for index, vector in ((1, ey), (0, ex)):
        if index < len(directions) and directions[index] in ("north", "south"):
            up = vector / np.linalg.norm(vector)
            if directions[index] == "south":
                up = -up
            return np.array([up[1], -up[0]])  # a quarter-turn clockwise (east when up is north)
    return None


@dataclass(frozen=True)
class Geometry:
    """What an affine transform does on the ground at one place."""

    u: np.ndarray  # east/north metres per pixel column step
    v: np.ndarray  # east/north metres per pixel row step
    column_m: float  # pixel width on the ground
    row_m: float  # pixel height on the ground
    ground_pixel_m: float  # sqrt(column_m * row_m)
    anisotropy: float  # long side over short side of the ground pixel
    shear_degrees: float  # how far the pixel axes are from perpendicular
    handedness: str  # "normal" (columns east, rows south) or "mirrored"
    rotation_degrees: float | None  # the picture against the CRS's grid; None when the grid has no usable north
    rotation_reference: str


def geometry(crs: pyproj.CRS, gt: list[float], ex: np.ndarray, ey: np.ndarray) -> Geometry:
    """The ground geometry of the geotransform `gt` where the CRS's local basis is `ex`, `ey` (each of shape (2,))."""
    u = gt[1] * ex + gt[4] * ey
    v = gt[2] * ex + gt[5] * ey
    column_m, row_m = float(np.linalg.norm(u)), float(np.linalg.norm(v))
    cosine = float(np.clip(np.dot(u, v) / (column_m * row_m), -1.0, 1.0))
    shear = abs(math.degrees(math.acos(cosine)) - 90.0)
    cross = float(u[0] * v[1] - u[1] * v[0])
    right = grid_right(crs, ex, ey)
    rotation = None
    if right is not None:
        rotation = (_angle(u) - _angle(right) + 180.0) % 360.0 - 180.0
    return Geometry(
        u=u,
        v=v,
        column_m=column_m,
        row_m=row_m,
        ground_pixel_m=math.sqrt(column_m * row_m),
        anisotropy=max(column_m, row_m) / min(column_m, row_m),
        shear_degrees=shear,
        handedness="mirrored" if cross > 0 else "normal",
        rotation_degrees=rotation,
        rotation_reference="the CRS's own up axis" if right is not None else "not defined for this CRS",
    )
