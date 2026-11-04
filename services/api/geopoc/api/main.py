import logging
import re
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

import geopoc
from geopoc.api.errors import install_error_handlers
from geopoc.api.routers import (
    annotations,
    assessment,
    assets,
    control_points,
    crs,
    earth_engine,
    georeferencing,
    health,
    imagery,
    imagery_s3,
    indices,
    jobs,
    layers,
    map_sheets,
    observations,
    photo_locations,
    projects,
    rastertools,
    reports,
    sidecars,
    terrain,
    video_analysis,
    workspace,
)
from geopoc.geo import projenv
from geopoc.storage import store

logger = logging.getLogger("geopoc.api")

REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._\-]{1,64}$")


def _log_proj_environment() -> None:
    """Run the PROJ checks once at startup so a problem is in the log, not only on /health. Never stops the API."""
    try:
        report = projenv.report()
    except Exception:  # noqa: BLE001 - diagnostics must not prevent startup
        logger.exception("The PROJ environment check itself failed")
        return
    if report["status"] != "ok":
        problems = [f"{c['code']}: {c['message']}" for c in report["checks"] if c["status"] != "ok"]
        logger.warning("PROJ environment is %s: %s", report["status"], " | ".join(problems))


@asynccontextmanager
async def lifespan(app: FastAPI):
    _log_proj_environment()
    try:
        store.init_storage()
        removed = store.remove_orphaned_uploads()
        if removed:
            logger.info("Removed %d interrupted upload(s) older than a day", removed)
    except OSError:
        # /health reports the storage problem; the API still starts so the problem can be diagnosed.
        logger.error("Local storage could not be initialised")
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="Geospatial PoC API",
        version=geopoc.__version__,
        description="Local-first geospatial observation workbench. Workspace profiles, projects, CRS search, asset "
        "ingestion, background jobs, previews, ground-image analysis, observation review, map layers and exports.",
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        supplied = request.headers.get("X-Request-ID", "")
        request.state.request_id = supplied if REQUEST_ID_RE.match(supplied) else uuid.uuid4().hex
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    install_error_handlers(app)
    app.include_router(health.router)
    app.include_router(workspace.router)
    app.include_router(projects.router)
    app.include_router(assets.router)
    app.include_router(jobs.router)
    app.include_router(observations.router)
    app.include_router(layers.router)
    app.include_router(georeferencing.router)
    app.include_router(rastertools.router)
    app.include_router(sidecars.router)
    app.include_router(photo_locations.router)
    app.include_router(annotations.router)
    app.include_router(reports.router)
    app.include_router(video_analysis.router)
    app.include_router(terrain.router)
    app.include_router(crs.router)
    app.include_router(assessment.router)
    app.include_router(map_sheets.router)
    app.include_router(control_points.router)
    app.include_router(earth_engine.router)
    app.include_router(imagery.router)
    app.include_router(imagery_s3.router)
    app.include_router(indices.router)
    return app


app = create_app()
