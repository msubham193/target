"""Observations: listing with their current review state, review actions, conflicts and exports.

Every response keeps the model's original output (`observation`) apart from its current, reviewed state
(`current`), so a prediction is never presented as a verified finding.
"""

import csv
import io
import json
import uuid
from collections import defaultdict
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query
from fastapi.responses import Response
from sqlalchemy import and_, select, tuple_
from sqlalchemy.orm import Session

from geopoc import annotation_export
from geopoc.api.deps import ProfileDep, RequestIdDep, SessionDep, record_audit
from geopoc.api.errors import ProblemError, problem_responses
from geopoc.api.schemas import (
    AllowedActionOut,
    ConflictMemberOut,
    ConflictOut,
    LinkGroupOut,
    ObservationCurrent,
    ObservationDetail,
    ObservationOut,
    ReviewRequest,
    ReviewStatusName,
    RevisionOut,
)
from geopoc.contracts.observation import Observation
from geopoc.db.models import (
    Asset,
    Conflict,
    ConflictMember,
    LinkGroup,
    LinkMember,
    ObservationRecord,
    ObservationRevision,
    PipelineRun,
    Project,
)
from geopoc.geo import photolocation
from geopoc.review.service import ReviewError, allowed_actions, review

router = APIRouter(prefix="/api/v1", tags=["observations"], responses=problem_responses(422))
MAX_LIST = 500


def _project(session: Session, project_id: uuid.UUID) -> Project:
    project = session.get(Project, project_id)
    if project is None:
        raise ProblemError(404, "PROJECT_NOT_FOUND", "Project not found")
    return project


def _views(session: Session, rows: list[ObservationRecord]) -> list[ObservationOut]:
    """Build the API view of several observations with a fixed number of queries."""
    if not rows:
        return []
    ids = [row.id for row in rows]
    names: dict[uuid.UUID, str] = {
        asset_id: name
        for asset_id, name in session.execute(
            select(Asset.id, Asset.original_filename).where(Asset.id.in_({r.asset_id for r in rows}))
        )
    }
    conflicts: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
    for observation_id, conflict_id in session.execute(
        select(ConflictMember.observation_id, ConflictMember.conflict_id)
        .join(Conflict, Conflict.id == ConflictMember.conflict_id)
        .where(ConflictMember.observation_id.in_(ids), Conflict.status == "open")
    ):
        conflicts[observation_id].append(conflict_id)
    links: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
    for observation_id, group_id in session.execute(
        select(LinkMember.observation_id, LinkMember.link_group_id).where(LinkMember.observation_id.in_(ids))
    ):
        links[observation_id].append(group_id)
    revised = [(r.id, r.revision) for r in rows if r.revision > 0]
    latest: dict[uuid.UUID, ObservationRevision] = {}
    if revised:
        for revision in session.scalars(
            select(ObservationRevision).where(
                tuple_(ObservationRevision.observation_id, ObservationRevision.revision_no).in_(revised)
            )
        ):
            latest[revision.observation_id] = revision
    notes: dict[uuid.UUID, str] = {}
    if revised:
        for observation_id, note in session.execute(
            select(ObservationRevision.observation_id, ObservationRevision.note)
            .where(
                ObservationRevision.observation_id.in_([i for i, _ in revised]), ObservationRevision.note.isnot(None)
            )
            .order_by(ObservationRevision.observation_id, ObservationRevision.revision_no)
        ):
            notes[observation_id] = note  # the last one wins

    views = []
    for row in rows:
        last = latest.get(row.id)
        views.append(
            ObservationOut(
                observation=Observation.model_validate(row.record),
                current=ObservationCurrent(
                    status=row.review_status,
                    category=row.current_category,
                    relabelled=row.current_category != row.category,
                    revision=row.revision,
                    last_changed_at=last.created_at if last else None,
                    last_changed_by=last.actor_label if last else None,
                    last_note=notes.get(row.id),
                ),
                asset_filename=names.get(row.asset_id, ""),
                open_conflict_ids=conflicts.get(row.id, []),
                possible_duplicate_group_ids=links.get(row.id, []),
                allowed_actions=[AllowedActionOut(**vars(a)) for a in allowed_actions(row.review_status)],
            )
        )
    return views


