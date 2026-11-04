"""Map annotations: points, lines and polygons a person draws on the map, with a note.

Every operation belongs to one project: an annotation id from another project is "not found". Annotations are kept
apart from AI observations and photograph locations (separate table, separate endpoints, `feature_type:
user_annotation`), they are append-only, and deleting hides an annotation from the normal views without removing its
history.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from geopoc.api.deps import ProfileDep, RequestIdDep, SessionDep, record_audit
from geopoc.api.errors import ProblemError, problem_responses
from geopoc.api.schemas import (
    AnnotationCreate,
    AnnotationHistoryOut,
    AnnotationMeasurement,
    AnnotationOut,
    AnnotationsOut,
    AnnotationUpdate,
    AnnotationVersionOut,
)
from geopoc.db.models import MapAnnotation, Project
from geopoc.geo import annotations as service
from geopoc.geo.annotations import AnnotationError


def annotations_available(session: Session) -> None:
    """Annotations need their table. A database that has not been migrated gets a clear answer, not a server error."""
    if session.execute(text("SELECT to_regclass('map_annotation')")).scalar_one() is None:
        raise ProblemError(
            503,
            "ANNOTATIONS_NOT_MIGRATED",
            "Annotations are not available yet",
            "The database has not been updated for map annotations. Nothing was read or saved.",
        )


def _available(session: SessionDep) -> None:
    annotations_available(session)


router = APIRouter(
    prefix="/api/v1/projects/{project_id}/annotations",
    tags=["annotations"],
    responses=problem_responses(404, 422, 503),
    dependencies=[Depends(_available)],
)


def _project(session: Session, project_id: uuid.UUID) -> Project:
    project = session.get(Project, project_id)
    if project is None:
        raise ProblemError(404, "PROJECT_NOT_FOUND", "Project not found")
    return project


def _problem(exc: AnnotationError) -> ProblemError:
    return ProblemError(exc.status, exc.code, exc.title, exc.detail)


def _out(latest: MapAnnotation, first: MapAnnotation) -> AnnotationOut:
    geometry = service.geometry_of(latest)
    measured = service.measurement_of(geometry)
    deleted = latest.status == "deleted"
    return AnnotationOut(
        annotation_id=latest.annotation_id,
        project_id=latest.project_id,
        kind=latest.kind,  # type: ignore[arg-type]
        status=latest.status,  # type: ignore[arg-type]
        version_no=latest.version_no,
        geometry=geometry,
        crs_id=latest.crs_id,
        note=latest.note,
        measurement=(
            AnnotationMeasurement(
                length_m=measured.length_m,
                area_m2=measured.area_m2,
                perimeter_m=measured.perimeter_m,
                method=measured.method,
                limitations=measured.limitations,
            )
            if measured and not deleted
            else None
        ),
        created_by=first.actor_label,
        created_at=first.created_at,
        updated_by=latest.actor_label,
        updated_at=latest.created_at,
        deleted_by=latest.actor_label if deleted else None,
        deleted_at=latest.created_at if deleted else None,
    )


def _one(
    session: Session, project_id: uuid.UUID, annotation_id: uuid.UUID
) -> tuple[list[MapAnnotation], AnnotationOut]:
    _project(session, project_id)
    rows = service.history(session, project_id, annotation_id)
    if not rows:
        raise ProblemError(404, "ANNOTATION_NOT_FOUND", "Annotation not found")
    return rows, _out(rows[-1], rows[0])


@router.get("", response_model=AnnotationsOut)
def list_annotations(
    project_id: uuid.UUID,
    session: SessionDep,
    include_deleted: Annotated[bool, Query(description="Also list deleted annotations (hidden by default).")] = False,
):
    """The project's annotations as they are now. Deleted ones are left out unless `include_deleted` is set."""
    _project(session, project_id)
    latest = service.latest_rows(session, project_id)
    first = service.first_versions(session, project_id)
    shown = [r for r in latest if include_deleted or r.status == "active"]
    shown.sort(key=lambda r: (first[r.annotation_id].created_at, str(r.annotation_id)))
    return AnnotationsOut(
        items=[_out(r, first[r.annotation_id]) for r in shown],
        active=sum(r.status == "active" for r in latest),
        deleted=sum(r.status == "deleted" for r in latest),
        include_deleted=include_deleted,
        note=service.NOTE,
    )


@router.post("", response_model=AnnotationOut, status_code=201, responses=problem_responses(400, 401, 422))
def create_annotation(
    project_id: uuid.UUID, body: AnnotationCreate, session: SessionDep, profile: ProfileDep, request_id: RequestIdDep
):
    """Save a new point, line or polygon with a note."""
    _project(session, project_id)
    try:
        row = service.create(
            session,
            project_id,
            kind=body.kind,
            geometry=body.geometry,
            crs_id=body.crs_id,
            note=body.note,
            actor=profile,
            request_id=request_id,
        )
    except AnnotationError as exc:
        session.rollback()
        _audit_refusal(session, project_id, None, profile, request_id, "create", exc)
        raise _problem(exc) from exc
    record_audit(
        session,
        actor=profile,
        action="annotation.create",
        entity_type="map_annotation",
        entity_id=row.annotation_id,
        request_id=request_id,
        details={"project_id": str(project_id), "kind": row.kind, "version": row.version_no},
    )
    session.commit()
    return _out(row, row)


