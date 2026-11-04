"""Reads the GPS position from a photograph's EXIF block, as an unverified candidate.

This module only reads and checks numbers. It never decides that a photograph is located: the result is held as a
`candidate` until a person confirms it. Only the position is read (latitude, longitude, hemisphere references, the
declared datum and the receiver's validity flag). Altitude, speed, heading, timestamps and every other tag are ignored,
so nothing else in the GPS block is stored or shown.
"""

import math
from dataclasses import dataclass, field
from typing import Any, Literal

from PIL import Image

GPS_IFD = 0x8825
TAG_LAT_REF, TAG_LAT, TAG_LON_REF, TAG_LON = 1, 2, 3, 4
TAG_STATUS, TAG_DATUM = 9, 18

# EXIF's own default for the GPS block is WGS 84, so a missing datum tag is accepted and said so. A declared datum is
# accepted only when it names WGS 84; anything else would need a datum shift this application does not guess at.
WGS84_NAMES = {"WGS-84", "WGS84", "WGS 84", "EPSG:4326"}

PROBLEMS = {
    "GPS_INCOMPLETE": "The GPS block lacks a latitude or longitude (or its N/S/E/W reference).",
    "GPS_MALFORMED": "The latitude or longitude is not three numbers (degrees, minutes, seconds).",
    "GPS_REFERENCE_INVALID": "A hemisphere reference is not N, S, E or W.",
    "GPS_NOT_FINITE": "A coordinate value is not a finite number.",
    "GPS_OUT_OF_RANGE": "A coordinate is outside latitude -90 to 90 or longitude -180 to 180.",
    "GPS_VOID": "The device marked the reading as a void measurement (no valid fix).",
    "GPS_DATUM_NOT_WGS84": "The GPS block declares a datum other than WGS 84. No datum shift is applied.",
    "GPS_ZERO_COORDINATE": "The position is exactly 0, 0, which devices commonly write when they have no fix.",
    "GPS_UNREADABLE": "The GPS block could not be read.",
}


@dataclass(frozen=True)
class GpsReading:
    state: Literal["absent", "candidate", "unusable"]
    latitude: float | None = None
    longitude: float | None = None
    problems: tuple[str, ...] = ()
    details: dict[str, Any] = field(default_factory=dict)

    def messages(self) -> list[dict[str, str]]:
        return [{"code": code, "message": PROBLEMS[code]} for code in self.problems]


def _dms(value: Any) -> tuple[float, float, float] | None:
    """Three finite numbers, or None when the value is not a degrees/minutes/seconds triple."""
    if not isinstance(value, tuple | list) or len(value) != 3:
        return None
    numbers = []
    for part in value:
        try:
            numbers.append(float(part))
        except (TypeError, ValueError, ZeroDivisionError):
            return None
    return numbers[0], numbers[1], numbers[2]


def _decimal(parts: tuple[float, float, float]) -> float | None:
    degrees, minutes, seconds = parts
    if not all(math.isfinite(p) for p in parts):
        return None
    return degrees + minutes / 60.0 + seconds / 3600.0


def _text(value: Any) -> str | None:
    if isinstance(value, bytes):
        value = value.decode("ascii", errors="replace")
    return value.strip().strip("\x00").strip() if isinstance(value, str) else None


def read_gps(exif: Image.Exif) -> GpsReading:
    """The photograph's GPS position as a candidate, or the reasons it cannot be one."""
    try:
        block = exif.get_ifd(GPS_IFD)
    except Exception:  # noqa: BLE001 - a malformed block must never fail an upload
        return GpsReading("unusable", problems=("GPS_UNREADABLE",))
    if not block:
        return GpsReading("absent")

    problems: list[str] = []
    lat_ref = (_text(block.get(TAG_LAT_REF)) or "").upper()
    lon_ref = (_text(block.get(TAG_LON_REF)) or "").upper()
    if TAG_LAT not in block or TAG_LON not in block or not lat_ref or not lon_ref:
        return GpsReading("unusable", problems=("GPS_INCOMPLETE",))
    lat_parts, lon_parts = _dms(block.get(TAG_LAT)), _dms(block.get(TAG_LON))
    if lat_parts is None or lon_parts is None:
        return GpsReading("unusable", problems=("GPS_MALFORMED",))
    if lat_ref not in ("N", "S") or lon_ref not in ("E", "W"):
        problems.append("GPS_REFERENCE_INVALID")
    latitude, longitude = _decimal(lat_parts), _decimal(lon_parts)
    if latitude is None or longitude is None:
        problems.append("GPS_NOT_FINITE")
    else:
        latitude = round(-latitude if lat_ref == "S" else latitude, 8) + 0.0
        longitude = round(-longitude if lon_ref == "W" else longitude, 8) + 0.0
        # Minutes and seconds are below 60 by definition; larger values are a sign of a damaged or non-standard block.
        sexagesimal_ok = min(lat_parts + lon_parts) >= 0 and max(lat_parts[1:] + lon_parts[1:]) < 60
        if not (abs(latitude) <= 90 and abs(longitude) <= 180) or not sexagesimal_ok:
            problems.append("GPS_OUT_OF_RANGE")
        elif latitude == 0 and longitude == 0:
            problems.append("GPS_ZERO_COORDINATE")

    if (_text(block.get(TAG_STATUS)) or "").upper() == "V":
        problems.append("GPS_VOID")
    datum = _text(block.get(TAG_DATUM))
    if datum and datum.upper() not in WGS84_NAMES:
        problems.append("GPS_DATUM_NOT_WGS84")

    if problems:
        return GpsReading("unusable", problems=tuple(dict.fromkeys(problems)))
    return GpsReading(
        "candidate",
        latitude=latitude,
        longitude=longitude,
        details={
            "latitude_ref": lat_ref,
            "longitude_ref": lon_ref,
            "latitude_dms": list(lat_parts),
            "longitude_dms": list(lon_parts),
            "datum": datum or "not declared (EXIF default WGS 84)",
            "datum_declared": datum is not None,
        },
    )
