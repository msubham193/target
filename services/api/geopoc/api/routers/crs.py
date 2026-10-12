from typing import Annotated

import pyproj
from fastapi import APIRouter, Query

from geopoc.api.errors import ProblemError, problem_responses
from geopoc.api.schemas import CrsOut, CrsSearchResult, clean_text
from geopoc.geo import crs as crs_registry

router = APIRouter(prefix="/api/v1/crs", tags=["crs"], responses=problem_responses(422))


@router.get("", response_model=CrsSearchResult)
def search_crs(
    q: Annotated[str, Query(min_length=1, max_length=100, description="Authority code or words in the CRS name")],
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
):
    """Search the local PROJ registry. Works offline. A search hit is a candidate, never an assignment."""
    try:
        clean_text(q)
    except ValueError as exc:
        raise ProblemError(422, "VALIDATION_ERROR", "Request validation failed", str(exc)) from exc
    return CrsSearchResult(
        items=[CrsOut(**item) for item in crs_registry.search(q, limit)],
        registry=f"PROJ {pyproj.proj_version_str} database (local)",
    )