def _conflicts(session: Session, conflict_rows: list[Conflict]) -> list[ConflictOut]:
    if not conflict_rows:
        return []
    members = session.execute(
        select(ConflictMember, ObservationRecord, Asset.original_filename)
        .join(ObservationRecord, ObservationRecord.id == ConflictMember.observation_id)
        .join(Asset, Asset.id == ObservationRecord.asset_id)
        .where(ConflictMember.conflict_id.in_([c.id for c in conflict_rows]))
        .order_by(ObservationRecord.created_at, ObservationRecord.id)
    ).all()
    by_conflict: dict[uuid.UUID, list[ConflictMemberOut]] = defaultdict(list)
    for member, observation, filename in members:
        by_conflict[member.conflict_id].append(
            ConflictMemberOut(
                observation_id=observation.id,
                asset_id=observation.asset_id,
                asset_filename=filename,
                value=member.value,
                current_status=observation.review_status,
                current_category=observation.current_category,
            )
        )
    return [
        ConflictOut(
            id=c.id,
            attribute=c.attribute,
            status=c.status,
            rule=c.rule,
            details=c.details,
            created_by_run_id=c.created_by_run_id,
            created_at=c.created_at,
            resolved_at=c.resolved_at,
            resolution_note=c.resolution_note,
            members=by_conflict.get(c.id, []),
        )
        for c in conflict_rows
    ]


def _detail(session: Session, row: ObservationRecord) -> ObservationDetail:
    view = _views(session, [row])[0]
    revisions = session.scalars(
        select(ObservationRevision)
        .where(ObservationRevision.observation_id == row.id)
        .order_by(ObservationRevision.revision_no)
    ).all()
    conflict_rows = session.scalars(
        select(Conflict)
        .join(ConflictMember, ConflictMember.conflict_id == Conflict.id)
        .where(ConflictMember.observation_id == row.id)
        .order_by(Conflict.created_at)
    ).all()
    groups = session.scalars(
        select(LinkGroup)
        .join(LinkMember, LinkMember.link_group_id == LinkGroup.id)
        .where(LinkMember.observation_id == row.id)
    ).all()
    members: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
    if groups:
        for group_id, observation_id in session.execute(
            select(LinkMember.link_group_id, LinkMember.observation_id).where(
                LinkMember.link_group_id.in_([g.id for g in groups])
            )
        ):
            members[group_id].append(observation_id)
    return ObservationDetail(
        **view.model_dump(),
        revisions=[
            RevisionOut(
                revision_no=r.revision_no,
                action=r.action,
                status_before=r.status_before,
                status_after=r.status_after,
                category_before=r.category_before,
                category_after=r.category_after,
                note=r.note,
                actor_kind=r.actor_kind,
                actor_label=r.actor_label,
                details=r.details,
                created_at=r.created_at,
            )
            for r in revisions
        ],
        conflicts=_conflicts(session, list(conflict_rows)),
        links=[
            LinkGroupOut(
                id=g.id,
                kind=g.kind,
                method=g.method,
                status=g.status,
                rationale=g.rationale,
                observation_ids=members.get(g.id, []),
            )
            for g in groups
        ],
    )


def _query(
    session: Session,
    project_id: uuid.UUID,
    *,
    asset_id: uuid.UUID | None = None,
    run_id: uuid.UUID | None = None,
    frame_index: int | None = None,
    status: list[ReviewStatusName] | None = None,
    category: str | None = None,
    in_conflict: bool | None = None,
    limit: int = MAX_LIST,
) -> list[ObservationRecord]:
    stmt = select(ObservationRecord).where(ObservationRecord.project_id == project_id)
    if asset_id:
        stmt = stmt.where(ObservationRecord.asset_id == asset_id)
    if run_id:
        stmt = stmt.where(ObservationRecord.run_id == run_id)
    if frame_index is not None:
        # Findings of one sampled video frame: the frame number is in the finding's own evidence.
        stmt = stmt.where(ObservationRecord.record["evidence"][0]["frame_index"].astext == str(frame_index))
    if status:
        stmt = stmt.where(ObservationRecord.review_status.in_(status))
    if category:
        stmt = stmt.where(ObservationRecord.current_category == category)
    if in_conflict is not None:
        open_member = (
            select(ConflictMember.observation_id)
            .join(Conflict, Conflict.id == ConflictMember.conflict_id)
            .where(Conflict.status == "open")
        )
        stmt = stmt.where(
            ObservationRecord.id.in_(open_member) if in_conflict else ObservationRecord.id.not_in(open_member)
        )
    stmt = stmt.order_by(ObservationRecord.score_raw.desc().nulls_last(), ObservationRecord.id).limit(limit)
    return list(session.scalars(stmt).all())


