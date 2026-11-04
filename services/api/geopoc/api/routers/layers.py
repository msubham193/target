"""Map layers, raster tiles and coordinate conversion for the Map & Analysis screen.

Only rasters whose own CRS and geotransform are valid are placed. Everything else is listed as excluded, with the
reason; nothing is positioned from a project location, a filename or its appearance.
"""

import math
import uuid
from types import SimpleNamespace
from typing import Annotated

import numpy as np
import pyproj
from affine import Affine
from fastapi import APIRouter, Path, Query
from fastapi.responses import Response
from geoalchemy2.shape import to_shape
from pyproj import Transformer
from shapely.geometry import mapping
from sqlalchemy import func, select

from geopoc.api.deps import SessionDep
from geopoc.api.errors import ProblemError, problem_responses
from geopoc.api.routers.rastertools import resolve_display
from geopoc.api.schemas import (
    AoiLayerOut,
    BasemapOut,
    CrsAxis,
    CrsTransformOut,
    ExcludedAssetOut,
    LayersOut,
    LayerTransformationOut,
    MapSheetLayerOut,
    ObservationLayerSummary,
    RasterLayerOut,
)
from geopoc.api.transformation import candidates, problem_for
from geopoc.db.models import Asset, Blob, ObservationRecord, Project
from geopoc.geo import antimeridian, assessment, colormaps, convert, georeference, mapsheet, orientation, tiles
from geopoc.geo import transform as transforms
from geopoc.geo.crs import CrsResolutionError, normalise_identifier, resolve
from geopoc.ingestion.raster import footprint_checked
from geopoc.rastertools import display_source
from geopoc.storage import store

router = APIRouter(prefix="/api/v1", tags=["map"], responses=problem_responses(422))

GEOGRAPHIC = "EPSG:4326"


_candidate_errors = candidates  # the same list of options every refused conversion reports


NOT_PLACEABLE = {
    "ground_image": "Ground photographs carry no georeferencing. Their findings are positions in the image only.",
    "video": "Videos carry no georeferencing. No camera position or footprint is inferred.",
}


def _display(session, asset: Asset) -> tuple[list[int], tuple, object, str | None]:
    """Bands, per-band stretch and the reason a raster cannot be drawn by default (None if it can)."""
    dtypes = asset.raster.dtypes
    kinds = {np.dtype(d).kind for d in dtypes}
    bands = [1, 2, 3] if asset.raster.band_count >= 3 else [1]
    if not kinds <= {"u", "i", "f"}:
        return bands, (), None, "This raster's pixel type cannot be drawn as a picture."
    try:
        resolved = resolve_display(session, asset, None, None, None, None)
    except ProblemError as exc:
        return bands, (), None, exc.detail or exc.title
    return list(resolved.bands), resolved.ranges, resolved.description, None


def _colormap_display(asset: Asset, colormap: str, text_range: str | None) -> SimpleNamespace:
    """The display for a single float band drawn through a colour ramp. Only the drawing changes: nothing is stored."""
    try:
        ramp = colormaps.get(colormap)
        low, high = colormaps.parse_range(text_range, ramp.default_range)
    except colormaps.ColormapError as exc:
        raise ProblemError(422, "COLORMAP_INVALID", "This colour ramp request is not valid", str(exc)) from exc
    raster = asset.raster
    if raster is None or raster.band_count != 1 or np.dtype(raster.dtypes[0]).kind != "f":
        raise ProblemError(
            422,
            "COLORMAP_NOT_APPLICABLE",
            "A colour ramp needs a single-band raster of real numbers",
            "Index rasters such as NDVI are single-band float32. This raster is not.",
        )
    return SimpleNamespace(bands=(1,), ranges=((low, high),), key=f"cmap:{colormap}:{low:g}:{high:g}")


def _pixel_size_m(state: georeference.Effective, latitude: float) -> float:
    size = min(state.pixel_size or [0.0])
    units = (state.pixel_units or "").lower()
    if "degree" in units:
        return size * 111_320 * max(0.01, math.cos(math.radians(latitude)))
    if "metre" in units or "meter" in units:
        return size
    if "foot" in units:
        return size * 0.3048
    return 0.0


