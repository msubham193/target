"""Sidecar metadata for rasters: world files (.tfw/.tifw/.tiffw/.wld), .prj and GDAL .aux.xml (PAM) files.

A sidecar is evidence to review, never an authority because it exists:

  - It is stored unmodified and re-read from its stored bytes (checksum verified) whenever it is shown or used.
  - Each file is parsed on its own, strictly. Anything missing, malformed, contradictory or not understood is reported
    as such; nothing is filled in, and unknown content is listed as "present, not used", never imported.
  - A world file gives pixel coordinates (an affine transform) but no CRS. A .prj gives a CRS but no coordinates.
  - A .prj or .aux.xml CRS is accepted only when the local registry (the existing CRS machinery) has exactly one
    definition that matches it fully; the change then names that registry code and is validated like any other.
  - Metadata the raster file itself owns and that is valid is never replaced. A sidecar that disagrees with it is
    shown as a conflict and the file's value stays.
  - What a sidecar can supply becomes a proposal. Recording it needs a reason and an explicit confirmation, and goes
    through `georeference.validate`/`apply`: the same checks, versioning, audit, checksum verification and tile
    versioning as georeferencing entered by hand.

Band metadata in .aux.xml (description, nodata, scale/offset, units, colour interpretation) is parsed and compared
with what the raster declares, but not applied: it is shown for information only.
"""

import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import PurePath
from typing import Any, Literal

import numpy as np
import pyproj
from affine import Affine

from geopoc.db.models import Asset, RasterMetadata, Sidecar
from geopoc.geo import georeference
from geopoc.geo.crs import CrsResolutionError, resolve

PARSER_VERSION = "sidecars-v1"
Kind = Literal["world_file", "prj", "aux_xml"]
KIND_LABELS: dict[str, str] = {"world_file": "World file", "prj": ".prj CRS definition", "aux_xml": "GDAL .aux.xml"}
WORLD_EXTENSIONS = (".tfw", ".tifw", ".tiffw", ".wld")
RASTER_EXTENSIONS = (".tif", ".tiff")
SIDECAR_EXTENSIONS = (*WORLD_EXTENSIONS, ".prj", ".aux.xml")
MAX_BYTES: dict[str, int] = {"world_file": 64 * 1024, "prj": 64 * 1024, "aux_xml": 4 * 1024 * 1024}
NUMBER = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$")
# Relative tolerance for "the same coordinates": far below a millimetre at any realistic coordinate magnitude.
SAME_RELATIVE = 1e-9


# --- naming and pairing -----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Naming:
    kind: Kind
    stem: str  # the raster name without its extension that this sidecar belongs to
    raster_name: str | None  # for name.tif.aux.xml: the exact raster file name


def naming(filename: str) -> Naming | None:
    """Which kind of sidecar a file name is, and which raster name it belongs to. None if it is not a sidecar."""
    lower = filename.lower()
    if lower.endswith(".aux.xml"):
        base = filename[: -len(".aux.xml")]
        if not base:
            return None
        suffix = PurePath(base).suffix.lower()
        if suffix in RASTER_EXTENSIONS:
            return Naming("aux_xml", base[: -len(suffix)], base)
        return Naming("aux_xml", base, None)
    for extension in (*WORLD_EXTENSIONS, ".prj"):
        if lower.endswith(extension) and len(filename) > len(extension):
            return Naming("world_file" if extension != ".prj" else "prj", filename[: -len(extension)], None)
    return None


def pairs_with(name: Naming, raster_filename: str) -> bool:
    """Whether a raster file name is the one a sidecar name refers to. Case-insensitive, as on Windows."""
    raster = PurePath(raster_filename)
    if raster.suffix.lower() not in RASTER_EXTENSIONS:
        return False
    if name.raster_name is not None:
        return raster_filename.lower() == name.raster_name.lower()
    return raster_filename[: -len(raster.suffix)].lower() == name.stem.lower()


