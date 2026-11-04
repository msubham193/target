from typing import Annotated

import pyproj
from fastapi import APIRouter, Query

from geopoc.api.errors import ProblemError, problem_responses
from geopoc.api.schemas import CrsOperationsOut, CrsOut, CrsSearchResult, clean_text
from geopoc.geo import assessment
from geopoc.geo import crs as crs_registry
from geopoc.geo import transform as transforms

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


@router.get("/operations", response_model=CrsOperationsOut, responses=problem_responses(422))
def list_operations(
    source: Annotated[str, Query(min_length=1, max_length=100, description="Authority code of the input CRS")],
    target: Annotated[str, Query(min_length=1, max_length=100, description="Authority code of the output CRS")],
    west: Annotated[
        float | None, Query(ge=-180, le=180, description="Optional area (degrees); narrows the list")
    ] = None,
    south: Annotated[float | None, Query(ge=-90, le=90)] = None,
    east: Annotated[float | None, Query(ge=-180, le=180)] = None,
    north: Annotated[float | None, Query(ge=-90, le=90)] = None,
):
    """Every coordinate operation the local PROJ database offers between two registered CRSs, each classified as exact,
    approximate (with its documented accuracy), ballpark or unavailable (a datum-shift grid is missing). Read-only.
    Without an area, every registered operation for the pair is listed, so the list can be long."""
    given = [v is not None for v in (west, south, east, north)]
    if any(given) and not all(given):
        raise ProblemError(422, "VALIDATION_ERROR", "Give all four of west, south, east and north, or none")
    bounds = (west, south, east, north) if all(given) else None  # type: ignore[arg-type]
    if bounds is not None and not (west < east and south < north):  # type: ignore[operator]
        raise ProblemError(422, "VALIDATION_ERROR", "The area must have west < east and south < north")
    try:
        source_id, target_id = crs_registry.normalise_identifier(source), crs_registry.normalise_identifier(target)
        source_crs, target_crs = crs_registry.resolve(source_id), crs_registry.resolve(target_id)
    except crs_registry.CrsResolutionError as exc:
        raise ProblemError(422, "CRS_UNRESOLVED", "The CRS could not be resolved", str(exc)) from exc
    for crs in (source_crs, target_crs):
        if not (crs.is_geographic or crs.is_projected) or crs.is_compound:
            raise ProblemError(422, "CRS_UNSUITABLE", "Only 2D geographic or projected CRSs can be used here")
    plan = transforms.plan(source_crs, target_crs, bounds)
    used = None
    if bounds is not None:
        used = {"west": bounds[0], "south": bounds[1], "east": bounds[2], "north": bounds[3]}
    return CrsOperationsOut(
        plan=assessment.plan_summary(plan),
        area_used=used,
        accuracy_notes=list(transforms.ACCURACY_NOTES),
        class_explanations=transforms.KLASS_EXPLANATION,
        versions={"proj": pyproj.proj_version_str, "pyproj": pyproj.__version__, "network": "disabled"},
    )
