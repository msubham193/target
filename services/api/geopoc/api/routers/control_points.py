"""Georeferencing a scanned map sheet from control points a person supplies.

The same discipline as the other georeferencing routes: the original is never written, a refused change stores nothing
(and is audited), an accepted one is a new append-only version with its validation and the evidence for it. The points,
their CRS and their source are the person's; the application only fits and measures. See `geopoc.geo.controlpoints`.
"""

import uuid
from dataclasses import asdict

import numpy as np
import pyproj
from fastapi import APIRouter
from sqlalchemy.orm import Session

from geopoc.api.deps import ProfileDep, RequestIdDep, SessionDep, record_audit
from geopoc.api.errors import ProblemError, problem_responses
from geopoc.api.routers.georeferencing import EXPLANATIONS, _out, _state
from geopoc.api.schemas import (
    ControlPointFitOut,
    ControlPointResidualOut,
    ControlPointsApply,
    ControlPointsRequest,
    ControlPointsValidation,
    GeoreferenceCheck,
    GeoreferenceValidation,
    GeoreferencingOut,
)
from geopoc.db.models import Asset
from geopoc.geo import assessment, controlpoints, georeference
from geopoc.geo.crs import CrsResolutionError, resolve
from geopoc.geo.georeference import GeoreferenceError, Proposal

router = APIRouter(prefix="/api/v1", tags=["georeferencing"], responses=problem_responses(422))

EXPLANATION = (
    "The six affine numbers are fitted to your control points by least squares, and each point's residual is shown. "
    "Nothing is placed from the series, the file name or the picture. The fit uses one affine map in the CRS you "
    "chose: "
    "if the sheet is printed in a different projection, residuals will not be zero and say so. The file is not changed."
)


def _asset(session: Session, asset_id: uuid.UUID) -> Asset:
    asset = session.get(Asset, asset_id)
    if asset is None:
        raise ProblemError(404, "ASSET_NOT_FOUND", "Asset not found")
    if asset.modality != "overhead_raster" or asset.raster is None:
        raise ProblemError(409, "NOT_A_RASTER", "Only rasters can be georeferenced with control points")
    return asset


def _crs(identifier: str) -> pyproj.CRS:
    try:
        crs = resolve(identifier)
    except CrsResolutionError as exc:
        raise ProblemError(422, "CRS_UNRESOLVED", "The control point CRS is not usable", str(exc)) from exc
    if crs.is_compound or not (crs.is_geographic or crs.is_projected) or len(crs.axis_info) != 2:
        raise ProblemError(
            422,
            "CRS_UNRESOLVED",
            "The control point CRS is not usable",
            f"{identifier} is not a 2D geographic or projected CRS.",
        )
    return crs


def _points(body: ControlPointsRequest) -> list[controlpoints.Point]:
    return [controlpoints.Point(p.label, p.column, p.row, p.x, p.y) for p in body.points]


def _source_text(body: ControlPointsRequest, count: int) -> str:
    return f"{count} control points entered by a person. Source of coordinates: {body.source}"


def _proposal(body: ControlPointsRequest, geotransform: list[float]) -> Proposal:
    return Proposal(
        operation="set_geotransform",
        crs_id=body.crs_id,
        geotransform=geotransform,
        geotransform_source=_source_text(body, len(body.points)),
        reason=body.reason,
        transformation_operation=body.transformation_operation,
        acknowledge_transformation=body.acknowledge_transformation,
    )


def _fit_out(result: controlpoints.Fit) -> ControlPointFitOut:
    return ControlPointFitOut(
        method=controlpoints.METHOD,
        point_count=len(result.residuals),
        geotransform=result.geotransform,
        rms_pixels=result.rms_pixels,
        max_pixels=result.max_pixels,
        rms_map_units=result.rms_map_units,
        map_units=result.map_units,
        pixel_size=result.pixel_size,
        rotation_degrees=result.rotation_degrees,
        scale_ratio=result.scale_ratio,
        redundant=result.redundant,
        rms_m=result.rms_m,
        max_m=result.max_m,
        rms_m_allowing_for_fit=result.rms_m_dof,
        exact_fit=not result.redundant,
        ground_pixel_m=result.ground_pixel_m,
        ground_resolution_m=result.ground_resolution_m,
        shear_degrees=result.shear_degrees,
        shear_effect_m=result.shear_effect_m,
        handedness=result.handedness,
        rotation_reference=result.rotation_reference,
        tolerance=result.tolerance,
        leave_one_out={k: v for k, v in (result.leave_one_out or {}).items() if k != "per_point"},
        corner_uncertainty_m=result.corner_uncertainty_m,
        corners=result.corners,
        residuals=[ControlPointResidualOut(**r) for r in result.residuals],
        note="Least-squares affine fit: x = a + b·column + c·row, y = d + e·column + f·row. Residuals are the distance "
        "between each point and where the fit puts it.",
    )


def _checks(checks) -> list[GeoreferenceCheck]:
    return [GeoreferenceCheck(**asdict(c)) for c in checks]