# --- parsing ----------------------------------------------------------------------------------------------------------


@dataclass
class Parsed:
    """What one sidecar file says. `status` is about the file; usability of each value is in `values`/`problems`."""

    kind: Kind
    status: Literal["parsed", "malformed", "unsupported"]
    summary: str
    values: dict[str, Any] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)
    ignored: list[str] = field(default_factory=list)  # present in the file, not used
    geotransform: list[float] | None = None  # usable GDAL-order geotransform, if this file gives one
    crs_id: str | None = None  # registry code of a fully matching CRS, if this file gives one


def _text(data: bytes) -> tuple[str | None, str | None]:
    if b"\x00" in data:
        return None, "It contains NUL bytes, so it is not a text file."
    try:
        return data.decode("utf-8-sig"), None
    except UnicodeDecodeError:
        return None, "It is not UTF-8 or ASCII text."


def _number(text: str) -> float | None:
    """A plain decimal number. Commas as decimal separators, NaN, infinity and other notations are refused."""
    text = text.strip()
    if not NUMBER.match(text):
        return None
    value = float(text)
    return value if math.isfinite(value) else None


def _describe_geotransform(values: list[float]) -> dict[str, Any]:
    transform = Affine.from_gdal(*values)
    return {
        "geotransform": values,
        "rotated": transform.b != 0 or transform.d != 0,
        "pixel_size": [math.hypot(transform.a, transform.d), math.hypot(transform.b, transform.e)],
    }


def parse_world_file(data: bytes) -> Parsed:
    text, problem = _text(data)
    if text is None:
        return Parsed("world_file", "malformed", "Not readable as a world file.", problems=[problem or ""])
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) != 6:
        return Parsed(
            "world_file",
            "malformed",
            "Not readable as a world file.",
            problems=[
                f"A world file has exactly six numbers, one per line; this one has {len(lines)} non-empty lines."
            ],
        )
    numbers = [_number(line) for line in lines]
    bad = [f"line {i + 1} ({lines[i][:40]!r})" for i, n in enumerate(numbers) if n is None]
    if bad:
        return Parsed(
            "world_file",
            "malformed",
            "Not readable as a world file.",
            problems=[f"Not a plain decimal number: {', '.join(bad)}."],
        )
    values = georeference.parse_world_file("\n".join(lines))  # the same conversion the georeferencing checks use
    parsed = Parsed(
        "world_file",
        "parsed",
        "Six numbers: pixel size, rotation and the centre of the top-left pixel. No CRS: a world file does not say "
        "which coordinate system its numbers are in.",
        values={
            "lines": dict(zip(("A", "D", "B", "E", "C", "F"), numbers, strict=True)),
            **_describe_geotransform(values),
        },
    )
    problem = georeference.geotransform_problem(values)
    if problem:
        parsed.problems.append(problem)
    else:
        parsed.geotransform = values
    return parsed


def _kind_of(crs: pyproj.CRS) -> str:
    if crs.is_compound:
        return "compound"
    if crs.is_geographic:
        return "geographic"
    if crs.is_projected:
        return "projected"
    if crs.is_vertical:
        return "vertical"
    if crs.is_geocentric:
        return "geocentric"
    return "other"


