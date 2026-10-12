import re
import uuid

from fastapi import FastAPI, Request

import geopoc
from geopoc.api.errors import install_error_handlers
from geopoc.api.routers import crs, health, projects, workspace

REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._\-]{1,64}$")


def create_app() -> FastAPI:
    app = FastAPI(
        title="Geospatial PoC API",
        version=geopoc.__version__,
        description="Local-first geospatial observation workbench. Phase 1: workspace profiles, projects, "
        "CRS search and health.",
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
    app.include_router(crs.router)
    return app


app = create_app()
