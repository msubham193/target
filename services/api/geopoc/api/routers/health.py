import platform
import shutil

import pyproj
import sqlalchemy
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

import geopoc
from geopoc.api.deps import SessionDep
from geopoc.api.schemas import HealthOut, ModelHealthOut
from geopoc.config import get_settings
from geopoc.registry.manifest import read_manifest

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthOut, responses={503: {"model": HealthOut}})
def health(session: SessionDep):
    settings = get_settings()

    database: dict = {"reachable": False, "postgis_version": None, "schema_revision": None}
    try:
        database["postgis_version"] = session.execute(text("SELECT postgis_lib_version()")).scalar_one()
        database["schema_revision"] = session.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one_or_none()
        database["reachable"] = True
    except SQLAlchemyError:
        database["error_code"] = "DATABASE_UNAVAILABLE"

    storage: dict = {"data_root": str(settings.data_root), "data_root_ok": False, "free_bytes": None}
    if settings.data_root.is_dir():
        storage["data_root_ok"] = True
        storage["free_bytes"] = shutil.disk_usage(settings.data_root).free
    else:
        storage["error_code"] = "DATA_ROOT_MISSING"

    ok = database["reachable"] and storage["data_root_ok"]
    body = HealthOut(
        status="ok" if ok else "unavailable",
        database=database,
        storage=storage,
        worker={"implemented": False, "note": "The job worker is delivered in Phase 2."},
        versions={
            "app": geopoc.__version__,
            "python": platform.python_version(),
            "sqlalchemy": sqlalchemy.__version__,
            "pyproj": pyproj.__version__,
            "proj": pyproj.proj_version_str,
        },
        online={"enabled": settings.online_enabled, "proj_network": pyproj.network.is_network_enabled()},
        bind_host=settings.api_host,
    )
    return JSONResponse(body.model_dump(), status_code=200 if ok else 503)


@router.get("/health/models", response_model=ModelHealthOut)
def health_models():
    manifest = read_manifest(get_settings().manifest_path)
    return ModelHealthOut(
        **manifest,
        llm_runtime={
            "configured": False,
            "checked": False,
            "note": "No aggregation model has been selected or verified. Local LLM checks arrive in Phase 5.",
        },
    )