def resolve_wkt(wkt: str) -> tuple[dict[str, Any], str | None, list[str]]:
    """Match a WKT CRS definition to the local registry. Returns (description, registry code or None, problems).

    Accepted only when exactly one registry entry matches with full confidence, the existing CRS machinery resolves
    it, the two are equivalent (axis order aside: rasters are always read in x/y order), and it is a 2D geographic
    or projected CRS. A name is never enough: a definition called "UTM zone 43N" with other parameters is refused.
    """
    try:
        crs = pyproj.CRS.from_wkt(wkt)
    except pyproj.exceptions.CRSError as exc:
        return {}, None, [f"Not a CRS definition PROJ can read: {str(exc).split(':')[0]}."]
    info: dict[str, Any] = {"name": crs.name, "kind": _kind_of(crs)}
    matches = crs.list_authority(min_confidence=25)
    info["registry_matches"] = [
        {"identifier": f"{m.auth_name}:{m.code}", "confidence": m.confidence} for m in matches[:5]
    ]
    full = [m for m in matches if m.confidence == 100]
    if not full:
        nearest = (
            f" The nearest is {matches[0].auth_name}:{matches[0].code} at {matches[0].confidence}%." if matches else ""
        )
        return info, None, [f"No CRS in the local registry matches this definition completely.{nearest}"]
    if len(full) > 1:
        names = ", ".join(f"{m.auth_name}:{m.code}" for m in full)
        return info, None, [f"Several registry CRSs match this definition ({names}); it is not chosen between them."]
    identifier = f"{full[0].auth_name}:{full[0].code}"
    try:
        registry = resolve(identifier)
    except CrsResolutionError as exc:
        return info, None, [str(exc)]
    info["identifier"] = identifier
    info["registry_name"] = registry.name
    if not registry.equals(crs, ignore_axis_order=True):
        return info, None, [f"{identifier} matches by name, but its parameters are not equivalent to this definition."]
    if registry.is_compound or not (registry.is_geographic or registry.is_projected):
        return info, None, [f"{identifier} is a {_kind_of(registry)} CRS, not a 2D geographic or projected one."]
    info["equivalent"] = True
    return info, identifier, []


def parse_prj(data: bytes) -> Parsed:
    text, problem = _text(data)
    if text is None:
        return Parsed("prj", "malformed", "Not readable as a CRS definition.", problems=[problem or ""])
    text = text.strip()
    if not text:
        return Parsed("prj", "malformed", "The file is empty.", problems=["There is no CRS definition in it."])
    if text.startswith("+") or text.lower().startswith("epsg:"):
        return Parsed(
            "prj",
            "unsupported",
            "Not a WKT CRS definition.",
            problems=["A .prj holds a WKT CRS definition; PROJ strings and bare codes are not accepted from files."],
        )
    info, identifier, problems = resolve_wkt(text)
    if not info:
        return Parsed("prj", "malformed", "Not readable as a CRS definition.", problems=problems)
    summary = (
        f"A CRS definition matching {identifier} ({info['registry_name']}) in the local registry."
        if identifier
        else f"A CRS definition ({info['name']}) that does not match one registry CRS completely; it is not used."
    )
    return Parsed("prj", "parsed", summary, values={"crs": info}, problems=problems, crs_id=identifier)


def _expected_axis_mapping(crs: pyproj.CRS) -> str:
    """GDAL's data-axis-to-CRS-axis mapping for rasters read in x/y (easting/longitude first) order."""
    first = (crs.axis_info[0].direction or "").lower() if crs.axis_info else ""
    return "2,1" if first in ("north", "south") else "1,2"


BAND_FIELDS = {
    "Description": "description",
    "NoDataValue": "nodata",
    "Offset": "offset",
    "Scale": "scale",
    "UnitType": "units",
    "ColorInterp": "color_interpretation",
}


