"""Sidecar metadata files (.tfw/.tifw/.tiffw/.wld, .prj, .aux.xml) for rasters: upload, review, confirm.

A sidecar is stored unmodified and linked to one raster, either because its name matches the raster's or because a
person attached it to that raster. It is never applied on its own: the review shows what it says, its problems, what
was not used, and how it compares with the raster's own metadata and the state in use. A change is recorded only
with a reason and an explicit confirmation, as a new georeferencing version that went through the same validation,
checksum verification, audit and tile versioning as georeferencing entered by hand.
"""

import hashlib
import uuid
from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.requests import ClientDisconnect

from geopoc.api.deps import ProfileDep, RequestIdDep, SessionDep, record_audit
from geopoc.api.errors import ProblemError, problem_responses
from geopoc.api.routers.assets import BINARY_BODY, _Rejection
from geopoc.api.routers.georeferencing import EXPLANATIONS, _out, _state
from geopoc.api.schemas import (
    GeoreferenceCheck,
    GeoreferenceValidation,
    GeoreferencingOut,
    SidecarApply,
    SidecarChoice,
    SidecarComparison,
    SidecarOut,
    SidecarProposalOut,
    SidecarRequest,
    SidecarReviewOut,
    SidecarUploadOut,
    SidecarValidation,
)
from geopoc.db.models import Asset, Blob, Project, Sidecar, utcnow
from geopoc.geo import georeference, sidecars
from geopoc.geo.georeference import Check, GeoreferenceError
from geopoc.ingestion import rules
from geopoc.storage import store

router = APIRouter(prefix="/api/v1", tags=["sidecars"], responses=problem_responses(422))

NOTE = (
    "Sidecar files are kept exactly as received and are never applied on their own. A world file gives pixel "
    "coordinates but no CRS; a .prj gives a CRS but no coordinates. A CRS is accepted only when the local registry has "
    "exactly one definition that matches it completely. Metadata in the raster file that is valid is never replaced. "
    "Band metadata from .aux.xml files is shown for information and not applied."
)


# --- helpers ----------------------------------------------------------------------------------------------------------


def _raster(session: Session, asset_id: uuid.UUID) -> Asset:
    asset = session.get(Asset, asset_id)
    if asset is None:
        raise ProblemError(404, "ASSET_NOT_FOUND", "Asset not found")
    if asset.modality != "overhead_raster" or asset.raster is None:
        raise ProblemError(409, "NOT_A_RASTER", "Sidecar files belong to rasters only")
    return asset


def _rows(session: Session, asset_id: uuid.UUID) -> list[Sidecar]:
    return list(
        session.scalars(
            select(Sidecar).where(Sidecar.asset_id == asset_id).order_by(Sidecar.uploaded_at, Sidecar.id)
        ).unique()
    )


def _read(session: Session, row: Sidecar) -> bytes:
    """The stored bytes of a sidecar, after checking them against the checksum recorded at upload."""
    blob = session.get(Blob, row.blob_sha256)
    path = store.absolute_path(blob.storage_path) if blob else None
    if path is None or not path.is_file():
        raise ProblemError(500, "STORAGE_ERROR", "A stored sidecar file is missing", row.original_filename)
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != row.blob_sha256:
        raise ProblemError(
            409,
            "SIDECAR_CHANGED",
            "A stored sidecar file no longer matches its checksum",
            f"{row.original_filename} was changed after it was received. It is not used.",
        )
    return data


def _contents(session: Session, rows: list[Sidecar]) -> dict[str, bytes]:
    return {str(row.id): _read(session, row) for row in rows}


def _review(session: Session, asset: Asset, body: SidecarRequest | None = None) -> sidecars.Review:
    rows = _rows(session, asset.id)
    try:
        return sidecars.review(
            asset,
            asset.raster,
            rows,
            _contents(session, rows),
            geotransform_from=str(body.geotransform_from) if body and body.geotransform_from else None,
            crs_from=str(body.crs_from) if body and body.crs_from else None,
        )
    except ValueError as exc:
        raise ProblemError(422, "SIDECAR_CHOICE_INVALID", "That sidecar cannot be chosen", str(exc)) from exc