@router.get(
    "/projects/{project_id}/observations", response_model=list[ObservationOut], responses=problem_responses(404)
)
def list_observations(
    project_id: uuid.UUID,
    session: SessionDep,
    asset_id: uuid.UUID | None = None,
    run_id: uuid.UUID | None = None,
    frame_index: Annotated[int | None, Query(ge=0, description="Only findings in this sampled video frame.")] = None,
    status: Annotated[list[ReviewStatusName] | None, Query(description="Current review state(s).")] = None,
    category: Annotated[str | None, Query(max_length=200, description="Current category (after relabelling).")] = None,
    in_conflict: Annotated[bool | None, Query(description="Only members (or non-members) of open conflicts.")] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIST)] = 200,
):
    """Observations with the model's original output and their current review state."""
    _project(session, project_id)
    rows = _query(
        session,
        project_id,
        asset_id=asset_id,
        run_id=run_id,
        frame_index=frame_index,
        status=status,
        category=category,
        in_conflict=in_conflict,
        limit=limit,
    )
    return _views(session, rows)


@router.get("/observations/{observation_id}", response_model=ObservationDetail, responses=problem_responses(404))
def get_observation(observation_id: uuid.UUID, session: SessionDep):
    """One observation with its full revision history, conflicts and possible-duplicate links."""
    row = session.get(ObservationRecord, observation_id)
    if row is None:
        raise ProblemError(404, "OBSERVATION_NOT_FOUND", "Observation not found")
    return _detail(session, row)


@router.patch(
    "/observations/{observation_id}/review",
    response_model=ObservationDetail,
    responses=problem_responses(400, 401, 404, 409, 422),
)
def review_observation(
    observation_id: uuid.UUID, body: ReviewRequest, session: SessionDep, profile: ProfileDep, request_id: RequestIdDep
):
    """Record one review action. The model's output is kept unchanged; the action becomes a new revision."""
    try:
        row, revision = review(
            session,
            observation_id,
            action=body.action,
            note=body.note,
            category=body.category,
            expected_revision=body.expected_revision,
            reviewer=profile,
            request_id=request_id,
        )
    except ReviewError as exc:
        session.rollback()
        raise ProblemError(exc.status, exc.code, exc.title, exc.detail) from exc
    record_audit(
        session,
        actor=profile,
        action=f"observation.{body.action}",
        entity_type="observation",
        entity_id=row.id,
        request_id=request_id,
        details={
            "revision_no": revision.revision_no,
            "status": [revision.status_before, revision.status_after],
            "category": [revision.category_before, revision.category_after],
        },
    )
    session.commit()
    return _detail(session, row)


@router.get("/projects/{project_id}/conflicts", response_model=list[ConflictOut], responses=problem_responses(404))
def list_conflicts(
    project_id: uuid.UUID,
    session: SessionDep,
    status: Literal["open", "resolved_documented"] | None = None,
):
    """Conflicts between supported observations, with every member kept."""
    _project(session, project_id)
    stmt = select(Conflict).where(Conflict.project_id == project_id)
    if status:
        stmt = stmt.where(Conflict.status == status)
    return _conflicts(session, list(session.scalars(stmt.order_by(Conflict.created_at.desc())).all()))


# --- exports ------------------------------------------------------------------------------------------

CSV_COLUMNS = [
    ("observation_id", "observation_id"),
    ("asset_id", "asset_id"),
    ("source_file", "source_file"),
    ("source_sha256", "source_sha256"),
    ("pipeline", "pipeline"),
    ("model", "model"),
    ("model_version", "model_version"),
    ("run_id", "run_id"),
    ("model_category", "model_category (as the model output it)"),
    ("current_category", "current_category (after review)"),
    ("raw_score", "raw_score (model's own value, not a probability)"),
    ("score_calibrated", "score_calibrated"),
    ("review_status", "review_status (current)"),
    ("revision", "revision"),
    ("region_type", "region_type"),
    ("px_x_min", "px_x_min (source image pixels, top-left origin)"),
    ("px_y_min", "px_y_min (source image pixels, top-left origin)"),
    ("px_x_max", "px_x_max (source image pixels, top-left origin)"),
    ("px_y_max", "px_y_max (source image pixels, top-left origin)"),
    ("image_width_px", "image_width_px"),
    ("image_height_px", "image_height_px"),
    ("geometry_crs", "geometry_crs (empty: no geographic location)"),
    ("quality_flags", "quality_flags"),
    ("created_at", "created_at (UTC)"),
    # Video findings only: which sampled frame the pixel columns above refer to (empty for photographs).
    ("frame_index", "frame_index (video: 0-based frame number; the px columns are in that frame)"),
    ("video_timestamp_ms", "video_timestamp_ms (video: ms from the first frame)"),
]


