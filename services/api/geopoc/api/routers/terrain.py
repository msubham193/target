"""Height grids of a project's rasters for the local 3D view. Read-only: nothing is stored, queued or audited here.

Everything is scoped to a project and one of its rasters. A raster that is not elevation data (RGB imagery, 8-bit
pictures,
multi-band imagery) is never offered as terrain, and nothing is generated where elevation is absent.
"""

import hashlib
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Query
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from geopoc.api.deps import SessionDep
from geopoc.api.errors import ProblemError, problem_responses
from geopoc.api.schemas import TerrainBandOut, TerrainListOut, TerrainOut, TerrainRasterOut
from geopoc.api.transformation import OPERATION_DESCRIPTION
from geopoc.config import get_settings
from geopoc.db.models import Asset, Blob, Project
from geopoc.geo import terrain
from geopoc.geo.terrain import TerrainError
from geopoc.storage import store

router = APIRouter(prefix="/api/v1/projects/{project_id}", tags=["terrain"], responses=problem_responses(404, 409, 422))

DEFAULT_GRID_SIDE = 256
NOTE = (
    "Only rasters that are georeferenced and have a band declared (or confirmed by a person) as elevation can be "
    "drawn as "
    "terrain. Colour imagery is never elevation. Nothing is generated where elevation is missing."
)
GRID_FORMAT = (
    "float32, little-endian, row-major, north row first, west column first; NaN means no data. Values are in the "
    "unit given "
    "under `elevation`."
)

UnitName = Literal["metre", "foot", "us_survey_foot"]


def _project(session: Session, project_id: uuid.UUID) -> None:
    if session.get(Project, project_id) is None:
        raise ProblemError(404, "PROJECT_NOT_FOUND", "Project not found")


def _raster(session: Session, project_id: uuid.UUID, asset_id: uuid.UUID) -> tuple[Asset, object]:
    _project(session, project_id)
    asset = session.get(Asset, asset_id)
    if asset is None or asset.project_id != project_id:
        raise ProblemError(404, "ASSET_NOT_FOUND", "Asset not found in this project")
    if asset.modality != "overhead_raster" or asset.raster is None:
        raise ProblemError(409, "NOT_A_RASTER", "Only rasters can be shown as terrain")
    blob = session.get(Blob, asset.blob_sha256)
    path = store.absolute_path(blob.storage_path) if blob else None
    if path is None or not path.is_file():
        raise ProblemError(500, "STORAGE_ERROR", "The stored original is missing")
    return asset, path


@router.get("/terrain", response_model=TerrainListOut)
def list_terrain(project_id: uuid.UUID, session: SessionDep):
    """Every raster of the project, whether it can be drawn as terrain, and why or why not. Reads no pixels."""
    _project(session, project_id)
    settings = get_settings()
    assets = (
        session.scalars(
            select(Asset)
            .where(Asset.project_id == project_id, Asset.modality == "overhead_raster")
            .order_by(Asset.ingested_at, Asset.id)
        )
        .unique()
        .all()
    )
    items = []
    for asset in assets:
        if asset.raster is None:
            continue
        verdict = terrain.assess(asset, settings)
        items.append(
            TerrainRasterOut(
                asset_id=asset.id,
                filename=asset.original_filename,
                width=asset.raster.width,
                height=asset.raster.height,
                crs_id=asset.raster.crs_identifier,
                status=verdict.status,  # type: ignore[arg-type]
                reasons=verdict.reasons,
                bands=[
                    TerrainBandOut(
                        band=b.band,
                        dtype=b.dtype,
                        description=b.description,
                        colour_interpretation=b.colour,
                        declared_unit=b.unit,
                        role=b.role,  # type: ignore[arg-type]
                        reasons=b.reasons,
                    )
                    for b in verdict.bands
                ],
            )
        )
    return TerrainListOut(
        items=items,
        usable=sum(i.status == "usable" for i in items),
        needs_confirmation=sum(i.status == "needs_confirmation" for i in items),
        not_available=sum(i.status in ("not_elevation", "unusable") for i in items),
        max_grid_side=settings.terrain_max_grid_side,
        default_grid_side=DEFAULT_GRID_SIDE,
        note=NOTE,
    )


Band = Annotated[int | None, Query(ge=1, description="The band to draw. Defaults to the first elevation candidate.")]
Unit = Annotated[
    UnitName | None,
    Query(description="The elevation unit, stated for this view. Only accepted when the file declares none."),
]
Confirm = Annotated[
    bool, Query(description="A person's statement, for this view only, that the band is elevation. Not stored.")
]
Side = Annotated[int, Query(ge=16, le=512, description="The most cells per side of the grid.")]