def _exclusion(asset: Asset, state: georeference.Effective) -> tuple[str, str]:
    r = asset.raster
    if not state.crs_usable:
        if state.source == "recorded":
            return "CRS_MISSING", georeference.unplaced_reason(r, state) or ""
        if r.crs_status == "missing":
            return "CRS_MISSING", "The file declares no coordinate reference system. None is assumed."
        return "CRS_INVALID", "The file's CRS cannot be used to give map positions."
    if not state.geotransform_usable:
        return "NO_GEOTRANSFORM", "There is no usable geotransform, so the pixels have no map position."
    return "INVALID_GEOREFERENCING", asset.unknowns.get("georeferencing", "The raster's georeferencing is not trusted.")


_recorded_operation = convert.recorded_operation  # the choice kept with a recorded georeferencing version


def _layer_transformation(
    state: georeference.Effective,
    chosen_id: str | None,
    recorded: bool,
    acknowledgement: transforms.Acknowledgement | None = None,
) -> tuple[LayerTransformationOut, transforms.Resolved | None, str | None]:
    """The layer's transformation summary, the operation to use (None if it cannot be used yet), and a reason when the
    layer must not be drawn at all (ballpark, a missing grid, no operation)."""
    crs = transforms.crs_from_wkt(state.crs_wkt)
    bounds = state.footprint.bounds if state.footprint is not None else None
    try:
        try:
            resolved = transforms.resolve(crs, GEOGRAPHIC, bounds, chosen_id, acknowledgement=acknowledgement)
        except transforms.TransformationError as exc:
            if exc.code != "TRANSFORMATION_UNKNOWN_OPERATION":
                raise
            # a stale choice is ignored
            resolved = transforms.resolve(crs, GEOGRAPHIC, bounds, None, acknowledgement=acknowledgement)
    except transforms.TransformationError as exc:
        plan = exc.plan
        candidates = [assessment.operation_out(o, plan, None) for o in plan.usable] if plan else []
        summary = LayerTransformationOut(
            status=plan.status if plan else "unavailable",
            classification=None,
            operation_id=None,
            accuracy_m=None,
            chosen_by=None,
            selection_required=exc.code == "TRANSFORMATION_SELECTION_REQUIRED",
            message=exc.detail if exc.code == "TRANSFORMATION_SELECTION_REQUIRED" else f"{exc.title}. {exc.detail}",
            candidates=candidates,
            missing_grids=plan.missing_grids() if plan else [],
            requires_acknowledgement=exc.code == transforms.ACK_REQUIRED_CODE,
        )
        return summary, None, None if summary.selection_required else summary.message
    operation = resolved.operation
    usable = resolved.plan.usable
    summary = LayerTransformationOut(
        status=resolved.plan.status,
        classification=operation.klass,
        operation_id=operation.id,
        accuracy_m=operation.accuracy_m,
        chosen_by="recorded" if recorded else ("selection" if chosen_id == operation.id else "only_option"),
        selection_required=False,
        message=assessment.PLAN_STATUS_TEXT[resolved.plan.status],
        candidates=[assessment.operation_out(o, resolved.plan, operation.id) for o in usable]
        if len(usable) > 1
        else [],
        missing_grids=resolved.plan.missing_grids(),
        requires_acknowledgement=operation.requires_acknowledgement,
        acknowledged=resolved.acknowledgement is not None,
    )
    return summary, resolved, None


