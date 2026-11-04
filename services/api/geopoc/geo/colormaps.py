"""Colour ramps for drawing a single-band raster on the map (display only).

A ramp is a list of (value, colour) stops in the raster's own units, so a legend can be drawn from the same list the
tile renderer uses. The ramp is applied to a display range chosen separately from the data, and it never changes a
stored value: the GeoTIFF keeps its scientific numbers, this only decides which colour a number is drawn in.
"""

from dataclasses import dataclass
from functools import lru_cache

import numpy as np


@dataclass(frozen=True)
class Colormap:
    key: str
    title: str
    default_range: tuple[float, float]
    stops: tuple[tuple[float, str], ...]  # (value, "#rrggbb"), increasing in value


COLORMAPS: dict[str, Colormap] = {
    # Vegetation: blue-grey below zero (water, shadow, cloud edges), sand around zero (bare ground, built-up, cloud),
    # then yellow through green as vegetation increases.
    "ndvi": Colormap(
        key="ndvi",
        title="NDVI",
        default_range=(-0.2, 0.9),
        stops=(
            (-0.2, "#3b5b92"),
            (-0.05, "#9db4cf"),
            (0.0, "#e8dfc8"),
            (0.1, "#e9d98c"),
            (0.2, "#c8dd74"),
            (0.35, "#8fc95b"),
            (0.5, "#4fae4a"),
            (0.7, "#1f8a3c"),
            (0.9, "#0b5d2a"),
        ),
    ),
}

MAX_ABS_RANGE = 1000.0


class ColormapError(ValueError):
    pass


def get(key: str) -> Colormap:
    if key not in COLORMAPS:
        raise ColormapError(f"Unknown colour ramp '{key}'.")
    return COLORMAPS[key]


def parse_range(text: str | None, default: tuple[float, float]) -> tuple[float, float]:
    """'low,high' as two finite numbers with low < high; the ramp's own range when none is given."""
    if text is None or text == "":
        return default
    parts = text.split(",")
    try:
        low, high = (float(p) for p in parts) if len(parts) == 2 else (float("nan"), float("nan"))
    except ValueError:
        low = high = float("nan")
    finite = np.isfinite([low, high]).all()
    if not finite or not low < high or max(abs(low), abs(high)) > MAX_ABS_RANGE:
        raise ColormapError("The display range must be two finite numbers 'low,high' with low smaller than high.")
    return low, high


def _rgb(colour: str) -> tuple[int, int, int]:
    return int(colour[1:3], 16), int(colour[3:5], 16), int(colour[5:7], 16)


@lru_cache(maxsize=64)
def lut(key: str, low: float, high: float) -> np.ndarray:
    """A 256 x 3 uint8 table: row i is the colour for the value at i/255 of the way from `low` to `high`.

    Colours are interpolated linearly between the stops and held constant beyond the first and last stop."""
    ramp = get(key)
    values = np.array([v for v, _ in ramp.stops], dtype="float64")
    colours = np.array([_rgb(c) for _, c in ramp.stops], dtype="float64")
    at = low + np.linspace(0.0, 1.0, 256) * (high - low)
    table = np.stack([np.interp(at, values, colours[:, channel]) for channel in range(3)], axis=1)
    return np.rint(table).astype("uint8")


def describe(key: str, low: float | None = None, high: float | None = None) -> dict:
    """What a legend needs: the ramp's stops and the display range, from the same source the tiles use."""
    ramp = get(key)
    lo, hi = (low, high) if low is not None and high is not None else ramp.default_range
    return {
        "colormap": ramp.key,
        "title": ramp.title,
        "low": lo,
        "high": hi,
        "stops": [{"value": v, "color": c} for v, c in ramp.stops],
    }
