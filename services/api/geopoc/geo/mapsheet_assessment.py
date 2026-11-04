"""Metadata assessment of a map sheet (OSM / ESM): what the file states, what it does not, and what would resolve it.

This reports; it never decides. Nothing is assigned, stored or inferred: a CRS, datum or projection that the file (or a
person-recorded version) does not state is listed as unknown, with what is needed to resolve it. A series declared at
upload (OSM, ESM) is attribution and changes none of the facts below.

Names such as "Everest 1956", "Polyconic" or "Lambert Conformal Conic" are not coordinate reference system definitions.
They are recognised only so the report can say what else a complete definition needs. No Everest-56 or ESM definition,
and no transformation for one, is installed, guessed or implied here.
"""

from typing import Any

from pyproj import Geod

from geopoc.db.models import Asset, Sidecar
from geopoc.geo import georeference, mapsheet

FIELD_LABELS: dict[str, str] = {
    "datum": "Datum",
    "ellipsoid": "Ellipsoid",
    "prime_meridian": "Prime meridian",
    "projection": "Projection",
    "projection_parameters": "Projection parameters",
    "units": "Units",
    "coordinate_grid": "Coordinate grid",
    "extent": "Geographic extent",
    "transformation_to_wgs84": "Transformation to WGS 84",
}

# What a name in a CRS does and does not tell you. Matched against the CRS the sheet actually has.
FAMILIES: tuple[tuple[str, str, str], ...] = (
    (
        "Everest",
        "everest",
        "Several Everest ellipsoids and datums exist (for example 1830, 1937, 1948, 1956, 1969) and their parameters "
        "differ, so the name does not identify one. A usable datum also needs published shift parameters to WGS 84 "
        "from an authoritative source; none is assumed here.",
    ),
    (
        "Polyconic",
        "polyconic",
        "Polyconic names a projection method. A definition also needs the ellipsoid, the latitude and longitude of "
        "origin, false easting and northing, and units, and these may differ between sheets.",
    ),
    (
        "Lambert Conic Conformal",
        "lambert conic conformal",
        "Lambert Conic Conformal names a projection method. A definition also needs one or two standard parallels, the "
        "latitude and longitude of origin, false easting and northing, scale factor, ellipsoid and units.",
    ),
    (
        "Indian / Kalianpur datums",
        "kalianpur",
        "Several Indian and Kalianpur datums exist (1880, 1937, 1956, 1962, 1975). Which one a sheet uses must be "
        "stated by the sheet or its supplier; the registry's shifts to WGS 84 differ in accuracy.",
    ),
    (
        "Indian / Kalianpur datums",
        "indian ",
        "Several Indian and Kalianpur datums exist (1880, 1937, 1956, 1962, 1975). Which one a sheet uses must be "
        "stated by the sheet or its supplier; the registry's shifts to WGS 84 differ in accuracy.",
    ),
)

LIMITATIONS: tuple[str, ...] = (
    "Only what the file or a recorded georeferencing version states is reported. Printed marginalia, grid lines and "
    "graticule ticks are picture content; the application does not read them.",
    "No Everest-56, polyconic or other ESM definition is installed, and no transformation for one is available. Real "
    "sheets and authoritative parameters are needed before any ESM transformation can be claimed.",
    "A synthetic test file shows how the application behaves, not that it can read real Open Series or Everest Series "
    "sheets.",
    "A note recorded about a sheet is a record for review. It is never applied as a CRS, a datum or a georeferencing.",
)

REQUIRED_FOR_ESM: tuple[str, ...] = (
    "An authoritative written definition of the sheet's datum, ellipsoid, projection and every projection parameter, "
    "with its source document.",
    "Published datum-shift parameters (or a grid) to WGS 84, with their stated accuracy and area of validity.",
    "Confirmation of whether each sheet, or only the series, shares one set of parameters: they are not assumed equal.",
    "Real sample sheets, with their margin text, so control points and the printed grid can be checked against the "
    "definition.",
)