@router.get("/{annotation_id}", response_model=AnnotationOut)
def get_annotation(project_id: uuid.UUID, annotation_id: uuid.UUID, session: SessionDep):
    """One annotation of this project, including a deleted one (its status says so)."""
    return _one(session, project_id, annotation_id)[1]


@router.patch("/{annotation_id}", response_model=AnnotationOut, responses=problem_responses(400, 401, 409, 422))
def update_annotation(
    project_id: uuid.UUID,
    annotation_id: uuid.UUID,
    body: AnnotationUpdate,
    session: SessionDep,
    profile: ProfileDep,
    request_id: RequestIdDep,
):
    """Save a new version with a new shape and/or note. The kind cannot change."""
    _project(session, project_id)
    rows = service.history(session, project_id, annotation_id)
    try:
        row = service.update(
            session,
            rows,
            expected_version=body.expected_version,
            geometry=body.geometry,
            crs_id=body.crs_id,
            note=body.note,
            reason=body.reason,
            actor=profile,
            request_id=request_id,
        )
    except AnnotationError as exc:
        session.rollback()
        _audit_refusal(session, project_id, annotation_id, profile, request_id, "update", exc)
        raise _problem(exc) from exc
    except IntegrityError as exc:  # two requests raced past the version check
        session.rollback()
        raise ProblemError(409, "ANNOTATION_VERSION_CONFLICT", "The annotation changed since you opened it") from exc
    record_audit(
        session,
        actor=profile,
        action="annotation.update",
        entity_type="map_annotation",
        entity_id=annotation_id,
        request_id=request_id,
        details={
            "project_id": str(project_id),
            "kind": row.kind,
            "version": row.version_no,
            "changed": [
                name
                for name, changed in (
                    ("shape", service.geometry_of(row) != service.geometry_of(rows[-1])),
                    ("note", row.note != rows[-1].note),
                )
                if changed
            ],
        },
    )
    session.commit()
    return _out(row, rows[0])


@router.delete("/{annotation_id}", response_model=AnnotationOut, responses=problem_responses(401, 409, 422))
def delete_annotation(
    project_id: uuid.UUID,
    annotation_id: uuid.UUID,
    session: SessionDep,
    profile: ProfileDep,
    request_id: RequestIdDep,
    expected_version: Annotated[int, Query(ge=1, description="The version you are deleting.")],
    reason: Annotated[str | None, Query(max_length=1000)] = None,
):
    """Delete an annotation: a new `deleted` version hides it from the normal views. Its history is kept."""
    _project(session, project_id)
    rows = service.history(session, project_id, annotation_id)
    try:
        row = service.delete(
            session, rows, expected_version=expected_version, reason=reason, actor=profile, request_id=request_id
        )
    except AnnotationError as exc:
        session.rollback()
        raise _problem(exc) from exc
    except IntegrityError as exc:
        session.rollback()
        raise ProblemError(409, "ANNOTATION_VERSION_CONFLICT", "The annotation changed since you opened it") from exc
    record_audit(
        session,
        actor=profile,
        action="annotation.delete",
        entity_type="map_annotation",
        entity_id=annotation_id,
        request_id=request_id,
        details={"project_id": str(project_id), "kind": row.kind, "version": row.version_no},
    )
    session.commit()
    return _out(row, rows[0])


@router.get("/{annotation_id}/history", response_model=AnnotationHistoryOut)
def annotation_history(project_id: uuid.UUID, annotation_id: uuid.UUID, session: SessionDep):
    """Every version, oldest first, including the deletion."""
    rows, _ = _one(session, project_id, annotation_id)
    return AnnotationHistoryOut(
        annotation_id=annotation_id,
        project_id=project_id,
        kind=rows[0].kind,  # type: ignore[arg-type]
        versions=[
            AnnotationVersionOut(
                version_no=r.version_no,
                action=r.action,  # type: ignore[arg-type]
                status=r.status,  # type: ignore[arg-type]
                geometry=service.geometry_of(r),
                note=r.note,
                reason=r.reason,
                actor_label=r.actor_label,
                created_at=r.created_at,
            )
            for r in rows
        ],
        note=service.NOTE,
    )


def _audit_refusal(
    session: Session,
    project_id: uuid.UUID,
    annotation_id: uuid.UUID | None,
    profile,
    request_id: str,
    action: str,
    exc: AnnotationError,
) -> None:
    """A refused shape is worth an audit line; nothing about it is stored."""
    if exc.status != 422:
        return
    record_audit(
        session,
        actor=profile,
        action="annotation.refused",
        entity_type="map_annotation",
        entity_id=annotation_id,
        request_id=request_id,
        details={"project_id": str(project_id), "action": action, "code": exc.code},
    )
    session.commit()
