"""The findings report: one read-only document assembled from what is already stored for one project.

Nothing here writes, derives or estimates evidence. A value that is not stored is `None` and is listed in the record's
`unavailable_fields`; it is never replaced by a default, a zero or a guess. The queries reuse the observation and photo
location helpers the exports already use, so the report and the exports agree on what a record is.
"""

import uuid
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from geopoc import annotation_export
from geopoc.api.routers.assets import preview_info
from geopoc.api.routers.observations import _conflicts, _project, _query, _views
from geopoc.api.schemas import AnnotationOut, ConflictOut, ReviewStatusName
from geopoc.contracts.observation import PixelRegion
from geopoc.db.models import (
    Asset,
    Conflict,
    ObservationRecord,
    ObservationRevision,
    PhotoLocation,
    PipelineRun,
    WorkspaceUser,
)
from geopoc.geo import photolocation

REPORT_SCHEMA_VERSION = "1.1"  # 1.1 adds `annotations` and the two annotation counts; nothing else changed
MAX_REPORT_OBSERVATIONS = 100_000  # the same ceiling the exports use

ALL_STATUSES: tuple[ReviewStatusName, ...] = (
    "proposed",
    "needs_review",
    "verified",
    "inconclusive",
    "disputed",
    "rejected",
)
REVIEWED_STATUSES = frozenset({"verified", "rejected", "inconclusive"})

COUNTING_RULES = [
    "Findings: every stored observation of this project, counted once, whatever its status. Observations of other "
    "projects are never included.",
    "Reviewed: the current status is verified, rejected or inconclusive, which means a reviewer has concluded "
    "something about the finding.",
    "Unreviewed: the current status is proposed, needs_review or disputed, so nothing is concluded. Disputed means "
    "supported findings disagree and no documented rule has resolved it. The count of each status is shown as well.",
    "A note or a relabel that leaves the status unchanged does not make a finding reviewed.",
    "Located finding: the finding itself stores a geographic geometry. Unlocated: it stores none. A finding in a "
    "photograph is a region of pixels and is unlocated; the position of the photograph is never copied onto it.",
    "Photographs: the project's ground images. Confirmed location: the latest location record is a position that a "
    "person confirmed, typed or chose on the map. Unconfirmed candidate: a GPS position was read from the file but no "
    "person has confirmed it; it is not a location and its coordinates are not shown. No location: nothing is "
    "recorded, or the person cleared it.",
    "Confidence scores: 'calibrated' only where the stored score states that it is calibrated. 'Unavailable' means the "
    "observation stores no score value. These are three separate counts, and no count stands for another.",
    "Map annotations: marks a person drew on the map, with a note. They are not findings and are not included in any "
    "finding, photograph or location count. Active annotations and deleted annotations are counted separately; a "
    "deleted annotation is listed apart, marked deleted, and is not an active mark. Each annotation is counted "
    "once, at its current version.",
]

LIMITATIONS = [
    "Model results require human review. A finding that no person has reviewed is the model's proposal, not an "
    "established fact.",
    "A confidence score is the model's own value. Unless an observation states that its score is calibrated, it is "
    "not a probability and should not be read as one. Scores from different models are not comparable.",
    "A confirmed photograph position is where a person records the camera as being, based on the file's GPS data, "
    "typed coordinates or a point on a map. It is not the precise location of any object in the photograph, and it "
    "has no stated accuracy unless one is shown.",
    "A finding in a photograph is a region of pixels in the original image. No geographic coordinate is derived from "
    "it.",
    "Where this report says a value is unavailable, it is not stored for this project. It has not been estimated, "
    "defaulted or taken from elsewhere.",
    "The report is a read-only summary of stored records at the time shown. It does not replace the original files, "
    "which are kept unchanged and are identified here by their SHA-256 checksums.",
    "A map annotation is a mark a person drew, with a note. It is not a detection or a finding, and the software "
    "cannot check that it is correct. Its author is the local profile that made the change: profiles are not "
    "authenticated, so this is attribution, not proof of identity. A length or area is a geodesic figure computed "
    "from the drawn vertices, not a survey measurement.",
]


