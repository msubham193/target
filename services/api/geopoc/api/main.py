import logging
import re
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

import geopoc
from geopoc.api.errors import install_error_handlers
from geopoc.api.routers import assets, crs, health, projects, workspace
from geopoc.storage import store

logger = logging.getLogger("geopoc.api")

REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._\-]{1,64}$")


@asynccontextmanager
async def lifespan(app: FastAPI):
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
        description="Local-first geospatial observation workbench. Phases 1-2: workspace profiles, projects, "
        "CRS search, asset ingestion and health.",
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
    app.include_router(crs.router)
    return app


app = create_app()
