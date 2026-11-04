"""Map-sheet intake and listing: Open Series (OSM) and Everest Series (ESM) sheets as GeoTIFF, scanned pictures or PDF.

A sheet is stored like any raster (original unmodified, metadata read from the file) with the uploader's declaration
kept
as attribution. The series never selects or implies a CRS: a sheet is placed only by its own file metadata, a sidecar a
person confirmed, or control points a person supplied. See `geopoc.ingestion.mapsheet`.
"""

import uuid
from functools import partial
from typing import Annotated

from fastapi import APIRouter, Query, Request
from sqlalchemy import select

from geopoc.api.deps import ProfileDep, RequestIdDep, SessionDep, record_audit
from geopoc.api.errors import ProblemError, problem_responses
from geopoc.api.routers.assets import BINARY_BODY, receive_upload
from geopoc.api.schemas import (
    MapSeries,
    MapSheetAssessmentOut,
    MapSheetInfo,
    MapSheetListOut,
    MapSheetNoteIn,
    MapSheetNoteOut,
    MapSheetSummaryOut,
    MapSheetUploadOut,
    OverlayAlignmentOut,
)
from geopoc.db.models import Asset, AuditEvent, Project, Sidecar, WorkspaceUser
from geopoc.geo import alignment, assessment, georeference, mapsheet, mapsheet_assessment
from geopoc.ingestion import mapsheet as intake
from geopoc.ingestion import rules

router = APIRouter(prefix="/api/v1", tags=["map-sheets"], responses=problem_responses(422))

LIST_NOTE = (
    "A map sheet is placed only by its own file metadata, a world file or .prj a person confirmed, or control points a "
    "person supplied. The series (OSM / ESM) is a label given at upload: it is not evidence of a CRS, a datum or a "
    "projection, and nothing is assigned from it."
)


@router.post(
    "/projects/{project_id}/map-sheets",
    response_model=MapSheetUploadOut,
    status_code=201,
    openapi_extra=BINARY_BODY,
    responses=problem_responses(400, 401, 404, 409, 413, 415, 500, 503, 507),
)
async def upload_map_sheet(
    project_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    profile: ProfileDep,
    request_id: RequestIdDep,
    filename: Annotated[str, Query(min_length=1, max_length=500, description="Original file name")],
    series: Annotated[
        MapSeries, Query(description="The series the uploader says this sheet belongs to. Attribution only.")
    ] = "unspecified",
    sheet_label: Annotated[
        str | None, Query(max_length=intake.MAX_LABEL, description="Sheet number or name as printed")
    ] = None,
    scale_denominator: Annotated[
        int | None, Query(ge=1, le=100_000_000, description="The N of a 1:N printed scale, if known")
    ] = None,
    edition: Annotated[str | None, Query(max_length=intake.MAX_EDITION)] = None,
):
    """Take in one map sheet: a GeoTIFF, a scanned TIFF / PNG / JPEG, or a PDF. The body is the raw file.

    The original is stored unmodified. A sheet whose file carries no valid georeferencing is stored unplaced; it can be
    georeferenced afterwards from a world file / .prj or from control points, each reviewed and recorded by a person.
    """
    declaration = intake.Declaration(
        series=series,
        label=(sheet_label or "").strip() or None,
        scale_denominator=scale_denominator,
        edition=(edition or "").strip() or None,
        declared_by=profile.display_name,
    )
    uploaded = await receive_upload(
        project_id,
        request,
        session,
        profile,
        request_id,
        modality="overhead_raster",
        filename=filename,
        accepted=intake.EXTENSIONS,
        label="Map sheets",
        inspector=partial(intake.inspect_map_sheet, declaration=declaration),
        context={"intake": "map_sheet", "series": series},
        intake={**declaration.record(), "format_family": rules.EXTENSION_FAMILY.get(rules.extension_of(filename))},
    )
    sheet = MapSheetInfo(**_info_of(session, uploaded.asset.id))
    return MapSheetUploadOut(
        asset=uploaded.asset,
        map_sheet=sheet,
        duplicate=uploaded.duplicate,
        duplicate_of_asset_id=uploaded.duplicate_of_asset_id,
        stored_original_reused=uploaded.stored_original_reused,
        message=uploaded.message,
    )


def _info_of(session, asset_id: uuid.UUID) -> dict:
    asset = session.get(Asset, asset_id)
    info = mapsheet.info(asset)
    assert info is not None
    return info


def summary(asset: Asset) -> MapSheetSummaryOut:
    info = mapsheet.info(asset)
    assert info is not None
    r = asset.raster
    return MapSheetSummaryOut(
        asset_id=asset.id,
        filename=asset.original_filename,
        info=MapSheetInfo(**info),
        crs_id=r.crs_identifier if r else None,
        crs_name=r.crs_name if r else None,
        crs_status=r.crs_status if r else None,
        width=r.width if r else None,
        height=r.height if r else None,
        quality_flags=asset.quality_flags,
        next_step=mapsheet.next_step(asset),
        ingested_at=asset.ingested_at,
    )


