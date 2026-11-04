"""Read-only CRS assessment of a raster: what its coordinate system is and what moving its coordinates involves.

Nothing is stored, queued, audited or changed here, and no CRS is ever assigned. See `geopoc.geo.assessment`.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Query

from geopoc.api.deps import SessionDep
from geopoc.api.errors import ProblemError, problem_responses
from geopoc.api.schemas import CrsAssessmentOut
from geopoc.db.models import Asset
from geopoc.geo import assessment, georeference

router = APIRouter(prefix="/api/v1", tags=["crs"], responses=problem_responses(404, 409, 422))


@router.get("/assets/{asset_id}/crs-assessment", response_model=CrsAssessmentOut)
def get_crs_assessment(
    asset_id: uuid.UUID,
    session: SessionDep,
    operation: Annotated[
        str | None,
        Query(
            min_length=1,
            max_length=100,
            description="Echo a chosen transformation id (from a previous assessment) to see what would then be used. "
            "Nothing is stored.",
        ),
    ] = None,
):
    """CRS details (datum, ellipsoid, projection and parameters, units), the eight statuses, and every transformation
    candidate with its class, documented accuracy and missing grids. Read-only."""
    asset = session.get(Asset, asset_id)
    if asset is None:
        raise ProblemError(404, "ASSET_NOT_FOUND", "Asset not found")
    if asset.modality != "overhead_raster" or asset.raster is None:
        raise ProblemError(409, "NOT_A_RASTER", "Only rasters have a CRS assessment")
    raster = asset.raster
    session.refresh(raster)
    state = georeference.effective(raster)
    return CrsAssessmentOut(**assessment.assess(asset, raster, state, operation))
