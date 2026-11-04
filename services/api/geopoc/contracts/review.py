"""The six review states of the Version 3.0 specification (V3 sections 4 and 5), their meanings and transitions.

The states keep four different situations apart, and no transition may blur them:

    pending review   needs_review   someone has to look; nothing is concluded yet
    uncertainty      inconclusive   the available input cannot support the claim
    disagreement     disputed       supported observations conflict and no documented rule resolves it
    rejection        rejected       the observation is established to be invalid (and is kept)
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from geopoc.contracts.flags import forces_review


class ReviewStatus(StrEnum):
    PROPOSED = "proposed"
    NEEDS_REVIEW = "needs_review"
    VERIFIED = "verified"
    INCONCLUSIVE = "inconclusive"
    DISPUTED = "disputed"
    REJECTED = "rejected"


MEANINGS: dict[ReviewStatus, str] = {
    ReviewStatus.PROPOSED: (
        "Evidence exists and passes the required technical validation. This is not human verification."
    ),
    ReviewStatus.NEEDS_REVIEW: (
        "Quality, confidence, provenance or a source conflict requires human inspection. Nothing is concluded yet."
    ),
    ReviewStatus.INCONCLUSIVE: "The available input cannot support the claim.",
    ReviewStatus.DISPUTED: (
        "Two or more supported observations conflict and no documented rule resolves the difference."
    ),
    ReviewStatus.VERIFIED: (
        "A reviewer, or an explicitly approved validation process, confirms the finding. Who or what verified "
        "it is recorded."
    ),
    ReviewStatus.REJECTED: (
        "A reviewer, or a deterministic validation, establishes that the observation is invalid. It is kept "
        "in the audit history."
    ),
}

Actor = Literal["reviewer", "system"]

P, N, V, I, D, R = (  # noqa: E741
    ReviewStatus.PROPOSED,
    ReviewStatus.NEEDS_REVIEW,
    ReviewStatus.VERIFIED,
    ReviewStatus.INCONCLUSIVE,
    ReviewStatus.DISPUTED,
    ReviewStatus.REJECTED,
)


@dataclass(frozen=True)
class Rule:
    actors: frozenset[str]
    note_required: bool
    why: str


def _rule(actors: str, note: bool, why: str) -> Rule:
    return Rule(frozenset(actors.split("|")), note, why)


# (from, to) -> rule. Anything absent is not permitted.
TRANSITIONS: dict[tuple[ReviewStatus, ReviewStatus], Rule] = {
    (P, N): _rule("reviewer|system", False, "A quality, confidence or provenance concern was raised."),
    (P, V): _rule("reviewer", False, "A reviewer confirms the finding."),
    (P, R): _rule("reviewer|system", True, "Established invalid; the reason is recorded."),
    (P, I): _rule("reviewer|system", True, "The input cannot support the claim."),
    (P, D): _rule("system", False, "Conflict detection found a disagreeing supported observation."),
    (N, V): _rule("reviewer", False, "A reviewer inspected and confirms the finding."),
    (N, R): _rule("reviewer", True, "A reviewer inspected and rejects the finding."),
    (N, I): _rule("reviewer", True, "A reviewer finds the input cannot support the claim."),
    (N, D): _rule("system", False, "Conflict detection found a disagreeing supported observation."),
    # Uncertainty is not resolved by assertion: an inconclusive finding goes back to review first.
    (I, N): _rule("reviewer|system", True, "Reopened, for example because new evidence arrived."),
    (I, R): _rule("reviewer", True, "A reviewer rejects the finding."),
    (I, D): _rule("system", False, "Conflict detection found a disagreeing supported observation."),
    # Leaving a dispute always needs a documented resolution, and only a person may conclude it.
    (D, N): _rule("reviewer|system", True, "The conflict no longer applies; the resolution is documented."),
    (D, V): _rule("reviewer", True, "A reviewer resolves the dispute in favour of this finding."),
    (D, R): _rule("reviewer", True, "A reviewer resolves the dispute against this finding."),
    # A concluded finding is never silently changed: it is reopened, with a reason, and reviewed again.
    (V, N): _rule("reviewer", True, "Reopened by a reviewer."),
    (V, D): _rule("system", False, "A later observation conflicts with this verified finding."),
    (R, N): _rule("reviewer", True, "Reopened by a reviewer."),
}


class TransitionError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def check_transition(
    current: ReviewStatus | str,
    target: ReviewStatus | str,
    *,
    actor: Actor,
    actor_id: str | None,
    note: str | None,
) -> Rule:
    """Validate one change of review state. Raises TransitionError; never changes anything itself."""
    current, target = ReviewStatus(current), ReviewStatus(target)
    if current == target:
        raise TransitionError("NO_CHANGE", f"The observation is already {current.value}.")
    rule = TRANSITIONS.get((current, target))
    if rule is None:
        raise TransitionError("INVALID_TRANSITION", f"An observation cannot go from {current.value} to {target.value}.")
    if actor not in rule.actors:
        allowed = " or ".join(sorted(rule.actors))
        raise TransitionError(
            "ACTOR_NOT_PERMITTED",
            f"Only a {allowed} may change an observation from {current.value} to {target.value}.",
        )
    if not (actor_id and actor_id.strip()):
        # V3 section 5: "record who/what verified it". The same holds for every change of state.
        raise TransitionError("ACTOR_REQUIRED", "Who or what made this change must be recorded.")
    if rule.note_required and not (note and note.strip()):
        raise TransitionError(
            "NOTE_REQUIRED", f"A reason must be recorded to change from {current.value} to {target.value}."
        )
    return rule


def initial_status(
    *,
    quality_flags: list[str],
    score_calibrated: bool,
    below_threshold: bool = False,
    input_cannot_support_claim: bool = False,
    in_open_conflict: bool = False,
) -> ReviewStatus:
    """The state a newly created model observation starts in. A model never assigns `verified` or `rejected`."""
    if in_open_conflict:
        return ReviewStatus.DISPUTED
    if input_cannot_support_claim:
        return ReviewStatus.INCONCLUSIVE
    # CON-002: without a validated threshold the score is uncalibrated and review is required.
    if not score_calibrated or below_threshold or any(forces_review(flag) for flag in quality_flags):
        return ReviewStatus.NEEDS_REVIEW
    return ReviewStatus.PROPOSED
