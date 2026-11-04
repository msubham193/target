"""A read-only account of a raster's coordinate reference system and of what moving its coordinates would involve.

It combines what is already stored (the file's own CRS and geotransform, or the latest version recorded by a person)
with
what the local PROJ database says about that CRS and about the operations that would carry it to longitude/latitude
(footprints, readouts) and to Web Mercator (map tiles). Nothing is stored, queued, audited or changed, and nothing is
assigned: a CRS the raster does not already have is never filled in from its filename, its series or its look.

Eight statuses summarise the situation. They are facts derived from stored data; the reasons say in plain words why each
one holds or does not.
"""

from typing import Any

import pyproj
from affine import Affine
from pyproj import CRS
from pyproj.exceptions import CRSError

from geopoc.db.models import Asset, RasterMetadata
from geopoc.geo import convert
from geopoc.geo import transform as transforms
from geopoc.geo.georeference import Effective

DISPLAY_TARGETS: tuple[tuple[str, str, str], ...] = (
    ("EPSG:4326", "coordinates", "Footprints, coordinate readouts and exports (longitude and latitude, WGS 84)"),
    ("EPSG:3857", "tiles", "Map display tiles (Web Mercator, built on WGS 84)"),
)

STATUS_LABELS: dict[str, str] = {
    "georeferenced": "Georeferenced",
    "crs_identified": "CRS identified",
    "crs_missing": "CRS missing",
    "crs_ambiguous": "CRS ambiguous",
    "transformation_available": "Transformation available",
    "transformation_unavailable": "Transformation unavailable",
    "georeferencing_required": "Georeferencing required",
    "validation_failed": "Validation failed",
}

PLAN_STATUS_TEXT: dict[str, str] = {
    "exact": "No datum change is involved, so the conversion is exact.",
    "approximate": "One registered datum shift applies. Its documented accuracy is shown; the position is approximate.",
    "selection_required": "Several registered datum shifts apply and they give different positions. Choose one.",
    "unavailable": "No usable transformation exists: only a blocked ballpark fallback, or operations that need a "
    "grid that is not installed.",
}

LIMITATIONS: tuple[str, ...] = (
    "This assessment lists the operations to longitude/latitude (WGS 84) and to Web Mercator for display.",
    "Pixel lookup, the longitude/latitude of measured points, the area of interest and the 3D position follow the same "
    "rules: exact or a single registered shift is used, several need a choice, and a ballpark or missing-grid "
    "conversion is refused. See docs/crs-conversion-coverage.md.",
    "A choice made for a raster that uses its own file georeferencing lasts for this browser view. A choice made while "
    "recording georeferencing is kept in that version's history.",
    "No Everest-56, Survey of India or other map-series CRS definition is installed or assumed. A CRS is only what the "
    "file, a registry code or a person-recorded version states.",
    "Nothing here shows that a position is correct. A valid CRS can still be the wrong one.",
)


def crs_details(crs: CRS) -> dict[str, Any]:
    """Datum, ellipsoid, projection and units, as PROJ reads them from the definition. Nothing is inferred."""
    authority = crs.to_authority(min_confidence=100)
    matches = [f"{m.auth_name}:{m.code}" for m in crs.list_authority(min_confidence=100)]
    datum = crs.datum
    members: list[str] = []
    if datum is not None:
        description = datum.to_json_dict()
        if description.get("type") == "DatumEnsemble":
            members = [str(m.get("name", "")) for m in description.get("members", [])]
    ellipsoid = crs.ellipsoid
    projection = None
    operation = crs.coordinate_operation
    if crs.is_projected and operation is not None:
        projection = {
            "method": operation.method_name,
            "name": operation.name,
            "parameters": [
                {"name": p.name, "value": p.value, "unit": p.unit_name or "unity"} for p in (operation.params or [])
            ],
        }
    axes = [
        {
            "name": a.name,
            "abbreviation": a.abbrev,
            "direction": a.direction,
            "unit": a.unit_name,
            "unit_to_si": a.unit_conversion_factor,
        }
        for a in crs.axis_info
    ]
    area = crs.area_of_use
    if area is None and authority:
        # A CRS read from a file often carries no area of use; the matching registry entry does.
        try:
            area = CRS.from_authority(*authority).area_of_use
        except CRSError:
            area = None
    return {
        "identifier": f"{authority[0]}:{authority[1]}" if authority else None,
        "name": crs.name,
        "kind": "projected" if crs.is_projected else "geographic" if crs.is_geographic else "other",
        "registry_matches": matches,
        "datum": {
            "name": datum.name if datum is not None else None,
            "is_ensemble": bool(members),
            "ensemble_members": members,
        },
        "ellipsoid": {
            "name": ellipsoid.name,
            "semi_major_m": ellipsoid.semi_major_metre,
            "inverse_flattening": ellipsoid.inverse_flattening or None,  # 0 means a sphere
            "semi_minor_m": ellipsoid.semi_minor_metre,
        }
        if ellipsoid is not None
        else None,
        "prime_meridian": {"name": crs.prime_meridian.name, "longitude_degrees": crs.prime_meridian.longitude}
        if crs.prime_meridian is not None
        else None,
        "projection": projection,
        "axes": axes,
        "coordinate_units": list(dict.fromkeys(a["unit"] for a in axes)),
        "area_of_use": {
            "name": area.name,
            "west": area.west,
            "south": area.south,
            "east": area.east,
            "north": area.north,
        }
        if area
        else None,
        "deprecated": crs.is_deprecated,
        "towgs84": [float(v) for v in operation.towgs84] if operation is not None and operation.towgs84 else [],
    }


