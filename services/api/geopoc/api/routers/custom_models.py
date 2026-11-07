"""Upload and remove detection models ("custom models"). See geopoc.registry.custom_models.

An upload is streamed to a temporary file, checksummed, then opened and test-run in a separate process before it is
accepted; nothing is recorded for a file that fails. Once accepted, the model is listed by /health/models and can be
chosen for photo and video analysis like any model from the manifest.
"""

import hashlib
import shutil
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from starlette.requests import ClientDisconnect

from geopoc.api.deps import ProfileDep, RequestIdDep, SessionDep, record_audit
from geopoc.api.errors import ProblemError, problem_responses
from geopoc.api.routers.health import model_out, worker_state
from geopoc.api.schemas import ModelOut
from geopoc.config import get_settings
from geopoc.db.models import Job
from geopoc.ingestion import rules
from geopoc.jobs import queue
from geopoc.registry import custom_models
from geopoc.registry.custom_models import CODE_RUNNING_RUNTIMES, CustomModelError
from geopoc.registry.manifest import load_manifest
from geopoc.registry.model_check import run_check
from geopoc.storage import store

router = APIRouter(prefix="/api/v1", tags=["models"], responses=problem_responses(422))

MODEL_BODY = {
    "requestBody": {
        "required": True,
        "description": "The raw bytes of the model file (.onnx, .pt or .pth). Not multipart.",
        "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}},
    }
}
STATUS_FOR = {
    "UNSUPPORTED_FILE_TYPE": 415,
    "MODEL_FORMAT_UNKNOWN": 422,
    "TOO_MANY_CLASSES": 422,
    "INVALID_CLASS_NAME": 422,
    "MODEL_NAME_TAKEN": 409,
    "MODEL_DUPLICATE": 409,
    "MODEL_NOT_FOUND": 404,
}


def _problem(exc: CustomModelError, title: str) -> ProblemError:
    return ProblemError(STATUS_FOR.get(exc.code, 422), exc.code, title, str(exc))


def _sentence(text: str) -> str:
    text = text.strip()
    return (text[:1].upper() + text[1:]) if text else text


