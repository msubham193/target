"""Human review of observations: state changes, relabelling and notes, each one an append-only revision.

The model's original output is never changed. What changes is the observation's current state (`review_status`,
`current_category`, `revision`), and every change is written to `observation_revision` and the audit log. The
state rules are those of `geopoc.contracts.review`; this module only applies them.
"""

import uuid
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from geopoc.contracts.review import TRANSITIONS, ReviewStatus, TransitionError, check_transition
from geopoc.db.models import (
    Conflict,
    ConflictMember,
    ObservationRecord,
    ObservationRevision,
    WorkspaceUser,
    utcnow,
)

ReviewAction = Literal["confirm", "reject", "mark_inconclusive", "reopen", "relabel", "note"]

# The review action a person takes -> the state it leads to. Relabelling and notes keep the state.
ACTION_TARGET: dict[str, ReviewStatus] = {
    "confirm": ReviewStatus.VERIFIED,
    "reject": ReviewStatus.REJECTED,
    "mark_inconclusive": ReviewStatus.INCONCLUSIVE,
    "reopen": ReviewStatus.NEEDS_REVIEW,
}
MAX_CATEGORY_LENGTH = 100


class ReviewError(Exception):
    def __init__(self, status: int, code: str, title: str, detail: str | None = None) -> None:
        super().__init__(title)
        self.status, self.code, self.title, self.detail = status, code, title, detail


@dataclass(frozen=True)
class AllowedAction:
    action: str
    target_status: str | None
    note_required: bool


def allowed_actions(status: str) -> list[AllowedAction]:
    """What a reviewer may do to an observation in this state. The interface offers exactly these."""
    current = ReviewStatus(status)
    allowed = []
    for action, target in ACTION_TARGET.items():
        rule = TRANSITIONS.get((current, target))
        if rule is not None and "reviewer" in rule.actors:
            allowed.append(AllowedAction(action, target.value, rule.note_required))
    if current != ReviewStatus.REJECTED:
        # A label correction is a claim by a person, so its reason is always recorded.
        allowed.append(AllowedAction("relabel", None, True))
    allowed.append(AllowedAction("note", None, True))
    return allowed


def _revise(
    session: Session,
    observation: ObservationRecord,
    *,
    action: str,
    status_after: str,
    category_after: str,
    note: str | None,
    actor_kind: Literal["reviewer", "system"],
    actor: WorkspaceUser | None,
    actor_label: str,
    request_id: str | None,
    details: dict[str, Any] | None = None,
) -> ObservationRevision:
    revision = ObservationRevision(
        id=uuid.uuid4(),
        observation_id=observation.id,
        revision_no=observation.revision + 1,
        action=action,
        status_before=observation.review_status,
        status_after=status_after,
        category_before=observation.current_category,
        category_after=category_after,
        note=note,
        actor_kind=actor_kind,
        actor_id=actor.id if actor else None,
        actor_label=actor_label,
        details=details or {},
        request_id=request_id,
        created_at=utcnow(),
    )
    session.add(revision)
    observation.revision = revision.revision_no
    observation.review_status = status_after
    observation.current_category = category_after
    return revision


