"""Non-AI raster tools (Phase 3b): band information and statistics, display settings and image-space rendering,
pixel values, distance and area measurement, display copies, and pixel comparison of aligned rasters.

Nothing here is AI analysis and nothing creates an observation. Originals are only read. Display settings change
only how pixels are drawn.
"""

import math
import uuid
import warnings
from typing import Annotated

import numpy as np
import rasterio
from affine import Affine
from fastapi import APIRouter, Query
from fastapi.responses import Response
from rasterio.errors import NotGeoreferencedWarning
from rasterio.windows import Window
from sqlalchemy import select
from sqlalchemy.orm import Session

from geopoc.api.deps import ProfileDep, RequestIdDep, SessionDep, record_audit
from geopoc.api.errors import ProblemError, problem_responses
from geopoc.api.schemas import (
    AlignmentOut,
    ComparisonOut,
    ComparisonRequest,
    DisplayOut,
    JobBrief,
    MeasureOut,
    MeasureRequest,
    PixelBandValue,
    PixelOut,
    RasterToolsOut,
)
from geopoc.api.transformation import OPERATION_DESCRIPTION, problem_for
from geopoc.config import get_settings
from geopoc.db.models import Artifact, Asset, AssetLineage, Blob, Job, Project
from geopoc.geo import antimeridian, compare, convert, display, displaycopy, georeference, measure, tiles
from geopoc.geo.transform import TransformationError
from geopoc.jobs import queue
from geopoc.rastertools import DISPLAY_KIND, artifact, display_source, statistics
from geopoc.storage import store

router = APIRouter(prefix="/api/v1", tags=["raster tools"], responses=problem_responses(422))
MAX_RENDER_SIDE = 2048


def _raster(session: Session, asset_id: uuid.UUID) -> Asset:
    asset = session.get(Asset, asset_id)
    if asset is None:
        raise ProblemError(404, "ASSET_NOT_FOUND", "Asset not found")
    if asset.modality != "overhead_raster" or asset.raster is None:
        raise ProblemError(409, "NOT_A_RASTER", "This tool works on rasters only")
    return asset


def _original(session: Session, asset: Asset):
    blob = session.get(Blob, asset.blob_sha256)
    path = store.absolute_path(blob.storage_path) if blob else None
    if path is None or not path.is_file():
        raise ProblemError(500, "STORAGE_ERROR", "The stored original is missing")
    return path


def _latest_job(session: Session, asset_id: uuid.UUID, kind: str) -> JobBrief | None:
    job = session.scalars(
        select(Job).where(Job.asset_id == asset_id, Job.kind == kind).order_by(Job.created_at.desc()).limit(1)
    ).first()
    if job is None:
        return None
    return JobBrief(
        id=job.id,
        status=job.status,
        error_code=job.error_code,
        error_message=job.error_message,
        created_at=job.created_at,
    )


def _preview_details(session: Session, asset_id: uuid.UUID) -> dict | None:
    preview = session.scalars(
        select(Artifact).where(Artifact.asset_id == asset_id, Artifact.kind == "preview", Artifact.variant == "view")
    ).first()
    return preview.details if preview else None


def resolve_display(session: Session, asset: Asset, bands, stretch, low, high) -> display.Display:
    try:
        return display.resolve(
            dtypes=asset.raster.dtypes,
            band_count=asset.raster.band_count,
            bands_text=bands,
            stretch=stretch,
            low_text=low,
            high_text=high,
            statistics=statistics(session, asset.id),
            preview_details=_preview_details(session, asset.id),
        )
    except display.DisplayError as exc:
        raise ProblemError(exc.status, exc.code, "These display settings cannot be used", str(exc)) from exc


def _declared_bands(asset: Asset) -> list[dict]:
    declared = (asset.extracted_metadata or {}).get("bands")
    if declared:
        return [{**b, "dtype": asset.raster.dtypes[b["band"] - 1]} for b in declared]
    # Assets ingested before Phase 3b recorded no band descriptions: say only what is known.
    return [
        {"band": i + 1, "dtype": d, "description": None, "color_interpretation": None, "units": None}
        for i, d in enumerate(asset.raster.dtypes)
    ]