def _band(element: ET.Element, raster: RasterMetadata, problems: list[str], ignored: list[str]) -> dict[str, Any]:
    number_text = element.get("band", "")
    band: dict[str, Any] = {"band": None}
    if not number_text.isdigit() or not 1 <= int(number_text) <= raster.band_count:
        problems.append(f"A band entry names band {number_text!r}; the raster has bands 1 to {raster.band_count}.")
        return band
    number = int(number_text)
    band["band"] = number
    dtype = (raster.dtypes or [None] * raster.band_count)[number - 1]
    for child in element:
        key = BAND_FIELDS.get(child.tag)
        text = (child.text or "").strip()
        if key is None:
            ignored.append(f"Band {number}: <{child.tag}> is not used.")
            continue
        if key in ("nodata", "offset", "scale"):
            value = float("nan") if key == "nodata" and text.lower() == "nan" else _number(text)
            if value is None:
                problems.append(f"Band {number}: {child.tag} {text[:40]!r} is not a number.")
                continue
            if key == "nodata" and dtype and not math.isnan(value) and np.issubdtype(np.dtype(dtype), np.integer):
                limits = np.iinfo(np.dtype(dtype))
                if not (value.is_integer() and limits.min <= value <= limits.max):
                    problems.append(f"Band {number}: nodata {text} cannot occur in a {dtype} band.")
                    continue
            band[key] = "nan" if isinstance(value, float) and math.isnan(value) else value
        else:
            band[key] = text[:500]
    return band


def parse_aux_xml(data: bytes, raster: RasterMetadata) -> Parsed:
    lowered = data[:65536].lower()
    if b"<!doctype" in lowered or b"<!entity" in lowered:
        return Parsed(
            "aux_xml",
            "malformed",
            "Not accepted as a GDAL .aux.xml.",
            problems=["It contains a document type or entity declaration; GDAL .aux.xml files never do."],
        )
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        return Parsed("aux_xml", "malformed", "Not readable as XML.", problems=[f"XML error: {exc}."])
    if root.tag != "PAMDataset":
        return Parsed(
            "aux_xml",
            "unsupported",
            "Not a GDAL .aux.xml.",
            problems=[f"The root element is <{root.tag}>, not <PAMDataset>."],
        )
    parsed = Parsed("aux_xml", "parsed", "")
    bands: list[dict[str, Any]] = []
    for child in root:
        if child.tag == "SRS":
            wkt = (child.text or "").strip()
            info, identifier, problems = resolve_wkt(wkt) if wkt else ({}, None, ["The <SRS> element is empty."])
            mapping = child.get("dataAxisToSRSAxisMapping")
            info["axis_mapping"] = mapping
            if identifier and mapping is not None:
                expected = _expected_axis_mapping(resolve(identifier))
                if mapping.replace(" ", "") != expected:
                    problems.append(
                        f"Its data axis mapping is {mapping}, not the x/y order ({expected}) this application reads "
                        "rasters in; the CRS is not used."
                    )
                    identifier = None
            parsed.values["crs"] = info
            parsed.problems.extend(f"CRS: {p}" for p in problems)
            parsed.crs_id = identifier
        elif child.tag == "GeoTransform":
            parts = (child.text or "").split(",")
            numbers = [_number(p) for p in parts]
            if len(parts) != 6 or any(n is None for n in numbers):
                parsed.problems.append("Geotransform: <GeoTransform> must hold six plain numbers separated by commas.")
                continue
            values = [float(n) for n in numbers]  # type: ignore[arg-type]
            parsed.values.update(_describe_geotransform(values))
            problem = georeference.geotransform_problem(values)
            if problem:
                parsed.problems.append(f"Geotransform: {problem}")
            else:
                parsed.geotransform = values
        elif child.tag == "PAMRasterBand":
            bands.append(_band(child, raster, parsed.problems, parsed.ignored))
        elif child.tag == "GCPList":
            count = len(child.findall("GCP"))
            parsed.ignored.append(
                f"{count} ground control point(s): not used. Nothing is fitted or estimated from control points."
            )
        elif child.tag == "Metadata":
            domain = child.get("domain") or "default"
            items = len(list(child))
            if domain.upper() == "RPC":
                parsed.ignored.append(f"An RPC sensor model ({items} items): not used; there is no orthorectification.")
            else:
                parsed.ignored.append(f"Dataset metadata, domain {domain!r} ({items} items): not used.")
        else:
            parsed.ignored.append(f"<{child.tag}>: not understood, not used.")
    if bands:
        parsed.values["bands"] = [b for b in bands if b["band"] is not None]
    found = [
        label
        for label, present in (
            ("a CRS", "crs" in parsed.values),
            ("a geotransform", "geotransform" in parsed.values),
            ("band metadata", bool(parsed.values.get("bands"))),
        )
        if present
    ]
    if not found:
        parsed.status = "unsupported"
        parsed.summary = "A GDAL .aux.xml with none of the fields this application understands."
    else:
        parsed.summary = f"A GDAL .aux.xml with {', '.join(found)}."
    return parsed