def _sidecar_out(entry: sidecars.Entry) -> SidecarOut:
    row, parsed = entry.row, entry.parsed
    return SidecarOut(
        id=row.id,
        kind=row.kind,
        kind_label=sidecars.KIND_LABELS[row.kind],
        filename=row.original_filename,
        sha256=row.blob_sha256,
        size_bytes=row.size_bytes,
        pairing=row.pairing,
        uploaded_by=row.uploader.display_name,
        uploaded_at=row.uploaded_at,
        current=entry.current,
        status=parsed.status,
        summary=parsed.summary,
        values=rules.json_safe(parsed.values),
        problems=parsed.problems,
        ignored=parsed.ignored,
        comparisons=[SidecarComparison(**rules.json_safe(c)) for c in entry.comparisons],
        role=entry.role,
    )


def _review_out(asset: Asset, result: sidecars.Review) -> SidecarReviewOut:
    proposal = result.proposal
    return SidecarReviewOut(
        asset_id=asset.id,
        expected_version=asset.raster.georef_version,
        sidecar_ids=[e.row.id for e in result.entries],
        state=result.state,
        title=result.title,
        explanation=result.explanation,
        conflicts=result.conflicts,
        choices={k: [SidecarChoice(**c) for c in v] for k, v in result.choices.items()},
        proposal=SidecarProposalOut(
            operation=proposal.operation,
            crs_id=proposal.crs_id,
            geotransform=proposal.geotransform
            if proposal.geotransform is not None
            else (georeference.parse_world_file(proposal.world_file) if proposal.world_file else None),
            world_file=proposal.world_file,
            geotransform_source=proposal.geotransform_source,
            sources=result.sources,
        )
        if proposal is not None
        else None,
        sidecars=[_sidecar_out(e) for e in result.entries],
        accepted_extensions=list(sidecars.SIDECAR_EXTENSIONS),
        note=NOTE,
    )


def _used(result: sidecars.Review) -> list[sidecars.Entry]:
    """The sidecars the proposal uses: the geotransform's source first, then the CRS's (each once)."""
    by_id = {str(e.row.id): e for e in result.entries}
    ordered = [result.sources.get(k, {}).get("sidecar_id") for k in ("geotransform", "crs")]
    return [by_id[i] for i in dict.fromkeys(ordered) if i in by_id]


def _extra_checks(result: sidecars.Review) -> list[Check]:
    checks = [
        Check(
            "SIDECAR_UNCHANGED",
            "ok",
            f"{e.row.original_filename} matches the SHA-256 recorded when it was received ({e.row.blob_sha256[:16]}…).",
        )
        for e in _used(result)
    ]
    checks.extend(
        Check("SIDECAR_CONFLICT", "warning", f"{c} The raster file's value is kept.") for c in result.conflicts
    )
    for entry in _used(result):
        checks.extend(
            Check("SIDECAR_PROBLEM", "warning", f"{entry.row.original_filename}: {p}") for p in entry.parsed.problems
        )
    return checks


# --- upload -----------------------------------------------------------------------------------------------------------


async def _receive(request: Request, limit: int) -> bytes | None:
    """The request body, or None if it is larger than the limit."""
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


def _store(session: Session, data: bytes, sha256: str) -> bool:
    """Store the bytes once, read-only, like an original. Returns whether a new file was written."""
    blob = session.get(Blob, sha256)
    if blob is not None and store.absolute_path(blob.storage_path).is_file():
        return False
    if blob is None:
        session.add(Blob(sha256=sha256, size_bytes=len(data), storage_path=store.relative_path(sha256)))
        session.flush()
    temp = store.new_temp_path()
    try:
        temp.write_bytes(data)
        store.commit_original(temp, sha256)
    finally:
        store.discard_temp(temp)
    return True