def _reject_if_placed(asset: Asset) -> None:
    if asset.raster.georeferenced:
        raise ProblemError(
            409,
            "ALREADY_GEOREFERENCED",
            "This sheet's own georeferencing is valid",
            "The CRS and geotransform in the file are used. Control points do not override them.",
        )


@router.post(
    "/assets/{asset_id}/control-points/validate",
    response_model=ControlPointsValidation,
    responses=problem_responses(400, 404, 409, 422),
)
def validate_control_points(asset_id: uuid.UUID, body: ControlPointsRequest, session: SessionDep):
    """Fit the points and run every georeferencing check on the result. Nothing is stored."""
    asset = _asset(session, asset_id)
    _reject_if_placed(asset)
    crs = _crs(body.crs_id)
    raster = asset.raster
    result = controlpoints.fit(
        _points(body),
        raster.width,
        raster.height,
        crs,
        reference_uncertainty_m=body.reference_uncertainty_m,
        source_uncertainty_m=body.source_uncertainty_m,
    )
    assert result is not None
    if not result.geotransform:
        return ControlPointsValidation(
            valid=False, fit=None, checks=_checks(result.checks), validation=None, explanation=EXPLANATION
        )
    outcome = georeference.validate(
        asset, raster, _proposal(body, result.geotransform), georeference.original_path(session, asset)
    )
    checks = [*result.checks, *outcome.checks]
    valid = outcome.ok and result.ok
    return ControlPointsValidation(
        valid=valid,
        fit=_fit_out(result),
        checks=_checks(checks),
        validation=GeoreferenceValidation(
            valid=outcome.ok,
            checks=_checks(outcome.checks),
            proposed=_state(outcome.result) if outcome.result is not None else None,
            corners=outcome.corners,
            method=outcome.method,
            explanation=EXPLANATIONS["set_geotransform"],
            transformation_plan=assessment.plan_summary(outcome.transformation_plan, body.transformation_operation)
            if outcome.transformation_plan is not None
            else None,
        ),
        explanation=EXPLANATION,
    )


@router.post(
    "/assets/{asset_id}/control-points",
    response_model=GeoreferencingOut,
    status_code=201,
    responses=problem_responses(400, 401, 404, 409, 422),
)
def record_control_points(
    asset_id: uuid.UUID, body: ControlPointsApply, session: SessionDep, profile: ProfileDep, request_id: RequestIdDep
):
    """Record a reviewed control-point georeferencing as a new version. Fitted and validated again here."""
    asset = _asset(session, asset_id)
    if not body.confirm:
        raise ProblemError(422, "CONFIRMATION_REQUIRED", "Review the fit and confirm it", "Nothing was recorded.")
    crs = _crs(body.crs_id)
    raster = asset.raster
    result = controlpoints.fit(
        _points(body),
        raster.width,
        raster.height,
        crs,
        reference_uncertainty_m=body.reference_uncertainty_m,
        source_uncertainty_m=body.source_uncertainty_m,
    )
    assert result is not None
    if not result.geotransform or not result.ok:
        raise ProblemError(
            422,
            "CONTROL_POINTS_INVALID",
            "The control points did not pass validation",
            "Nothing was recorded; the sheet keeps its current state.",
            errors=[{"field": c.code, "message": c.message} for c in result.checks if c.status == "failed"],
        )
    evidence = controlpoints.evidence(
        result,
        crs_id=body.crs_id,
        source=body.source,
        software=f"numpy {np.__version__}, pyproj {pyproj.__version__}",
    )
    try:
        assertion, outcome = georeference.apply(
            session,
            asset.id,
            _proposal(body, result.geotransform),
            expected_version=body.expected_version,
            actor=profile,
            request_id=request_id,
            extra_checks=result.checks,
            control_points=evidence,
        )
    except GeoreferenceError as exc:
        session.rollback()
        if exc.checks:
            record_audit(
                session,
                actor=profile,
                action="georeference.refused",
                entity_type="asset",
                entity_id=asset.id,
                request_id=request_id,
                details={
                    "operation": "control_points",
                    "crs_id": body.crs_id,
                    "points": len(body.points),
                    "failed": [c.code for c in exc.checks if c.status == "failed"],
                },
            )
            session.commit()
        raise ProblemError(
            exc.status,
            exc.code,
            exc.title,
            exc.detail,
            errors=[{"field": c.code, "message": c.message} for c in exc.checks if c.status == "failed"],
        ) from exc
    record_audit(
        session,
        actor=profile,
        action="georeference.control_points",
        entity_type="asset",
        entity_id=asset.id,
        request_id=request_id,
        details={
            "version": assertion.version_no,
            "crs_id": assertion.crs_id,
            "points": len(body.points),
            "rms_pixels": round(result.rms_pixels, 4),
            "max_pixels": round(result.max_pixels, 4),
            "redundant": result.redundant,
            "source": body.source,
            "warnings": [c.code for c in outcome.checks if c.status == "warning"],
        },
    )
    session.commit()
    return _out(session, asset)
