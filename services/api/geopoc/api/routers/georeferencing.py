"""Georeferencing recorded by a person for rasters whose own metadata is missing or invalid.

The original file is never written. Each accepted change is a new append-only version with its validation results;
a refused change stores nothing and is written to the audit log with the reasons. Local profiles attribute changes;
they are not authentication.
"""

import uuid
from dataclasses import asdict

from fastapi import APIRouter
from sqlalchemy import select
from sqlalchemy.orm import Session

from geopoc.api.deps import ProfileDep, RequestIdDep, SessionDep, record_audit
from geopoc.api.errors import ProblemError, problem_responses
from geopoc.api.schemas import (
    GeoreferenceCheck,
    GeoreferenceFileMetadata,
    GeoreferenceOptions,
    GeoreferenceState,
    GeoreferenceValidation,
    GeoreferenceVersionOut,
    GeoreferencingApply,
    GeoreferencingOut,
    GeoreferencingRequest,
)
from geopoc.db.models import Asset, GeorefAssertion
from geopoc.geo import assessment, georeference
from geopoc.geo.georeference import Effective, GeoreferenceError, Proposal

router = APIRouter(prefix="/api/v1", tags=["georeferencing"], responses=problem_responses(422))

NOTE = (
    "Recording a CRS or geotransform tells the application how to interpret the raster's pixels. It does not change "
    "the file, reproject or resample any pixel, or show that the location is correct: a valid CRS can still be the "
    "wrong one. Rasters placed this way are labelled as placed from recorded georeferencing."
)
EXPLANATIONS = {
    "assign_crs": "The raster's existing coordinates are kept exactly. The CRS says what those numbers mean "
    "(which datum and projection). No pixel or coordinate is changed or moved.",
    "set_geotransform": "The six numbers say where each pixel is, in the chosen CRS. They are used as entered; "
    "nothing is fitted, estimated or adjusted, and the file is not changed.",
    "revert_to_file": "The metadata in the file is used again. Earlier versions stay in the history.",
}


def _asset(session: Session, asset_id: uuid.UUID) -> Asset:
    asset = session.get(Asset, asset_id)
    if asset is None:
        raise ProblemError(404, "ASSET_NOT_FOUND", "Asset not found")
    if asset.modality != "overhead_raster" or asset.raster is None:
        raise ProblemError(409, "NOT_A_RASTER", "Only rasters can be georeferenced")
    return asset


def _state(state: Effective) -> GeoreferenceState:
    snapshot = state.snapshot()
    return GeoreferenceState(**{k: v for k, v in snapshot.items() if k in GeoreferenceState.model_fields})


def _out(session: Session, asset: Asset) -> GeoreferencingOut:
    raster = asset.raster
    session.refresh(raster)
    current = georeference.effective(raster)
    versions = session.scalars(
        select(GeorefAssertion).where(GeorefAssertion.asset_id == asset.id).order_by(GeorefAssertion.version_no)
    ).all()
    options = georeference.options(raster, current)
    return GeoreferencingOut(
        asset_id=asset.id,
        source_checksum=asset.blob_sha256,
        width=raster.width,
        height=raster.height,
        file=GeoreferenceFileMetadata(
            crs_status=raster.crs_status,
            crs_identifier=raster.crs_identifier,
            crs_name=raster.crs_name,
            geotransform=list(raster.geotransform) if raster.geotransform else None,
            geotransform_problem=georeference.geotransform_problem(raster.geotransform)
            if raster.pixel_size_x is None or raster.geotransform is None
            else None,
            georeferenced=raster.georeferenced,
            ground_control_points=int((asset.extracted_metadata or {}).get("ground_control_points") or 0),
            unknowns={
                k: v
                for k, v in (asset.unknowns or {}).items()
                if k
                in ("crs", "crs_identifier", "geotransform", "georeferencing", "ground_control_points", "pixel_units")
            },
        ),
        current=_state(current),
        unplaced_reason=georeference.unplaced_reason(raster, current),
        options=GeoreferenceOptions(**asdict(options)),
        history=[
            GeoreferenceVersionOut(
                version_no=v.version_no,
                operation=v.operation,
                method=v.method,
                crs_id=v.crs_id,
                crs_name=v.crs_name,
                geotransform=v.geotransform,
                geotransform_source=v.geotransform_source,
                world_file=v.world_file,
                reason=v.reason,
                previous=v.previous,
                result=v.result,
                validation=v.validation,
                georeferenced=v.georeferenced,
                source_checksum=v.source_checksum,
                actor_label=v.actor_label,
                created_at=v.created_at,
            )
            for v in versions
        ],
        note=NOTE,
    )


