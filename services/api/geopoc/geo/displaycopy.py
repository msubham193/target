"""Display copies: a Cloud Optimized GeoTIFF with overviews, made from the original for faster drawing.

The copy is lossless at full resolution (DEFLATE compression, same data type, same nodata, same CRS and
geotransform as the file declares) and is verified against the original before it is used: dimensions, bands,
types, nodata, geotransform and CRS are compared, and evenly spaced windows of full-resolution pixels must be
identical. Overviews (reduced-resolution levels) are averages and are used only for drawing zoomed-out views.

The original is only read. Statistics, pixel values, measurements and comparisons always read the original.
"""

import hashlib
import os
import time
import warnings
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from rasterio.errors import NotGeoreferencedWarning
from rasterio.shutil import copy as gdal_copy
from rasterio.windows import Window

CREATION_OPTIONS = {
    "COMPRESS": "DEFLATE",
    "PREDICTOR": "NO",
    "BLOCKSIZE": "512",
    "OVERVIEWS": "AUTO",
    "OVERVIEW_RESAMPLING": "AVERAGE",
    "BIGTIFF": "IF_SAFER",
    "NUM_THREADS": "ALL_CPUS",
}
CHECK_WINDOWS = 5  # per side: 5 x 5 = 25 full-resolution windows compared
CHECK_SIZE = 256


class DisplayCopyError(ValueError):
    pass


def needs_copy(width: int, height: int, overview_levels: list[int] | None, min_side: int) -> bool:
    return max(width, height) > min_side and not overview_levels


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _same(a, b) -> bool:
    return (a is None and b is None) or (a is not None and b is not None and a == b)


def verify(original: Path, copy: Path) -> dict[str, Any]:
    """Compare the copy with the original. Raises DisplayCopyError on any difference at full resolution."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", NotGeoreferencedWarning)
        with rasterio.open(original) as a, rasterio.open(copy) as b:
            checks = {
                "dimensions": (a.width, a.height) == (b.width, b.height),
                "band_count": a.count == b.count,
                "dtypes": list(a.dtypes) == list(b.dtypes),
                "nodata": [str(v) for v in a.nodatavals] == [str(v) for v in b.nodatavals],
                "geotransform": a.transform.almost_equals(b.transform, precision=1e-12) if a.transform else True,
                "crs": _same(a.crs, b.crs),
            }
            failed = [name for name, ok in checks.items() if not ok]
            if failed:
                raise DisplayCopyError(f"The display copy differs from the original in: {', '.join(failed)}.")
            compared = 0
            for row in np.linspace(0, max(0, a.height - CHECK_SIZE), CHECK_WINDOWS).astype(int):
                for col in np.linspace(0, max(0, a.width - CHECK_SIZE), CHECK_WINDOWS).astype(int):
                    window = Window(int(col), int(row), min(CHECK_SIZE, a.width), min(CHECK_SIZE, a.height))
                    left, right = a.read(window=window), b.read(window=window)
                    if not np.array_equal(left, right, equal_nan=np.dtype(a.dtypes[0]).kind == "f"):
                        raise DisplayCopyError(
                            f"Full-resolution pixels differ from the original in the window at column {col}, row {row}."
                        )
                    compared += 1
            overview_levels = b.overviews(1)
    return {
        "checks": checks,
        "windows_compared": compared,
        "window_size": CHECK_SIZE,
        "overview_levels": overview_levels,
    }


def build(original: Path, target: Path) -> dict[str, Any]:
    """Write and verify a display copy. The target is written via a temporary file and only kept if verified."""
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(".tmp.tif")
    started = time.perf_counter()
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", NotGeoreferencedWarning)
            with rasterio.Env(GDAL_CACHEMAX=256):
                gdal_copy(str(original), str(temp), driver="COG", **CREATION_OPTIONS)
        built_s = time.perf_counter() - started
        verification = verify(original, temp)
        os.replace(temp, target)
    except Exception:
        temp.unlink(missing_ok=True)
        raise
    return {
        "creation_options": CREATION_OPTIONS,
        "gdal_version": rasterio.__gdal_version__,
        "build_seconds": round(built_s, 2),
        "verification": verification,
        "size_bytes": target.stat().st_size,
        "sha256": _sha256(target),
        "note": "Lossless at full resolution; overviews are averages used only for zoomed-out drawing.",
    }