@router.get("/projects/{project_id}/layers", response_model=LayersOut, responses=problem_responses(404))
def get_layers(
    project_id: uuid.UUID,
    session: SessionDep,
    operation: Annotated[
        list[str] | None,
        Query(
            max_length=50,
            description="Datum transformation chosen for a raster, as `asset_id:operation_id`. Repeat for several. "
            "Only needed where the CRS assessment says a choice is required. Nothing is stored.",
        ),
    ] = None,
):
    """Layers that are safe to place, and every asset that is not, with the reason."""
    project = session.get(Project, project_id)
    if project is None:
        raise ProblemError(404, "PROJECT_NOT_FOUND", "Project not found")
    selections = dict(item.split(":", 1) for item in (operation or []) if ":" in item)
    assets = (
        session.scalars(select(Asset).where(Asset.project_id == project_id).order_by(Asset.ingested_at, Asset.id))
        .unique()
        .all()
    )

    rasters: list[RasterLayerOut] = []
    excluded: list[ExcludedAssetOut] = []
    for asset in assets:
        if asset.modality != "overhead_raster":
            excluded.append(
                ExcludedAssetOut(
                    asset_id=asset.id,
                    title=asset.original_filename,
                    modality=asset.modality,
                    reason="NOT_GEOREFERENCED",
                    detail=NOT_PLACEABLE.get(asset.modality, "This kind of input has no georeferencing."),
                )
            )
            continue
        r = asset.raster
        sheet = mapsheet.layer_info(asset)
        if r is None:
            if sheet is not None:  # a PDF map sheet: kept and listed, never placed
                excluded.append(
                    ExcludedAssetOut(
                        asset_id=asset.id,
                        title=asset.original_filename,
                        modality=asset.modality,
                        reason="NOT_GEOREFERENCED",
                        detail=mapsheet.next_step(asset),
                        map_sheet=MapSheetLayerOut(**sheet),
                    )
                )
            continue
        state = georeference.effective(r)
        if not state.georeferenced or state.footprint is None:
            reason, detail = _exclusion(asset, state)
            excluded.append(
                ExcludedAssetOut(
                    asset_id=asset.id,
                    title=asset.original_filename,
                    modality=asset.modality,
                    reason=reason,
                    detail=detail,
                    georeferencing_can_be_recorded=True,
                    map_sheet=MapSheetLayerOut(**sheet) if sheet else None,
                )
            )
            continue
        if antimeridian.geometry_crosses(state.footprint):
            excluded.append(
                ExcludedAssetOut(
                    asset_id=asset.id,
                    title=asset.original_filename,
                    modality=asset.modality,
                    reason="ANTIMERIDIAN_CROSSING_UNSUPPORTED",
                    detail=antimeridian.AntimeridianUnsupported("Drawing this raster on the map").detail,
                    map_sheet=MapSheetLayerOut(**sheet) if sheet else None,
                )
            )
            continue
        # How the raster is carried to longitude/latitude: exact, one registered shift, or a choice that is still open.
        # The acknowledgement comes only from what was recorded with the georeferencing version in use.
        selection = convert.selection_for(r, state.source, selections.get(str(asset.id)))
        recorded_id = _recorded_operation(r) if state.source == "recorded" else None
        layer_tx, resolved, blocked_detail = _layer_transformation(
            state, selection.operation_id, recorded_id is not None, selection.acknowledgement
        )
        if blocked_detail is not None:
            excluded.append(
                ExcludedAssetOut(
                    asset_id=asset.id,
                    title=asset.original_filename,
                    modality=asset.modality,
                    reason="TRANSFORMATION_BLOCKED",
                    detail=blocked_detail,
                    georeferencing_can_be_recorded=True,
                    map_sheet=MapSheetLayerOut(**sheet) if sheet else None,
                )
            )
            continue
        outline = state.footprint
        outline_record = None
        if resolved is not None and resolved.operation.klass == "approximate" and state.geotransform:
            # Draw the outline through the operation that will be used, not through PROJ's default.
            redrawn = footprint_checked(
                Affine.from_gdal(*state.geotransform),
                r.width,
                r.height,
                transforms.crs_from_wkt(state.crs_wkt),
                operation_id=resolved.operation.id,
                acknowledgement=resolved.acknowledgement,
            )
            if redrawn.polygon is not None:
                outline, outline_record = redrawn.polygon, redrawn.record
        west, south, east, north = outline.bounds
        native = tiles.native_zoom(_pixel_size_m(state, (south + north) / 2), (south + north) / 2)
        bands, _stretch, stretch_info, problem = _display(session, asset)
        if layer_tx.selection_required and not problem:
            problem = layer_tx.message
        # Recorded georeferencing, or a derived raster computed from rasters placed that way.
        recorded = state.source == "recorded" or "CRS_USER_ASSERTED" in (asset.quality_flags or [])
        transformation = (
            (r.current_assertion.validation or {}).get("transformation")
            if recorded and r.current_assertion
            else outline_record or asset.extracted_metadata.get("footprint_transformation")
        )
        chosen_here = selections.get(str(asset.id)) if recorded_id is None else None
        rasters.append(
            RasterLayerOut(
                asset_id=asset.id,
                title=asset.original_filename,
                crs_id=state.crs_id,
                crs_name=state.crs_name,
                placement="user_asserted" if recorded else "trusted",
                georef_version=state.version,
                band_count=r.band_count,
                dtypes=r.dtypes,
                bounds_4326=[west, south, east, north],
                footprint_4326=mapping(outline),
                # The version in the address means a browser never shows a tile made with older metadata.
                tile_url=None
                if problem
                else f"/api/v1/assets/{asset.id}/tiles/{{z}}/{{x}}/{{y}}.png?v={state.version}"
                + (f"&operation={chosen_here}" if chosen_here and layer_tx.operation_id == chosen_here else ""),
                display_unavailable_reason=problem,
                min_zoom=0,
                max_zoom=min(tiles.MAX_ZOOM, native + 3),
                native_zoom=native,
                bands_shown=bands,
                display_stretch=stretch_info,
                pixel_size=state.pixel_size or [0, 0],
                pixel_units=state.pixel_units,
                footprint_transformation=transformation,
                transformation=layer_tx,
                map_sheet=MapSheetLayerOut(**sheet) if sheet else None,
            )
        )

    aoi = None
    if project.aoi_geom is not None:
        source = project.aoi_source or {}
        aoi = AoiLayerOut(
            geometry_4326=mapping(to_shape(project.aoi_geom)),
            entered_crs_id=source.get("crs_id"),
            note="Entered by a person as project context. It is not evidence of where any file was captured.",
        )

    with_location = session.scalar(
        select(func.count())
        .select_from(ObservationRecord)
        .where(
            ObservationRecord.project_id == project_id,
            ObservationRecord.record["geospatial"]["geometry"].isnot(None),
            func.jsonb_typeof(ObservationRecord.record["geospatial"]["geometry"]) != "null",
        )
    )
    total = session.scalar(
        select(func.count()).select_from(ObservationRecord).where(ObservationRecord.project_id == project_id)
    )
    return LayersOut(
        rasters=rasters,
        excluded=excluded,
        aoi=aoi,
        observations=ObservationLayerSummary(
            with_location=with_location or 0,
            without_location=(total or 0) - (with_location or 0),
            note="Only observations with an established geographic location can be drawn. Findings in ground "
            "photographs are positions in the image and are shown on the Ground Image Analysis screen.",
        ),
        basemap=BasemapOut(
            configured=False,
            detail="No offline basemap is configured (decision U-013 is open). Local layers are drawn on a plain "
            "background with a graticule; no tiles are requested from any online service.",
        ),
    )


