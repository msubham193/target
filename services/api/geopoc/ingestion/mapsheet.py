"""Map-sheet intake: a georeferenced GeoTIFF, a scanned picture (TIFF, PNG, JPEG) or a PDF of a printed map sheet.

A map sheet is stored like any other raster (the original unmodified, its metadata read from the file) and carries a
`map_sheet` record in its metadata: what the uploader *declared* about it (series, sheet label, scale, edition) and
how it was processed. The declaration is attribution. It never selects or implies a CRS, a datum or a
transformation: only the file's own metadata, a sidecar a person confirmed, or control points a person supplied can
place a sheet.

A PDF cannot be drawn or georeferenced here: the bundled GDAL cannot read PDF and no renderer is installed. It is
stored, its structure is scanned for facts (page count, page size, embedded images, GeoPDF markers) and any
georeferencing it claims is reported as unverified candidate evidence only. Convert a page to a TIFF or PNG and upload
that to georeference.
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import rasterio

from geopoc.config import Settings
from geopoc.ingestion.raster import inspect_raster
from geopoc.ingestion.rules import Inspection, UnreadableAsset, json_safe

INSPECTOR_VERSION = "mapsheet-v1"

# Series a sheet may be declared as. Defence Series Maps are out of scope for this application and are not offered.
SERIES = ("osm", "esm", "unspecified")
SERIES_LABELS = {
    "osm": "Open Series Map (OSM)",
    "esm": "Everest Series Map (ESM)",
    "unspecified": "Series not stated",
}
EXTENSIONS = (".tif", ".tiff", ".png", ".jpg", ".jpeg", ".pdf")
PICTURE_FAMILIES = ("tiff", "png", "jpeg")

# Flags that describe the sheet's kind rather than a problem with it.
INFORMATIONAL_FLAGS = {"MAP_SHEET", "MAP_SHEET_SCANNED", "MAP_SHEET_PDF", "PDF_NOT_RENDERED"}

MAX_LABEL = 120
MAX_EDITION = 60


@dataclass(frozen=True)
class Declaration:
    """What the uploader says about the sheet. Recorded as said; never checked against, or used as, a CRS."""

    series: str = "unspecified"
    label: str | None = None
    scale_denominator: int | None = None
    edition: str | None = None
    declared_by: str | None = None

    def record(self) -> dict[str, Any]:
        return {
            "series": self.series,
            "series_label": SERIES_LABELS[self.series],
            "basis": "declared_by_uploader",
            "sheet_label": self.label,
            "scale_denominator": self.scale_denominator,
            "edition": self.edition,
            "declared_by": self.declared_by,
            "note": "Declared by the person who uploaded the file. It is not evidence of the sheet's CRS, datum, "
            "projection or position, and nothing is assigned from it.",
        }


# --- PDF structure scan -----------------------------------------------------------------------------------------------

_PAGE = re.compile(rb"/Type\s*/Page(?![A-Za-z])")
_COUNT = re.compile(rb"/Type\s*/Pages\b[^>]{0,400}?/Count\s+(\d+)|/Count\s+(\d+)[^>]{0,400}?/Type\s*/Pages\b", re.S)
_MEDIABOX = re.compile(rb"/MediaBox\s*\[\s*([-+0-9.eE\s]+?)\s*\]")
_IMAGE = re.compile(rb"/Subtype\s*/Image\b")
_ENCRYPT = re.compile(rb"/Encrypt\b")
_MEASURE_GEO = re.compile(rb"/Subtype\s*/GEO\b")
_LGIDICT = re.compile(rb"/LGIDict\b")
_VIEWPORT = re.compile(rb"/VP\b")
_GPTS = re.compile(rb"/GPTS\s*\[\s*([-+0-9.eE\s]+?)\s*\]")
_EPSG = re.compile(rb"/EPSG\s+(\d{3,6})")
_PRODUCER = re.compile(rb"/Producer\s*\(([^)]{0,200})\)")
_VERSION = re.compile(rb"^%PDF-(\d\.\d)")
_FULL_SCAN_BYTES = 64 * 1024**2
_PART_BYTES = 8 * 1024**2
_POINT_MM = 25.4 / 72


def _numbers(raw: bytes) -> list[float]:
    values = []
    for token in raw.split():
        try:
            values.append(float(token))
        except ValueError:
            return []
    return values


def inspect_pdf(path: Path) -> dict[str, Any]:
    """Facts from a text scan of the PDF's structure. Nothing is rendered and nothing in the file is executed.

    Compressed object streams are not unpacked, so a marker that is not found proves nothing about the file. A GeoPDF's
    declared control points are returned as candidates; they are not applied.
    """
    size = path.stat().st_size
    partial = size > _FULL_SCAN_BYTES
    with open(path, "rb") as handle:
        head = handle.read(_PART_BYTES if partial else size)
        tail = b""
        if partial:
            handle.seek(max(0, size - _PART_BYTES))
            tail = handle.read(_PART_BYTES)
    data = head + b"\n" + tail
    version = _VERSION.match(head)
    # A PDF ends with %%EOF; one that does not has been cut short.
    if b"%%EOF" not in (tail or head)[-2048:]:
        raise UnreadableAsset("ASSET_UNREADABLE", "The PDF is truncated or damaged: it has no end-of-file marker.")
    encrypted = bool(_ENCRYPT.search(data))

    counts = [int(a or b) for a, b in _COUNT.findall(data)]
    page_objects = len(_PAGE.findall(data))
    pages = max(counts) if counts else (page_objects or None)
    box = None
    match = _MEDIABOX.search(data)
    if match:
        values = _numbers(match.group(1))
        if len(values) == 4:
            width, height = abs(values[2] - values[0]), abs(values[3] - values[1])
            box = {
                "width_pt": width,
                "height_pt": height,
                "width_mm": round(width * _POINT_MM, 1),
                "height_mm": round(height * _POINT_MM, 1),
            }
    geo_markers = []
    for label, pattern in (("ISO 32000 geospatial measure (/GEO)", _MEASURE_GEO), ("TerraGo LGIDict", _LGIDICT)):
        if pattern.search(data):
            geo_markers.append(label)
    gpts = None
    match = _GPTS.search(data)
    if match:
        values = _numbers(match.group(1))
        if values and len(values) % 2 == 0:
            gpts = [{"latitude": values[i], "longitude": values[i + 1]} for i in range(0, len(values), 2)]
    epsg = _EPSG.search(data)
    producer = _PRODUCER.search(data)
    return {
        "pdf_version": version.group(1).decode() if version else None,
        "encrypted": encrypted,
        "page_count": pages,
        "page_count_basis": "page tree /Count" if counts else ("page objects found" if page_objects else None),
        "first_page_size": box,
        "embedded_images_found": len(_IMAGE.findall(data)),
        "viewport_entries_found": len(_VIEWPORT.findall(data)),
        "geo_markers": geo_markers,
        "declared_corner_points": gpts,
        "declared_epsg": int(epsg.group(1)) if epsg else None,
        "producer": producer.group(1).decode("latin-1", "replace") if producer else None,
        "scan": "first and last 8 MB only" if partial else "whole file",
        "scan_note": "Read from the file's text structure without rendering it. Compressed object streams are not "
        "unpacked, so a marker or page that is not listed may still exist.",
    }


# --- the inspector ----------------------------------------------------------------------------------------------------


def _processing(family: str, georeferenced: bool, detail: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "step": "stored_original",
            "note": "The uploaded bytes are stored unmodified, identified by their SHA-256. Nothing is converted, "
            "resampled or rewritten.",
        },
        {
            "step": "inspected",
            "tool": "pdf structure scan" if family == "pdf" else f"rasterio / GDAL {rasterio.__gdal_version__}",
            "inspector": INSPECTOR_VERSION,
            "note": "Metadata was read from the file as it declares it. No CRS was assigned from the file name, the "
            "declared series or the picture's appearance.",
            **detail,
        },
        {
            "step": "georeferencing",
            "status": "from the file's own metadata" if georeferenced else "none: the sheet is not georeferenced",
        },
    ]


def inspect_map_sheet(path: Path, family: str, settings: Settings, declaration: Declaration) -> Inspection:
    """Inspect a map-sheet upload and attach the declaration and processing record to its metadata."""
    if family == "pdf":
        facts = inspect_pdf(path)
        result = Inspection(detected_format="PDF", media_type="application/pdf", no_preview=True)
        result.metadata = {"pdf": facts}
        result.unknowns["georeferencing"] = (
            "A PDF is not read as a raster here, so it has no CRS or geotransform and cannot be placed on a map."
        )
        result.unknowns["pdf_rendering"] = (
            "No PDF renderer is available (GDAL here cannot read PDF and nothing is downloaded). Convert the map page "
            "to a TIFF or PNG elsewhere and upload that to georeference it."
        )
        result.unknowns["acquisition_time"] = "A map sheet carries no acquisition time."
        result.flag("MAP_SHEET")
        result.flag("MAP_SHEET_PDF")
        result.flag("NOT_GEOREFERENCED")
        result.flag("PDF_NOT_RENDERED")
        result.flag("TIMESTAMP_UNKNOWN")
        if facts["encrypted"]:
            result.flag("PDF_ENCRYPTED")
            result.unknowns["pdf_encryption"] = "The PDF is encrypted; only its outer structure could be scanned."
        if facts["geo_markers"] or facts["declared_corner_points"]:
            result.flag("PDF_GEO_METADATA_UNVERIFIED")
            result.unknowns["pdf_geo_metadata"] = (
                "The PDF declares geospatial metadata ("
                + ", ".join(facts["geo_markers"] or ["corner points"])
                + "). It is kept as unverified evidence and is not applied: nothing places the sheet until a person "
                "supplies georeferenced data or control points for a rendered copy."
            )
        kind = "pdf_document"
        georeferenced = False
        detail = {"pdf_pages_found": facts["page_count"]}
    else:
        if family not in PICTURE_FAMILIES:
            raise UnreadableAsset("ASSET_UNREADABLE", "This file type cannot be taken as a map sheet.")
        result = inspect_raster(path, family, settings)
        georeferenced = bool(result.raster and result.raster["georeferenced"])
        result.flag("MAP_SHEET")
        if family == "tiff":
            kind = "georeferenced_raster" if georeferenced else "scanned_raster"
        else:
            kind = "scanned_image"
        if not georeferenced:
            result.flag("MAP_SHEET_SCANNED")
        detail = {"driver": result.raster["driver"] if result.raster else None}
    result.metadata["map_sheet"] = {
        "declaration": declaration.record(),
        "intake": kind,
        "format_family": family,
        "processing": _processing(family, georeferenced, detail),
    }
    result.metadata = json_safe(result.metadata)
    return result
