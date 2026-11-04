"""How a raster is drawn: which bands, and how stored values map to screen brightness. Display only.

A display setting never changes stored values, statistics, measurements or exports. Bands are chosen by number;
what a band represents is shown only as the file declares it, never inferred from its position or values.

Stretch modes:
  default     8-bit rasters as stored; others with the stretch computed with the preview
  none        values shown as stored (8-bit only: other types have no direct screen meaning)
  minmax      linear between each band's minimum and maximum (from band statistics)
  p0.5-99.5, p1-99, p2-98, p5-95
              linear between two percentiles of each band (from band statistics)
  manual      linear between a low and a high value given per band
"""

import math
from dataclasses import dataclass
from typing import Any

PERCENTILE_MODES = {"p0.5-99.5": ("0.5", "99.5"), "p1-99": ("1", "99"), "p2-98": ("2", "98"), "p5-95": ("5", "95")}
MODES = {"default", "none", "minmax", "manual", *PERCENTILE_MODES}


class DisplayError(ValueError):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code = status, code


@dataclass(frozen=True)
class Display:
    bands: tuple[int, ...]
    mode: str
    ranges: tuple[tuple[float, float] | None, ...]  # per band; None = shown as stored (8-bit)
    description: str

    @property
    def key(self) -> str:
        return f"{self.bands}:{self.mode}:{self.ranges}"


def _numbers(text: str | None, name: str) -> list[float]:
    if not text:
        return []
    try:
        values = [float(v) for v in text.split(",")]
    except ValueError as exc:
        raise DisplayError(422, "DISPLAY_INVALID", f"{name} must be comma-separated numbers.") from exc
    if not all(math.isfinite(v) for v in values):
        raise DisplayError(422, "DISPLAY_INVALID", f"{name} must be finite numbers.")
    return values


def parse_bands(text: str | None, band_count: int, default: list[int]) -> tuple[int, ...]:
    if not text:
        return tuple(default)
    try:
        bands = tuple(int(v) for v in text.split(","))
    except ValueError as exc:
        raise DisplayError(422, "DISPLAY_INVALID", "bands must be one or three band numbers, e.g. 1 or 4,3,2.") from exc
    if len(bands) not in (1, 3):
        raise DisplayError(422, "DISPLAY_INVALID", "Choose one band (grey) or three bands (red, green, blue display).")
    if not all(1 <= b <= band_count for b in bands):
        raise DisplayError(422, "DISPLAY_INVALID", f"Band numbers must be between 1 and {band_count}.")
    return bands


def resolve(
    *,
    dtypes: list[str],
    band_count: int,
    bands_text: str | None,
    stretch: str | None,
    low_text: str | None,
    high_text: str | None,
    statistics: dict[str, Any] | None,
    preview_details: dict[str, Any] | None,
) -> Display:
    """Turn request parameters into per-band display ranges, or explain why they cannot be used."""
    mode = stretch or "default"
    if mode not in MODES:
        raise DisplayError(422, "DISPLAY_INVALID", f"Unknown stretch {mode!r}. Use one of: {', '.join(sorted(MODES))}.")
    default_bands = [1, 2, 3] if band_count >= 3 else [1]
    bands = parse_bands(bands_text, band_count, default_bands)
    eight_bit = all(dtypes[b - 1] == "uint8" for b in bands)

    if mode == "default":
        if eight_bit:
            return Display(bands, mode, tuple(None for _ in bands), "8-bit values shown as stored")
        stretch_info = (preview_details or {}).get("display_stretch")
        shown = (preview_details or {}).get("bands_shown")
        if isinstance(stretch_info, dict) and list(bands) == list(shown or []):
            ranges = tuple((s["low"], s["high"]) if s else None for s in stretch_info.get("bands", []))
            return Display(bands, mode, ranges, "stretched between the 2nd and 98th percentile of the preview sample")
        if statistics is None:
            raise DisplayError(
                409,
                "DISPLAY_NOT_READY",
                "Band statistics are needed to draw these bands; they are computed in the background.",
            )
        mode = "p2-98"

    if mode == "none":
        if not eight_bit:
            raise DisplayError(
                422, "DISPLAY_INVALID", "Values can be shown as stored only for 8-bit bands; choose a stretch."
            )
        return Display(bands, mode, tuple(None for _ in bands), "8-bit values shown as stored")

    if mode == "manual":
        lows, highs = _numbers(low_text, "low"), _numbers(high_text, "high")
        if len(lows) != len(bands) or len(highs) != len(bands):
            raise DisplayError(422, "DISPLAY_INVALID", "Give one low and one high value for each band shown.")
        if any(lo >= hi for lo, hi in zip(lows, highs, strict=True)):
            raise DisplayError(422, "DISPLAY_INVALID", "Each low value must be smaller than its high value.")
        ranges = tuple(zip(lows, highs, strict=True))
        return Display(bands, mode, ranges, "stretched linearly between the values you entered")

    if statistics is None:
        raise DisplayError(409, "DISPLAY_NOT_READY", "Band statistics are needed for this stretch; compute them first.")
    ranges = []
    for band in bands:
        entry = statistics["bands"][band - 1]
        if entry.get("min") is None:
            raise DisplayError(409, "DISPLAY_NOT_AVAILABLE", f"Band {band} has no valid pixels to stretch.")
        if mode == "minmax":
            low, high = entry["min"], entry["max"]
        else:
            low_key, high_key = PERCENTILE_MODES[mode]
            low, high = entry["percentiles"][low_key], entry["percentiles"][high_key]
        if high <= low:
            low, high = entry["min"], entry["max"] if entry["max"] > entry["min"] else entry["min"] + 1
        ranges.append((float(low), float(high)))
    label = (
        "stretched between each band's minimum and maximum"
        if mode == "minmax"
        else "stretched between the {} and {} percentiles of each band".format(*PERCENTILE_MODES[mode])
    )
    method = statistics.get("method")
    return Display(bands, mode, tuple(ranges), f"{label} ({'exact' if method == 'exact' else 'sampled'} statistics)")