@router.post(
    "/projects/{project_id}/sidecars",
    response_model=SidecarUploadOut,
    status_code=201,
    openapi_extra=BINARY_BODY,
    responses=problem_responses(400, 401, 404, 409, 413, 415, 500),
)
async def upload_sidecar(
    project_id: uuid.UUID,
    request: Request,
    response: Response,
    session: SessionDep,
    profile: ProfileDep,
    request_id: RequestIdDep,
    filename: Annotated[str, Query(min_length=1, max_length=500, description="Original file name")],
    asset_id: Annotated[
        uuid.UUID | None,
        Query(description="The raster it belongs to. If omitted, the raster whose file name matches is used."),
    ] = None,
):
    """Add a world file, .prj or .aux.xml for a raster. Stored unmodified for review; nothing is applied."""
    project = session.get(Project, project_id)
    if project is None:
        raise ProblemError(404, "PROJECT_NOT_FOUND", "Project not found")
    if project.archived_at is not None:
        raise ProblemError(409, "PROJECT_ARCHIVED", "This project is archived", "Restore it before adding files.")
    name = rules.clean_filename(filename)
    reject = _Rejection(
        session, project, profile, request_id, {"filename": name, "asset_id": str(asset_id) if asset_id else None},
        action="sidecar.upload_rejected",
    )  # fmt: skip
    naming = sidecars.naming(name) if name else None
    if naming is None:
        raise reject(
            415,
            "SIDECAR_UNSUPPORTED_TYPE",
            "This is not a supported sidecar file",
            f"Sidecar files end in {', '.join(sidecars.SIDECAR_EXTENSIONS)}.",
        )

    limit = sidecars.MAX_BYTES[naming.kind]
    try:
        data = await _receive(request, limit)
    except ClientDisconnect:
        raise reject(400, "UPLOAD_INTERRUPTED", "The upload was interrupted", "Nothing was stored.") from None
    if data is None:
        raise reject(
            413,
            "FILE_TOO_LARGE",
            "The sidecar file is too large",
            f"The limit for a {sidecars.KIND_LABELS[naming.kind]} is {limit // 1024} KB.",
        )
    if not data:
        raise reject(422, "EMPTY_FILE", "The file is empty", "Nothing was stored.")

    # --- which raster it belongs to ---
    if asset_id is not None:
        asset = session.get(Asset, asset_id)
        if asset is None or asset.project_id != project.id:
            raise reject(404, "ASSET_NOT_FOUND", "That raster is not in this project", "Nothing was stored.")
        if asset.modality != "overhead_raster" or asset.raster is None:
            raise reject(409, "NOT_A_RASTER", "Sidecar files belong to rasters only", "Nothing was stored.")
        pairing = "filename_match" if sidecars.pairs_with(naming, asset.original_filename) else "explicit"
    else:
        candidates = [
            a
            for a in session.scalars(
                select(Asset).where(Asset.project_id == project.id, Asset.modality == "overhead_raster")
            ).unique()
            if a.raster is not None and sidecars.pairs_with(naming, a.original_filename)
        ]
        if not candidates:
            raise reject(
                422,
                "SIDECAR_NO_MATCHING_RASTER",
                "No raster in this project matches this sidecar's name",
                f"{name} would belong to a raster named {naming.raster_name or naming.stem + '.tif'}. Upload the "
                "raster first, or attach the file from the raster's page. Nothing was stored.",
            )
        if len(candidates) > 1:
            raise reject(
                409,
                "SIDECAR_PAIRING_AMBIGUOUS",
                "Several rasters match this sidecar's name",
                f"{', '.join(sorted(a.original_filename for a in candidates))} all match. Attach the file from the "
                "right raster's page. Nothing was stored.",
            )
        asset, pairing = candidates[0], "filename_match"

    sha256 = hashlib.sha256(data).hexdigest()
    existing = session.scalars(
        select(Sidecar).where(Sidecar.asset_id == asset.id, Sidecar.blob_sha256 == sha256, Sidecar.kind == naming.kind)
    ).first()
    if existing is not None:
        rows = _rows(session, asset.id)
        result = sidecars.review(asset, asset.raster, rows, _contents(session, rows))
        entry = next(e for e in result.entries if e.row.id == existing.id)
        response.status_code = 200
        return SidecarUploadOut(
            sidecar=_sidecar_out(entry),
            asset_id=asset.id,
            raster_filename=asset.original_filename,
            duplicate=True,
            message=f"Identical content was already added for {asset.original_filename} as "
            f"{existing.original_filename}. Nothing new was stored.",
        )

    parsed = sidecars.parse(naming.kind, data, asset.raster)
    try:
        written = await run_in_threadpool(_store, session, data, sha256)
    except (store.StorageError, OSError) as exc:
        raise reject(500, "STORAGE_ERROR", "The sidecar could not be stored", "Nothing was stored.") from exc
    row = Sidecar(
        id=uuid.uuid4(),
        project_id=project.id,
        asset_id=asset.id,
        kind=naming.kind,
        original_filename=name,
        blob_sha256=sha256,
        size_bytes=len(data),
        pairing=pairing,
        upload_status=parsed.status,
        parser_version=sidecars.PARSER_VERSION,
        uploaded_by=profile.id,
        uploaded_at=utcnow(),
        request_id=request_id,
    )
    session.add(row)
    record_audit(
        session,
        actor=profile,
        action="sidecar.upload",
        entity_type="asset",
        entity_id=asset.id,
        request_id=request_id,
        details={
            "sidecar_id": str(row.id),
            "filename": name,
            "kind": naming.kind,
            "sha256": sha256,
            "size_bytes": len(data),
            "pairing": pairing,
            "status": parsed.status,
            "problems": len(parsed.problems),
        },
    )
    try:
        session.commit()
    except Exception:
        session.rollback()
        if written:
            store.remove_original(sha256)
        raise
    rows = _rows(session, asset.id)
    result = sidecars.review(asset, asset.raster, rows, _contents(session, rows))
    entry = next(e for e in result.entries if e.row.id == row.id)
    matched = "matched by name" if pairing == "filename_match" else "attached by you; the names do not match"
    return SidecarUploadOut(
        sidecar=_sidecar_out(entry),
        asset_id=asset.id,
        raster_filename=asset.original_filename,
        duplicate=False,
        message=f"Kept for review with {asset.original_filename} ({matched}). Nothing has been applied.",
    )