@router.get("/assets/{asset_id}/raster-tools", response_model=RasterToolsOut, responses=problem_responses(404, 409))
def get_raster_tools(asset_id: uuid.UUID, session: SessionDep):
    """Band information as the file declares it, band statistics, and the display copy, with their job states."""
    asset = _raster(session, asset_id)
    copy = artifact(session, asset.id, DISPLAY_KIND)
    stats = statistics(session, asset.id)
    lineage = session.scalars(select(AssetLineage).where(AssetLineage.child_asset_id == asset.id)).all()
    source, _ = display_source(session, asset, _original(session, asset))
    return RasterToolsOut(
        asset_id=asset.id,
        width=asset.raster.width,
        height=asset.raster.height,
        band_count=asset.raster.band_count,
        dtypes=asset.raster.dtypes,
        bands=stats["bands"] if stats else _declared_bands(asset),
        statistics=stats,
        statistics_job=_latest_job(session, asset.id, "statistics"),
        display_copy={**copy.details, "created_at": copy.created_at.isoformat()} if copy else None,
        display_copy_needed=displaycopy.needs_copy(
            asset.raster.width,
            asset.raster.height,
            (asset.extracted_metadata or {}).get("overview_levels"),
            get_settings().display_copy_min_side,
        ),
        display_copy_job=_latest_job(session, asset.id, "display_copy"),
        display_source="original" if source == _original(session, asset) else "display_copy",
        derived_from=[
            {
                "asset_id": str(row.parent_asset_id),
                "role": row.role,
                "relation": row.relation,
                "tool": f"{row.tool} v{row.tool_version}",
                "parameters": row.parameters,
                "parent_checksum": row.parent_checksum,
                "parent_georef_version": row.parent_georef_version,
            }
            for row in lineage
        ],
        note="Band descriptions, colour interpretation and units are shown as the file declares them. What a band "
        "measures is never inferred from its number or values.",
    )


def _enqueue(session: Session, asset: Asset, kind: str, profile, request_id: str) -> JobBrief:
    active = session.scalars(
        select(Job).where(Job.asset_id == asset.id, Job.kind == kind, Job.status.in_(queue.ACTIVE))
    ).first()
    if active is not None:
        raise ProblemError(409, "JOB_ALREADY_ACTIVE", "This is already being computed")
    queue.enqueue(session, kind=kind, project_id=asset.project_id, asset_id=asset.id, created_by=profile.id)
    record_audit(
        session, actor=profile, action=f"job.{kind}", entity_type="asset", entity_id=asset.id, request_id=request_id
    )
    session.commit()
    return _latest_job(session, asset.id, kind)


@router.post(
    "/assets/{asset_id}/statistics",
    response_model=JobBrief,
    status_code=202,
    responses=problem_responses(401, 404, 409),
)
def request_statistics(asset_id: uuid.UUID, session: SessionDep, profile: ProfileDep, request_id: RequestIdDep):
    """Compute (or recompute) band statistics in the background."""
    return _enqueue(session, _raster(session, asset_id), "statistics", profile, request_id)


@router.post(
    "/assets/{asset_id}/display-copy",
    response_model=JobBrief,
    status_code=202,
    responses=problem_responses(401, 404, 409),
)
def request_display_copy(asset_id: uuid.UUID, session: SessionDep, profile: ProfileDep, request_id: RequestIdDep):
    """Build a verified display copy with overviews in the background. The original is not changed."""
    return _enqueue(session, _raster(session, asset_id), "display_copy", profile, request_id)


BandsParam = Annotated[str | None, Query(max_length=20, description="One band (grey) or three, e.g. 4,3,2")]
StretchParam = Annotated[str | None, Query(max_length=20)]
LowParam = Annotated[str | None, Query(max_length=200)]
HighParam = Annotated[str | None, Query(max_length=200)]


