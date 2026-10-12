import base64
import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query
from geoalchemy2.shape import from_shape, to_shape
from shapely.geometry import mapping
from sqlalchemy import or_, select, tuple_

from geopoc.api.deps import ProfileDep, RequestIdDep, SessionDep, record_audit
from geopoc.api.errors import ProblemError, problem_responses
from geopoc.api.routers.workspace import profile_out
from geopoc.api.schemas import (
    AoiInput,
    AoiOut,
    ProjectCreate,
    ProjectOut,
    ProjectPage,
    ProjectUpdate,
    clean_text,
)
from geopoc.db.models import Project, utcnow
from geopoc.geo.aoi import AoiError, build_aoi
from geopoc.geo.crs import CrsResolutionError

router = APIRouter(prefix="/api/v1/projects", tags=["projects"], responses=problem_responses(422))


def project_out(project: Project) -> ProjectOut:
    aoi = None
    if project.aoi_geom is not None and project.aoi_source is not None:
        aoi = AoiOut(
            geometry_4326=mapping(to_shape(project.aoi_geom)),
            source=project.aoi_source,
            warnings=project.aoi_source.get("warnings", []),
        )
    return ProjectOut(
        id=project.id,
        name=project.name,
        description=project.description,
        tags=project.tags,
        aoi=aoi,
        source_preference=project.source_preference,
        created_by=profile_out(project.creator),
        created_at=project.created_at,
        updated_at=project.updated_at,
        archived_at=project.archived_at,
    )


def _apply_aoi(project: Project, aoi: AoiInput | None) -> None:
    if aoi is None:
        project.aoi_geom = None
        project.aoi_source = None
        return
    try:
        built = build_aoi(aoi.geometry, aoi.crs_id)
    except CrsResolutionError as exc:
        raise ProblemError(
            422,
            "CRS_UNRESOLVED",
            "The CRS could not be identified",
            str(exc),
            [{"field": "body.aoi.crs_id", "message": str(exc)}],
        ) from exc
    except AoiError as exc:
        raise ProblemError(
            422,
            exc.code,
            "The area of interest is not valid",
            str(exc),
            [{"field": "body.aoi.geometry", "message": str(exc)}],
        ) from exc
    project.aoi_geom = from_shape(built.geom_4326, srid=4326)
    project.aoi_source = built.source


def _get_project(session, project_id: uuid.UUID) -> Project:
    project = session.get(Project, project_id)
    if project is None:
        raise ProblemError(404, "PROJECT_NOT_FOUND", "Project not found")
    return project


def _encode_cursor(project: Project) -> str:
    raw = f"{project.created_at.isoformat()}|{project.id}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def _decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        created_at, project_id = base64.urlsafe_b64decode(cursor.encode()).decode().split("|")
        return datetime.fromisoformat(created_at), uuid.UUID(project_id)
    except Exception as exc:  # any malformed cursor is the same client error
        raise ProblemError(422, "INVALID_CURSOR", "The pagination cursor is not valid") from exc


@router.post("", response_model=ProjectOut, status_code=201, responses=problem_responses(400, 401))
def create_project(body: ProjectCreate, session: SessionDep, profile: ProfileDep, request_id: RequestIdDep):
    project = Project(
        name=body.name,
        description=body.description,
        tags=body.tags,
        source_preference=body.source_preference,
        created_by=profile.id,
    )
    _apply_aoi(project, body.aoi)
    session.add(project)
    session.flush()
    record_audit(
        session,
        actor=profile,
        action="project.create",
        entity_type="project",
        entity_id=project.id,
        request_id=request_id,
        details={"name": project.name, "has_aoi": body.aoi is not None},
    )
    session.commit()
    session.refresh(project)
    return project_out(project)


@router.get("", response_model=ProjectPage)
def list_projects(
    session: SessionDep,
    q: Annotated[str | None, Query(max_length=200, description="Search in name and description")] = None,
    tag: Annotated[str | None, Query(max_length=50)] = None,
    archived: Annotated[bool, Query(description="List archived projects instead of active ones")] = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
):
    try:
        for value in (q, tag):
            if value is not None:
                clean_text(value)
    except ValueError as exc:
        raise ProblemError(422, "VALIDATION_ERROR", "Request validation failed", str(exc)) from exc

    stmt = select(Project)
    stmt = stmt.where(Project.archived_at.is_not(None) if archived else Project.archived_at.is_(None))
    if q and q.strip():
        pattern = "%" + q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        stmt = stmt.where(
            or_(Project.name.ilike(pattern, escape="\\"), Project.description.ilike(pattern, escape="\\"))
        )
    if tag and tag.strip():
        stmt = stmt.where(Project.tags.contains([tag.strip()]))
    if cursor:
        stmt = stmt.where(tuple_(Project.created_at, Project.id) < _decode_cursor(cursor))
    stmt = stmt.order_by(Project.created_at.desc(), Project.id.desc()).limit(limit + 1)

    rows = list(session.scalars(stmt).all())
    page = rows[:limit]
    return ProjectPage(
        items=[project_out(p) for p in page],
        next_cursor=_encode_cursor(page[-1]) if len(rows) > limit else None,
    )


@router.get("/{project_id}", response_model=ProjectOut, responses=problem_responses(404))
def get_project(project_id: uuid.UUID, session: SessionDep):
    return project_out(_get_project(session, project_id))


@router.patch("/{project_id}", response_model=ProjectOut, responses=problem_responses(400, 401, 404))
def update_project(
    project_id: uuid.UUID,
    body: ProjectUpdate,
    session: SessionDep,
    profile: ProfileDep,
    request_id: RequestIdDep,
):
    project = _get_project(session, project_id)
    sent = body.model_fields_set
    changed: list[str] = []

    for field in ("name", "tags", "source_preference"):
        value = getattr(body, field)
        if field in sent:
            if value is None:
                raise ProblemError(
                    422,
                    "VALIDATION_ERROR",
                    "Request validation failed",
                    errors=[{"field": f"body.{field}", "message": "must not be null"}],
                )
            setattr(project, field, value)
            changed.append(field)
    if "description" in sent:
        project.description = body.description
        changed.append("description")
    if "aoi" in sent:
        _apply_aoi(project, body.aoi)
        changed.append("aoi")
    if "archived" in sent and body.archived is not None:
        project.archived_at = (project.archived_at or utcnow()) if body.archived else None
        changed.append("archived")

    if changed:
        project.updated_at = utcnow()
        record_audit(
            session,
            actor=profile,
            action="project.update",
            entity_type="project",
            entity_id=project.id,
            request_id=request_id,
            details={"changed": changed},
        )
        session.commit()
        session.refresh(project)
    return project_out(project)