@router.post(
    "/models/custom",
    response_model=ModelOut,
    status_code=201,
    openapi_extra=MODEL_BODY,
    responses=problem_responses(400, 401, 404, 409, 413, 415, 500, 507),
)
async def upload_custom_model(
    request: Request,
    session: SessionDep,
    profile: ProfileDep,
    request_id: RequestIdDep,
    filename: Annotated[str, Query(min_length=1, max_length=300, description="Original file name")],
    name: Annotated[
        str, Query(min_length=2, max_length=63, description="Short name: lower-case letters, digits, '_', '-' or '.'")
    ],
    version: Annotated[str, Query(min_length=1, max_length=40)] = "v1",
    model_format: Annotated[
        Literal["auto", "onnx", "ultralytics", "torchvision"],
        Query(alias="format", description="auto: .onnx is ONNX, .pt is Ultralytics YOLO, .pth is torchvision"),
    ] = "auto",
    classes: Annotated[
        str | None,
        Query(max_length=40000, description="Class names in the model's order, one per line or comma-separated"),
    ] = None,
    min_score: Annotated[
        float | None, Query(ge=0.01, le=0.99, description="Detections scoring under this are not stored")
    ] = None,
    architecture: Annotated[
        Literal[
            "fasterrcnn_resnet50_fpn_v2",
            "fasterrcnn_resnet50_fpn",
            "fasterrcnn_mobilenet_v3_large_fpn",
            "fasterrcnn_mobilenet_v3_large_320_fpn",
        ]
        | None,
        Query(description="torchvision only. Omit to detect it from the weights (the two MobileNet ones look alike)"),
    ] = None,
    licence: Annotated[str | None, Query(max_length=500)] = None,
    trust_code: Annotated[
        bool, Query(description="Required for a YOLO .pt file: the uploader trusts its source, because it can run code")
    ] = False,
):
    """Add a detection model. The file is opened and test-run in a separate process before it is accepted."""
    settings = get_settings()
    original = rules.clean_filename(filename)
    if not original:
        raise ProblemError(422, "INVALID_FILENAME", "The file name is not valid", "Provide the original file name.")
    if not custom_models.NAME_PATTERN.match(name):
        raise ProblemError(
            422,
            "INVALID_MODEL_NAME",
            "The model name is not valid",
            "Use 2 to 63 lower-case letters, digits, '_', '-' or '.', starting with a letter or digit.",
        )
    if not custom_models.VERSION_PATTERN.match(version):
        raise ProblemError(
            422, "INVALID_MODEL_VERSION", "The version is not valid", "Use letters, digits, '_', '-' or '.'."
        )
    try:
        runtime = custom_models.runtime_for(original, model_format)
        class_names = custom_models.parse_classes(classes)
    except CustomModelError as exc:
        raise _problem(exc, "This model cannot be added") from exc
    if runtime in CODE_RUNNING_RUNTIMES and not trust_code:
        raise ProblemError(
            422,
            "TRUST_REQUIRED",
            "Confirm that you trust this file",
            "A YOLO .pt file can run code on this computer when it is opened. Upload it only if you trust where it "
            "came from, and confirm that you do. Exporting it to ONNX avoids the risk.",
        )
    manifest = load_manifest()
    if manifest.get(name, version) is not None:
        raise ProblemError(409, "MODEL_NAME_TAKEN", "This name is taken", f"A model called {name} {version} exists.")

    limit = settings.custom_model_max_bytes
    too_large = (413, "FILE_TOO_LARGE", "The file is too large", f"The limit for a model is {limit / 1024**2:,.0f} MB.")
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        raise ProblemError(*too_large)
    try:
        if store.free_bytes() - (int(declared) if declared.isdigit() else limit) < settings.storage_reserve_bytes:
            raise ProblemError(507, "INSUFFICIENT_STORAGE", "Not enough free disk space", "Nothing was stored.")
    except OSError as exc:
        raise ProblemError(500, "STORAGE_ERROR", "Local storage is not available", "Nothing was stored.") from exc

    model_id, folder = custom_models.new_folder()
    target = folder / original
    digest = hashlib.sha256()
    received = 0
    accepted = False
    try:
        try:
            with open(target, "wb") as handle:
                async for chunk in request.stream():
                    if not chunk:
                        continue
                    received += len(chunk)
                    if received > limit:
                        raise ProblemError(*too_large)
                    digest.update(chunk)
                    handle.write(chunk)
        except ClientDisconnect:
            raise ProblemError(400, "UPLOAD_INTERRUPTED", "The upload was interrupted", "Nothing was stored.") from None
        except OSError as exc:
            raise ProblemError(500, "STORAGE_ERROR", "The file could not be written", "Nothing was stored.") from exc
        if received == 0:
            raise ProblemError(422, "EMPTY_FILE", "The file is empty", "Nothing was stored.")

        model_settings = custom_models.default_settings(runtime, min_score)
        if runtime == "torchvision" and architecture:
            model_settings["architecture"] = architecture
        check = await run_in_threadpool(
            run_check,
            runtime,
            target,
            {**model_settings, "classes": class_names},
            settings.custom_model_check_timeout_s,
        )
        if not check.get("ok"):
            raise ProblemError(
                422,
                "MODEL_CHECK_FAILED",
                "The model could not be opened and run",
                f"{_sentence(str(check.get('error', 'Unknown error.')))} Nothing was stored.",
            )
        model_settings.update({k: v for k, v in (check.get("settings") or {}).items() if v is not None})
        try:
            model = custom_models.add(
                model_id=model_id,
                name=name,
                version=version,
                runtime=runtime,
                file=target,
                sha256=digest.hexdigest(),
                size_bytes=received,
                original_filename=original,
                added_by=profile.display_name,
                licence=licence.strip() if licence and licence.strip() else None,
                classes=[str(c) for c in check.get("classes") or []],
                settings=model_settings,
                taken={(m.name, m.version) for m in manifest.models},
            )
        except CustomModelError as exc:
            raise _problem(exc, "This model cannot be added") from exc
        accepted = True
    finally:
        if not accepted:
            shutil.rmtree(folder, ignore_errors=True)

    record_audit(
        session,
        actor=profile,
        action="model.custom_added",
        entity_type="model",
        entity_id=uuid.UUID(model.id),
        request_id=request_id,
        details={
            "name": model.name,
            "version": model.version,
            "runtime": model.runtime,
            "sha256": model.sha256,
            "size_bytes": model.size_bytes,
            "original_filename": model.original_filename,
            "classes": len(model.classes),
            "trusted_code": runtime in CODE_RUNNING_RUNTIMES,
            "device_checked": check.get("device"),
        },
    )
    session.commit()
    entry = load_manifest().get(model.name, model.version)
    assert entry is not None
    return model_out(entry, worker_state(session).get("capabilities"))


@router.delete(
    "/models/custom/{name}/{version}",
    status_code=204,
    response_class=Response,
    responses=problem_responses(401, 404, 409),
)
def delete_custom_model(name: str, version: str, session: SessionDep, profile: ProfileDep, request_id: RequestIdDep):
    """Remove an uploaded model and its file. Past results keep the model's name, version and checksum."""
    if custom_models.find(name, version) is None:
        raise ProblemError(404, "MODEL_NOT_FOUND", "No uploaded model has this name and version")
    for job in session.scalars(select(Job).where(Job.kind == "analyze", Job.status.in_(queue.ACTIVE))):
        model = (job.payload or {}).get("model") or {}
        if model.get("name") == name and model.get("version") == version:
            raise ProblemError(
                409, "MODEL_IN_USE", "This model is in use", f"Job {job.id} is queued or running with it."
            )
    try:
        model = custom_models.remove(name, version)
    except CustomModelError as exc:
        raise _problem(exc, "This model cannot be removed") from exc
    record_audit(
        session,
        actor=profile,
        action="model.custom_removed",
        entity_type="model",
        entity_id=uuid.UUID(model.id),
        request_id=request_id,
        details={"name": model.name, "version": model.version, "sha256": model.sha256},
    )
    session.commit()
    return Response(status_code=204)
