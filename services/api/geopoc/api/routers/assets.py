import hashlib
import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from geoalchemy2.shape import to_shape
from shapely.geometry import mapping
from sqlalchemy import select, tuple_
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.requests import ClientDisconnect

from geopoc.api.deps import ProfileDep, RequestIdDep, SessionDep, record_audit
from geopoc.api.errors import ProblemError, problem_responses
from geopoc.api.pagination import decode_cursor, encode_cursor
from geopoc.api.routers.workspace import profile_out
from geopoc.api.schemas import (
    AssetOut,
    AssetPage,
    AssetStatus,
    AssetUploadOut,
    IngestionRule,
    IngestionRules,
    ModalityName,
    RasterOut,
    VideoOut,
)
from geopoc.config import get_settings
from geopoc.db.models import Asset, Blob, Project, WorkspaceUser
from geopoc.ingestion import rules, service
from geopoc.storage import store

router = APIRouter(prefix="/api/v1", tags=["assets"], responses=problem_responses(422))

BINARY_BODY = {
    "requestBody": {
        "required": True,
        "description": "The raw bytes of the file. Not multipart: the body is streamed straight to storage.",
        "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}},
    }
}


def asset_out(asset: Asset) -> AssetOut:
    raster = None
    if asset.raster is not None:
        r = asset.raster
        raster = RasterOut(
            driver=r.driver,
            width=r.width,
            height=r.height,
            band_count=r.band_count,
            dtypes=r.dtypes,
            nodata=r.nodata,
            crs_status=r.crs_status,
            crs_identifier=r.crs_identifier,
            crs_name=r.crs_name,
            crs_wkt=r.crs_wkt,
            georeferenced=r.georeferenced,
            geotransform=r.geotransform,
            pixel_size_x=r.pixel_size_x,
            pixel_size_y=r.pixel_size_y,
            pixel_units=r.pixel_units,
            bounds_native=r.bounds_native,
            footprint_4326=mapping(to_shape(r.footprint_4326)) if r.footprint_4326 is not None else None,
        )
    video = None
    if asset.video is not None:
        v = asset.video
        video = VideoOut(
            container=v.container,
            video_codec=v.video_codec,
            width=v.width,
            height=v.height,
            duration_ms=v.duration_ms,
            avg_frame_rate=v.avg_frame_rate,
            frame_rate=v.frame_rate,
            time_base=v.time_base,
            frame_count=v.frame_count,
            creation_time=v.creation_time,
            has_audio=v.has_audio,
        )
    return AssetOut(
        id=asset.id,
        project_id=asset.project_id,
        original_filename=asset.original_filename,
        modality=asset.modality,
        detected_format=asset.detected_format,
        media_type=asset.media_type,
        size_bytes=asset.size_bytes,
        sha256=asset.blob_sha256,
        status=asset.status,
        duplicate_of_asset_id=asset.duplicate_of_asset_id,
        quality_flags=asset.quality_flags,
        unknowns=asset.unknowns,
        metadata=asset.extracted_metadata,
        acquisition_time=asset.acquisition_time,
        uploaded_by=profile_out(asset.uploader),
        ingested_at=asset.ingested_at,
        raster=raster,
        video=video,
    )


def _megabytes(size: int) -> str:
    return f"{size / 1024**2:,.0f} MB"


@router.get("/ingestion/rules", response_model=IngestionRules)
def ingestion_rules():
    """Accepted formats and size limits, so the UI states exactly what the server enforces."""
    settings = get_settings()
    return IngestionRules(
        rules=[
            IngestionRule(
                modality=modality,
                label=rules.MODALITY_LABELS[modality],
                extensions=list(extensions),
                max_bytes=rules.max_bytes(modality, settings),
            )
            for modality, extensions in rules.MODALITY_EXTENSIONS.items()
        ],
        notes=[
            "Limits bound what is accepted; they do not guarantee that a file can be processed.",
            "A file is checked by its content and by actually reading it, not by its name.",
            "Identical content is stored once; a repeated upload is linked to the first and flagged.",
        ],
    )


class _Rejection:
    """Records a refused upload in the audit history, then raises the error returned to the client."""

    def __init__(self, session: Session, project: Project, profile: WorkspaceUser, request_id: str, context: dict):
        self.session, self.project, self.profile = session, project, profile
        self.request_id, self.context = request_id, context

    def __call__(self, status: int, code: str, title: str, detail: str, **extra) -> ProblemError:
        try:
            self.session.rollback()
            record_audit(
                self.session,
                actor=self.profile,
                action="asset.upload_rejected",
                entity_type="project",
                entity_id=self.project.id,
                request_id=self.request_id,
                details={**self.context, **extra, "code": code},
            )
            self.session.commit()
        except SQLAlchemyError:
            self.session.rollback()  # the rejection itself must still reach the client
        return ProblemError(status, code, title, detail)


