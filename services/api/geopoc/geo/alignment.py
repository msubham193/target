"""Alignment inspection of two placed rasters (for example a map sheet over a satellite image).

Read-only. It states how the two footprints relate, how each layer was positioned, and what each position is documented
to be good to. It does not measure alignment from the pixels, and overlapping footprints do not prove that features
coincide: that has to be checked against features visible in both.
"""

import math
from typing import Any

from pyproj import Geod
from shapely.geometry import Polygon

from geopoc.db.models import Asset
from geopoc.geo import antimeridian, convert, georeference, mapsheet
from geopoc.geo import transform as transforms

GEOGRAPHIC = "EPSG:4326"
METRES_PER_DEGREE = 111_320.0


def _ground_pixel(crs, pixel_size: list[float], latitude: float) -> float:
    """The side of a pixel on the ground in metres (geometric mean of its two sides)."""
    size_x, size_y = pixel_size
    if "degree" in (crs.axis_info[0].unit_name or "").lower():
        return math.sqrt(size_x * METRES_PER_DEGREE * math.cos(math.radians(latitude)) * size_y * METRES_PER_DEGREE)
    return math.sqrt(size_x * size_y) * (crs.axis_info[0].unit_conversion_factor or 1.0)


def layer(asset: Asset, state: georeference.Effective) -> dict[str, Any]:
    """One layer's positioning facts."""
    info = mapsheet.info(asset)
    basis = mapsheet.georeferencing_basis(asset)
    footprint: Polygon | None = state.footprint
    if antimeridian.geometry_crosses(footprint):
        antimeridian.refuse("Aligning this raster")
    transformation_class = None
    accuracy = None
    selection_required = False
    needs_acknowledgement = False
    if state.crs_usable and state.crs_wkt:
        crs = transforms.crs_from_wkt(state.crs_wkt)
        bounds = footprint.bounds if footprint is not None else None
        try:
            selection = convert.selection_for(asset.raster, state.source, None)
            resolved = transforms.resolve(
                crs, GEOGRAPHIC, bounds, selection.operation_id, acknowledgement=selection.acknowledgement
            )
            transformation_class = resolved.operation.klass
            accuracy = resolved.operation.accuracy_m
        except transforms.TransformationError as exc:
            selection_required = exc.code == "TRANSFORMATION_SELECTION_REQUIRED"
            needs_acknowledgement = exc.code == transforms.ACK_REQUIRED_CODE
            transformation_class = "approximate" if selection_required or needs_acknowledgement else "unavailable"
            if needs_acknowledgement:
                accuracy = exc.extra["accuracy_m"]  # documented, but not used: the layer is not carried to the map
    rms_m = None
    assertion = asset.raster.current_assertion if asset.raster else None
    if basis == "control_points" and assertion is not None:
        rms_m = ((assertion.validation or {}).get("control_points") or {}).get("rms_m")
    ground_pixel = None
    if footprint is not None and state.pixel_size and state.crs_wkt:
        ground_pixel = _ground_pixel(transforms.crs_from_wkt(state.crs_wkt), state.pixel_size, footprint.centroid.y)
    return {
        "asset_id": asset.id,
        "title": asset.original_filename,
        "kind": "map_sheet" if info is not None else "raster",
        "series": info["declaration"]["series"] if info is not None else None,
        "crs_id": state.crs_id,
        "crs_name": state.crs_name,
        "placement": "recorded_by_person" if state.source == "recorded" else "from_file",
        "georeferencing_basis": basis,
        "transformation_class": transformation_class,
        "datum_shift_accuracy_m": accuracy,
        "selection_required": selection_required,
        "needs_acknowledgement": needs_acknowledgement,
        "control_point_rms_m": rms_m,
        "ground_pixel_m": ground_pixel,
        "bounds_4326": list(footprint.bounds) if footprint is not None else None,
    }


def _area_km2(polygon: Polygon) -> float:
    area, _ = Geod(ellps="WGS84").geometry_area_perimeter(polygon)
    return abs(area) / 1e6


