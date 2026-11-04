"""Web Mercator map tiles rendered on request from a georeferenced raster's stored original.

The original is only opened for reading. Each tile is produced by reading the part of the raster that covers the
tile (decimated when the tile is coarser than the raster) and reprojecting it with GDAL from the raster's own CRS
and full affine geotransform, so rotated and sheared rasters are placed correctly. Nothing is assumed about the
CRS: rasters without valid georeferencing never reach this module.

Tiles are for display only. Brightness is rescaled with the same per-band stretch the preview uses, so tiles and
preview look the same and no seams appear between tiles; the stretched values are not the measured values.
"""

import io
import math
import warnings
from functools import lru_cache
from pathlib import Path

import numpy as np
import rasterio
from affine import Affine
from PIL import Image
from rasterio.crs import CRS
from rasterio.enums import Resampling
from rasterio.errors import NotGeoreferencedWarning
from rasterio.transform import from_bounds
from rasterio.warp import reproject, transform_bounds
from rasterio.windows import Window
from rasterio.windows import transform as window_transform

from geopoc.geo import colormaps
from geopoc.geo.transform import transform_bounds_with

TILE = 256
WEB_MERCATOR = "EPSG:3857"
ORIGIN = 20037508.342789244  # half the width of the Web Mercator square, in metres
MAX_ZOOM = 24
# The source window is read at no more than this many pixels per side, whatever the zoom.
MAX_READ = 2 * TILE
CACHE_MB = 64


class TileError(ValueError):
    pass