def review(
    session: Session,
    observation_id: uuid.UUID,
    *,
    action: ReviewAction,
    note: str | None,
    category: str | None,
    expected_revision: int,
    reviewer: WorkspaceUser,
    request_id: str | None,
) -> tuple[ObservationRecord, ObservationRevision]:
    """Apply one review action. The caller commits. Raises ReviewError with a stable code."""
    # Lock the row so two reviewers cannot both act on the same revision.
    observation = session.scalars(
        select(ObservationRecord).where(ObservationRecord.id == observation_id).with_for_update()
    ).first()
    if observation is None:
        raise ReviewError(404, "OBSERVATION_NOT_FOUND", "Observation not found")
    if observation.revision != expected_revision:
        raise ReviewError(
            409,
            "REVISION_CONFLICT",
            "This observation was changed by someone else",
            f"You reviewed revision {expected_revision}; it is now at revision {observation.revision}. "
            "Reload it, check the change, and try again.",
        )
    note = note.strip() if note and note.strip() else None

    status_after = observation.review_status
    category_after = observation.current_category
    if action in ACTION_TARGET:
        target = ACTION_TARGET[action]
        try:
            check_transition(observation.review_status, target, actor="reviewer", actor_id=str(reviewer.id), note=note)
        except TransitionError as exc:
            raise ReviewError(422, exc.code, str(exc)) from exc
        status_after = target.value
    elif action == "relabel":
        if observation.review_status == ReviewStatus.REJECTED:
            raise ReviewError(
                422, "INVALID_TRANSITION", "A rejected observation cannot be relabelled", "Reopen it first."
            )
        label = (category or "").strip()
        if not label or len(label) > MAX_CATEGORY_LENGTH or "\x00" in label:
            raise ReviewError(422, "CATEGORY_INVALID", f"Enter a category of 1 to {MAX_CATEGORY_LENGTH} characters.")
        if label == observation.current_category:
            raise ReviewError(422, "NO_CHANGE", f"The category is already {label!r}.")
        if not note:
            raise ReviewError(422, "NOTE_REQUIRED", "A reason must be recorded to change the category.")
        category_after = label
    elif action == "note":
        if not note:
            raise ReviewError(422, "NOTE_REQUIRED", "Enter the note to record.")
    else:  # pragma: no cover - the API schema restricts the values
        raise ReviewError(422, "UNKNOWN_ACTION", f"Unknown review action {action!r}.")

    revision = _revise(
        session,
        observation,
        action=action,
        status_after=status_after,
        category_after=category_after,
        note=note,
        actor_kind="reviewer",
        actor=reviewer,
        actor_label=reviewer.display_name,
        request_id=request_id,
    )
    resolve_settled_conflicts(session, observation.id, note=note, reviewer=reviewer)
    return observation, revision


def system_transition(
    session: Session,
    observation: ObservationRecord,
    target: ReviewStatus,
    *,
    action: Literal["system_dispute", "system_resolve"],
    actor_label: str,
    note: str | None,
    details: dict[str, Any],
) -> ObservationRevision | None:
    """A state change made by a documented rule (conflict detection). Returns None if the rule does not apply."""
    if observation.review_status == target.value:
        return None
    try:
        check_transition(observation.review_status, target, actor="system", actor_id=actor_label, note=note)
    except TransitionError:
        return None
    return _revise(
        session,
        observation,
        action=action,
        status_after=target.value,
        category_after=observation.current_category,
        note=note,
        actor_kind="system",
        actor=None,
        actor_label=actor_label,
        request_id=None,
        details=details,
    )


def resolve_settled_conflicts(
    session: Session, observation_id: uuid.UUID, *, note: str | None, reviewer: WorkspaceUser
) -> list[Conflict]:
    """Close open conflicts in which no member is still disputed. The reviewer's reason is the resolution."""
    session.flush()
    conflicts = session.scalars(
        select(Conflict)
        .join(ConflictMember, ConflictMember.conflict_id == Conflict.id)
        .where(ConflictMember.observation_id == observation_id, Conflict.status == "open")
    ).all()
    closed = []
    for conflict in conflicts:
        statuses = session.scalars(
            select(ObservationRecord.review_status)
            .join(ConflictMember, ConflictMember.observation_id == ObservationRecord.id)
            .where(ConflictMember.conflict_id == conflict.id)
        ).all()
        if ReviewStatus.DISPUTED.value not in statuses:
            conflict.status = "resolved_documented"
            conflict.resolved_at = utcnow()
            conflict.resolution_note = f"Resolved by review ({reviewer.display_name}): {note or 'no note'}"
            closed.append(conflict)
    return closed