@router.post(
    "/assets/{asset_id}/sidecars",
    response_model=SidecarUploadOut,
    status_code=201,
    openapi_extra=BINARY_BODY,
    responses=problem_responses(400, 401, 404, 409, 413, 415, 500),
)
async def attach_sidecar(
    asset_id: uuid.UUID,
    request: Request,
    response: Response,
    session: SessionDep,
    profile: ProfileDep,
    request_id: RequestIdDep,
    filename: Annotated[str, Query(min_length=1, max_length=500, description="Original file name")],
):
    """Add a sidecar file for this raster, whatever its name. Stored unmodified for review; nothing is applied."""
    asset = _raster(session, asset_id)
    return await upload_sidecar(
        asset.project_id, request, response, session, profile, request_id, filename=filename, asset_id=asset.id
    )


# --- review and confirm -----------------------------------------------------------------------------------------------


@router.get("/assets/{asset_id}/sidecars", response_model=SidecarReviewOut, responses=problem_responses(404, 409))
def get_sidecars(asset_id: uuid.UUID, session: SessionDep):
    """The raster's sidecar files, what each says, how it compares with the raster, and what could be recorded."""
    asset = _raster(session, asset_id)
    return _review_out(asset, _review(session, asset))


@router.post(
    "/assets/{asset_id}/sidecars/validate",
    response_model=SidecarValidation,
    responses=problem_responses(400, 404, 409),
)
def validate_sidecars(asset_id: uuid.UUID, body: SidecarRequest, session: SessionDep):
    """Check the change the sidecar files propose, with the same checks as georeferencing entered by hand. Stores
    nothing."""
    asset = _raster(session, asset_id)
    result = _review(session, asset, body)
    if result.proposal is None:
        return SidecarValidation(review=_review_out(asset, result), validation=None)
    proposal = result.proposal
    proposal.reason = body.reason
    outcome = georeference.validate(asset, asset.raster, proposal, georeference.original_path(session, asset))
    outcome.checks = _extra_checks(result) + outcome.checks
    return SidecarValidation(
        review=_review_out(asset, result),
        validation=GeoreferenceValidation(
            valid=outcome.ok,
            checks=[GeoreferenceCheck(**asdict(c)) for c in outcome.checks],
            proposed=_state(outcome.result) if outcome.result is not None else None,
            corners=outcome.corners,
            method="sidecar_geotransform" if proposal.operation == "set_geotransform" else "sidecar_crs",
            explanation=EXPLANATIONS[proposal.operation]
            + " The values come from the sidecar files named; the files themselves are not changed.",
        ),
    )