SERIES_NOTES: dict[str, str] = {
    "osm": "Declared by the uploader as an Open Series Map. That is a label: the sheet's CRS, datum and projection are "
    "only what its file states or a person recorded.",
    "esm": "Declared by the uploader as an Everest Series Map. Everest-56, Polyconic and LCC are names, not "
    "definitions. Nothing is assigned from them, and other ESM sheets are not assumed to share these parameters.",
    "unspecified": "No series was declared. The sheet's CRS, datum and projection are only what its file states or a "
    "person recorded.",
}


def _source_state(basis: str, origin: str | None = None) -> str:
    """Who states the CRS: the file, or a person who recorded one. `origin` is the assessed CRS source, if any."""
    if origin == "file" or (origin is None and basis == "file_metadata"):
        return "stated_by_file"
    if origin == "recorded" or basis not in ("none", "file_metadata"):
        return "recorded_by_person"
    return "unknown"


def _where(basis: str, origin: str | None = None) -> str:
    if origin == "file":
        return "as the CRS definition in the file states it"
    return {
        "file_metadata": "as the CRS definition in the file states it",
        "sidecar": "from a sidecar a person reviewed and confirmed",
        "world_file": "from a world file entered by a person",
        "control_points": "from the CRS a person chose for their control points",
        "recorded": "from a CRS a person recorded",
    }.get(basis, "")


def family_hints(details: dict[str, Any] | None) -> list[dict[str, str]]:
    """Names in the sheet's own CRS that need a complete authoritative definition. A name is not a definition."""
    if details is None:
        return []
    projection = details.get("projection") or {}
    fields = {
        "CRS": details.get("name"),
        "datum": (details.get("datum") or {}).get("name"),
        "ellipsoid": (details.get("ellipsoid") or {}).get("name"),
        "projection": " ".join(filter(None, [projection.get("method"), projection.get("name")])),
    }
    seen: set[tuple[str, str]] = set()
    hints = []
    for family, needle, meaning in FAMILIES:
        for where, text in fields.items():
            if text and needle in f"{text.lower()} ":
                key = (family, f"{where}: {text}")
                if key not in seen:
                    seen.add(key)
                    hints.append({"family": family, "matched": f"{where}: {text}", "meaning": meaning})
    return hints


def _extent(asset: Asset, basis: str, origin: str | None = None) -> dict[str, Any]:
    raster = asset.raster
    if raster is None:
        return {"state": "unknown", "value": None, "detail": "A PDF is not read as a raster, so it has no extent here."}
    state = georeference.effective(raster)
    if not state.georeferenced or state.footprint is None:
        return {
            "state": "unknown",
            "value": {"pixels": [raster.width, raster.height]},
            "detail": "Not placed, so its geographic extent is unknown. Its size in the picture is "
            f"{raster.width} x {raster.height} pixels.",
        }
    west, south, east, north = state.footprint.bounds
    area_m2, _ = Geod(ellps="WGS84").geometry_area_perimeter(state.footprint)
    return {
        "state": _source_state(basis, origin),
        "value": {
            "bounds_4326": [west, south, east, north],
            "bounds_native": state.bounds_native,
            "area_km2": abs(area_m2) / 1e6,
        },
        "detail": "Computed from the sheet's CRS and geotransform "
        f"({_where(basis, origin) or 'as recorded'}). It is only as good as that georeferencing and the datum "
        "transformation.",
    }


def _unknown(field: str, reason: str) -> dict[str, Any]:
    return {
        "field": field,
        "label": FIELD_LABELS[field],
        "state": "unknown",
        "value": None,
        "detail": reason,
    }