def tile_bounds(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    """(left, bottom, right, top) of an XYZ tile in EPSG:3857 metres."""
    if not (0 <= z <= MAX_ZOOM and 0 <= x < 2**z and 0 <= y < 2**z):
        raise TileError("The tile address is outside the tile grid.")
    size = 2 * ORIGIN / 2**z
    left = -ORIGIN + x * size
    top = ORIGIN - y * size
    return left, top - size, left + size, top


def native_zoom(pixel_size_m: float, latitude: float) -> int:
    """The zoom level whose tile pixels are about the size of the raster's pixels."""
    ground = 2 * ORIGIN / TILE * math.cos(math.radians(latitude))
    return max(0, min(MAX_ZOOM, math.ceil(math.log2(ground / pixel_size_m)))) if pixel_size_m > 0 else 0


def _source_window(
    crs, transform: Affine, width: int, height: int, bounds_3857, operation: str | None = None
) -> Window | None:
    """The pixel window of the source covering the tile, from the inverse of the full affine transform.

    With `operation` (a PROJ pipeline from the raster's CRS to Web Mercator) the window follows that operation, so a
    chosen datum shift of hundreds of metres cannot push the raster outside the window that is read.
    """
    try:
        if operation is None:
            left, bottom, right, top = transform_bounds(WEB_MERCATOR, crs, *bounds_3857, densify_pts=21)
        else:
            left, bottom, right, top = transform_bounds_with(operation, bounds_3857)
    except Exception:  # noqa: BLE001 - PROJ raises several error types for points outside its domain
        return None
    if not all(math.isfinite(v) for v in (left, bottom, right, top)):
        return None
    inverse = ~transform
    corners = [inverse @ (x, y) for x, y in ((left, top), (right, top), (right, bottom), (left, bottom))]
    cols = [c for c, _ in corners]
    rows = [r for _, r in corners]
    col0, col1 = max(0, math.floor(min(cols)) - 1), min(width, math.ceil(max(cols)) + 1)
    row0, row1 = max(0, math.floor(min(rows)) - 1), min(height, math.ceil(max(rows)) + 1)
    if col1 <= col0 or row1 <= row0:
        return None
    return Window(col0, row0, col1 - col0, row1 - row0)


def _scale(band: np.ndarray, valid: np.ndarray, stretch: dict[str, float] | None, dtype: str) -> np.ndarray:
    if stretch is None:  # shown as stored (8-bit only; the display resolver refuses it for other types)
        return np.clip(np.nan_to_num(band), 0, 255).astype("uint8")
    low, high = stretch["low"], stretch["high"]
    span = (high - low) or 1.0
    values = np.nan_to_num(band.astype("float64"), nan=low, posinf=high, neginf=low)
    scaled = (np.clip((values - low) / span, 0, 1) * 255).astype("uint8")
    scaled[~valid] = 0
    return scaled


def render_tile(
    path: Path,
    bands: tuple[int, ...],
    stretch: tuple[tuple[float, float] | None, ...],
    z: int,
    x: int,
    y: int,
    georef: tuple[str, tuple[float, ...]] | None = None,
    operation: str | None = None,
    colormap: str | None = None,
) -> bytes | None:
    """A 256 x 256 RGBA PNG, or None when the raster does not cover the tile.

    `colormap` (a key of geo.colormaps) draws one band through a colour ramp over its stretch range instead of as grey;
    nodata stays transparent and no stored value changes. Without it the tile is drawn exactly as before.

    `georef` is (CRS WKT, GDAL-order geotransform) to use instead of the file's own, for rasters whose metadata was
    recorded by a person. The file itself is only read.

    `operation` is a PROJ pipeline from the raster's CRS to Web Mercator for a raster whose CRS has a datum shift. It is
    handed to GDAL so the tile is drawn through that shift and not through GDAL's own default.
    """
    bounds = tile_bounds(z, x, y)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", NotGeoreferencedWarning)
        with rasterio.Env(GDAL_CACHEMAX=CACHE_MB), rasterio.open(path) as src:
            if georef is not None:
                source_crs = CRS.from_wkt(georef[0])
                full_transform = Affine.from_gdal(*georef[1])
            else:
                if src.crs is None:
                    raise TileError("The raster has no CRS.")
                source_crs, full_transform = src.crs, src.transform
            window = _source_window(source_crs, full_transform, src.width, src.height, bounds, operation)
            if window is None:
                return None
            factor = max(window.width, window.height) / MAX_READ
            out_w = max(1, round(window.width / factor)) if factor > 1 else int(window.width)
            out_h = max(1, round(window.height / factor)) if factor > 1 else int(window.height)
            data = src.read(
                list(bands),
                window=window,
                out_shape=(len(bands), out_h, out_w),
                resampling=Resampling.average if factor > 1 else Resampling.nearest,
                masked=True,
            )
            source_transform = window_transform(window, full_transform) @ Affine.scale(
                window.width / out_w, window.height / out_h
            )
            dtype = src.dtypes[0]

    dst_transform = from_bounds(*bounds, TILE, TILE)
    warp_options = {"COORDINATE_OPERATION": operation} if operation else {}
    valid_source = (~np.ma.getmaskarray(data)).all(axis=0).astype("uint8")
    valid = np.zeros((TILE, TILE), dtype="uint8")
    reproject(
        valid_source,
        valid,
        src_transform=source_transform,
        src_crs=source_crs,
        dst_transform=dst_transform,
        dst_crs=WEB_MERCATOR,
        resampling=Resampling.nearest,
        dst_nodata=0,
        **warp_options,
    )
    if not valid.any():
        return None
    channels = []
    for index, band in enumerate(data):
        target = np.zeros((TILE, TILE), dtype="float64")
        reproject(
            band.filled(0).astype("float64"),
            target,
            src_transform=source_transform,
            src_crs=source_crs,
            dst_transform=dst_transform,
            dst_crs=WEB_MERCATOR,
            resampling=Resampling.bilinear,
            **warp_options,
        )
        band_stretch = stretch[index] if index < len(stretch) else None
        channels.append(
            _scale(
                target,
                valid.astype(bool),
                {"low": band_stretch[0], "high": band_stretch[1]} if band_stretch else None,
                dtype,
            )
        )
    if colormap is not None:
        if len(channels) != 1 or not stretch or stretch[0] is None:
            raise TileError("A colour ramp needs exactly one band and a display range.")
        table = colormaps.lut(colormap, float(stretch[0][0]), float(stretch[0][1]))
        channels = [table[channels[0], channel] for channel in range(3)]
    elif len(channels) == 1:
        channels = channels * 3
    rgba = np.dstack([*channels, valid * 255]).astype("uint8")
    buffer = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(buffer, "PNG", optimize=False)
    return buffer.getvalue()


@lru_cache(maxsize=512)
def cached_tile(
    key: str,
    path: str,
    bands: tuple[int, ...],
    stretch: tuple[tuple[float, float] | None, ...],
    z: int,
    x: int,
    y: int,
    georef: tuple[str, tuple[float, ...]] | None = None,
    operation: str | None = None,
    colormap: str | None = None,
) -> bytes | None:
    """`key` names the asset, its georeferencing version, the datum shift and display settings: a new version or a
    different datum shift never reuses a tile."""
    return render_tile(Path(path), bands, stretch, z, x, y, georef, operation, colormap)


def render_image(
    path: Path,
    bands: tuple[int, ...],
    stretch: tuple[tuple[float, float] | None, ...],
    max_side: int,
) -> tuple[bytes, int, int]:
    """The whole raster in image space (no georeferencing needed), reduced to at most `max_side` pixels.

    For looking only: values are averaged when reduced and rescaled for display.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", NotGeoreferencedWarning)
        with rasterio.Env(GDAL_CACHEMAX=CACHE_MB), rasterio.open(path) as src:
            scale = min(1.0, max_side / max(src.width, src.height))
            out_w, out_h = max(1, round(src.width * scale)), max(1, round(src.height * scale))
            data = src.read(
                list(bands),
                out_shape=(len(bands), out_h, out_w),
                resampling=Resampling.average if scale < 1 else Resampling.nearest,
                masked=True,
            )
            dtype = src.dtypes[bands[0] - 1]
    valid = (~np.ma.getmaskarray(data)).all(axis=0)
    channels = []
    for index, band in enumerate(data):
        band_stretch = stretch[index] if index < len(stretch) else None
        channels.append(
            _scale(
                band.filled(0).astype("float64"),
                valid,
                {"low": band_stretch[0], "high": band_stretch[1]} if band_stretch else None,
                dtype,
            )
        )
    if len(channels) == 1:
        channels = channels * 3
    rgba = np.dstack([*channels, valid.astype("uint8") * 255]).astype("uint8")
    buffer = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(buffer, "PNG")
    return buffer.getvalue(), out_w, out_h


@lru_cache(maxsize=64)
def cached_image(
    key: str, path: str, bands: tuple[int, ...], stretch: tuple[tuple[float, float] | None, ...], max_side: int
) -> tuple[bytes, int, int]:
    return render_image(Path(path), bands, stretch, max_side)