def parse(kind: str, data: bytes, raster: RasterMetadata) -> Parsed:
    if kind == "world_file":
        return parse_world_file(data)
    if kind == "prj":
        return parse_prj(data)
    return parse_aux_xml(data, raster)


# --- comparing with the raster and choosing ---------------------------------------------------------------------------


def same_geotransform(a: list[float] | None, b: list[float] | None) -> bool:
    if a is None or b is None or len(a) != 6 or len(b) != 6:
        return False
    return all(abs(x - y) <= SAME_RELATIVE * max(1.0, abs(x), abs(y)) for x, y in zip(a, b, strict=True))


def _crs(identifier_or_wkt: str | None) -> pyproj.CRS | None:
    if not identifier_or_wkt:
        return None
    try:
        if ":" in identifier_or_wkt and "[" not in identifier_or_wkt:
            return resolve(identifier_or_wkt)
        return pyproj.CRS.from_wkt(identifier_or_wkt)
    except (CrsResolutionError, pyproj.exceptions.CRSError):
        return None


def same_crs(a: str | None, b: str | None) -> bool:
    first, second = _crs(a), _crs(b)
    return first is not None and second is not None and first.equals(second, ignore_axis_order=True)


@dataclass
class FileFacts:
    """What the raster file itself declares and whether each part is usable (never changed)."""

    geotransform: list[float] | None
    geotransform_ok: bool
    crs_wkt: str | None
    crs_label: str | None
    crs_ok: bool
    georeferenced: bool


def file_facts(raster: RasterMetadata) -> FileFacts:
    crs = None
    if raster.crs_status == "valid" and raster.crs_wkt:
        crs = _crs(raster.crs_wkt)
    usable = crs is not None and (crs.is_geographic or crs.is_projected) and not crs.is_compound
    return FileFacts(
        geotransform=list(raster.geotransform) if raster.geotransform else None,
        geotransform_ok=georeference.geotransform_problem(raster.geotransform) is None
        and raster.pixel_size_x is not None,
        crs_wkt=raster.crs_wkt if usable else None,
        crs_label=raster.crs_identifier or raster.crs_name,
        crs_ok=usable,
        georeferenced=raster.georeferenced,
    )


@dataclass
class Entry:
    """One stored sidecar with what it says now and how it relates to the raster."""

    row: Sidecar
    parsed: Parsed
    current: bool  # the latest of its kind for this raster
    superseded_by: Sidecar | None = None
    comparisons: list[dict[str, Any]] = field(default_factory=list)
    role: str = ""


@dataclass
class Review:
    state: Literal["none", "file_valid", "proposal", "conflict", "insufficient", "in_use"]
    title: str
    explanation: list[str]
    entries: list[Entry]
    proposal: georeference.Proposal | None = None
    sources: dict[str, Any] = field(default_factory=dict)  # {"geotransform": {...}, "crs": {...}}
    choices: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    conflicts: list[str] = field(default_factory=list)


def _relation(sidecar_value, file_value, in_use_value, same) -> dict[str, str]:
    def rel(other):
        if other is None:
            return "none"
        return "same" if same(sidecar_value, other) else "differs"

    return {"file": rel(file_value), "in_use": rel(in_use_value)}