def _proposal(body: GeoreferencingRequest) -> Proposal:
    return Proposal(
        operation=body.operation,
        crs_id=body.crs_id,
        geotransform=body.geotransform,
        world_file=body.world_file,
        geotransform_source=body.geotransform_source,
        reason=body.reason,
        transformation_operation=body.transformation_operation,
        acknowledge_transformation=body.acknowledge_transformation,
    )


@router.get(
    "/assets/{asset_id}/georeferencing", response_model=GeoreferencingOut, responses=problem_responses(404, 409)
)
def get_georeferencing(asset_id: uuid.UUID, session: SessionDep):
    """What the file declares, what is used now, why it is or is not placed, what can be done, and the history."""
    return _out(session, _asset(session, asset_id))


@router.post(
    "/assets/{asset_id}/georeferencing/validate",
    response_model=GeoreferenceValidation,
    responses=problem_responses(400, 404, 409),
)
def validate_georeferencing(asset_id: uuid.UUID, body: GeoreferencingRequest, session: SessionDep):
    """Check a proposed change and show its result. Nothing is stored."""
    asset = _asset(session, asset_id)
    if asset.raster.georeferenced and body.operation != "revert_to_file":
        raise ProblemError(
            409,
            "ALREADY_GEOREFERENCED",
            "This raster's own georeferencing is valid",
            "The CRS and geotransform in the file are used. They are not overridden.",
        )
    outcome = georeference.validate(asset, asset.raster, _proposal(body), georeference.original_path(session, asset))
    return GeoreferenceValidation(
        valid=outcome.ok,
        checks=[GeoreferenceCheck(**asdict(c)) for c in outcome.checks],
        proposed=_state(outcome.result) if outcome.result is not None else None,
        corners=outcome.corners,
        method=outcome.method,
        explanation=EXPLANATIONS[body.operation],
        transformation_plan=assessment.plan_summary(outcome.transformation_plan, body.transformation_operation)
        if outcome.transformation_plan is not None
        else None,
    )


@router.post(
    "/assets/{asset_id}/georeferencing",
    response_model=GeoreferencingOut,
    status_code=201,
    responses=problem_responses(400, 401, 404, 409, 422),
)
def record_georeferencing(
    asset_id: uuid.UUID, body: GeoreferencingApply, session: SessionDep, profile: ProfileDep, request_id: RequestIdDep
):
    """Record a reviewed change as a new version. It is validated again here; nothing is stored unless it passes."""
    asset = _asset(session, asset_id)
    if not body.confirm:
        raise ProblemError(
            422, "CONFIRMATION_REQUIRED", "Review the proposed change and confirm it", "Nothing was recorded."
        )
    try:
        assertion, outcome = georeference.apply(
            session,
            asset.id,
            _proposal(body),
            expected_version=body.expected_version,
            actor=profile,
            request_id=request_id,
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
                    "operation": body.operation,
                    "crs_id": body.crs_id,
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
        action=f"georeference.{body.operation}",
        entity_type="asset",
        entity_id=asset.id,
        request_id=request_id,
        details={
            "version": assertion.version_no,
            "method": assertion.method,
            "crs_id": assertion.crs_id,
            "georeferenced": assertion.georeferenced,
            "warnings": [c.code for c in outcome.checks if c.status == "warning"],
        },
    )
    session.commit()
    return _out(session, asset)
