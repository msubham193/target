import uuid
from typing import Annotated, Any

from fastapi import Depends, Header, Request
from sqlalchemy.orm import Session

from geopoc.api.errors import ProblemError
from geopoc.db.models import AuditEvent, WorkspaceUser
from geopoc.db.session import get_session

SessionDep = Annotated[Session, Depends(get_session)]


def get_request_id(request: Request) -> str:
    return request.state.request_id


RequestIdDep = Annotated[str, Depends(get_request_id)]


def current_profile(
    session: SessionDep,
    x_workspace_profile: Annotated[
        str | None,
        Header(description="ID of the active local profile. Audit attribution only; not authentication."),
    ] = None,
) -> WorkspaceUser:
    try:
        profile_id = uuid.UUID(x_workspace_profile or "")
    except ValueError:
        profile_id = None
    profile = session.get(WorkspaceUser, profile_id) if profile_id else None
    if profile is None:
        raise ProblemError(
            401,
            "PROFILE_REQUIRED",
            "A local workspace profile is required",
            "Select or create a profile; changes are attributed to it in the audit history.",
        )
    return profile


ProfileDep = Annotated[WorkspaceUser, Depends(current_profile)]


def record_audit(
    session: Session,
    *,
    actor: WorkspaceUser | None,
    action: str,
    entity_type: str,
    entity_id: uuid.UUID | None,
    request_id: str,
    details: dict[str, Any] | None = None,
) -> None:
    session.add(
        AuditEvent(
            actor_id=actor.id if actor else None,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            request_id=request_id,
            details=details or {},
        )
    )