def _band_comparisons(asset: Asset, raster: RasterMetadata, bands: list[dict[str, Any]]) -> list[dict[str, Any]]:
    declared = (asset.extracted_metadata or {}).get("bands") or []
    nodata = raster.nodata or []
    rows = []
    for band in bands:
        number = band["band"]
        own = declared[number - 1] if number - 1 < len(declared) else {}
        file_values = {
            "description": own.get("description"),
            "units": own.get("units"),
            "scale": own.get("scale"),
            "offset": own.get("offset"),
            "color_interpretation": own.get("color_interpretation"),
            "nodata": nodata[number - 1] if number - 1 < len(nodata) else None,
        }
        for key, value in band.items():
            if key == "band":
                continue
            other = file_values.get(key)
            if other in (None, ""):
                relation = "none"
            elif isinstance(value, float) and isinstance(other, int | float):
                relation = "same" if math.isclose(value, float(other), rel_tol=1e-12, abs_tol=0) else "differs"
            else:
                relation = "same" if str(value).lower() == str(other).lower() else "differs"
            rows.append(
                {
                    "field": f"band {number} {key.replace('_', ' ')}",
                    "sidecar": value,
                    "file": other,
                    "in_use": other,
                    "relation_file": relation,
                    "relation_in_use": relation,
                    "applied": False,
                }
            )
    return rows


def _gt_label(values: list[float] | None) -> str | None:
    return ", ".join(f"{v:.10g}" for v in values) if values else None