@router.post(
    "/projects/{project_id}/assets",
    response_model=AssetUploadOut,
    status_code=201,
    openapi_extra=BINARY_BODY,
    responses=problem_responses(400, 401, 404, 409, 413, 415, 500, 503, 507),
)
async def upload_asset(
    project_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    profile: ProfileDep,
    request_id: RequestIdDep,
    modality: Annotated[ModalityName, Query(description="Which kind of input this file is")],
    filename: Annotated[str, Query(min_length=1, max_length=500, description="Original file name")],
):
    """Upload one original file into a project. The body is validated, checksummed and stored unmodified."""
    settings = get_settings()
    project = session.get(Project, project_id)
    if project is None:
        raise ProblemError(404, "PROJECT_NOT_FOUND", "Project not found")
    if project.archived_at is not None:
        raise ProblemError(409, "PROJECT_ARCHIVED", "This project is archived", "Restore it before adding files.")

    name = rules.clean_filename(filename)
    reject = _Rejection(session, project, profile, request_id, {"filename": name, "modality": modality})
    if not name:
        raise reject(422, "INVALID_FILENAME", "The file name is not valid", "Provide the original file name.")

    extension = rules.extension_of(name)
    accepted = rules.MODALITY_EXTENSIONS[modality]
    if extension not in accepted:
        raise reject(
            415,
            "UNSUPPORTED_FILE_TYPE",
            "This file type is not supported here",
            f"{rules.MODALITY_LABELS[modality]} accepts {', '.join(accepted)}; "
            f"this file ends in {extension or '(no extension)'}.",
        )
    family = rules.EXTENSION_FAMILY[extension]

    limit = rules.max_bytes(modality, settings)
    too_large = (
        413,
        "FILE_TOO_LARGE",
        "The file is too large",
        f"The limit for {rules.MODALITY_LABELS[modality].lower()} is {_megabytes(limit)}.",
    )
    declared = request.headers.get("content-length", "")
    declared_size = int(declared) if declared.isdigit() else None
    if declared_size is not None and declared_size > limit:
        raise reject(*too_large, declared_bytes=declared_size)

    try:
        available = store.free_bytes()
    except OSError as exc:
        raise reject(500, "STORAGE_ERROR", "Local storage is not available", "The data folder cannot be read.") from exc
    if available - (declared_size if declared_size is not None else limit) < settings.storage_reserve_bytes:
        raise reject(
            507,
            "INSUFFICIENT_STORAGE",
            "Not enough free disk space",
            "Free up space on the data drive and try again. Nothing was stored.",
        )

    # --- stream the body to a temporary file: constant memory, hashing as it goes -----------------------
    temp_path = store.new_temp_path()
    digest = hashlib.sha256()
    received = 0
    header = b""
    sniffed: str | None = None
    signature_ok: bool | None = None  # None until enough bytes have arrived to decide
    over_limit = False
    try:
        try:
            with open(temp_path, "wb") as handle:
                async for chunk in request.stream():
                    if not chunk:
                        continue
                    received += len(chunk)
                    if received > limit:
                        over_limit = True
                        break
                    if signature_ok is None:
                        header += chunk[: rules.HEADER_BYTES - len(header)]
                        if len(header) >= rules.HEADER_BYTES:
                            sniffed = rules.sniff(header)
                            signature_ok = sniffed == family
                    if signature_ok is False:
                        continue  # keep draining so the client gets a clean answer, but store nothing more
                    digest.update(chunk)
                    handle.write(chunk)
        except ClientDisconnect:
            raise reject(400, "UPLOAD_INTERRUPTED", "The upload was interrupted", "Nothing was stored.") from None
        except OSError as exc:
            raise reject(
                500, "STORAGE_ERROR", "The file could not be written to local storage", "Nothing was stored."
            ) from exc

        if over_limit:
            raise reject(*too_large, received_bytes=received)
        if received == 0:
            raise reject(422, "EMPTY_FILE", "The file is empty", "Nothing was stored.")
        if signature_ok is None:  # shorter than the signature window
            sniffed = rules.sniff(header)
            signature_ok = sniffed == family
        if not signature_ok:
            actual = rules.FAMILY_LABELS.get(sniffed or "", "not a recognised format")
            raise reject(
                415,
                "SIGNATURE_MISMATCH",
                "The file's content does not match its name",
                f"The name ends in {extension}, which should be a {rules.FAMILY_LABELS[family]}, "
                f"but the content is {actual}.",
                received_bytes=received,
            )

        sha256 = digest.hexdigest()
        try:
            result = await run_in_threadpool(
                service.ingest,
                session,
                project=project,
                profile=profile,
                filename=name,
                modality=modality,
                family=family,
                temp_path=temp_path,
                sha256=sha256,
                size_bytes=received,
                request_id=request_id,
            )
        except rules.UnreadableAsset as exc:
            raise reject(
                422, exc.code, "The file could not be read", f"{exc} Nothing was stored.", sha256=sha256
            ) from exc
        except rules.ToolingUnavailable as exc:
            raise reject(
                503,
                "MEDIA_TOOLING_UNAVAILABLE",
                "Video cannot be validated on this machine",
                "FFmpeg (ffprobe and ffmpeg) must be installed and on PATH. Nothing was stored.",
            ) from exc
        except (store.StorageError, OSError) as exc:
            raise reject(
                500, "STORAGE_ERROR", "The file could not be stored", "Nothing was stored.", sha256=sha256
            ) from exc
    finally:
        store.discard_temp(temp_path)

    duplicate = result.duplicate_of_asset_id is not None
    if duplicate:
        message = (
            "Identical content is already in this project. This upload was linked to it and flagged as a duplicate."
        )
    elif result.stored_original_reused:
        message = "Stored. Identical content already existed in local storage, so no second copy was written."
    else:
        message = "Stored and validated."
    return AssetUploadOut(
        asset=asset_out(result.asset),
        duplicate=duplicate,
        duplicate_of_asset_id=result.duplicate_of_asset_id,
        stored_original_reused=result.stored_original_reused,
        message=message,
    )


