"""Band statistics of a raster, computed from its stored original (read only).

Exact when a band has at most `statistics_exact_max_pixels` pixels; above that, from an evenly spaced
nearest-neighbour sample of real pixel values (never an average, so every value counted occurs in the file). The
method and the share of pixels used are recorded with the result. Nodata (declared value or mask) and non-finite
values are excluded and counted.
"""

import math
import warnings
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.errors import NotGeoreferencedWarning

PERCENTILES = (0.5, 1, 2, 5, 25, 50, 75, 95, 98, 99, 99.5)
HISTOGRAM_BINS = 64
CACHE_MB = 64


class StatisticsUnsupported(ValueError):
    pass


def band_info(src) -> list[dict[str, Any]]:
    """What the file declares about each band. Nothing is inferred from band order or values."""
    return [
        {
            "band": index + 1,
            "dtype": src.dtypes[index],
            "nodata": None
            if src.nodatavals[index] is None
            or (isinstance(src.nodatavals[index], float) and math.isnan(src.nodatavals[index]))
            else src.nodatavals[index],
            "nodata_is_nan": isinstance(src.nodatavals[index], float) and math.isnan(src.nodatavals[index]),
            "description": src.descriptions[index] or None,
            "color_interpretation": src.colorinterp[index].name,
            "units": (src.units[index] or None) if src.units else None,
            "scale": src.scales[index] if src.scales else None,
            "offset": src.offsets[index] if src.offsets else None,
        }
        for index in range(src.count)
    ]


def compute(path: Path, max_exact_pixels: int, progress=None) -> dict[str, Any]:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", NotGeoreferencedWarning)
        with rasterio.Env(GDAL_CACHEMAX=CACHE_MB), rasterio.open(path) as src:
            kinds = {np.dtype(d).kind for d in src.dtypes}
            if not kinds <= {"u", "i", "f", "b"}:
                raise StatisticsUnsupported("Statistics are only computed for integer and real-valued bands.")
            width, height = src.width, src.height
            total = width * height
            if total <= max_exact_pixels:
                shape, method = (height, width), "exact"
            else:
                factor = math.sqrt(total / max_exact_pixels)
                shape = (max(1, math.floor(height / factor)), max(1, math.floor(width / factor)))
                method = "sample"
            bands = []
            info = band_info(src)
            for index in range(src.count):
                data = src.read(index + 1, out_shape=shape, resampling=Resampling.nearest, masked=True)
                mask = np.ma.getmaskarray(data)
                values = data.data.astype("float64")
                finite = np.isfinite(values)
                valid = ~mask & finite
                selected = values[valid]
                read = int(values.size)
                entry: dict[str, Any] = {
                    **info[index],
                    "pixels_read": read,
                    "valid": int(selected.size),
                    "nodata_or_masked": int(mask.sum()),
                    "non_finite": int((~finite & ~mask).sum()),
                }
                if selected.size:
                    low, high = float(selected.min()), float(selected.max())
                    entry.update(
                        min=low,
                        max=high,
                        mean=float(selected.mean()),
                        std=float(selected.std()),
                        percentiles={
                            str(p): float(v)
                            for p, v in zip(PERCENTILES, np.percentile(selected, PERCENTILES), strict=True)
                        },
                    )
                    counts, edges = np.histogram(
                        selected, bins=HISTOGRAM_BINS, range=(low, high if high > low else low + 1)
                    )
                    entry["histogram"] = {"counts": counts.tolist(), "edges": [float(e) for e in edges]}
                else:
                    entry.update(min=None, max=None, mean=None, std=None, percentiles={}, histogram=None)
                bands.append(entry)
                if progress:
                    progress((index + 1) / src.count)
    return {
        "width": width,
        "height": height,
        "band_count": len(bands),
        "method": method,
        "sample_shape": list(shape),
        "fraction_of_pixels": (shape[0] * shape[1]) / total,
        "method_note": "Every pixel was read."
        if method == "exact"
        else f"An evenly spaced sample of {shape[1]} × {shape[0]} real pixel values (nearest neighbour, no "
        "averaging) was read; the statistics are estimates of the whole raster's.",
        "percentiles_listed": list(PERCENTILES),
        "gdal_version": rasterio.__gdal_version__,
        "bands": bands,
    }