def _bounds_for_planning(
    raster: RasterMetadata, state: Effective, crs: CRS | None
) -> tuple[float, float, float, float] | None:
    """Where the raster is, roughly, so only operations that apply there are listed."""
    if state.footprint is not None:
        west, south, east, north = state.footprint.bounds
        return west, south, east, north
    if crs is not None and state.geotransform_usable and state.geotransform:
        transform = Affine.from_gdal(*state.geotransform)
        corners = [
            transform @ (c, r)
            for c, r in ((0, 0), (raster.width, 0), (raster.width, raster.height), (0, raster.height))
        ]
        return transforms.locate(crs, [x for x, _ in corners], [y for _, y in corners])
    if crs is not None and crs.area_of_use is not None:
        area = crs.area_of_use
        return area.west, area.south, area.east, area.north
    return None


def operation_out(operation: transforms.Operation, plan: transforms.Plan, selected_id: str | None) -> dict[str, Any]:
    return {
        "id": operation.id,
        "name": operation.name,
        "classification": operation.klass,
        "accuracy_m": operation.accuracy_m,
        "authority_code": operation.authority_code,
        "area_of_use": operation.area_of_use,
        "grids": [{"name": g.name, "available": g.available} for g in operation.grids],
        "summary": operation.summary(),
        "usable": operation.usable,
        "recommended": operation.id == plan.recommended_id and plan.status == "selection_required",
        "selected": operation.id == selected_id,
        "requires_acknowledgement": operation.requires_acknowledgement,
        "acknowledgement_reason": operation.ack_reason,
        "acknowledgement_threshold_m": transforms.ACK_ACCURACY_M,
    }


def plan_summary(plan: transforms.Plan, selected_id: str | None = None) -> dict[str, Any]:
    """The plan as it is shown and sent: statuses, every candidate with its class, accuracy and missing grids."""
    return {
        "source_crs": plan.source,
        "target_crs": plan.target,
        "status": plan.status,
        "status_message": PLAN_STATUS_TEXT[plan.status],
        "recommended_id": plan.recommended_id if plan.status == "selection_required" else None,
        "selected_id": selected_id,
        "operations": [operation_out(o, plan, selected_id) for o in plan.operations],
        "missing_grids": plan.missing_grids(),
        "messages": plan.messages,
    }


def _plan_for_target(
    crs: CRS,
    target: str,
    purpose: str,
    label: str,
    bounds: tuple[float, float, float, float] | None,
    operation_id: str | None,
    acknowledgement: transforms.Acknowledgement | None = None,
) -> dict[str, Any]:
    plan = transforms.plan(crs, target, bounds)
    selected = operation_id if operation_id and plan.find(operation_id) is not None else None
    out = plan_summary(plan, selected)
    out.update({"purpose": purpose, "purpose_label": label, "resolution": None, "blocked": None})
    try:
        resolved = transforms.resolve(crs, target, bounds, operation_id, acknowledgement=acknowledgement)
        out["resolution"] = {
            "operation_id": resolved.operation.id,
            "classification": resolved.operation.klass,
            "accuracy_m": resolved.operation.accuracy_m,
            "chosen_by": "selection" if operation_id else "only_option",
            "requires_acknowledgement": resolved.operation.requires_acknowledgement,
            "acknowledged": resolved.acknowledgement is not None,
        }
    except transforms.TransformationError as exc:
        out["blocked"] = {"code": exc.code, "title": exc.title, "detail": exc.detail}
    return out