@router.get("/assets/{asset_id}/display", response_model=DisplayOut, responses=problem_responses(404, 409))
def get_display(
    asset_id: uuid.UUID,
    session: SessionDep,
    bands: BandsParam = None,
    stretch: StretchParam = None,
    low: LowParam = None,
    high: HighParam = None,
):
    """Check display settings and say exactly how values will be mapped to brightness."""
    resolved = resolve_display(session, _raster(session, asset_id), bands, stretch, low, high)
    return DisplayOut(
        bands=list(resolved.bands),
        mode=resolved.mode,
        ranges=[list(r) if r else None for r in resolved.ranges],
        description=resolved.description,
        note="Display only: stored values, statistics, measurements and exports are not changed.",
    )


@router.get(
    "/assets/{asset_id}/render.png",
    response_class=Response,
    responses={200: {"content": {"image/png": {}}}, **problem_responses(404, 409)},
)
def render(
    asset_id: uuid.UUID,
    session: SessionDep,
    bands: BandsParam = None,
    stretch: StretchParam = None,
    low: LowParam = None,
    high: HighParam = None,
    max_side: Annotated[int, Query(ge=64, le=MAX_RENDER_SIDE)] = 1280,
):
    """The whole raster in image space with the chosen bands and stretch. For looking only."""
    asset = _raster(session, asset_id)
    resolved = resolve_display(session, asset, bands, stretch, low, high)
    source, source_key = display_source(session, asset, _original(session, asset))
    try:
        png, width, height = tiles.cached_image(
            f"{asset.id}:{source_key}:{resolved.key}", str(source), resolved.bands, resolved.ranges, max_side
        )
    except Exception as exc:  # noqa: BLE001 - the picture fails, nothing stored changes
        raise ProblemError(
            500, "RENDER_FAILED", "The raster could not be drawn", f"{type(exc).__name__}: {exc}"
        ) from exc
    return Response(
        png,
        media_type="image/png",
        headers={"Cache-Control": "private, max-age=3600", "X-Render-Size": f"{width}x{height}"},
    )


@router.get("/assets/{asset_id}/pixel", response_model=PixelOut, responses=problem_responses(404, 409))
def get_pixel(
    asset_id: uuid.UUID,
    session: SessionDep,
    col: Annotated[int | None, Query(ge=0)] = None,
    row: Annotated[int | None, Query(ge=0)] = None,
    lon: Annotated[float | None, Query(ge=-180, le=180)] = None,
    lat: Annotated[float | None, Query(ge=-90, le=90)] = None,
    operation: Annotated[str | None, Query(max_length=100, description=OPERATION_DESCRIPTION)] = None,
):
    """The stored values of every band at one pixel, read from the original. By pixel, or by map position.

    Longitude/latitude to and from the raster's CRS follows the datum transformation policy: an exact conversion or
    the single registered shift is used, several shifts need `operation`, and a ballpark or missing-grid conversion
    is refused (by position) or leaves the longitude/latitude out (by pixel)."""
    asset = _raster(session, asset_id)
    raster = asset.raster
    state = georeference.effective(raster)
    transform = Affine.from_gdal(*state.geotransform) if state.geotransform_usable and state.geotransform else None
    # The operation may be chosen in the request; the acknowledgement only comes from the recorded georeferencing.
    selection = convert.selection_for(raster, state.source, operation)
    chosen, acknowledged = selection.operation_id, selection.acknowledgement
    crossing = antimeridian.geometry_crosses(state.footprint)
    if lon is not None and lat is not None:
        if not state.georeferenced or transform is None:
            raise ProblemError(409, "CRS_UNRESOLVED", "This raster has no map position", "Inspect it by pixel instead.")
        if crossing:
            antimeridian.refuse("Looking up a pixel by map position")
        try:
            point = convert.from_geographic(state.crs_wkt, lon, lat, operation_id=chosen, acknowledgement=acknowledged)
        except TransformationError as exc:
            raise problem_for(exc) from exc
        c, r = ~transform @ (point.x, point.y)
        col, row = math.floor(c), math.floor(r)
    if col is None or row is None:
        raise ProblemError(422, "VALIDATION_ERROR", "Give col and row, or lon and lat")
    if not (0 <= col < raster.width and 0 <= row < raster.height):
        raise ProblemError(404, "OUTSIDE_RASTER", "That position is outside the raster")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", NotGeoreferencedWarning)
        with rasterio.open(_original(session, asset)) as src:
            data = src.read(window=Window(col, row, 1, 1), masked=True)
            scales, offsets = src.scales, src.offsets
            units, descriptions = src.units, src.descriptions
    values = []
    for index in range(raster.band_count):
        cell = data[index, 0, 0]
        masked = bool(np.ma.getmaskarray(data)[index, 0, 0])
        raw = None if masked else cell.item()
        if isinstance(raw, float) and not math.isfinite(raw):
            raw, masked = None, True
        declared_scale = scales[index] if scales else 1.0
        declared_offset = offsets[index] if offsets else 0.0
        scaled = (
            raw * declared_scale + declared_offset
            if raw is not None and (declared_scale != 1.0 or declared_offset != 0.0)
            else None
        )
        values.append(
            PixelBandValue(
                band=index + 1,
                raw=raw,
                nodata=masked,
                scaled=scaled,
                units=(units[index] or None) if units else None,
                description=descriptions[index] or None,
            )
        )
    native = lon_lat = lon_lat_note = provenance = None
    if transform is not None:
        x, y = transform @ (col + 0.5, row + 0.5)
        native = {"x": x, "y": y, "crs_id": state.crs_id, "crs_usable": state.crs_usable, "point": "pixel centre"}
        if state.georeferenced and crossing:
            lon_lat_note = antimeridian.AntimeridianUnsupported("Giving the longitude and latitude of a pixel").detail
        elif state.georeferenced:
            try:
                converted = convert.to_geographic(
                    state.crs_wkt, x, y, operation_id=chosen, acknowledgement=acknowledged
                )
                lon_lat = [float(converted.x), float(converted.y)]
                provenance = converted.provenance
            except TransformationError as exc:
                lon_lat_note = convert.unavailable_reason(exc)
    return PixelOut(
        col=col,
        row=row,
        values=values,
        dtypes=raster.dtypes,
        native=native,
        lon_lat=lon_lat,
        lon_lat_note=lon_lat_note,
        transformation=provenance,
        georeferencing_source=state.source if state.georeferenced else None,
        note="Values are as stored in the original. A scaled value is shown only when the file declares a scale or "
        "offset; units only when the file declares them.",
    )