@router.post(
    "/assets/{asset_id}/sidecars/apply",
    response_model=GeoreferencingOut,
    status_code=201,
    responses=problem_responses(400, 401, 404, 409, 422),
)
def apply_sidecars(
    asset_id: uuid.UUID, body: SidecarApply, session: SessionDep, profile: ProfileDep, request_id: RequestIdDep
):
    """Record the reviewed change as a new georeferencing version. Validated again; nothing is stored unless it
    passes, the sidecars are the ones reviewed and unchanged, and the change is confirmed."""
    asset = _raster(session, asset_id)
    if not body.confirm:
        raise ProblemError(
            422, "CONFIRMATION_REQUIRED", "Review the proposed change and confirm it", "Nothing was recorded."
        )
    if asset.raster.georef_version != body.expected_version:
        raise ProblemError(
            409,
            "GEOREFERENCE_VERSION_CONFLICT",
            "The raster's georeferencing was changed by someone else",
            f"You reviewed version {body.expected_version}; it is now at version {asset.raster.georef_version}. "
            "Review the sidecar files again. Nothing was recorded.",
        )
    result = _review(session, asset, body)
    if {e.row.id for e in result.entries} != set(body.sidecar_ids):
        raise ProblemError(
            409,
            "SIDECARS_CHANGED",
            "The raster's sidecar files changed after your review",
            "A sidecar file was added since the review you confirmed. Review again. Nothing was recorded.",
        )
    if result.state == "file_valid":
        raise ProblemError(
            409,
            "ALREADY_GEOREFERENCED",
            "This raster's own georeferencing is valid",
            "The CRS and geotransform in the file are used. Sidecar files do not override them.",
        )
    if result.proposal is None or result.state != "proposal":
        raise ProblemError(
            409 if result.state in ("conflict", "in_use") else 422,
            "SIDECAR_NOTHING_TO_RECORD",
            result.title or "The sidecar files do not give a change to record",
            " ".join(result.explanation) + " Nothing was recorded.",
        )
    proposal = result.proposal
    proposal.reason = body.reason
    used = _used(result)
    provenance = [
        {
            "sidecar_id": str(e.row.id),
            "filename": e.row.original_filename,
            "kind": e.row.kind,
            "sha256": e.row.blob_sha256,
            "uploaded_by": e.row.uploader.display_name,
            "uploaded_at": e.row.uploaded_at.isoformat(),
            "role": [k for k, s in result.sources.items() if s.get("sidecar_id") == str(e.row.id)],
        }
        for e in used
    ]
    method = "sidecar_geotransform" if proposal.operation == "set_geotransform" else "sidecar_crs"
    try:
        assertion, outcome = georeference.apply(
            session,
            asset.id,
            proposal,
            expected_version=body.expected_version,
            actor=profile,
            request_id=request_id,
            method=method,
            provenance=provenance,
            extra_checks=_extra_checks(result),
        )
    except GeoreferenceError as exc:
        session.rollback()
        if exc.checks:
            record_audit(
                session,
                actor=profile,
                action="georeference.refused",
                entity_type="asset",
                entity_id=asset.id,
                request_id=request_id,
                details={
                    "operation": proposal.operation,
                    "method": method,
                    "sidecars": [p["sidecar_id"] for p in provenance],
                    "failed": [c.code for c in exc.checks if c.status == "failed"],
                },
            )
            session.commit()
        raise ProblemError(
            exc.status,
            exc.code,
            exc.title,
            exc.detail,
            errors=[{"field": c.code, "message": c.message} for c in exc.checks if c.status == "failed"],
        ) from exc
    record_audit(
        session,
        actor=profile,
        action=f"georeference.{proposal.operation}",
        entity_type="asset",
        entity_id=asset.id,
        request_id=request_id,
        details={
            "version": assertion.version_no,
            "method": assertion.method,
            "crs_id": assertion.crs_id,
            "georeferenced": assertion.georeferenced,
            "sidecars": [p["sidecar_id"] for p in provenance],
            "warnings": [c.code for c in outcome.checks if c.status == "warning"],
        },
    )
    session.commit()
    return _out(session, asset)
