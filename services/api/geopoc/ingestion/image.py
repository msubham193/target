"""Ground photograph validation and metadata.

A ground photograph is never given a geographic location here. Its EXIF GPS position, if any, is read as an unverified
candidate (ingestion/gps.py) that stays a candidate until a person confirms it.
"""

from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, UnidentifiedImageError

from geopoc.config import Settings
from geopoc.ingestion.gps import GpsReading, read_gps
from geopoc.ingestion.rules import Inspection, UnreadableAsset, json_safe

# The pixel cap is enforced explicitly below, with a clear error, instead of Pillow's warning.
Image.MAX_IMAGE_PIXELS = None

FORMATS = {"jpeg": ("JPEG", "image/jpeg"), "png": ("PNG", "image/png"), "webp": ("WEBP", "image/webp")}

EXIF_IFD = 0x8769
GPS_IFD = 0x8825
SHARPNESS_METHOD = "variance of the 4-neighbour Laplacian on a greyscale copy downscaled to at most 1024 px"


def read_exif(image: Image.Image) -> dict[str, Any]:
    exif = image.getexif()
    if not exif:
        return {}
    detail = exif.get_ifd(EXIF_IFD)
    return {
        "make": exif.get(271),
        "model": exif.get(272),
        "orientation": exif.get(274),
        "datetime": exif.get(306),
        "datetime_original": detail.get(36867),
        "offset_time_original": detail.get(36881),
        "gps_tags_present": bool(exif.get_ifd(GPS_IFD)),
    }


def _capture_time(exif: dict[str, Any]) -> datetime | None:
    """Only a capture time that states its UTC offset is unambiguous enough to store as a timestamp."""
    stamp, offset = exif.get("datetime_original"), exif.get("offset_time_original")
    if not (isinstance(stamp, str) and isinstance(offset, str)):
        return None
    try:
        return datetime.strptime(f"{stamp.strip()} {offset.strip()}", "%Y:%m:%d %H:%M:%S %z")
    except ValueError:
        return None


def _sharpness(image: Image.Image) -> float | None:
    grey = image.convert("L")
    grey.thumbnail((1024, 1024))
    pixels = np.asarray(grey, dtype=np.float32)
    if min(pixels.shape) < 3:
        return None
    laplacian = pixels[:-2, 1:-1] + pixels[2:, 1:-1] + pixels[1:-1, :-2] + pixels[1:-1, 2:] - 4 * pixels[1:-1, 1:-1]
    return float(laplacian.var())


def inspect_image(path: Path, family: str, settings: Settings) -> Inspection:
    expected_format, media_type = FORMATS[family]
    try:
        with Image.open(path) as probe:
            actual_format = probe.format
            width, height = probe.size
            frames = getattr(probe, "n_frames", 1)
            mode = probe.mode
            if actual_format != expected_format:
                raise UnreadableAsset("ASSET_UNREADABLE", f"The file could not be read as a {expected_format} image.")
            if width * height > settings.max_image_pixels:
                raise UnreadableAsset(
                    "IMAGE_TOO_MANY_PIXELS",
                    f"The image is {width} x {height} pixels, above the limit of {settings.max_image_pixels:,} pixels.",
                )
            probe.verify()
        # verify() leaves the image unusable, so reopen for a full decode: this is what proves readability.
        with Image.open(path) as image:
            if family == "jpeg":
                image.draft("RGB", (2048, 2048))  # decode at reduced scale to bound memory
            image.load()
            sharpness = _sharpness(image)
            try:
                exif: dict[str, Any] | None = read_exif(image)
                exif_error = None
            except Exception as exc:  # EXIF blocks are frequently malformed; the image itself is still valid
                exif, exif_error = None, type(exc).__name__
            try:
                gps = read_gps(image.getexif())
            except Exception:  # noqa: BLE001 - never let a damaged GPS block fail the upload
                gps = GpsReading("unusable", problems=("GPS_UNREADABLE",))
    except UnreadableAsset:
        raise
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, EOFError) as exc:
        raise UnreadableAsset(
            "ASSET_UNREADABLE", f"The file is corrupt or not a readable {expected_format} image."
        ) from exc

    result = Inspection(detected_format=expected_format, media_type=media_type)
    result.metadata = {
        "width": width,
        "height": height,
        "mode": mode,
        "frames": frames,
        "quality_indicators": {
            "sharpness": {
                "value": sharpness,
                "method": SHARPNESS_METHOD,
                "threshold": None,
                "note": "Raw indicator only. No validated threshold exists, so it makes no blur judgement.",
            }
        },
    }

    if exif_error is not None:
        result.unknowns["exif"] = f"Embedded EXIF metadata could not be read ({exif_error})."
        result.flag("METADATA_INCOMPLETE")
    else:
        assert exif is not None
        result.metadata["exif"] = exif

    result.acquisition_time = _capture_time(exif or {})
    if result.acquisition_time is None:
        if exif and exif.get("datetime_original"):
            reason = "The EXIF capture time has no UTC offset, so the moment of capture is ambiguous."
        else:
            reason = "The file carries no capture time."
        result.unknowns["acquisition_time"] = reason
        result.flag("TIMESTAMP_UNKNOWN")

    result.photo_gps = gps
    # Only the state and problem codes go on the asset; the coordinates are held in the photo's location history.
    result.metadata["exif_gps"] = {"state": gps.state, "problems": list(gps.problems)}
    if gps.state == "candidate":
        result.unknowns["geolocation"] = (
            "EXIF GPS coordinates were read and are held as an unverified candidate. They are not a location "
            "until a person confirms them."
        )
    elif gps.state == "unusable":
        reasons = " ".join(m["message"] for m in gps.messages())
        result.unknowns["geolocation"] = (
            f"EXIF GPS tags are present but cannot be used as a location candidate. {reasons}"
        )
    elif exif and exif.get("gps_tags_present"):
        result.unknowns["geolocation"] = (
            "EXIF GPS tags are present but could not be read; they are not used as a location."
        )
    else:
        result.unknowns["geolocation"] = "A ground photograph has no georeferencing; no location is inferred."
    result.flag("NOT_GEOREFERENCED")

    result.metadata = json_safe(result.metadata)
    return result
