import platform
import shutil
from datetime import timedelta
from typing import Any

import PIL
import pyproj
import rasterio
import sqlalchemy
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

import geopoc
from geopoc.api.deps import SessionDep
from geopoc.api.schemas import HealthOut, ModelHealthOut, ModelOut
from geopoc.config import database_environment, get_settings
from geopoc.db.models import WorkerHeartbeat, utcnow
from geopoc.geo import projenv
from geopoc.ingestion.video import tool_path
from geopoc.registry.manifest import ModelEntry, load_manifest, runtime_problem, weights_problem

router = APIRouter(tags=["health"])

# A worker reports several times a second while busy and once per poll when idle.
WORKER_ALIVE_WITHIN_S = 15


def worker_state(session: Session) -> dict[str, Any]:
    """The most recently seen worker, and whether it is still reporting."""
    try:
        latest = session.scalars(select(WorkerHeartbeat).order_by(WorkerHeartbeat.at.desc()).limit(1)).first()
    except SQLAlchemyError:
        session.rollback()
        return {"running": False, "last_seen_at": None, "capabilities": None, "error_code": "DATABASE_UNAVAILABLE"}
    if latest is None:
        return {"running": False, "last_seen_at": None, "capabilities": None}
    running = utcnow() - latest.at < timedelta(seconds=WORKER_ALIVE_WITHIN_S)
    return {
        "running": running,
        "worker_id": latest.worker_id,
        "last_seen_at": latest.at.isoformat(),
        "capabilities": latest.capabilities if running else None,
    }


def model_out(entry: ModelEntry, capabilities: dict[str, Any] | None) -> ModelOut:
    reason = weights_problem(entry) or runtime_problem(entry, capabilities)
    return ModelOut(
        name=entry.name,
        version=entry.version,
        pipeline=entry.pipeline,
        task=entry.task,
        runtime=entry.runtime,
        licence=entry.licence,
        score_semantics=entry.score_semantics,
        score_calibrated=entry.score_calibrated,
        settings=entry.settings,
        weights_sha256=entry.weights_sha256,
        weights_size_bytes=entry.weights_size_bytes,
        available=reason is None,
        reason_unavailable=reason,
        custom=entry.custom,
        original_filename=entry.original_filename,
        added_at=entry.added_at,
    )


@router.get("/health", response_model=HealthOut, responses={503: {"model": HealthOut}})
def health(session: SessionDep):
    settings = get_settings()

    database: dict = {"reachable": False, "postgis_version": None, "schema_revision": None}
    try:
        database["postgis_version"] = session.execute(text("SELECT postgis_lib_version()")).scalar_one()
        # Which database this API is really connected to (asked of the server, not taken from configuration), so a
        # smoke test can refuse to write anywhere but a scratch database.
        database["name"] = session.execute(text("SELECT current_database()")).scalar_one()
        database["environment"] = database_environment(database["name"])
        database["role_is_superuser"] = session.execute(
            text("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
        ).scalar_one()
        database["schema_revision"] = session.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one_or_none()
        database["reachable"] = True
    except SQLAlchemyError:
        session.rollback()
        database["error_code"] = "DATABASE_UNAVAILABLE"

    storage: dict = {"data_root": str(settings.data_root), "data_root_ok": False, "free_bytes": None}
    if settings.data_root.is_dir():
        storage["data_root_ok"] = True
        storage["free_bytes"] = shutil.disk_usage(settings.data_root).free
    else:
        storage["error_code"] = "DATA_ROOT_MISSING"

    worker = worker_state(session) if database["reachable"] else {"running": False, "capabilities": None}
    worker.pop("capabilities", None)
    if not worker["running"]:
        worker["note"] = "No worker is running. Queued jobs (previews, analysis) wait until one is started."

    ok = database["reachable"] and storage["data_root_ok"]
    body = HealthOut(
        status="ok" if ok else "unavailable",
        database=database,
        storage=storage,
        worker=worker,
        versions={
            "app": geopoc.__version__,
            "python": platform.python_version(),
            "sqlalchemy": sqlalchemy.__version__,
            "pyproj": pyproj.__version__,
            "proj": pyproj.proj_version_str,
            "rasterio": rasterio.__version__,
            "gdal": rasterio.__gdal_version__,
            "pillow": PIL.__version__,
            "ffprobe": "available" if tool_path("ffprobe") else "not found",
            "ffmpeg": "available" if tool_path("ffmpeg") else "not found",
        },
        online={"enabled": settings.online_enabled, "proj_network": pyproj.network.is_network_enabled()},
        bind_host=settings.api_host,
        proj=projenv.report(),
    )
    return JSONResponse(body.model_dump(), status_code=200 if ok else 503)


@router.get("/health/models", response_model=ModelHealthOut)
def health_models(session: SessionDep):
    """The models in the manifest and whether each can be run here. Never loads a model."""
    manifest = load_manifest()
    worker = worker_state(session)
    return ModelHealthOut(
        manifest_found=manifest.found,
        models=[model_out(entry, worker.get("capabilities")) for entry in manifest.models],
        errors=manifest.errors,
        worker=worker,
        llm_runtime={
            "configured": False,
            "checked": False,
            "note": "No aggregation model has been selected or verified. Local LLM checks arrive in Phase 5.",
        },
    )