class ReportProject(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    created_at: datetime
    created_by: str
    archived_at: datetime | None


class ReportAsset(BaseModel):
    asset_id: uuid.UUID
    filename: str
    modality: str
    media_type: str
    detected_format: str
    size_bytes: int
    sha256: str
    status: str
    ingested_at: datetime
    uploaded_by: str
    duplicate_of_asset_id: uuid.UUID | None
    quality_flags: list[str]
    thumbnail_available: bool
    observation_count: int


class ReportRun(BaseModel):
    run_id: uuid.UUID
    asset_id: uuid.UUID
    asset_filename: str
    pipeline: str
    model_name: str
    model_version: str
    weights_sha256: str | None
    device: str | None
    settings: dict[str, Any]
    started_at: datetime
    finished_at: datetime
    observation_count: int
    unavailable_fields: list[str]


class ReportRevision(BaseModel):
    revision_no: int
    action: str
    status_before: ReviewStatusName
    status_after: ReviewStatusName
    category_before: str
    category_after: str
    note: str | None
    actor_kind: Literal["reviewer", "system"]
    actor_label: str
    created_at: datetime


class ReportEvidence(BaseModel):
    asset_id: uuid.UUID
    asset_filename: str | None
    region: PixelRegion | None
    frame_index: int | None
    timestamp_ms: int | None
    source_excerpt_or_metadata_key: str | None


class ReportModel(BaseModel):
    name: str
    version: str
    run_id: uuid.UUID


class ReportObservation(BaseModel):
    observation_id: uuid.UUID
    asset_id: uuid.UUID
    asset_filename: str
    pipeline: str
    claim_description: str
    claim_type: str
    model: ReportModel | None
    model_category: str
    current_category: str
    relabelled: bool
    score_value: float | None
    score_semantics: str
    score_calibrated: bool
    score_range: list[float] | None
    review_status: ReviewStatusName
    review_group: Literal["reviewed", "unreviewed"]
    revision: int
    evidence: list[ReportEvidence]
    quality_flags: list[str]
    geographically_located: bool
    open_conflict_ids: list[uuid.UUID]
    reviewer_notes: list[ReportRevision]
    revisions: list[ReportRevision]
    created_at: datetime
    unavailable_fields: list[str]


class ReportConfirmedLocation(BaseModel):
    latitude: float
    longitude: float
    crs_id: str
    source: str
    source_label: str
    confirmed_by: str
    confirmed_at: datetime
    version_no: int
    reason: str | None


class ReportLocationStep(BaseModel):
    """One history row. Coordinates are shown only for a row a person confirmed; a candidate row never shows them."""

    version_no: int
    status: Literal["candidate", "confirmed", "cleared"]
    source: str
    latitude: float | None
    longitude: float | None
    coordinates_withheld: bool
    crs_id: str
    candidate_version_no: int | None
    reason: str | None
    actor: str
    at: datetime


class ReportPhoto(BaseModel):
    asset_id: uuid.UUID
    filename: str
    sha256: str
    thumbnail_available: bool
    state: Literal["confirmed", "unconfirmed_candidate", "none"]
    confirmed: ReportConfirmedLocation | None
    history: list[ReportLocationStep]
    observation_count: int


class ReportSummary(BaseModel):
    assets_total: int
    runs_total: int
    observations_total: int
    observations_by_review_status: dict[str, int]
    observations_reviewed: int
    observations_unreviewed: int
    observations_located: int
    observations_unlocated: int
    observations_with_reviewer_notes: int
    scores_calibrated: int
    scores_not_calibrated: int
    scores_unavailable: int
    conflicts_open: int
    conflicts_resolved: int
    photos_total: int
    photos_confirmed_location: int
    photos_unconfirmed_candidate: int
    photos_without_location: int
    annotations_active: int | None  # null: this database has no annotation table, so nothing can be counted
    annotations_deleted: int | None


class ReportAnnotationVersion(BaseModel):
    version_no: int
    action: Literal["create", "update", "delete"]
    status: Literal["active", "deleted"]
    note: str
    reason: str | None
    actor_label: str
    created_at: datetime
    geometry_changed: bool = Field(description="False when this version kept the shape of the one before it.")


class ReportAnnotation(AnnotationOut):
    """The annotation as it is now (`status` says active or deleted) and every version it has had, oldest first."""

    versions: list[ReportAnnotationVersion]


class ReportAnnotations(BaseModel):
    """User-drawn map annotations. Apart from the findings: they are not observations and are not counted as such."""

    available: bool = Field(description="False when the database has not been updated for annotations.")
    note: str
    active: int | None
    deleted: int | None
    items: list[ReportAnnotation] = Field(description="Active and deleted annotations, oldest first; see `status`.")


class FindingsReport(BaseModel):
    """The whole report. Every list is the stored data of this project only; an empty list means nothing is stored."""

    schema_version: str
    generated_at: datetime
    generated_by: str
    read_only: bool
    project: ReportProject
    summary: ReportSummary
    counting_rules: list[str]
    assets: list[ReportAsset]
    runs: list[ReportRun]
    observations: list[ReportObservation]
    photo_locations: list[ReportPhoto]
    conflicts: list[ConflictOut]
    annotations: ReportAnnotations
    limitations: list[str]


def _annotation_section(collected: annotation_export.Collected) -> ReportAnnotations:
    items = []
    for item in collected.items:
        rows = collected.versions[item.annotation_id]
        items.append(
            ReportAnnotation(
                **item.model_dump(),
                versions=[
                    ReportAnnotationVersion(
                        version_no=r.version_no,
                        action=r.action,  # type: ignore[arg-type]
                        status=r.status,  # type: ignore[arg-type]
                        note=r.note,
                        reason=r.reason,
                        actor_label=r.actor_label,
                        created_at=r.created_at,
                        geometry_changed=changed,
                    )
                    for r, changed in zip(rows, annotation_export.geometry_changed(rows), strict=True)
                ],
            )
        )
    return ReportAnnotations(
        available=collected.available,
        note=collected.note,
        active=collected.active,
        deleted=collected.deleted,
        items=items,
    )


def _revision_out(r: ObservationRevision) -> ReportRevision:
    return ReportRevision(
        revision_no=r.revision_no,
        action=r.action,
        status_before=r.status_before,  # type: ignore[arg-type]
        status_after=r.status_after,  # type: ignore[arg-type]
        category_before=r.category_before,
        category_after=r.category_after,
        note=r.note,
        actor_kind=r.actor_kind,  # type: ignore[arg-type]
        actor_label=r.actor_label,
        created_at=r.created_at,
    )


def _photo_entries(session: Session, project_id: uuid.UUID, photos: list[Asset], obs_by_asset: dict[uuid.UUID, int]):
    """One entry per project photograph. History coordinates are withheld for candidate rows."""
    history: dict[uuid.UUID, list[PhotoLocation]] = defaultdict(list)
    for row in session.scalars(
        select(PhotoLocation).where(PhotoLocation.project_id == project_id).order_by(PhotoLocation.version_no)
    ):
        history[row.asset_id].append(row)
    previews = preview_info(session, [a.id for a in photos])
    entries = []
    for asset in photos:
        rows = history.get(asset.id, [])
        latest = photolocation.current(rows)
        confirmed = None
        if latest is not None and photolocation.is_located(latest) and None not in (latest.latitude, latest.longitude):
            state = "confirmed"
            confirmed = ReportConfirmedLocation(
                latitude=latest.latitude,  # type: ignore[arg-type]
                longitude=latest.longitude,  # type: ignore[arg-type]
                crs_id=latest.crs_id,
                source=latest.source,
                source_label=photolocation.SOURCE_LABELS.get(latest.source, latest.source),
                confirmed_by=latest.actor_label,
                confirmed_at=latest.created_at,
                version_no=latest.version_no,
                reason=latest.reason,
            )
        elif latest is not None and latest.status == "candidate":
            state = "unconfirmed_candidate"
        else:
            state = "none"
        entries.append(
            ReportPhoto(
                asset_id=asset.id,
                filename=asset.original_filename,
                sha256=asset.blob_sha256,
                thumbnail_available="thumb" in previews[asset.id].variants,
                state=state,
                confirmed=confirmed,
                history=[
                    ReportLocationStep(
                        version_no=r.version_no,
                        status=r.status,  # type: ignore[arg-type]
                        source=r.source,
                        latitude=r.latitude if r.status == "confirmed" else None,
                        longitude=r.longitude if r.status == "confirmed" else None,
                        coordinates_withheld=r.status == "candidate",
                        crs_id=r.crs_id,
                        candidate_version_no=r.candidate_version_no,
                        reason=r.reason,
                        actor=r.actor_label,
                        at=r.created_at,
                    )
                    for r in rows
                ],
                observation_count=obs_by_asset.get(asset.id, 0),
            )
        )
    return entries


def build_findings_report(session: Session, project_id: uuid.UUID, generated_by: str) -> FindingsReport:
    """Assemble the report. Only reads; raises ProblemError(404) for an unknown project."""
    project = _project(session, project_id)

    asset_rows = list(
        session.scalars(select(Asset).where(Asset.project_id == project_id).order_by(Asset.ingested_at, Asset.id))
        .unique()
        .all()
    )
    asset_names = {a.id: a.original_filename for a in asset_rows}
    previews = preview_info(session, [a.id for a in asset_rows])

    rows: list[ObservationRecord] = _query(session, project_id, limit=MAX_REPORT_OBSERVATIONS)
    views = _views(session, rows)
    obs_by_asset: dict[uuid.UUID, int] = defaultdict(int)
    for row in rows:
        obs_by_asset[row.asset_id] += 1
    obs_by_run: dict[uuid.UUID, int] = defaultdict(int)
    for row in rows:
        obs_by_run[row.run_id] += 1
    created_at = {row.id: row.created_at for row in rows}

    revisions: dict[uuid.UUID, list[ReportRevision]] = defaultdict(list)
    for revision in session.scalars(
        select(ObservationRevision)
        .join(ObservationRecord, ObservationRecord.id == ObservationRevision.observation_id)
        .where(ObservationRecord.project_id == project_id)
        .order_by(ObservationRevision.observation_id, ObservationRevision.revision_no)
    ):
        revisions[revision.observation_id].append(_revision_out(revision))

    runs = [
        ReportRun(
            run_id=run.id,
            asset_id=run.asset_id,
            asset_filename=asset_names.get(run.asset_id, ""),
            pipeline=run.pipeline,
            model_name=run.model_name,
            model_version=run.model_version,
            weights_sha256=run.weights_sha256,
            device=run.device,
            settings=run.settings,
            started_at=run.started_at,
            finished_at=run.finished_at,
            observation_count=obs_by_run.get(run.id, 0),
            unavailable_fields=[
                name for name, value in (("weights_sha256", run.weights_sha256), ("device", run.device)) if not value
            ],
        )
        for run in session.scalars(
            select(PipelineRun)
            .where(PipelineRun.project_id == project_id)
            .order_by(PipelineRun.started_at, PipelineRun.id)
        )
    ]

    observations: list[ReportObservation] = []
    for view in views:
        o = view.observation
        history = revisions.get(o.observation_id, [])
        regions = [e.region_px for e in o.evidence if e.region_px is not None]
        missing = []
        if o.model is None:
            missing.append("model")
        if o.score.raw_value is None:
            missing.append("score")
        if not regions:
            missing.append("evidence_region")
        observations.append(
            ReportObservation(
                observation_id=o.observation_id,
                asset_id=o.asset_id,
                asset_filename=view.asset_filename,
                pipeline=o.pipeline,
                claim_description=o.claim.description,
                claim_type=o.claim.claim_type,
                model=ReportModel(name=o.model.name, version=o.model.version, run_id=o.model.run_id)
                if o.model
                else None,
                model_category=o.claim.category,
                current_category=view.current.category,
                relabelled=view.current.relabelled,
                score_value=o.score.raw_value,
                score_semantics=o.score.semantics,
                score_calibrated=o.score.calibrated,
                score_range=list(o.score.value_range) if o.score.value_range else None,
                review_status=view.current.status,
                review_group="reviewed" if view.current.status in REVIEWED_STATUSES else "unreviewed",
                revision=view.current.revision,
                evidence=[
                    ReportEvidence(
                        asset_id=e.asset_id,
                        asset_filename=asset_names.get(e.asset_id),
                        region=e.region_px,
                        frame_index=e.frame_index,
                        timestamp_ms=e.timestamp_ms,
                        source_excerpt_or_metadata_key=e.source_excerpt_or_metadata_key,
                    )
                    for e in o.evidence
                ],
                quality_flags=list(o.quality_flags),
                geographically_located=o.geospatial.geometry is not None,
                open_conflict_ids=view.open_conflict_ids,
                reviewer_notes=[r for r in history if r.note],
                revisions=history,
                created_at=created_at[o.observation_id],
                unavailable_fields=missing,
            )
        )

    conflict_rows = list(
        session.scalars(
            select(Conflict).where(Conflict.project_id == project_id).order_by(Conflict.created_at, Conflict.id)
        )
    )
    conflicts = _conflicts(session, conflict_rows)

    photos = [a for a in asset_rows if a.modality == "ground_image"]
    photo_entries = _photo_entries(session, project_id, photos, obs_by_asset)

    annotations = _annotation_section(annotation_export.collect(session, project_id))

    by_status = {name: 0 for name in ALL_STATUSES}
    for item in observations:
        by_status[item.review_status] += 1
    reviewed = sum(1 for item in observations if item.review_group == "reviewed")
    located = sum(1 for item in observations if item.geographically_located)
    summary = ReportSummary(
        assets_total=len(asset_rows),
        runs_total=len(runs),
        observations_total=len(observations),
        observations_by_review_status=by_status,
        observations_reviewed=reviewed,
        observations_unreviewed=len(observations) - reviewed,
        observations_located=located,
        observations_unlocated=len(observations) - located,
        observations_with_reviewer_notes=sum(1 for item in observations if item.reviewer_notes),
        scores_calibrated=sum(1 for item in observations if item.score_value is not None and item.score_calibrated),
        scores_not_calibrated=sum(
            1 for item in observations if item.score_value is not None and not item.score_calibrated
        ),
        scores_unavailable=sum(1 for item in observations if item.score_value is None),
        conflicts_open=sum(1 for c in conflicts if c.status == "open"),
        conflicts_resolved=sum(1 for c in conflicts if c.status != "open"),
        photos_total=len(photo_entries),
        photos_confirmed_location=sum(1 for p in photo_entries if p.state == "confirmed"),
        photos_unconfirmed_candidate=sum(1 for p in photo_entries if p.state == "unconfirmed_candidate"),
        photos_without_location=sum(1 for p in photo_entries if p.state == "none"),
        annotations_active=annotations.active,
        annotations_deleted=annotations.deleted,
    )

    creator = session.get(WorkspaceUser, project.created_by)
    return FindingsReport(
        schema_version=REPORT_SCHEMA_VERSION,
        generated_at=datetime.now(UTC),
        generated_by=generated_by,
        read_only=True,
        project=ReportProject(
            id=project.id,
            name=project.name,
            description=project.description,
            created_at=project.created_at,
            created_by=creator.display_name if creator else "",
            archived_at=project.archived_at,
        ),
        summary=summary,
        counting_rules=COUNTING_RULES,
        assets=[
            ReportAsset(
                asset_id=a.id,
                filename=a.original_filename,
                modality=a.modality,
                media_type=a.media_type,
                detected_format=a.detected_format,
                size_bytes=a.size_bytes,
                sha256=a.blob_sha256,
                status=a.status,
                ingested_at=a.ingested_at,
                uploaded_by=a.uploader.display_name if a.uploader else "",
                duplicate_of_asset_id=a.duplicate_of_asset_id,
                quality_flags=list(a.quality_flags or []),
                thumbnail_available="thumb" in previews[a.id].variants,
                observation_count=obs_by_asset.get(a.id, 0),
            )
            for a in asset_rows
        ],
        runs=runs,
        observations=observations,
        photo_locations=photo_entries,
        conflicts=conflicts,
        annotations=annotations,
        limitations=LIMITATIONS,
    )