@router.get("/projects/{project_id}/map-sheets", response_model=MapSheetListOut, responses=problem_responses(404))
def list_map_sheets(project_id: uuid.UUID, session: SessionDep):
    """Every map sheet in the project, newest first, with how each is positioned and what it still needs."""
    if session.get(Project, project_id) is None:
        raise ProblemError(404, "PROJECT_NOT_FOUND", "Project not found")
    assets = (
        session.scalars(
            select(Asset)
            .where(Asset.project_id == project_id, Asset.modality == "overhead_raster")
            .order_by(Asset.ingested_at.desc(), Asset.id.desc())
        )
        .unique()
        .all()
    )
    return MapSheetListOut(items=[summary(a) for a in assets if mapsheet.is_map_sheet(a)], note=LIST_NOTE)


# --- metadata assessment and notes ------------------------------------------------------------------------------------

NOTE_ACTION = "map_sheet.metadata_note"


def _sheet(session, asset_id: uuid.UUID) -> Asset:
    asset = session.get(Asset, asset_id)
    if asset is None:
        raise ProblemError(404, "ASSET_NOT_FOUND", "Asset not found")
    if asset.modality != "overhead_raster" or not mapsheet.is_map_sheet(asset):
        raise ProblemError(
            409,
            "NOT_A_MAP_SHEET",
            "This asset was not taken in as a map sheet",
            "Upload it as a map sheet to assess it.",
        )
    return asset


def _notes(session, asset_id: uuid.UUID) -> list[dict]:
    rows = session.execute(
        select(AuditEvent, WorkspaceUser.display_name)
        .join(WorkspaceUser, WorkspaceUser.id == AuditEvent.actor_id, isouter=True)
        .where(AuditEvent.action == NOTE_ACTION, AuditEvent.entity_id == asset_id)
        .order_by(AuditEvent.at, AuditEvent.id)
    ).all()
    return [
        {
            "id": event.id,
            "field": event.details.get("field"),
            "value": event.details.get("value"),
            "source": event.details.get("source"),
            "status": event.details.get("status", "unverified"),
            "recorded_by": name,
            "recorded_at": event.at,
            "applied": False,
        }
        for event, name in rows
    ]


@router.get("/assets/{asset_id}/map-sheet", response_model=MapSheetAssessmentOut, responses=problem_responses(404, 409))
def get_map_sheet_assessment(asset_id: uuid.UUID, session: SessionDep):
    """What the sheet's file states about its datum, ellipsoid, projection, parameters, units, grid and extent, what it
    does not, what is therefore unresolved, and what is needed. Read-only: nothing is assigned or inferred."""
    asset = _sheet(session, asset_id)
    crs_assessment = None
    if asset.raster is not None:
        session.refresh(asset.raster)
        crs_assessment = assessment.assess(asset, asset.raster, georeference.effective(asset.raster), None)
    sidecars = list(
        session.scalars(select(Sidecar).where(Sidecar.asset_id == asset.id).order_by(Sidecar.uploaded_at, Sidecar.id))
    )
    return MapSheetAssessmentOut(
        **mapsheet_assessment.build(asset, crs_assessment, sidecars, _notes(session, asset.id))
    )


@router.post(
    "/assets/{asset_id}/map-sheet/metadata-notes",
    response_model=MapSheetNoteOut,
    status_code=201,
    responses=problem_responses(400, 401, 404, 409),
)
def record_metadata_note(
    asset_id: uuid.UUID, body: MapSheetNoteIn, session: SessionDep, profile: ProfileDep, request_id: RequestIdDep
):
    """Record what a person has learned about a sheet's unresolved metadata (for example a datum named in the sheet's
    margin), with where it comes from. It is kept in the append-only audit history and is never applied: it does not
    define a CRS, change a transformation or place the sheet."""
    asset = _sheet(session, asset_id)
    record_audit(
        session,
        actor=profile,
        action=NOTE_ACTION,
        entity_type="asset",
        entity_id=asset.id,
        request_id=request_id,
        details={"field": body.field, "value": body.value, "source": body.source, "status": body.status},
    )
    session.commit()
    latest = _notes(session, asset.id)[-1]
    return MapSheetNoteOut(**latest)


# --- alignment --------------------------------------------------------------------------------------------------------


@router.get(
    "/projects/{project_id}/alignment", response_model=OverlayAlignmentOut, responses=problem_responses(404, 409)
)
def get_alignment(
    project_id: uuid.UUID,
    session: SessionDep,
    first: uuid.UUID,
    second: uuid.UUID,
):
    """How two placed rasters (for example a map sheet and a satellite image) relate: overlap, how each is positioned
    and what each position is documented to be good to. Read-only."""
    if session.get(Project, project_id) is None:
        raise ProblemError(404, "PROJECT_NOT_FOUND", "Project not found")
    if first == second:
        raise ProblemError(422, "SAME_LAYER", "Choose two different layers")
    assets = []
    for asset_id in (first, second):
        asset = session.get(Asset, asset_id)
        if asset is None or asset.project_id != project_id:
            raise ProblemError(404, "ASSET_NOT_FOUND", "That layer is not in this project")
        if asset.modality != "overhead_raster" or asset.raster is None:
            raise ProblemError(409, "NOT_A_RASTER", "Only rasters can be compared", asset.original_filename)
        if not georeference.effective(asset.raster).georeferenced:
            raise ProblemError(
                409,
                "NOT_PLACED",
                "Both layers must be placed to be compared",
                f"{asset.original_filename} is not georeferenced.",
            )
        assets.append(asset)
    return OverlayAlignmentOut(**alignment.compare(assets[0], assets[1]))
