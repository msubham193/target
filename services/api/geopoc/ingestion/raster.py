"""GeoTIFF validation, raster metadata and georeferencing checks.

Nothing is guessed: with a missing or invalid CRS, or without a usable geotransform, no geographic
footprint or coordinate is produced and the reason is recorded.
"""

import math
import warnings
from pathlib import Path
from typing import Any

import numpy as np
import pyproj
import rasterio
from pyproj import Transformer
from pyproj.exceptions import CRSError, ProjError
from rasterio.errors import NotGeoreferencedWarning, RasterioError
from rasterio.windows import Window
from shapely.geometry import Polygon

from geopoc.config import Settings
from geopoc.ingestion.rules import Inspection, UnreadableAsset, json_safe

SAMPLE = 512  # edge length of the windows read to prove the pixel data is decodable
EDGE_POINTS = 21  # footprint outline density per edge


def _outline_pixels(width: int, height: int) -> list[tuple[float, float]]:
    steps = np.linspace(0.0, 1.0, EDGE_POINTS)
    top = [(s * width, 0.0) for s in steps]
    right = [(float(width), s * height) for s in steps[1:]]
    bottom = [(s * width, float(height)) for s in steps[::-1][1:]]
    left = [(0.0, s * height) for s in steps[::-1][1:]]
    return top + right + bottom + left


def _footprint(transform, width: int, height: int, crs: pyproj.CRS) -> tuple[Polygon | None, dict | None]:
    """Raster outline in EPSG:4326, or None when the coordinates do not make sense in the stated CRS."""
    xs, ys = zip(*[transform @ point for point in _outline_pixels(width, height)], strict=True)
    try:
        transformer = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
        lon, lat = transformer.transform(np.array(xs), np.array(ys))
    except ProjError:
        return None, None
    if not (np.isfinite(lon).all() and np.isfinite(lat).all()):
        return None, None
    if (np.abs(lon) > 180).any() or (np.abs(lat) > 90).any():
        return None, None
    polygon = Polygon(zip(lon.tolist(), lat.tolist(), strict=True))
    if polygon.is_empty or not polygon.is_valid or polygon.area == 0:
        return None, None
    accuracy = transformer.accuracy
    record = {
        "target_crs_id": "EPSG:4326",
        "definition": transformer.definition,
        "accuracy_m": accuracy if accuracy is not None and accuracy >= 0 else None,
        "proj_version": pyproj.proj_version_str,
        "pyproj_version": pyproj.__version__,
    }
    return polygon, record