def _export_rows(session: Session, project_id: uuid.UUID, status: list[ReviewStatusName] | None):
    rows = _query(session, project_id, status=status, limit=100_000)
    views = _views(session, rows)
    runs = (
        {r.id: r for r in session.scalars(select(PipelineRun).where(PipelineRun.id.in_({row.run_id for row in rows})))}
        if rows
        else {}
    )
    return rows, views, runs


def _audit_export(session: Session, profile, request_id: str, project_id: uuid.UUID, fmt: str, count: int, **extra):
    record_audit(
        session,
        actor=profile,
        action="export.observations",
        entity_type="project",
        entity_id=project_id,
        request_id=request_id,
        details={"format": fmt, "records": count, **extra},
    )
    session.commit()


def _filename(project_id: uuid.UUID, suffix: str) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"observations-{project_id}-{stamp}.{suffix}"


@router.get(
    "/projects/{project_id}/exports/observations.csv",
    response_class=Response,
    responses={200: {"content": {"text/csv": {}}}, **problem_responses(401, 404)},
)
def export_csv(
    project_id: uuid.UUID,
    session: SessionDep,
    profile: ProfileDep,
    request_id: RequestIdDep,
    status: Annotated[list[ReviewStatusName] | None, Query()] = None,
):
    """A table of observations. Image regions are in labelled pixel columns; nothing is presented as a map position.

    One row per observation and nothing else: map annotations and photograph locations are not rows of this table (they
    are in the JSON and GeoJSON exports and the findings report).
    """
    _project(session, project_id)
    _rows, views, _runs = _export_rows(session, project_id, status)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([header for _, header in CSV_COLUMNS])
    for view in views:
        o = view.observation
        region = o.evidence[0].region_px if o.evidence else None
        box = region.coordinates if region and region.type == "bbox_xyxy" else [None] * 4
        values: dict[str, Any] = {
            "observation_id": o.observation_id,
            "asset_id": o.asset_id,
            "source_file": view.asset_filename,
            "source_sha256": o.provenance.source_checksum,
            "pipeline": o.pipeline,
            "model": o.model.name if o.model else "",
            "model_version": o.model.version if o.model else "",
            "run_id": o.model.run_id if o.model else "",
            "model_category": o.claim.category,
            "current_category": view.current.category,
            "raw_score": "" if o.score.raw_value is None else o.score.raw_value,
            "score_calibrated": "yes" if o.score.calibrated else "no",
            "review_status": view.current.status,
            "revision": view.current.revision,
            "region_type": region.type if region else "",
            "px_x_min": box[0],
            "px_y_min": box[1],
            "px_x_max": box[2],
            "px_y_max": box[3],
            "image_width_px": region.image_width if region else "",
            "image_height_px": region.image_height if region else "",
            "geometry_crs": o.geospatial.crs_id or "",
            "quality_flags": ";".join(o.quality_flags),
            "created_at": o.provenance.created_at.isoformat(),
            "frame_index": o.evidence[0].frame_index if o.evidence else None,
            "video_timestamp_ms": o.evidence[0].timestamp_ms if o.evidence else None,
        }
        writer.writerow(["" if values[key] is None else values[key] for key, _ in CSV_COLUMNS])
    _audit_export(session, profile, request_id, project_id, "csv", len(views))
    return Response(
        buffer.getvalue().encode("utf-8-sig"),  # the BOM lets spreadsheet software detect UTF-8
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{_filename(project_id, "csv")}"'},
    )