def review(
    asset: Asset,
    raster: RasterMetadata,
    rows: list[Sidecar],
    contents: dict[str, bytes],
    *,
    geotransform_from: str | None = None,
    crs_from: str | None = None,
) -> Review:
    """What the raster's sidecars say, how they relate to the file and the state in use, and what could be recorded.

    `rows` are all sidecars of the raster; `contents` maps sidecar id to its verified bytes. The latest sidecar of
    each kind is the current one; older ones are shown as superseded and not used. Nothing is stored.
    """
    state = georeference.effective(raster)
    facts = file_facts(raster)
    ordered = sorted(rows, key=lambda r: (r.uploaded_at, str(r.id)))
    latest: dict[str, Sidecar] = {}
    for row in ordered:
        latest[row.kind] = row
    entries = []
    for row in reversed(ordered):
        parsed = parse(row.kind, contents[str(row.id)], raster)
        newest = latest[row.kind]
        entries.append(Entry(row, parsed, newest.id == row.id, None if newest.id == row.id else newest))
    current = [e for e in entries if e.current]
    if not entries:
        return Review(
            "none", "No sidecar files", ["No world file, .prj or .aux.xml has been added for this raster."], []
        )

    # --- comparisons with the file's own metadata and with the state in use ---
    for entry in entries:
        p = entry.parsed
        if "geotransform" in p.values:
            rel = _relation(p.values["geotransform"], facts.geotransform, state.geotransform, same_geotransform)
            entry.comparisons.append(
                {
                    "field": "geotransform",
                    "sidecar": _gt_label(p.values["geotransform"]),
                    "file": _gt_label(facts.geotransform),
                    "in_use": _gt_label(state.geotransform),
                    "relation_file": rel["file"],
                    "relation_in_use": rel["in_use"],
                    "applied": None,
                }
            )
        if p.crs_id or (p.values.get("crs") or {}).get("name"):
            label = p.crs_id or (p.values["crs"] or {}).get("name")
            rel = (
                _relation(p.crs_id, facts.crs_wkt, state.crs_wkt if state.crs_usable else None, same_crs)
                if p.crs_id
                else {"file": "unresolved", "in_use": "unresolved"}
            )
            entry.comparisons.append(
                {
                    "field": "CRS",
                    "sidecar": label,
                    "file": facts.crs_label if raster.crs_status != "missing" else None,
                    "in_use": (state.crs_id or state.crs_name) if state.crs_usable else None,
                    "relation_file": rel["file"],
                    "relation_in_use": rel["in_use"],
                    "applied": None,
                }
            )
        if p.values.get("bands"):
            entry.comparisons.extend(_band_comparisons(asset, raster, p.values["bands"]))

    conflicts = []
    for entry in current:
        for c in entry.comparisons:
            if c["field"] in ("geotransform", "CRS") and c["relation_file"] == "differs":
                conflicts.append(
                    f"{entry.row.original_filename}: its {c['field']} ({c['sidecar']}) differs from the one in the "
                    f"raster file ({c['file']})."
                )
    for entry in entries:
        if entry.superseded_by is not None:
            entry.role = f"Superseded by {entry.superseded_by.original_filename}, added later; not used."

    def finish(result: Review) -> Review:
        result.conflicts = conflicts
        for entry in current:
            if not entry.role:
                entry.role = "Not used for the change shown."
        return result

    if facts.georeferenced:
        for entry in current:
            entry.role = "Shown for comparison only: the raster file's own georeferencing is valid and is used."
        return finish(
            Review(
                "file_valid",
                "The raster's own georeferencing is valid",
                [
                    "The CRS and geotransform in the raster file are valid and used. Sidecar files never override "
                    "them; any disagreement is listed below and the file's values stay."
                ],
                entries,
            )
        )

    # When both the geotransform and the CRS in the file are usable but the raster is still not placed, its
    # coordinates do not fit its CRS: the CRS is the part in doubt, as in the georeferencing workflow.
    need_gt = not facts.geotransform_ok
    need_crs = not facts.crs_ok or facts.geotransform_ok
    explanation: list[str] = []
    result = Review("insufficient", "", explanation, entries)

    def candidates(kind: str) -> list[Entry]:
        found = []
        for entry in current:
            value = entry.parsed.geotransform if kind == "geotransform" else entry.parsed.crs_id
            if value is not None:
                found.append(entry)
        return found

    def pick(kind: str, wanted: str | None) -> tuple[Entry | None, bool]:
        """(chosen entry, needs a choice)."""
        found = candidates(kind)
        if wanted is not None:
            chosen = next((e for e in found if str(e.row.id) == wanted), None)
            if chosen is None:
                raise ValueError(f"The chosen {kind} source is not a current, usable sidecar of this raster.")
            return chosen, False
        if not found:
            return None, False
        same = same_geotransform if kind == "geotransform" else same_crs

        def value(e: Entry):
            return e.parsed.geotransform if kind == "geotransform" else e.parsed.crs_id

        if all(same(value(found[0]), value(other)) for other in found[1:]):
            return found[0], False
        return None, True

    gt_entry, gt_choice = pick("geotransform", geotransform_from) if need_gt else (None, False)
    crs_entry, crs_choice = pick("crs", crs_from) if need_crs else (None, False)

    if gt_choice or crs_choice:
        for kind, needed in (("geotransform", gt_choice), ("crs", crs_choice)):
            if needed:
                result.choices[kind] = [
                    {
                        "sidecar_id": str(e.row.id),
                        "filename": e.row.original_filename,
                        "value": _gt_label(e.parsed.geotransform) if kind == "geotransform" else e.parsed.crs_id,
                    }
                    for e in candidates(kind)
                ]
                for e in candidates(kind):
                    e.role = f"Disagrees with another sidecar about the {kind}; choose which one to review."
        result.state, result.title = "conflict", "The sidecar files disagree"
        explanation.append(
            "Two sidecar files give different values for the same thing. Nothing is proposed until you choose which "
            "file to review; the other stays recorded but unused."
        )
        return finish(result)

    # --- not enough to place the raster ---
    crs_kept = False  # a CRS in use (recorded earlier) is kept with a sidecar geotransform, as when entered by hand
    missing_crs = False
    if need_crs and crs_entry is None:
        if need_gt and state.crs_usable:
            crs_kept = True
        else:
            missing_crs = True
    missing_gt = need_gt and gt_entry is None
    if missing_gt or missing_crs:
        has_world = any(e.row.kind == "world_file" for e in current)
        if missing_crs and need_gt:
            explanation.append(
                "No sidecar gives a usable CRS. "
                + (
                    "A world file gives pixel coordinates, but not which coordinate system they are in, so it "
                    "cannot place the raster on its own. Add the matching .prj, or record the CRS from a documented "
                    "source in Spatial placement."
                    if has_world
                    else "Add a .prj, or record the CRS from a documented source in Spatial placement."
                )
            )
        elif missing_crs:
            explanation.append(
                "No sidecar gives a usable CRS for the coordinates the raster file already has. Add a .prj, or "
                "record the CRS from a documented source in Spatial placement."
            )
        if missing_gt:
            explanation.append(
                "No sidecar gives a usable geotransform, so the raster's pixels have no coordinates. "
                + (
                    "A .prj gives only a CRS; it cannot place a raster without a geotransform."
                    if any(e.row.kind == "prj" for e in current)
                    else "Add the matching world file, or enter the geotransform from a documented source."
                )
            )
        for entry in current:
            for problem in entry.parsed.problems:
                explanation.append(f"{entry.row.original_filename}: {problem}")
        result.title = "Not enough to place the raster" if need_gt else "No usable CRS in the sidecar files"
        explanation.append("The raster stays as it is and is not placed. Nothing is assumed or filled in.")
        return finish(result)

    # --- a proposal ---
    sources: dict[str, Any] = {}
    if need_gt and gt_entry is not None:
        row = gt_entry.row
        source = (
            f"Sidecar {row.original_filename} (SHA-256 {row.blob_sha256[:16]}…), "
            f"uploaded by {row.uploader.display_name}"
        )
        proposal = georeference.Proposal(
            operation="set_geotransform",
            crs_id=crs_entry.parsed.crs_id if crs_entry is not None else None,
            world_file=contents[str(row.id)].decode("utf-8-sig") if row.kind == "world_file" else None,
            geotransform=gt_entry.parsed.geotransform if row.kind != "world_file" else None,
            geotransform_source=source,
        )
        sources["geotransform"] = {"sidecar_id": str(row.id), "filename": row.original_filename, "kind": row.kind}
        gt_entry.role = "Gives the geotransform in the proposed change."
    else:
        proposal = georeference.Proposal(operation="assign_crs", crs_id=crs_entry.parsed.crs_id if crs_entry else None)
        sources["geotransform"] = {"from": "file", "filename": None}
    if crs_entry is not None:
        row = crs_entry.row
        sources["crs"] = {"sidecar_id": str(row.id), "filename": row.original_filename, "kind": row.kind}
        crs_entry.role = (
            "Gives the CRS in the proposed change."
            if crs_entry is not gt_entry
            else "Gives the geotransform and the CRS in the proposed change."
        )
    else:
        sources["crs"] = {"from": "in_use" if crs_kept else "file", "filename": None}
    result.proposal, result.sources = proposal, sources

    proposed_gt = gt_entry.parsed.geotransform if gt_entry is not None else facts.geotransform
    proposed_crs = crs_entry.parsed.crs_id if crs_entry is not None else (state.crs_wkt or facts.crs_wkt)
    if (
        state.georeferenced
        and same_geotransform(proposed_gt, state.geotransform)
        and same_crs(proposed_crs, state.crs_wkt)
    ):
        result.state, result.title = "in_use", "Already in use"
        explanation.append(
            f"The values from the sidecar files are the ones in use now (version {state.version}). Nothing to record."
        )
        return finish(result)
    result.state, result.title = "proposal", "A change can be reviewed"
    explanation.append(
        "The values below come from the sidecar files named. They are not applied until you check them, give a "
        "reason and confirm. Recording them does not show that the raster's location is correct."
    )
    if state.source == "recorded":
        explanation.append(
            f"It would replace the georeferencing recorded in version {state.version}; that stays in the history."
        )
    return finish(result)
