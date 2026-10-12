from fastapi import APIRouter
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from geopoc.api.deps import RequestIdDep, SessionDep, record_audit
from geopoc.api.errors import ProblemError, problem_responses
from geopoc.api.schemas import ProfileCreate, ProfileList, ProfileOut
from geopoc.db.models import WorkspaceUser

router = APIRouter(prefix="/api/v1/workspace", tags=["workspace"], responses=problem_responses(422))

NOTICE = (
    "Local profiles attribute changes in the audit history. They are not authentication and provide no access control."
)


def profile_out(user: WorkspaceUser) -> ProfileOut:
    return ProfileOut(id=user.id, display_name=user.display_name, created_at=user.created_at)


@router.get("/profiles", response_model=ProfileList)
def list_profiles(session: SessionDep):
    users = session.scalars(select(WorkspaceUser).order_by(WorkspaceUser.display_name)).all()
    return ProfileList(items=[profile_out(u) for u in users], notice=NOTICE)


@router.post("/profiles", response_model=ProfileOut, status_code=201, responses=problem_responses(400, 409))
def create_profile(body: ProfileCreate, session: SessionDep, request_id: RequestIdDep):
    user = WorkspaceUser(display_name=body.display_name)
    session.add(user)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise ProblemError(409, "PROFILE_EXISTS", "A profile with this name already exists") from exc
    record_audit(
        session,
        actor=user,
        action="profile.create",
        entity_type="workspace_user",
        entity_id=user.id,
        request_id=request_id,
    )
    session.commit()
    return profile_out(user)