def _build(session, project_id, asset_id, band, unit, confirm_elevation, max_side, operation=None):
    asset, path = _raster(session, project_id, asset_id)
    try:
        return asset, terrain.build_grid(
            path,
            asset,
            get_settings(),
            band=band,
            unit=unit,
            confirm_elevation=confirm_elevation,
            max_side=max_side,
            operation_id=operation,
        )
    except TerrainError as exc:
        raise ProblemError(exc.status, exc.code, "This raster cannot be shown as terrain", str(exc)) from exc


def _disclosures(meta: dict) -> list[str]:
    elevation, grid = meta["elevation"], meta["grid"]
    out = [
        f"The surface is a {grid['width']} x {grid['height']} grid made by taking every {grid['decimation']}th source "
        f"pixel "
        "(nearest pixel). Every value is a real source value; nothing is averaged, smoothed or filled in.",
        "No-data cells are left as holes. No terrain is generated for them.",
    ]
    if meta["horizontal"]["approximate"]:
        out.append(
            "The CRS is geographic (degrees), so distances are approximate: one metre size per degree is used across "
            "the "
            "whole area, at its centre latitude."
        )
    else:
        out.append("Horizontal distances are exact for the raster's projected CRS. The raster was not reprojected.")
    if elevation["unit_source"] == "stated_for_this_view":
        out.append(
            f"The file declares no elevation unit. {elevation['unit_label']} was stated by a person for this view "
            f"only; the "
            "software cannot check it."
        )
    if elevation["role_source"] == "confirmed_for_this_view":
        out.append(
            "The file does not say this band is elevation. A person confirmed it for this view only; the software "
            "cannot "
            "check it."
        )
    if elevation["declared_scale_offset_applied"]:
        out.append("The file's declared scale and offset were applied to the stored values.")
    if meta["vertical_datum"]["state"] == "not_stated":
        out.append("The vertical datum (what zero height means) is not stated by the file.")
    return out


@router.get("/assets/{asset_id}/terrain", response_model=TerrainOut, responses=problem_responses(500))
def get_terrain(
    project_id: uuid.UUID,
    asset_id: uuid.UUID,
    session: SessionDep,
    band: Band = None,
    unit: Unit = None,
    confirm_elevation: Confirm = False,
    max_side: Side = DEFAULT_GRID_SIDE,
    operation: Annotated[str | None, Query(max_length=100, description=OPERATION_DESCRIPTION)] = None,
):
    """What the 3D view needs to place and describe the surface: extent, cell size, units, statistics, disclosures.

    Its longitude/latitude position follows the datum transformation policy; when no transformation may be used the
    position is absent (`positioning` says why). Elevation values and the vertical datum are never touched."""
    asset, grid = _build(session, project_id, asset_id, band, unit, confirm_elevation, max_side, operation)
    meta = grid.meta
    query = f"band={meta['band']}&max_side={max_side}" + (f"&unit={unit}" if unit else "")
    query += "&confirm_elevation=true" if confirm_elevation else ""
    return TerrainOut(
        asset_id=asset.id,
        filename=asset.original_filename,
        source_checksum=asset.blob_sha256,
        disclosures=_disclosures(meta),
        grid_url=f"/api/v1/projects/{project_id}/assets/{asset.id}/terrain/grid?{query}",
        grid_format=GRID_FORMAT,
        **meta,
    )


@router.get(
    "/assets/{asset_id}/terrain/grid",
    response_class=Response,
    responses={
        200: {"description": GRID_FORMAT, "content": {"application/octet-stream": {}}},
        **problem_responses(500),
    },
)
def get_terrain_grid(
    project_id: uuid.UUID,
    asset_id: uuid.UUID,
    session: SessionDep,
    band: Band = None,
    unit: Unit = None,
    confirm_elevation: Confirm = False,
    max_side: Side = DEFAULT_GRID_SIDE,
):
    """The height grid itself, as raw float32 values (NaN = no data)."""
    asset, grid = _build(session, project_id, asset_id, band, unit, confirm_elevation, max_side)
    payload = grid.values.astype("<f4").tobytes()
    etag = hashlib.sha256(payload + asset.blob_sha256.encode()).hexdigest()
    return Response(
        payload,
        media_type="application/octet-stream",
        headers={
            "ETag": f'"{etag}"',
            "X-Grid-Width": str(grid.meta["grid"]["width"]),
            "X-Grid-Height": str(grid.meta["grid"]["height"]),
        },
    )
