"""The findings report: a read-only document built from the stored data of one project.

Generating a report is recorded in the audit log, like an export. It writes nothing else.
"""

import uuid

from fastapi import APIRouter, Response

from geopoc import report_summary, scene_description
from geopoc.api.deps import ProfileDep, RequestIdDep, SessionDep, record_audit
from geopoc.api.errors import ProblemError, problem_responses
from geopoc.db.models import Asset
from geopoc.report_summary import LlmError, LlmStatus, ReportSummaryDraft
from geopoc.reporting import FindingsReport, build_findings_report
from geopoc.scene_description import SceneDescriptionDraft

router = APIRouter(prefix="/api/v1", tags=["reports"], responses=problem_responses(422))


@router.get(
    "/projects/{project_id}/report",
    response_model=FindingsReport,
    responses=problem_responses(401, 404),
)
def get_findings_report(
    project_id: uuid.UUID, session: SessionDep, profile: ProfileDep, request_id: RequestIdDep, response: Response
):
    """Findings report for one project, built only from stored records.

    A value that is not stored is null and named in `unavailable_fields`; it is never defaulted or estimated. Photograph
    positions appear only where a person confirmed them. User-drawn map annotations are in their own `annotations`
    section, apart from the findings; deleted ones are marked `deleted`. Each request is recorded in the audit log.
    """
    report = build_findings_report(session, project_id, profile.display_name)
    record_audit(
        session,
        actor=profile,
        action="report.generate",
        entity_type="project",
        entity_id=project_id,
        request_id=request_id,
        details={
            "format": "json",
            "schema_version": report.schema_version,
            "observations": report.summary.observations_total,
            "assets": report.summary.assets_total,
            "photos_with_confirmed_location": report.summary.photos_confirmed_location,
            "annotations_active": report.summary.annotations_active,
            "annotations_deleted": report.summary.annotations_deleted,
        },
    )
    session.commit()
    response.headers["Cache-Control"] = "no-store"
    return report


@router.get("/report-summary/status", response_model=LlmStatus)
def report_summary_status():
    """Whether the local language model for the draft summary can be used. Loads nothing."""
    return report_summary.status()


@router.post(
    "/projects/{project_id}/report/summary",
    response_model=ReportSummaryDraft,
    responses=problem_responses(401, 404, 409, 502, 503, 504),
)
def write_report_summary(
    project_id: uuid.UUID, session: SessionDep, profile: ProfileDep, request_id: RequestIdDep, response: Response
):
    """A machine-written DRAFT summary of this project's findings report, from a local Qwen model.

    The facts are counted by code from the stored report; the model only words them. The draft is checked (numbers,
    photographs, forbidden language) before it is returned, is never stored, and is marked as machine-written and
    unreviewed. It never recommends or ranks anything. Each request is recorded in the audit log, without its text.
    """
    report = build_findings_report(session, project_id, profile.display_name)
    details = {
        "model": report_summary.get_settings().llm_model,
        "observations": report.summary.observations_total,
        "photos": report.summary.photos_total,
    }
    try:
        draft = report_summary.write_draft(report)
    except LlmError as error:
        record_audit(
            session,
            actor=profile,
            action="report.summary.refused",
            entity_type="project",
            entity_id=project_id,
            request_id=request_id,
            details={**details, "code": error.code},
        )
        session.commit()
        raise ProblemError(error.status, error.code, error.message) from error
    record_audit(
        session,
        actor=profile,
        action="report.summary.generate",
        entity_type="project",
        entity_id=project_id,
        request_id=request_id,
        details={**details, "attempts": draft.attempts, "machine_written": True},
    )
    session.commit()
    response.headers["Cache-Control"] = "no-store"
    return draft


@router.post(
    "/assets/{asset_id}/scene-description",
    response_model=SceneDescriptionDraft,
    responses=problem_responses(401, 404, 409, 502, 503, 504),
)
def write_scene_description(
    asset_id: uuid.UUID, session: SessionDep, profile: ProfileDep, request_id: RequestIdDep, response: Response
):
    """A machine-written DRAFT description of what a ground photograph shows, from the local Qwen vision model.

    The detector's counts for the photograph's latest analysis are worked out by code; the model looks at the preview
    picture and describes the scene and what is going on. The draft is checked (numbers, forbidden language) before it
    is returned, is never stored, and is marked as machine-written and unreviewed. Each request is recorded in the audit
    log, without its text.
    """
    asset = session.get(Asset, asset_id)
    if asset is None:
        raise ProblemError(404, "ASSET_NOT_FOUND", "Asset not found")
    if asset.modality != "ground_image":
        raise ProblemError(409, "NOT_A_PHOTOGRAPH", "Only ground photographs can be described")
    details = {"model": scene_description.get_settings().llm_model}
    try:
        draft = scene_description.describe(session, asset)
    except LlmError as error:
        record_audit(
            session,
            actor=profile,
            action="photo.scene_description.refused",
            entity_type="asset",
            entity_id=asset.id,
            request_id=request_id,
            details={**details, "code": error.code},
        )
        session.commit()
        raise ProblemError(error.status, error.code, error.message) from error
    record_audit(
        session,
        actor=profile,
        action="photo.scene_description.generate",
        entity_type="asset",
        entity_id=asset.id,
        request_id=request_id,
        details={**details, "attempts": draft.attempts, "machine_written": True},
    )
    session.commit()
    response.headers["Cache-Control"] = "no-store"
    return draft