@router.get(
    "/projects/{project_id}/exports/observations.geojson",
    response_class=Response,
    responses={200: {"content": {"application/geo+json": {}}}, **problem_responses(401, 404)},
)
def export_geojson(
    project_id: uuid.UUID,
    session: SessionDep,
    profile: ProfileDep,
    request_id: RequestIdDep,
    status: Annotated[list[ReviewStatusName] | None, Query()] = None,
):
    """Geographic features only. Observations without an established location are counted, never placed.

    Located photographs (`feature_type: photo_location`) and the project's active map annotations
    (`feature_type: user_annotation`) are separate features. Deleted annotations are not written as features; the
    `annotations` member counts them. The `status` filter applies to observations only.
    """
    _project(session, project_id)
    _rows, views, _runs = _export_rows(session, project_id, status)
    features, excluded = [], 0
    for view in views:
        o = view.observation
        if o.geospatial.geometry is None or o.geospatial.crs_id != "EPSG:4326":
            excluded += 1
            continue
        features.append(
            {
                "type": "Feature",
                "id": str(o.observation_id),
                "geometry": o.geospatial.geometry,
                "properties": {
                    "current_category": view.current.category,
                    "model_category": o.claim.category,
                    "review_status": view.current.status,
                    "raw_score": o.score.raw_value,
                    "score_calibrated": o.score.calibrated,
                    "derivation_method": o.geospatial.derivation_method,
                    "source_file": view.asset_filename,
                },
            }
        )
    photo_features, photo_summary = photolocation.geojson_features(session, project_id)
    annotation_features, annotation_summary = annotation_export.geojson_features(
        annotation_export.collect(session, project_id)
    )
    document = {
        "type": "FeatureCollection",
        "features": [*features, *photo_features, *annotation_features],
        # Foreign members (RFC 7946 section 6.1): what the file does and does not contain.
        "coordinate_reference": "WGS 84 longitude, latitude in decimal degrees (RFC 7946)",
        "excluded": {
            "count": excluded,
            "reason": "These observations have no established geographic location (for example findings in "
            "ground photographs, which are positions in the image only). They are not placed on the map or "
            "written as features. They are available in the CSV and JSON exports in pixel columns.",
        },
        # Located photographs are separate features (properties.feature_type == "photo_location"); `excluded` above
        # counts observations only.
        "photographs": photo_summary,
        # User-drawn map annotations are separate features (properties.feature_type == "user_annotation").
        "annotations": annotation_summary,
        "exported_at": datetime.now(UTC).isoformat(),
    }
    _audit_export(
        session,
        profile,
        request_id,
        project_id,
        "geojson",
        len(features),
        excluded=excluded,
        photos_located=photo_summary["located"],
        annotations_exported=annotation_summary["exported"],
        annotations_deleted_not_exported=annotation_summary["deleted_not_exported"],
    )
    return Response(
        json.dumps(document, indent=2, default=str),
        media_type="application/geo+json",
        headers={"Content-Disposition": f'attachment; filename="{_filename(project_id, "geojson")}"'},
    )


@router.get(
    "/projects/{project_id}/exports/observations.json",
    response_class=Response,
    responses={200: {"content": {"application/json": {}}}, **problem_responses(401, 404)},
)
def export_json(
    project_id: uuid.UUID,
    session: SessionDep,
    profile: ProfileDep,
    request_id: RequestIdDep,
    status: Annotated[list[ReviewStatusName] | None, Query()] = None,
):
    """Everything stored: original model output, current state, revision history, conflicts and run provenance.

    The `annotations` member lists the project's map annotations, active and deleted (each marked by `status`), with
    every version. They are never mixed into `observations`. The `status` filter applies to observations only.
    """
    project = _project(session, project_id)
    rows, views, runs = _export_rows(session, project_id, status)
    revisions: dict[uuid.UUID, list[dict[str, Any]]] = defaultdict(list)
    if rows:
        for r in session.scalars(
            select(ObservationRevision)
            .where(ObservationRevision.observation_id.in_([row.id for row in rows]))
            .order_by(ObservationRevision.observation_id, ObservationRevision.revision_no)
        ):
            revisions[r.observation_id].append(
                RevisionOut.model_validate(r, from_attributes=True).model_dump(mode="json")
            )
    conflict_rows = session.scalars(
        select(Conflict).where(and_(Conflict.project_id == project_id)).order_by(Conflict.created_at)
    ).all()
    annotations = annotation_export.json_section(annotation_export.collect(session, project_id))
    document = {
        "project": {"id": str(project.id), "name": project.name},
        "exported_at": datetime.now(UTC).isoformat(),
        "filter": {"status": status or "all"},
        "notes": [
            "`observation` is the model's output exactly as validated and stored; it is never changed.",
            "`current` is the state after human review. Raw scores are the model's own values, not probabilities.",
        ],
        "observations": [
            {**view.model_dump(mode="json"), "revisions": revisions.get(view.observation.observation_id, [])}
            for view in views
        ],
        "conflicts": [c.model_dump(mode="json") for c in _conflicts(session, list(conflict_rows))],
        "photo_locations": photolocation.export_history(session, project_id),
        "annotations": annotations,
        "runs": [
            {
                "id": str(run.id),
                "job_id": str(run.job_id),
                "asset_id": str(run.asset_id),
                "pipeline": run.pipeline,
                "model": f"{run.model_name} {run.model_version}",
                "weights_sha256": run.weights_sha256,
                "settings": run.settings,
                "device": run.device,
                "started_at": run.started_at.isoformat(),
                "finished_at": run.finished_at.isoformat(),
            }
            for run in runs.values()
        ],
    }
    _audit_export(
        session,
        profile,
        request_id,
        project_id,
        "json",
        len(views),
        annotations_active=annotations["active"],
        annotations_deleted=annotations["deleted"],
    )
    return Response(
        json.dumps(document, indent=2, default=str),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{_filename(project_id, "json")}"'},
    )