def _facts(
    asset: Asset, basis: str, details: dict[str, Any] | None, plan: dict[str, Any] | None, origin: str | None
) -> list[dict]:
    state = _source_state(basis, origin)
    if details is None:
        why = (
            "The sheet has no usable CRS, so this is unknown. Nothing is assumed from the series, the file name or "
            "the picture."
        )
        facts = [_unknown(f, why) for f in FIELD_LABELS if f not in ("extent", "transformation_to_wgs84")]
        facts.append(_unknown("transformation_to_wgs84", "Not applicable until the sheet has a usable CRS."))
        extent = _extent(asset, basis)
        facts.append({"field": "extent", "label": FIELD_LABELS["extent"], **extent})
        return facts
    where = _where(basis, origin)
    projection = details.get("projection")
    datum = details["datum"]
    ellipsoid = details.get("ellipsoid")
    meridian = details.get("prime_meridian")
    facts = [
        {
            "field": "datum",
            "label": FIELD_LABELS["datum"],
            "state": state,
            "value": datum,
            "detail": f"{datum['name'] or 'unnamed'}, {where}."
            + (
                " It is a datum ensemble, so positions are only as exact as the ensemble."
                if datum["is_ensemble"]
                else ""
            ),
        },
        {
            "field": "ellipsoid",
            "label": FIELD_LABELS["ellipsoid"],
            "state": state if ellipsoid else "unknown",
            "value": ellipsoid,
            "detail": f"{ellipsoid['name']}, {where}." if ellipsoid else "No ellipsoid is stated.",
        },
        {
            "field": "prime_meridian",
            "label": FIELD_LABELS["prime_meridian"],
            "state": state if meridian else "unknown",
            "value": meridian,
            "detail": f"{meridian['name']}, {where}." if meridian else "No prime meridian is stated.",
        },
        {
            "field": "projection",
            "label": FIELD_LABELS["projection"],
            "state": state,
            "value": projection or {"method": None, "name": None, "geographic": True},
            "detail": f"{projection['method']}, {where}."
            if projection
            else "None: the CRS is geographic, so coordinates are longitude and latitude.",
        },
        {
            "field": "projection_parameters",
            "label": FIELD_LABELS["projection_parameters"],
            "state": state,
            "value": projection["parameters"] if projection else [],
            "detail": f"{len(projection['parameters'])} parameters, {where}."
            if projection
            else "A geographic CRS has no projection parameters.",
        },
        {
            "field": "units",
            "label": FIELD_LABELS["units"],
            "state": state,
            "value": details["coordinate_units"],
            "detail": f"Coordinate units: {', '.join(details['coordinate_units'])}, {where}.",
        },
        {
            "field": "coordinate_grid",
            "label": FIELD_LABELS["coordinate_grid"],
            "state": state,
            "value": {
                "kind": details["kind"],
                "units": details["coordinate_units"],
                "area_of_use": details.get("area_of_use"),
            },
            "detail": (
                "The sheet's coordinates lie on the grid of its projected CRS."
                if details["kind"] == "projected"
                else "The sheet's coordinates are longitude and latitude."
            )
            + " Grid lines and graticule ticks printed on the picture are not read by this application.",
        },
        {"field": "extent", "label": FIELD_LABELS["extent"], **_extent(asset, basis, origin)},
    ]
    if plan is None:
        facts.append(_unknown("transformation_to_wgs84", "No transformation plan could be made."))
    else:
        resolution = plan.get("resolution") or {}
        facts.append(
            {
                "field": "transformation_to_wgs84",
                "label": FIELD_LABELS["transformation_to_wgs84"],
                "state": "from_registry",
                "value": {
                    "status": plan["status"],
                    "classification": resolution.get("classification"),
                    "accuracy_m": resolution.get("accuracy_m"),
                    "operations": [o["id"] for o in plan["operations"]],
                },
                "detail": plan["status_message"],
            }
        )
    return facts