@router.get("/projects/{project_id}/assets", response_model=AssetPage, responses=problem_responses(404))
def list_assets(
    project_id: uuid.UUID,
    session: SessionDep,
    modality: ModalityName | None = None,
    status: AssetStatus | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
):
    if session.get(Project, project_id) is None:
        raise ProblemError(404, "PROJECT_NOT_FOUND", "Project not found")
    stmt = select(Asset).where(Asset.project_id == project_id)
    if modality:
        stmt = stmt.where(Asset.modality == modality)
    if status:
        stmt = stmt.where(Asset.status == status)
    if cursor:
        stmt = stmt.where(tuple_(Asset.ingested_at, Asset.id) < decode_cursor(cursor))
    stmt = stmt.order_by(Asset.ingested_at.desc(), Asset.id.desc()).limit(limit + 1)
    rows = list(session.scalars(stmt).unique().all())
    page = rows[:limit]
    return AssetPage(
        items=[asset_out(a) for a in page],
        next_cursor=encode_cursor(page[-1].ingested_at, page[-1].id) if len(rows) > limit else None,
    )


def _get_asset(session: Session, asset_id: uuid.UUID) -> Asset:
    asset = session.get(Asset, asset_id)
    if asset is None:
        raise ProblemError(404, "ASSET_NOT_FOUND", "Asset not found")
    return asset


@router.get("/assets/{asset_id}", response_model=AssetOut, responses=problem_responses(404))
def get_asset(asset_id: uuid.UUID, session: SessionDep):
    return asset_out(_get_asset(session, asset_id))


@router.get(
    "/assets/{asset_id}/content",
    response_class=FileResponse,
    responses={
        200: {"description": "The original bytes, unmodified", "content": {"application/octet-stream": {}}},
        206: {"description": "Partial content"},
        416: {"description": "Range not satisfiable"},
        **problem_responses(404, 500),
    },
)
def get_asset_content(asset_id: uuid.UUID, session: SessionDep):
    """Download the stored original exactly as uploaded. Supports HTTP Range."""
    asset = _get_asset(session, asset_id)
    blob = session.get(Blob, asset.blob_sha256)
    path = store.absolute_path(blob.storage_path) if blob else None
    if path is None or not path.is_file():
        raise ProblemError(500, "STORAGE_ERROR", "The stored original is missing", "The file is not in local storage.")
    return FileResponse(path, media_type="application/octet-stream", filename=asset.original_filename)
