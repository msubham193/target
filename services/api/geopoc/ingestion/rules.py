"""What may be uploaded: modality, file extension, file signature and size limit."""

import math
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from geopoc.config import Settings

Modality = Literal["ground_image", "overhead_raster", "video"]

HEADER_BYTES = 16

# extension -> signature family the content must have
EXTENSION_FAMILY: dict[str, str] = {
    ".jpg": "jpeg",
    ".jpeg": "jpeg",
    ".png": "png",
    ".webp": "webp",
    ".tif": "tiff",
    ".tiff": "tiff",
    ".mp4": "isobmff",
    ".mov": "isobmff",
    ".mkv": "matroska",
    ".pdf": "pdf",
}

MODALITY_EXTENSIONS: dict[str, tuple[str, ...]] = {
    "ground_image": (".jpg", ".jpeg", ".png", ".webp"),
    "overhead_raster": (".tif", ".tiff"),
    "video": (".mp4", ".mov", ".mkv"),
}

MODALITY_LABELS: dict[str, str] = {
    "ground_image": "Ground photographs",
    "overhead_raster": "Satellite / aerial raster (GeoTIFF)",
    "video": "Video",
}

FAMILY_LABELS: dict[str, str] = {
    "jpeg": "JPEG image",
    "png": "PNG image",
    "webp": "WebP image",
    "tiff": "TIFF / GeoTIFF",
    "isobmff": "MP4 / MOV video",
    "matroska": "Matroska (MKV) video",
    "pdf": "PDF document",
}

_ISO_BMFF_FIRST_ATOMS = {b"ftyp", b"moov", b"mdat", b"wide", b"free", b"skip", b"pnot"}


def sniff(header: bytes) -> str | None:
    """Identify the content family from the first bytes of a file. None means unrecognised."""
    if header.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return "webp"
    if header[:4] in (b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+"):
        return "tiff"
    if header[4:8] in _ISO_BMFF_FIRST_ATOMS:
        return "isobmff"
    if header.startswith(b"\x1a\x45\xdf\xa3"):
        return "matroska"
    if header.startswith(b"%PDF-"):
        return "pdf"
    return None


def max_bytes(modality: str, settings: Settings) -> int:
    return {
        "ground_image": settings.max_ground_image_bytes,
        "overhead_raster": settings.max_raster_bytes,
        "video": settings.max_video_bytes,
    }[modality]


def clean_filename(raw: str) -> str:
    """The client's filename is kept as metadata only; it never becomes a storage path."""
    name = re.split(r"[\\/]", raw)[-1].replace("\x00", "").strip()
    return name[:255]


def extension_of(filename: str) -> str:
    dot = filename.rfind(".")
    return filename[dot:].lower() if dot > 0 else ""


class UnreadableAsset(Exception):
    """The content has the right signature but cannot be read as that format."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class ToolingUnavailable(Exception):
    """A required local tool (ffprobe/ffmpeg) is not installed, so readability cannot be established."""


@dataclass
class Inspection:
    detected_format: str
    media_type: str
    metadata: dict[str, Any] = field(default_factory=dict)
    # field name -> why it is unknown. Unknowns are recorded, never filled in.
    unknowns: dict[str, str] = field(default_factory=dict)
    quality_flags: list[str] = field(default_factory=list)
    acquisition_time: datetime | None = None
    raster: dict[str, Any] | None = None
    video: dict[str, Any] | None = None
    # A ground photograph's EXIF GPS reading (an unverified candidate, or why there is none). See ingestion/gps.py.
    photo_gps: Any = None
    # True when no picture can be made from the original here (a PDF: no renderer is installed), so none is queued.
    no_preview: bool = False

    def flag(self, code: str) -> None:
        if code not in self.quality_flags:
            self.quality_flags.append(code)


def json_safe(value: Any) -> Any:
    """Make extracted metadata storable as JSONB: no NUL characters, no non-finite numbers, bounded text."""
    if isinstance(value, dict):
        return {str(k).replace("\x00", ""): json_safe(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [json_safe(v) for v in value]
    if isinstance(value, bytes):
        return None
    if isinstance(value, str):
        return value.replace("\x00", "")[:2000]
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value).replace("\x00", "")[:2000]
    return number if math.isfinite(number) else str(number)