def compare(first: Asset, second: Asset) -> dict[str, Any]:
    state_a, state_b = georeference.effective(first.raster), georeference.effective(second.raster)
    a, b = layer(first, state_a), layer(second, state_b)
    fa, fb = state_a.footprint, state_b.footprint
    overlap = fa is not None and fb is not None and fa.intersects(fb)
    inter = fa.intersection(fb) if overlap else None
    inter_area = (
        _area_km2(inter)
        if inter is not None and not inter.is_empty and inter.geom_type in ("Polygon", "MultiPolygon")
        else 0.0
    )
    overlap = overlap and inter_area > 0
    area_a, area_b = (_area_km2(f) if f is not None else 0.0 for f in (fa, fb))
    ratio = None
    if a["ground_pixel_m"] and b["ground_pixel_m"]:
        coarse, fine = max(a["ground_pixel_m"], b["ground_pixel_m"]), min(a["ground_pixel_m"], b["ground_pixel_m"])
        ratio = coarse / fine if fine > 0 else None

    findings: list[str] = []
    cautions = [
        "Overlapping footprints show that both layers are placed in the same area. They do not show that features "
        "coincide: compare features visible in both layers to check alignment."
    ]
    if overlap:
        findings.append(
            f"The footprints overlap over {inter_area:,.2f} km², {inter_area / area_a:.0%} of the first layer and "
            f"{inter_area / area_b:.0%} of the second."
        )
    else:
        findings.append("The footprints do not overlap, so the layers cannot be compared on the map.")
    for label, item in (("First", a), ("Second", b)):
        findings.append(
            f"{label} layer ({item['title']}): positioned by "
            f"{mapsheet.BASIS_LABELS[item['georeferencing_basis']].lower()}"
            f" in {item['crs_id'] or item['crs_name'] or 'an unnamed CRS'}."
        )
        if item["georeferencing_basis"] == "control_points" and item["control_point_rms_m"] is not None:
            cautions.append(
                f"{label} layer was georeferenced from control points that fit to {item['control_point_rms_m']:.1f} m "
                "RMS at the points themselves. Positions between and beyond them can be further off."
            )
        elif item["georeferencing_basis"] in ("recorded", "world_file", "sidecar"):
            cautions.append(
                f"{label} layer's position was entered or confirmed by a person; the application cannot check it "
                "against the ground."
            )
        if item["selection_required"]:
            cautions.append(f"{label} layer needs a datum transformation chosen before it is drawn.")
        elif item["needs_acknowledgement"]:
            shown = item["datum_shift_accuracy_m"]
            cautions.append(
                f"{label} layer's only datum shift to WGS 84 "
                + (f"is documented to about {shown:g} m" if shown is not None else "has no stated accuracy")
                + f", worse than {transforms.ACK_ACCURACY_M:g} m, and has not been acknowledged. It is not carried "
                "to the "
                "map until it is, so the layers cannot be compared in WGS 84."
            )
        elif item["transformation_class"] == "approximate" and item["datum_shift_accuracy_m"] is not None:
            cautions.append(
                f"{label} layer is moved to WGS 84 by a registered datum shift documented to about "
                f"{item['datum_shift_accuracy_m']:g} m."
            )
        elif item["transformation_class"] == "unavailable":
            cautions.append(f"{label} layer has no usable transformation to WGS 84.")
    accuracies = [
        x["datum_shift_accuracy_m"] for x in (a, b) if x["datum_shift_accuracy_m"] and not x["needs_acknowledgement"]
    ]
    if len(accuracies) == 2:
        cautions.append(
            f"Both layers use an approximate datum shift, so they can differ by up to about {sum(accuracies):g} m "
            "from datum-shift error alone, before any georeferencing error."
        )
    if a["crs_id"] and b["crs_id"] and a["crs_id"] != b["crs_id"]:
        findings.append(
            f"The layers use different CRSs ({a['crs_id']} and {b['crs_id']}); each is carried to the map separately."
        )
    if ratio is not None:
        findings.append(
            f"Ground pixel sizes are {a['ground_pixel_m']:.2f} m and {b['ground_pixel_m']:.2f} m (ratio {ratio:.1f})."
        )
    return {
        "first": a,
        "second": b,
        "overlap": overlap,
        "overlap_bounds_4326": list(inter.bounds) if overlap and inter is not None else None,
        "overlap_area_km2": inter_area if overlap else None,
        "overlap_fraction_of_first": inter_area / area_a if overlap and area_a else None,
        "overlap_fraction_of_second": inter_area / area_b if overlap and area_b else None,
        "resolution_ratio": ratio,
        "findings": findings,
        "cautions": cautions,
    }