def _statuses(
    asset: Asset,
    raster: RasterMetadata,
    state: Effective,
    details: dict[str, Any] | None,
    plans: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    flags = set(asset.quality_flags or [])
    crs_usable = state.crs_usable and details is not None
    ambiguous = bool(details is not None and not details["identifier"] and len(details["registry_matches"]) > 1)
    in_registry = bool(details and (details["identifier"] or details["registry_matches"]))
    primary = next((p for p in plans if p["purpose"] == "coordinates"), None)
    awaiting_acknowledgement = bool(primary and (primary["blocked"] or {}).get("code") == transforms.ACK_REQUIRED_CODE)
    any_usable = (
        bool(primary)
        and primary["status"] in ("exact", "approximate", "selection_required")
        and not awaiting_acknowledgement
    )
    failed_reasons = []
    if raster.crs_status == "invalid" or "INVALID_CRS" in flags:
        failed_reasons.append((asset.unknowns or {}).get("crs") or "The file's CRS definition is not valid.")
    if "INVALID_GEOREFERENCING" in flags:
        failed_reasons.append((asset.unknowns or {}).get("georeferencing") or "The coordinates do not fit the CRS.")
    if "TRANSFORMATION_BLOCKED" in flags:
        failed_reasons.append((asset.unknowns or {}).get("transformation") or "The transformation was blocked.")

    def item(code: str, active: bool, message: str) -> dict[str, Any]:
        return {"code": code, "label": STATUS_LABELS[code], "active": active, "message": message}

    if crs_usable:
        origin = "recorded by a person" if state.source == "recorded" else "declared in the file"
        how = (
            f"It matches registry entry {details['identifier']}."
            if details["identifier"]
            else "It is fully defined but matches no single registry entry; its definition is kept as it is."
        )
        identified = item("crs_identified", not ambiguous, f"A usable CRS is {origin}. {how}")
        if ambiguous:
            identified["message"] = "A CRS is present, but see the ambiguity below."
    else:
        identified = item("crs_identified", False, "There is no usable CRS to identify.")

    if primary is None:
        available = item("transformation_available", False, "Without a usable CRS there is nothing to transform.")
        unavailable = item("transformation_unavailable", False, "Not applicable until the raster has a usable CRS.")
    elif any_usable:
        needs_choice = primary["status"] == "selection_required"
        available = item(
            "transformation_available",
            True,
            PLAN_STATUS_TEXT[primary["status"]],
        )
        unavailable = item(
            "transformation_unavailable",
            False,
            "A usable transformation exists."
            if not needs_choice
            else "Usable transformations exist, but none is chosen yet.",
        )
    else:
        available = item("transformation_available", False, "No usable transformation was found.")
        unavailable = item(
            "transformation_unavailable",
            True,
            (primary["blocked"] or {}).get("detail") or PLAN_STATUS_TEXT["unavailable"],
        )

    return [
        item(
            "georeferenced",
            state.georeferenced,
            "Its pixels have map positions." if state.georeferenced else "Its pixels do not yet have map positions.",
        ),
        identified,
        item(
            "crs_missing",
            not crs_usable and raster.crs_status == "missing" and state.source == "file",
            "The file declares no coordinate reference system. None is assumed."
            if not crs_usable and raster.crs_status == "missing" and state.source == "file"
            else "A CRS is declared or recorded."
            if crs_usable
            else "The CRS is not missing, but it cannot be used.",
        ),
        item(
            "crs_ambiguous",
            ambiguous,
            f"The definition matches several registry entries ({', '.join(details['registry_matches'])}) and none is "
            "chosen for you."
            if ambiguous and details
            else "The CRS is not ambiguous."
            if in_registry or crs_usable
            else "There is no CRS to be ambiguous.",
        ),
        available,
        unavailable,
        item(
            "georeferencing_required",
            not state.georeferenced,
            "Georeferencing must be recorded before it can be placed on a map."
            if not state.georeferenced
            else "Not required: it already has a CRS and a geotransform that fit.",
        ),
        item(
            "validation_failed",
            bool(failed_reasons),
            " ".join(failed_reasons) if failed_reasons else "No validation failure is recorded.",
        ),
    ]


def assess(asset: Asset, raster: RasterMetadata, state: Effective, operation_id: str | None = None) -> dict[str, Any]:
    """The full assessment for one raster. `operation_id` only picks among the listed operations; it is echoed."""
    crs: CRS | None = None
    if state.crs_usable and state.crs_wkt:
        try:
            crs = CRS.from_wkt(state.crs_wkt)
        except CRSError:
            crs = None
    details = crs_details(crs) if crs is not None else None
    bounds = _bounds_for_planning(raster, state, crs)
    stored_acknowledgement = convert.recorded_acknowledgement(raster) if state.source == "recorded" else None
    plans = (
        [
            _plan_for_target(crs, target, purpose, label, bounds, operation_id, stored_acknowledgement)
            for target, purpose, label in DISPLAY_TARGETS
        ]
        if crs is not None
        else []
    )
    statuses = _statuses(asset, raster, state, details, plans)
    active = [s["label"] for s in statuses if s["active"]]
    gt_ok = state.geotransform_usable
    return {
        "asset_id": asset.id,
        "georeferencing_source": state.source,
        "georeferencing_version": state.version,
        "crs": details,
        "raster": {
            "width": raster.width,
            "height": raster.height,
            "band_count": raster.band_count,
            "pixel_size": state.pixel_size,
            "pixel_units": state.pixel_units,
            "geotransform": state.geotransform if gt_ok else None,
            "bounds_native": state.bounds_native,
            "nodata": raster.nodata,
        },
        "statuses": statuses,
        "transformations": plans,
        "planning_area": {"west": bounds[0], "south": bounds[1], "east": bounds[2], "north": bounds[3]}
        if bounds
        else None,
        "selected_operation_id": operation_id,
        "summary": ", ".join(active) if active else "No status applies.",
        "accuracy_notes": list(transforms.ACCURACY_NOTES),
        "class_explanations": transforms.KLASS_EXPLANATION,
        "limitations": list(LIMITATIONS),
        "versions": {"proj": pyproj.proj_version_str, "pyproj": pyproj.__version__, "network": "disabled"},
    }
