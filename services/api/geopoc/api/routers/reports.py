"""The findings report: a read-only document built from the stored data of one project.

Generating a report is recorded in the audit log, like an export. It writes nothing else.
"""

import uuid

from fastapi import APIRouter, Response

from geopoc.api.deps import ProfileDep, RequestIdDep, SessionDep, record_audit
from geopoc.api.errors import problem_responses
from geopoc.reporting import FindingsReport, build_findings_report

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