def _unresolved(
    asset: Asset,
    basis: str,
    details: dict[str, Any] | None,
    plan: dict[str, Any] | None,
    hints: list[dict[str, str]],
    declaration: dict[str, Any],
) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []

    def add(field: str, status: str, detail: str, required: str) -> None:
        items.append(
            {
                "field": field,
                "label": FIELD_LABELS.get(field, field),
                "status": status,
                "detail": detail,
                "required": required,
            }
        )

    if details is None:
        if asset.raster is None:
            add(
                "datum",
                "missing",
                "A PDF is not read as a raster, so no CRS, datum or projection could be read from it.",
                "A georeferenced TIFF of the page, or control points on a rendered copy with the CRS stated by the "
                "supplier.",
            )
        else:
            for field in ("datum", "ellipsoid", "projection", "projection_parameters", "units"):
                add(
                    field,
                    "missing",
                    "The sheet has no usable CRS, so this is not known.",
                    "A CRS stated by the file, a confirmed .prj / world file, or a registry CRS a person chooses for "
                    "control points, with the source of that choice.",
                )
        add(
            "transformation_to_wgs84",
            "missing",
            "No CRS, so there is nothing to transform from.",
            "A defined CRS first; then the registered or published datum shift to WGS 84.",
        )
    else:
        if not details["identifier"] and not details["registry_matches"]:
            add(
                "datum",
                "needs_authority",
                "The CRS is defined only by the file's own text and matches no registry entry.",
                "Confirm its datum, ellipsoid and projection parameters against the authoritative definition.",
            )
        elif not details["identifier"] and len(details["registry_matches"]) > 1:
            add(
                "datum",
                "ambiguous",
                f"The definition matches several registry entries ({', '.join(details['registry_matches'])}).",
                "State which registry entry is meant. None is chosen for you.",
            )
        if plan is not None:
            if plan["status"] == "unavailable":
                add(
                    "transformation_to_wgs84",
                    "needs_authority",
                    plan["status_message"]
                    + (f" Missing grids: {', '.join(plan['missing_grids'])}." if plan["missing_grids"] else ""),
                    "An authoritative datum shift (parameters or grid) with its documented accuracy. A ballpark "
                    "fallback is blocked by default.",
                )
            elif plan["status"] == "selection_required":
                add(
                    "transformation_to_wgs84",
                    "ambiguous",
                    plan["status_message"],
                    "Choose one of the registered operations in the CRS assessment.",
                )
    for hint in hints:
        add(
            "datum" if hint["family"] in ("Everest", "Indian / Kalianpur datums") else "projection_parameters",
            "needs_authority",
            f"The CRS names {hint['matched']}. {hint['meaning']}",
            "The authoritative definition and its source document.",
        )
    if declaration["series"] == "esm":
        add(
            "projection_parameters",
            "declared_unverified",
            "Declared as an ESM sheet. Nothing in the file states the series, and other ESM sheets are not assumed to "
            "share this sheet's parameters.",
            "Per-sheet confirmation of the CRS parameters from the sheet's margin or supplier documentation.",
        )
    return items


def build(
    asset: Asset,
    crs_assessment: dict[str, Any] | None,
    sidecar_rows: list[Sidecar],
    notes: list[dict[str, Any]],
) -> dict[str, Any]:
    info = mapsheet.info(asset)
    assert info is not None
    declaration = info["declaration"]
    basis = info["georeferencing_basis"]
    details = crs_assessment["crs"] if crs_assessment else None
    plan = next((p for p in (crs_assessment or {}).get("transformations", []) if p["purpose"] == "coordinates"), None)
    hints = family_hints(details)
    unresolved = _unresolved(asset, basis, details, plan, hints, declaration)
    required: list[str] = []
    for item in unresolved:
        text = f"{item['label']}: {item['required']}"
        if text not in required:
            required.append(text)
    if declaration["series"] == "esm" or any(h["family"] == "Everest" for h in hints):
        required.extend(r for r in REQUIRED_FOR_ESM if r not in required)
    return {
        "asset_id": asset.id,
        "filename": asset.original_filename,
        "info": info,
        "facts": _facts(asset, basis, details, plan, (crs_assessment or {}).get("georeferencing_source")),
        "unresolved": unresolved,
        "required_information": required,
        "family_hints": hints,
        "sidecars": [
            {
                "id": s.id,
                "kind": s.kind,
                "filename": s.original_filename,
                "upload_status": s.upload_status,
                "uploaded_at": s.uploaded_at,
            }
            for s in sidecar_rows
        ],
        "notes": notes,
        "pdf": (asset.extracted_metadata or {}).get("pdf"),
        "crs_assessment": crs_assessment,
        "series_note": SERIES_NOTES[declaration["series"]],
        "limitations": list(LIMITATIONS),
    }