@router.post("/measurements", response_model=MeasureOut, responses=problem_responses(400, 404, 409))
def measure_points(body: MeasureRequest, session: SessionDep):
    """Measure a distance or an area between given points. Nothing is stored."""
    try:
        if body.space == "lonlat":
            result = measure.measure_lonlat(body.kind, body.points)
        else:
            if body.asset_id is None:
                raise ProblemError(422, "VALIDATION_ERROR", "Pixel measurements need the raster's asset_id")
            asset = _raster(session, body.asset_id)
            state = georeference.effective(asset.raster)
            if antimeridian.geometry_crosses(state.footprint):
                antimeridian.refuse("Measuring on this raster")
            selection = convert.selection_for(asset.raster, state.source, body.transformation_operation)
            result = measure.measure_pixels(
                body.kind,
                body.points,
                asset.raster.width,
                asset.raster.height,
                state,
                georeference.unplaced_reason(asset.raster, state),
                selection.operation_id,
                selection.acknowledgement,
            )
            if "CRS_USER_ASSERTED" in (asset.quality_flags or []) and "CRS_USER_ASSERTED" not in result.flags:
                result.flags.append("CRS_USER_ASSERTED")
    except measure.MeasureError as exc:
        raise ProblemError(422, exc.code, "The points cannot be measured", str(exc)) from exc
    return MeasureOut(**vars(result))


# --- pixel comparison -------------------------------------------------------------------------------------------------