def inspect_raster(path: Path, family: str, settings: Settings) -> Inspection:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", NotGeoreferencedWarning)
            with rasterio.open(path) as src:
                if src.driver != "GTiff":
                    raise UnreadableAsset("ASSET_UNREADABLE", "The file is not a readable TIFF / GeoTIFF.")
                width, height, count = src.width, src.height, src.count
                if width < 1 or height < 1 or count < 1:
                    raise UnreadableAsset("ASSET_UNREADABLE", "The raster has no pixel data.")
                # Read the first and last corners only: bounded memory, and truncation shows up at the end.
                w, h = min(SAMPLE, width), min(SAMPLE, height)
                src.read(1, window=Window(0, 0, w, h))
                src.read(count, window=Window(width - w, height - h, w, h))

                transform = src.transform
                has_geotransform = not transform.is_identity
                wkt = src.crs.to_wkt() if src.crs else ""
                raster: dict[str, Any] = {
                    "driver": src.driver,
                    "width": width,
                    "height": height,
                    "band_count": count,
                    "dtypes": list(src.dtypes),
                    "nodata": json_safe(list(src.nodatavals)),
                    "geotransform": list(transform.to_gdal()) if has_geotransform else None,
                    "bounds_native": None,
                }
                tags = {k: v for k, v in list(src.tags().items())[:50]}
                compression = src.compression.value if src.compression else None
                gcp_count = len(src.gcps[0])
                bounds = src.bounds
    except UnreadableAsset:
        raise
    except (RasterioError, OSError, ValueError) as exc:
        raise UnreadableAsset("ASSET_UNREADABLE", "The file is corrupt or not a readable GeoTIFF.") from exc

    result = Inspection(detected_format="GTiff", media_type="image/tiff")
    result.metadata = json_safe(
        {
            "tags": tags,
            "compression": compression,
            "ground_control_points": gcp_count,
            "gdal_version": rasterio.__gdal_version__,
        }
    )

    # --- geotransform ---------------------------------------------------------------------------------
    pixel_x = math.hypot(transform.a, transform.d)
    pixel_y = math.hypot(transform.b, transform.e)
    geotransform_ok = (
        has_geotransform
        and all(math.isfinite(v) for v in transform.to_gdal())
        and pixel_x > 0
        and pixel_y > 0
        and transform.determinant != 0
    )
    raster["pixel_size_x"] = pixel_x if geotransform_ok else None
    raster["pixel_size_y"] = pixel_y if geotransform_ok else None
    if not has_geotransform:
        result.unknowns["geotransform"] = "The file has no affine geotransform, so pixels have no map position."
    elif not geotransform_ok:
        result.unknowns["geotransform"] = "The file's geotransform is degenerate (zero or non-finite pixel size)."
    if gcp_count:
        result.unknowns["ground_control_points"] = (
            f"{gcp_count} ground control points are present but are not used for georeferencing."
        )

    # --- CRS ------------------------------------------------------------------------------------------
    crs: pyproj.CRS | None = None
    raster.update(crs_status="missing", crs_identifier=None, crs_name=None, crs_wkt=None, pixel_units=None)
    if not wkt:
        result.unknowns["crs"] = "The file declares no coordinate reference system. None is assumed."
        result.flag("MISSING_CRS")
    else:
        raster["crs_wkt"] = wkt
        try:
            parsed = pyproj.CRS.from_wkt(wkt)
        except CRSError:
            parsed = None
        if parsed is None:
            raster["crs_status"] = "invalid"
            result.unknowns["crs"] = "The file's CRS definition could not be interpreted."
            result.flag("INVALID_CRS")
        elif not (parsed.is_geographic or parsed.is_projected) or parsed.is_compound:
            raster["crs_status"] = "invalid"
            raster["crs_name"] = parsed.name
            result.unknowns["crs"] = (
                f"The file's CRS ({parsed.name!r}) is not a geographic or projected CRS tied to the Earth, "
                "so it cannot give map positions."
            )
            result.flag("INVALID_CRS")
        else:
            crs = parsed
            raster["crs_status"] = "valid"
            raster["crs_name"] = parsed.name
            authority = parsed.to_authority(min_confidence=100)
            if authority:
                raster["crs_identifier"] = f"{authority[0]}:{authority[1]}"
            else:
                result.unknowns["crs_identifier"] = (
                    "The CRS is fully defined in the file but matches no registry code exactly; "
                    "its WKT definition is kept."
                )
                result.flag("CRS_NO_AUTHORITY_CODE")

    # --- georeferencing = valid CRS + usable geotransform + coordinates that fit the CRS ---------------
    footprint = None
    raster["georeferenced"] = False
    if crs is not None and geotransform_ok:
        footprint, transformation = _footprint(transform, width, height, crs)
        if footprint is None:
            result.unknowns["georeferencing"] = (
                "The raster's coordinates fall outside the valid domain of its stated CRS, so the "
                "georeferencing is not trusted."
            )
            result.flag("INVALID_GEOREFERENCING")
        else:
            raster["georeferenced"] = True
            raster["pixel_units"] = crs.axis_info[0].unit_name
            raster["bounds_native"] = {
                "left": bounds.left,
                "bottom": bounds.bottom,
                "right": bounds.right,
                "top": bounds.top,
            }
            result.metadata["footprint_transformation"] = transformation
    if not raster["georeferenced"]:
        result.flag("NOT_GEOREFERENCED")
        result.unknowns["pixel_units"] = "Without valid georeferencing the ground size of a pixel is unknown."
        result.unknowns.setdefault(
            "georeferencing", "The raster cannot be placed on a map; it can only be viewed in image space."
        )

    tiff_time = tags.get("TIFFTAG_DATETIME")
    result.unknowns["acquisition_time"] = (
        "The TIFF date tag does not state what it measures or its time zone; it is kept as raw metadata."
        if tiff_time
        else "The file carries no acquisition time."
    )
    result.flag("TIMESTAMP_UNKNOWN")

    raster["footprint"] = footprint
    result.raster = raster
    return result