@router.get(
    "/assets/{asset_id}/tiles/{z}/{x}/{y}.png",
    response_class=Response,
    responses={
        200: {"description": "A 256 x 256 RGBA PNG in Web Mercator", "content": {"image/png": {}}},
        204: {"description": "The raster does not cover this tile"},
        **problem_responses(404, 409),
    },
)
def get_tile(
    asset_id: uuid.UUID,
    session: SessionDep,
    z: Annotated[int, Path(ge=0, le=tiles.MAX_ZOOM)],
    x: Annotated[int, Path(ge=0)],
    y: Annotated[int, Path(ge=0)],
    bands: Annotated[str | None, Query(max_length=20, description="One band or three, e.g. 4,3,2")] = None,
    stretch: Annotated[str | None, Query(max_length=20)] = None,
    low: Annotated[str | None, Query(max_length=200)] = None,
    high: Annotated[str | None, Query(max_length=200)] = None,
    operation: Annotated[
        str | None,
        Query(
            max_length=100,
            description="The chosen datum transformation (an id from the CRS assessment). Needed only where several "
            "registered operations apply; georeferencing recorded by a person keeps its own choice.",
        ),
    ] = None,
    colormap: Annotated[
        str | None,
        Query(
            max_length=20,
            description="Draw a single-band float raster through this colour ramp (display only; currently `ndvi`). "
            "Nodata stays transparent.",
        ),
    ] = None,
    colormap_range: Annotated[
        str | None,
        Query(max_length=60, description="`low,high`: the values the ramp spans. Defaults to the ramp's own range."),
    ] = None,
):
    """A display tile of a georeferenced raster, reprojected from its own CRS. Never served for other rasters.

    The tile is drawn through an exact conversion, the single registered datum shift that applies, or the one you
    chose. A ballpark transformation, a missing grid, or an open choice between several operations is refused (409)."""
    asset = session.get(Asset, asset_id)
    if asset is None:
        raise ProblemError(404, "ASSET_NOT_FOUND", "Asset not found")
    state = georeference.effective(asset.raster) if asset.raster is not None else None
    if asset.modality != "overhead_raster" or state is None or not state.georeferenced:
        raise ProblemError(
            409,
            "CRS_UNRESOLVED",
            "This asset cannot be placed on a map",
            "It has no valid CRS and geotransform. It can be viewed in image space only.",
        )
    if x >= 2**z or y >= 2**z:
        raise ProblemError(404, "TILE_OUT_OF_RANGE", "The tile address is outside the tile grid")
    if antimeridian.geometry_crosses(state.footprint):
        antimeridian.refuse("Drawing tiles")
    # The choice may come from the request, the acknowledgement only from the recorded georeferencing version.
    selection = convert.selection_for(asset.raster, state.source, operation)
    try:
        display = transforms.resolve(
            transforms.crs_from_wkt(state.crs_wkt),
            tiles.WEB_MERCATOR,
            state.footprint.bounds if state.footprint is not None else None,
            selection.operation_id,
            acknowledgement=selection.acknowledgement,
        )
    except transforms.TransformationError as exc:
        raise ProblemError(
            exc.status, exc.code, exc.title, exc.detail, errors=_candidate_errors(exc.plan), context=exc.extra or None
        ) from exc
    blob = session.get(Blob, asset.blob_sha256)
    path = store.absolute_path(blob.storage_path) if blob else None
    if path is None or not path.is_file():
        raise ProblemError(500, "STORAGE_ERROR", "The stored original is missing")
    if colormap is not None:
        resolved = _colormap_display(asset, colormap, colormap_range)
    else:
        resolved = resolve_display(session, asset, bands, stretch, low, high)
    # A verified display copy (same pixels at full resolution, plus overviews) is read when one exists.
    source, source_key = display_source(session, asset, path)
    # Recorded metadata is passed explicitly; the cache key includes the version, so a change never reuses a tile.
    georef = (state.crs_wkt, tuple(state.geotransform)) if state.source == "recorded" else None
    key = f"{asset.id}:v{state.version}:{state.assertion_id}:{source_key}:{resolved.key}:op={display.operation.id}"
    try:
        png = tiles.cached_tile(
            key, str(source), resolved.bands, resolved.ranges, z, x, y, georef, display.pipeline, colormap
        )
    except tiles.TileError as exc:
        raise ProblemError(409, "CRS_UNRESOLVED", "This raster cannot be tiled", str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - reported per tile; the raster's stored state is not affected
        raise ProblemError(
            500, "TILE_RENDER_FAILED", "This tile could not be rendered", f"{type(exc).__name__}: {exc}"
        ) from exc
    headers = {"Cache-Control": "private, max-age=3600"}
    if png is None:
        return Response(status_code=204, headers=headers)
    return Response(png, media_type="image/png", headers=headers)


@router.get("/crs/transform", response_model=CrsTransformOut, tags=["crs"])
def transform_point(
    source: Annotated[str, Query(min_length=1, max_length=100, description="Authority code of the input CRS")],
    target: Annotated[str, Query(min_length=1, max_length=100, description="Authority code of the output CRS")],
    x: Annotated[float, Query(description="Easting or longitude (x first, whatever the authority axis order)")],
    y: Annotated[float, Query(description="Northing or latitude")],
    operation: Annotated[
        str | None,
        Query(
            max_length=100,
            description="The chosen datum transformation (an id from /crs/operations). Needed where several registered "
            "operations apply to the point.",
        ),
    ] = None,
    accept_ballpark: Annotated[
        bool,
        Query(
            description="Explicitly accept a rough ballpark transformation of unknown accuracy. Off by default; the "
            "result is then marked BALLPARK_ACCEPTED.",
        ),
    ] = False,
    acknowledge_operation: Annotated[
        str | None,
        Query(
            max_length=100,
            description="Acknowledge, for this request only, the datum shift that was refused with "
            "TRANSFORMATION_ACKNOWLEDGEMENT_REQUIRED: its operation id. Nothing is stored, and it never applies to "
            "stored rasters.",
        ),
    ] = None,
    acknowledge_accuracy_m: Annotated[
        float | None,
        Query(
            description="The accuracy that was shown for that shift (omit if none was stated). It must match, so an "
            "acknowledgement of one accuracy cannot be reused for another.",
        ),
    ] = None,
):
    """Convert one point between registered CRSs with the local PROJ database. Offline; nothing is guessed.

    The operation used is reported with its class (exact, approximate with a documented accuracy, or ballpark). A
    ballpark transformation is refused unless accepted, a missing datum-shift grid is reported, and several applicable
    operations must be chosen between (409 with the candidates)."""
    if not (math.isfinite(x) and math.isfinite(y)):
        raise ProblemError(422, "VALIDATION_ERROR", "Coordinates must be finite numbers")
    try:
        source_id, target_id = normalise_identifier(source), normalise_identifier(target)
        source_crs, target_crs = resolve(source_id), resolve(target_id)
    except CrsResolutionError as exc:
        raise ProblemError(422, "CRS_UNRESOLVED", "The CRS could not be resolved", str(exc)) from exc
    for crs in (source_crs, target_crs):
        if not (crs.is_geographic or crs.is_projected) or crs.is_compound:
            raise ProblemError(422, "CRS_UNSUITABLE", "Only 2D geographic or projected CRSs can be used here")
    by_request = (
        transforms.Acknowledgement(
            operation_id=acknowledge_operation,
            source_crs=transforms.crs_label(source_crs),
            target_crs=transforms.datum_label(target_crs),
            accuracy_m=acknowledge_accuracy_m,
            scope="request",
        )
        if acknowledge_operation
        else None
    )
    try:
        converted = convert.convert(
            source_crs,
            target_crs,
            x,
            y,
            operation_id=operation,
            accept_ballpark=accept_ballpark,
            acknowledgement=by_request,
        )
    except transforms.TransformationError as exc:
        problem = problem_for(exc)
        if exc.code == transforms.ACK_REQUIRED_CODE:
            shown = exc.extra["accuracy_m"]
            problem.detail = (
                f"{exc.detail} To use it in this calculator only, repeat the request with "
                f"acknowledge_operation={exc.extra['operation_id']}"
                + ("" if shown is None else f" and acknowledge_accuracy_m={shown:g}")
                + ". Nothing is stored."
            )
        raise problem from exc
    chosen = converted.resolved
    out_x, out_y = float(converted.x), float(converted.y)
    if chosen is None:  # the same CRS: nothing was converted
        chosen = transforms.resolve(source_crs, target_crs)
    used = chosen.operation
    warnings_out = []
    area = target_crs.area_of_use
    if area is not None:
        lon, lat = Transformer.from_crs(source_crs, "EPSG:4326", always_xy=True).transform(x, y)
        inside_lon = area.west <= lon <= area.east if area.west <= area.east else (lon >= area.west or lon <= area.east)
        if not (inside_lon and area.south <= lat <= area.north):
            warnings_out.append("OUTSIDE_AREA_OF_USE")
    accuracy = used.accuracy_m
    if accuracy is None:
        warnings_out.append("ACCURACY_NOT_STATED")
    if used.klass == "approximate":
        warnings_out.append("APPROXIMATE_TRANSFORMATION")
    if chosen.accepted_ballpark:
        warnings_out.append("BALLPARK_ACCEPTED")
    if chosen.acknowledgement is not None:
        warnings_out.append("ACKNOWLEDGED_BY_REQUEST")  # not stored, and not valid for any stored raster
    return CrsTransformOut(
        source_crs_id=source_id,
        target_crs_id=target_id,
        x=out_x,
        y=out_y,
        target_axes=[
            CrsAxis(abbreviation=a.abbrev, direction=a.direction, unit=a.unit_name) for a in target_crs.axis_info
        ],
        xy_axes=[
            CrsAxis(abbreviation=a.abbrev, direction=a.direction, unit=a.unit_name)
            for a in orientation.visual_axes(target_crs)
        ],
        order_note=orientation.order_note(target_crs),
        transformation={
            "definition": used.pipeline,
            "accuracy_m": accuracy,
            "classification": used.klass,
            "operation_id": used.id,
            "proj_version": pyproj.proj_version_str,
            "pyproj_version": pyproj.__version__,
            "network": "disabled",
        },
        warnings=warnings_out,
        operation=assessment.operation_out(used, chosen.plan, used.id),
    )
