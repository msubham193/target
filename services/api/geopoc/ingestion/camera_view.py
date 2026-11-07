"""Which way a photograph's camera faced, and how wide its view was, as the file's EXIF states it. Hints, never facts.

Read from the stored original when asked and never stored. Nothing here places anything: the direction lets a person see
which way the picture looks from its confirmed position, and the lens figure says roughly how wide that view is. A phone
compass can be off by tens of degrees near metal or electronics, and the file does not say how good the reading was.
"""

import math
from dataclasses import dataclass
from typing import Any, Literal

from PIL import Image

GPS_IFD = 0x8825
EXIF_IFD = 0x8769
TAG_DIRECTION_REF, TAG_DIRECTION = 16, 17
TAG_FOCAL_LENGTH = 0x920A
TAG_FOCAL_35MM = 0xA405

PROBLEMS = {
    "HEADING_ABSENT": "The file does not record which way the camera faced (no GPS image direction).",
    "HEADING_INVALID": "The recorded camera direction is not a number between 0 and 360 degrees.",
    "FOCAL_35MM_ABSENT": (
        "The file does not give the lens's 35 mm equivalent focal length, so the width of view is not known."
    ),
    "EXIF_UNREADABLE": "The file's EXIF block could not be read.",
}

Reference = Literal["true_north", "magnetic_north", "not_stated"]


@dataclass(frozen=True)
class CameraView:
    heading_deg: float | None = None
    heading_reference: Reference | None = None
    focal_length_35mm_mm: float | None = None
    focal_length_mm: float | None = None
    problems: tuple[str, ...] = ()


def _number(value: Any) -> float | None:
    """A finite float, or None (missing, a zero-denominator rational, text, NaN)."""
    if isinstance(value, tuple | list) and len(value) == 2:
        try:
            value = float(value[0]) / float(value[1])
        except (TypeError, ValueError, ZeroDivisionError):
            return None
    try:
        number = float(value)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    return number if math.isfinite(number) else None


def _text(value: Any) -> str:
    if isinstance(value, bytes):
        value = value.decode("ascii", errors="replace")
    return value.strip().strip("\x00").strip().upper() if isinstance(value, str) else ""


def read_camera_view(exif: Image.Exif) -> CameraView:
    """The camera direction and lens figures the file states, with the reasons for anything missing."""
    try:
        gps = exif.get_ifd(GPS_IFD) or {}
        settings = exif.get_ifd(EXIF_IFD) or {}
    except Exception:  # noqa: BLE001 - a malformed block must never fail the request
        return CameraView(problems=("EXIF_UNREADABLE",))

    problems: list[str] = []
    heading: float | None = None
    reference: Reference | None = None
    if TAG_DIRECTION not in gps:
        problems.append("HEADING_ABSENT")
    else:
        heading = _number(gps.get(TAG_DIRECTION))
        if heading is None or not 0 <= heading <= 360:
            heading = None
            problems.append("HEADING_INVALID")
        else:
            heading = round(heading % 360, 2) + 0.0
            ref = _text(gps.get(TAG_DIRECTION_REF))
            reference = "true_north" if ref == "T" else "magnetic_north" if ref == "M" else "not_stated"

    focal_35 = _number(settings.get(TAG_FOCAL_35MM))
    if focal_35 is None or focal_35 <= 0:
        focal_35 = None
        problems.append("FOCAL_35MM_ABSENT")
    focal = _number(settings.get(TAG_FOCAL_LENGTH))
    if focal is not None and focal <= 0:
        focal = None

    return CameraView(
        heading_deg=heading,
        heading_reference=reference,
        focal_length_35mm_mm=focal_35,
        focal_length_mm=round(focal, 2) if focal is not None else None,
        problems=tuple(problems),
    )
