"""Non-AI pixel comparison of two aligned rasters: B − A, band by band, written as a new derived raster.

Only rasters on the *same grid* are compared: both validly georeferenced (from their files or from recorded
georeferencing), the same CRS, the same geotransform and the same dimensions. Nothing is resampled, reprojected
or shifted to make rasters fit. The result is a pixel difference: it is not an interpretation of change.

Pixels that are nodata, masked or non-finite in either input are nodata (NaN) in the result.
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
from rasterio.crs import CRS
from rasterio.errors import NotGeoreferencedWarning
from rasterio.windows import Window

from geopoc.geo.georeference import Effective

TOOL = "geopoc.pixel_difference"
TOOL_VERSION = "1"
GEOTRANSFORM_TOLERANCE = 1e-9  # relative, per parameter: floating-point noise only, not misalignment
ROWS_PER_BLOCK = 512


@dataclass
class Alignment:
    aligned: bool
    reasons: list[str] = field(default_factory=list)


def check_alignment(
    width_a: int, height_a: int, state_a: Effective, width_b: int, height_b: int, state_b: Effective
) -> Alignment:
    reasons = []
    for label, state in (("A", state_a), ("B", state_b)):
        if not state.georeferenced or not state.crs_wkt or not state.geotransform:
            reasons.append(
                f"Raster {label} has no valid georeferencing, so its pixels cannot be matched to the other's."
            )
    if reasons:
        return Alignment(False, reasons)
    if (width_a, height_a) != (width_b, height_b):
        reasons.append(f"The sizes differ ({width_a} × {height_a} and {width_b} × {height_b} pixels).")
    if pyproj.CRS.from_wkt(state_a.crs_wkt) != pyproj.CRS.from_wkt(state_b.crs_wkt):
        reasons.append(
            f"The CRSs differ ({state_a.crs_id or state_a.crs_name} and {state_b.crs_id or state_b.crs_name})."
        )
    for name, a, b in zip(
        ("x origin", "pixel width", "row rotation", "y origin", "column rotation", "pixel height"),
        state_a.geotransform,
        state_b.geotransform,
        strict=True,
    ):
        if abs(a - b) > GEOTRANSFORM_TOLERANCE * max(1.0, abs(a), abs(b)):
            reasons.append(f"The geotransforms differ in {name} ({a:g} and {b:g}).")
    return Alignment(not reasons, reasons)


def compute(
    path_a: Path,
    band_a: int,
    path_b: Path,
    band_b: int,
    target: Path,
    crs_wkt: str,
    geotransform: list[float],
    progress=None,
) -> dict[str, Any]:
    """Write B − A as a float GeoTIFF on the common grid. Returns summary statistics of the difference."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", NotGeoreferencedWarning)
        with rasterio.Env(GDAL_CACHEMAX=128), rasterio.open(path_a) as a, rasterio.open(path_b) as b:
            if not (1 <= band_a <= a.count and 1 <= band_b <= b.count):
                raise ValueError("A band number is outside the raster's bands.")
            kinds = {np.dtype(a.dtypes[band_a - 1]).kind, np.dtype(b.dtypes[band_b - 1]).kind}
            if not kinds <= {"u", "i", "f", "b"}:
                raise ValueError("Only integer and real-valued bands can be compared.")
            wide = "float64" in (a.dtypes[band_a - 1], b.dtypes[band_b - 1])
            out_dtype = "float64" if wide else "float32"
            profile = {
                "driver": "GTiff",
                "width": a.width,
                "height": a.height,
                "count": 1,
                "dtype": out_dtype,
                "crs": CRS.from_wkt(crs_wkt),
                "transform": Affine.from_gdal(*geotransform),
                "nodata": float("nan"),
                "compress": "DEFLATE",
                "tiled": True,
                "blockxsize": 256,
                "blockysize": 256,
            }
            target.parent.mkdir(parents=True, exist_ok=True)
            count = 0
            total = 0.0
            total_sq = 0.0
            minimum, maximum = math.inf, -math.inf
            with rasterio.open(target, "w", **profile) as out:
                out.set_band_description(1, f"B (band {band_b}) minus A (band {band_a}); pixel difference, not change")
                out.update_tags(
                    GEOPOC_TOOL=TOOL,
                    GEOPOC_TOOL_VERSION=TOOL_VERSION,
                    GEOPOC_FORMULA="B - A",
                    GEOPOC_NOTE="Derived by Geospatial PoC. Nodata where either input is nodata, masked or not finite.",
                )
                for row in range(0, a.height, ROWS_PER_BLOCK):
                    window = Window(0, row, a.width, min(ROWS_PER_BLOCK, a.height - row))
                    va = a.read(band_a, window=window, masked=True)
                    vb = b.read(band_b, window=window, masked=True)
                    xa, xb = va.data.astype("float64"), vb.data.astype("float64")
                    valid = ~np.ma.getmaskarray(va) & ~np.ma.getmaskarray(vb) & np.isfinite(xa) & np.isfinite(xb)
                    diff = np.where(valid, xb - xa, np.nan)
                    out.write(diff.astype(out_dtype), 1, window=window)
                    selected = diff[valid]
                    if selected.size:
                        count += int(selected.size)
                        total += float(selected.sum())
                        total_sq += float((selected * selected).sum())
                        minimum = min(minimum, float(selected.min()))
                        maximum = max(maximum, float(selected.max()))
                    if progress:
                        progress(min(1.0, (row + window.height) / a.height))
    mean = total / count if count else None
    return {
        "formula": "B - A",
        "valid_pixels": count,
        "pixels": a.width * a.height,
        "min": minimum if count else None,
        "max": maximum if count else None,
        "mean": mean,
        "std": math.sqrt(max(0.0, total_sq / count - mean * mean)) if count else None,
        "output_dtype": out_dtype,
        "gdal_version": rasterio.__gdal_version__,
    }
