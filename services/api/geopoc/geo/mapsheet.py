"""Map sheets as the application sees them: what was declared, how the sheet is georeferenced, and what is unresolved.

Nothing here writes to the database or assigns a CRS. A map sheet is an `overhead_raster` asset whose metadata holds the
uploader's declaration (see `geopoc.ingestion.mapsheet`); everything else is derived from the stored raster record and
its georeferencing history on each request.
"""

from typing import Any

from geopoc.db.models import Asset
from geopoc.geo import georeference

# How the position of a sheet was established. Only the first four place a sheet; none is assumed.
BASIS_LABELS: dict[str, str] = {
    "file_metadata": "The file's own CRS and geotransform",
    "sidecar": "A world file / .prj a person reviewed and confirmed",
    "world_file": "A world file entered by a person",
    "control_points": "Control points supplied by a person",
    "recorded": "A CRS or geotransform entered by a person",
    "none": "Not georeferenced",
}


def declared(asset: Asset) -> dict[str, Any] | None:
    """The map-sheet record kept in the asset's metadata, or None for any other asset."""
    record = (asset.extracted_metadata or {}).get("map_sheet")
    return record if isinstance(record, dict) else None


def is_map_sheet(asset: Asset) -> bool:
    return declared(asset) is not None


def georeferencing_basis(asset: Asset) -> str:
    """Where the sheet's position comes from, read from its raster record and recorded versions."""
    raster = asset.raster
    if raster is None:
        return "none"
    assertion = raster.current_assertion
    if assertion is not None and assertion.operation != "revert_to_file":
        validation = assertion.validation or {}
        if "control_points" in validation:
            return "control_points"
        if "sidecars" in validation:
            return "sidecar"
        if assertion.method == "world_file_entered":
            return "world_file"
        return "recorded"
    return "file_metadata" if raster.georeferenced else "none"


def placed(asset: Asset) -> bool:
    return asset.raster is not None and georeference.effective(asset.raster).georeferenced


def info(asset: Asset) -> dict[str, Any] | None:
    """The `MapSheetInfo` fields for a map-sheet asset, or None for any other asset."""
    record = declared(asset)
    if record is None:
        return None
    basis = georeferencing_basis(asset)
    return {
        "declaration": record["declaration"],
        "intake": record["intake"],
        "format_family": record["format_family"],
        "georeferencing_basis": basis,
        "georeferencing_basis_label": BASIS_LABELS[basis],
        "placed": placed(asset),
        "processing": record.get("processing", []),
    }


def layer_info(asset: Asset) -> dict[str, Any] | None:
    """The `MapSheetLayerOut` fields for a placed map sheet."""
    full = info(asset)
    if full is None:
        return None
    declaration = full["declaration"]
    return {
        "series": declaration["series"],
        "series_label": declaration["series_label"],
        "sheet_label": declaration.get("sheet_label"),
        "scale_denominator": declaration.get("scale_denominator"),
        "edition": declaration.get("edition"),
        "intake": full["intake"],
        "georeferencing_basis": full["georeferencing_basis"],
        "georeferencing_basis_label": full["georeferencing_basis_label"],
    }


def next_step(asset: Asset) -> str:
    """What has to happen before the sheet can be placed, in a sentence."""
    full = info(asset)
    assert full is not None
    if full["placed"]:
        return "Placed on the map. Check its CRS and datum transformation in the CRS assessment."
    if full["intake"] == "pdf_document":
        return (
            "A PDF cannot be georeferenced here. Convert the map page to a TIFF or PNG, upload that, and georeference "
            "it with control points."
        )
    raster = asset.raster
    state = georeference.effective(raster) if raster is not None else None
    if state is not None and state.crs_usable and state.geotransform_usable:
        return "Its CRS and geotransform are set but it is not placed: see the CRS assessment for the reason."
    return (
        "Not georeferenced. Confirm a world file or .prj, or enter control points. Nothing is placed from the series, "
        "the file name or the picture."
    )