def _alignment(session: Session, a: Asset, b: Asset) -> AlignmentOut:
    reasons = []
    if a.id == b.id:
        reasons.append("Choose two different rasters.")
    elif a.blob_sha256 == b.blob_sha256:
        reasons.append("The two files have identical content; their difference is zero everywhere.")
    if a.project_id != b.project_id:
        reasons.append("Both rasters must be in the same project.")
    if reasons:
        return AlignmentOut(aligned=False, reasons=reasons)
    check = compare.check_alignment(
        a.raster.width,
        a.raster.height,
        georeference.effective(a.raster),
        b.raster.width,
        b.raster.height,
        georeference.effective(b.raster),
    )
    pixels = a.raster.width * a.raster.height
    if check.aligned and pixels > get_settings().compare_max_pixels:
        return AlignmentOut(
            aligned=False,
            reasons=[f"The rasters have {pixels:,} pixels; the limit is {get_settings().compare_max_pixels:,}."],
        )
    return AlignmentOut(aligned=check.aligned, reasons=check.reasons)


@router.get(
    "/assets/{asset_id}/alignment/{other_id}", response_model=AlignmentOut, responses=problem_responses(404, 409)
)
def get_alignment(asset_id: uuid.UUID, other_id: uuid.UUID, session: SessionDep):
    """Whether two rasters are on the same grid, and if not, why. Nothing is resampled to make them fit."""
    return _alignment(session, _raster(session, asset_id), _raster(session, other_id))


@router.post(
    "/projects/{project_id}/comparisons",
    response_model=ComparisonOut,
    status_code=202,
    responses=problem_responses(400, 401, 404, 409),
)
def request_comparison(
    project_id: uuid.UUID, body: ComparisonRequest, session: SessionDep, profile: ProfileDep, request_id: RequestIdDep
):
    """Compute B − A for two aligned rasters in the background; the result is stored as a new derived raster."""
    a, b = _raster(session, body.asset_a), _raster(session, body.asset_b)
    if a.project_id != project_id:
        raise ProblemError(404, "ASSET_NOT_FOUND", "Raster A is not in this project")
    if body.band_a > a.raster.band_count or body.band_b > b.raster.band_count:
        raise ProblemError(422, "BAND_INVALID", "A band number is outside the raster's bands")
    alignment = _alignment(session, a, b)
    if not alignment.aligned:
        raise ProblemError(
            422,
            "NOT_ALIGNED",
            "These rasters cannot be compared pixel by pixel",
            " ".join(alignment.reasons),
            errors=[{"field": "alignment", "message": r} for r in alignment.reasons],
        )
    payload = {
        "asset_a": str(a.id),
        "asset_b": str(b.id),
        "band_a": body.band_a,
        "band_b": body.band_b,
        "reason": body.reason,
    }
    job = queue.enqueue(
        session, kind="compare", project_id=project_id, asset_id=b.id, payload=payload, created_by=profile.id
    )
    record_audit(
        session, actor=profile, action="job.compare", entity_type="asset", entity_id=b.id, request_id=request_id,
        details=payload,
    )  # fmt: skip
    session.commit()
    return _comparison_out(session, job)


def _comparison_out(session: Session, job: Job) -> ComparisonOut:
    result = job.result or {}
    derived = result.get("derived_asset_id")
    return ComparisonOut(
        job=JobBrief(
            id=job.id,
            status=job.status,
            error_code=job.error_code,
            error_message=job.error_message,
            created_at=job.created_at,
        ),
        asset_a=uuid.UUID(job.payload["asset_a"]),
        asset_b=uuid.UUID(job.payload["asset_b"]),
        band_a=int(job.payload["band_a"]),
        band_b=int(job.payload["band_b"]),
        derived_asset_id=uuid.UUID(derived) if derived else None,
        summary={k: v for k, v in result.items() if k != "derived_asset_id"} or None,
    )


@router.get("/projects/{project_id}/comparisons", response_model=list[ComparisonOut], responses=problem_responses(404))
def list_comparisons(project_id: uuid.UUID, session: SessionDep):
    """Pixel comparisons requested in this project, newest first, with their job state and result."""
    # Like every other project route: an unknown project is a 404, not an empty list (found by the contract tests).
    if session.get(Project, project_id) is None:
        raise ProblemError(404, "PROJECT_NOT_FOUND", "Project not found")
    jobs = session.scalars(
        select(Job)
        .where(Job.project_id == project_id, Job.kind == "compare")
        .order_by(Job.created_at.desc())
        .limit(100)
    ).all()
    return [_comparison_out(session, job) for job in jobs]
