"""Deterministic linking of observations after an analysis run.

Two observations are compared only when they come from different runs on the same image content (the same
asset, or a byte-identical copy of it), so their pixel regions are in the same grid. For each pair whose boxes
overlap by at least OVERLAP_IOU (intersection over union):

  * the same current category  -> a possible-duplicate link. It is a candidate only: nothing is merged, and the
                                  two findings are not counted as independent confirmation.
  * different categories       -> a category conflict. Both findings are kept and, where the state rules allow,
                                  set to `disputed` by the system until a reviewer documents a resolution.

Rejected findings take part in neither. OVERLAP_IOU is an operating setting chosen for this PoC, not a
validated value.
"""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from geopoc.contracts.review import ReviewStatus
from geopoc.db.models import (
    Asset,
    Conflict,
    ConflictMember,
    LinkGroup,
    LinkMember,
    ObservationRecord,
    PipelineRun,
    utcnow,
)
from geopoc.review.service import system_transition

OVERLAP_IOU = 0.5
RULE = "pixel_overlap_iou_0.5_v1"
ACTOR = f"conflict detection ({RULE})"


def _box(observation: ObservationRecord) -> tuple[float, float, float, float] | None:
    evidence = observation.record.get("evidence") or []
    region = evidence[0].get("region_px") if evidence else None
    if not region or region.get("type") != "bbox_xyxy":
        return None
    x0, y0, x1, y1 = (float(v) for v in region["coordinates"])
    return x0, y0, x1, y1


def iou(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def link_run(session: Session, run_id: uuid.UUID) -> dict[str, int]:
    """Create link and conflict records for the observations of one run. Returns counts for the job result."""
    run = session.get(PipelineRun, run_id)
    if run is None:
        return {"possible_duplicate_links": 0, "conflicts": 0}
    asset = session.get(Asset, run.asset_id)
    same_content = select(Asset.id).where(Asset.project_id == run.project_id, Asset.blob_sha256 == asset.blob_sha256)
    new = session.scalars(select(ObservationRecord).where(ObservationRecord.run_id == run_id)).all()
    existing = session.scalars(
        select(ObservationRecord).where(
            ObservationRecord.asset_id.in_(same_content),
            ObservationRecord.run_id != run_id,
            ObservationRecord.review_status != ReviewStatus.REJECTED.value,
        )
    ).all()
    if not new or not existing:
        return {"possible_duplicate_links": 0, "conflicts": 0}

    links = conflicts = 0
    for observation in new:
        box = _box(observation)
        if box is None:
            continue
        duplicates: list[tuple[ObservationRecord, float]] = []
        disagreeing: list[tuple[ObservationRecord, float]] = []
        for other in existing:
            other_box = _box(other)
            if other_box is None:
                continue
            overlap = iou(box, other_box)
            if overlap < OVERLAP_IOU:
                continue
            target = duplicates if other.current_category == observation.current_category else disagreeing
            target.append((other, overlap))

        for other, overlap in duplicates:
            _link(session, run, observation, other, overlap)
            links += 1
        if disagreeing:
            _conflict(session, run, observation, disagreeing)
            conflicts += 1
    return {"possible_duplicate_links": links, "conflicts": conflicts}


def _link(session: Session, run: PipelineRun, observation: ObservationRecord, other: ObservationRecord, overlap: float):
    group_id = session.scalars(
        select(LinkMember.link_group_id)
        .join(LinkGroup, LinkGroup.id == LinkMember.link_group_id)
        .where(LinkMember.observation_id == other.id, LinkGroup.kind == "possible_duplicate")
    ).first()
    if group_id is None:
        group = LinkGroup(
            id=uuid.uuid4(),
            project_id=run.project_id,
            kind="possible_duplicate",
            method=RULE,
            status="candidate",
            rationale=(
                f"Same category ({observation.current_category!r}) and overlapping regions (IoU {overlap:.2f}) "
                "in the same image content, from different analysis runs. Not independent confirmation."
            ),
            created_by_run_id=run.id,
            created_at=utcnow(),
        )
        session.add(group)
        session.flush()
        session.add(LinkMember(link_group_id=group.id, observation_id=other.id))
        group_id = group.id
    already = session.get(LinkMember, (group_id, observation.id))
    if already is None:
        session.add(LinkMember(link_group_id=group_id, observation_id=observation.id))
    session.flush()


def _conflict(
    session: Session,
    run: PipelineRun,
    observation: ObservationRecord,
    disagreeing: list[tuple[ObservationRecord, float]],
) -> None:
    members = [(observation, None), *disagreeing]
    conflict = Conflict(
        id=uuid.uuid4(),
        project_id=run.project_id,
        attribute="category",
        status="open",
        rule=RULE,
        details={
            "summary": (
                "Overlapping regions in the same image content were given different categories by different "
                "analysis runs. Both are kept; no rule decides between them."
            ),
            "overlap_iou_threshold": OVERLAP_IOU,
        },
        created_by_run_id=run.id,
        created_at=utcnow(),
    )
    session.add(conflict)
    session.flush()
    for member, overlap in members:
        value: dict[str, Any] = {
            "category": member.current_category,
            "run_id": str(member.run_id),
            "score_raw": member.score_raw,
        }
        if overlap is not None:
            value["iou_with_new_finding"] = round(overlap, 4)
        session.add(ConflictMember(conflict_id=conflict.id, observation_id=member.id, value=value))
    session.flush()
    for member, _ in members:
        system_transition(
            session,
            member,
            ReviewStatus.DISPUTED,
            action="system_dispute",
            actor_label=ACTOR,
            note=None,
            details={"conflict_id": str(conflict.id)},
        )
