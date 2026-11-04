"""Satellite catalogue discovery, Phase 1: list the providers and search for scenes. Nothing is downloaded or kept.

A search sends a coarse bounding box of the area of interest to a third-party catalogue. It needs the application-wide
gates, a project that allows online sources, and an explicit confirmation on the request itself; a dry run shows what
would be sent and sends nothing. The routes read the database only to find the project and the local profile
(attribution, not authentication); they write nothing to it. Results are returned to the caller and stored nowhere.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends

from geopoc.api.deps import ProfileDep, RequestIdDep, SessionDep
from geopoc.api.errors import ProblemError, problem_responses
from geopoc.api.schemas import (
    ImageryAssetOut,
    ImageryCollectionOut,
    ImageryFiltering,
    ImageryGates,
    ImageryProviderOut,
    ImageryProvidersOut,
    ImageryTransmission,
    SceneOut,
    SceneSearchOut,
    SceneSearchRequest,
)
from geopoc.config import Settings, get_settings
from geopoc.db.models import Project
from geopoc.imagery import service
from geopoc.imagery.aoi import parse_aoi
from geopoc.imagery.errors import ImageryRefused
from geopoc.imagery.models import Filtering, Page, SceneItem, SearchQuery
from geopoc.imagery.providers import StacProvider, build_registry, get_provider
from geopoc.imagery.stac import host_of

router = APIRouter(prefix="/api/v1", tags=["imagery"], responses=problem_responses(422))

TITLES = {
    "IMAGERY_DISABLED": "Catalogue search is switched off",
    "IMAGERY_NOT_CONFIGURED": "The catalogue is not configured",
    "IMAGERY_PROJECT_LOCAL_ONLY": "This project is local-only",
    "IMAGERY_PROJECT_ARCHIVED": "This project is archived",
    "IMAGERY_CONFIRMATION_REQUIRED": "Confirm before the area leaves this machine",
    "IMAGERY_UNKNOWN_PROVIDER": "Unknown provider",
    "IMAGERY_UNSUPPORTED_COLLECTION": "Unsupported collection",
    "IMAGERY_PAGE_TOKEN_INVALID": "Invalid page token",
    "IMAGERY_UPSTREAM_RATE_LIMITED": "The catalogue is limiting requests",
    "IMAGERY_UPSTREAM_REJECTED": "The catalogue refused the request",
    "IMAGERY_UPSTREAM_BAD_REQUEST": "The catalogue did not accept the search",
    "IMAGERY_UPSTREAM_ERROR": "The catalogue reported an error",
    "IMAGERY_UPSTREAM_UNREACHABLE": "The catalogue could not be reached",
    "IMAGERY_UPSTREAM_TIMEOUT": "The catalogue did not answer in time",
    "IMAGERY_BAD_RESPONSE": "The catalogue's answer could not be used",
}
SEARCH_REFUSALS = problem_responses(400, 401, 404, 409, 429, 502, 504)


@dataclass
class ImageryHooks:
    """Stand-ins for the network. None in production; tests override the dependency with a mock transport."""

    transport: httpx.BaseTransport | None = None


def get_hooks() -> ImageryHooks:
    return ImageryHooks()


def get_imagery_settings() -> Settings:
    """The application settings. A separate dependency so a test can replace them for these routes only."""
    return get_settings()


SettingsDep = Annotated[Settings, Depends(get_imagery_settings)]
HooksDep = Annotated[ImageryHooks, Depends(get_hooks)]


def _problem(exc: ImageryRefused) -> ProblemError:
    title = TITLES.get(exc.code) or ("Invalid search" if exc.status == 422 else "Catalogue search was not made")
    return ProblemError(exc.status, exc.code, title, str(exc))


def _gates_out(gates: service.Gates) -> ImageryGates:
    return ImageryGates(
        online_enabled=gates.online_enabled,
        imagery_enabled=gates.imagery_enabled,
        terms_confirmed=gates.terms_confirmed,
        missing=gates.missing,
    )


def _provider_out(provider: StacProvider, gates: service.Gates) -> ImageryProviderOut:
    problem = provider.configuration_problem()
    return ImageryProviderOut(
        id=provider.id,
        title=provider.title,
        kind="stac",
        host=host_of(provider.base_url) if problem is None else "",
        collections=[
            ImageryCollectionOut(id=c.id, title=c.title, mission=c.mission, level=c.level) for c in provider.collections
        ],
        credential_configured=provider.access_token is not None,
        search_needs_credentials=provider.search_needs_credentials,
        configuration_problem=problem,
        ready=gates.all_open and problem is None,
        licence=provider.licence,
        notes=list(provider.notes),
    )


def _scene_out(item: SceneItem) -> SceneOut:
    return SceneOut(
        provider=item.provider,
        collection=item.collection,
        scene_id=item.scene_id,
        acquisition_time=item.acquisition_time.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        cloud_percent=item.cloud_percent,
        platform=item.platform,
        constellation=item.constellation,
        instruments=item.instruments,
        processing_level=item.processing_level,
        processing_baseline=item.processing_baseline,
        mgrs_tile=item.mgrs_tile,
        epsg=item.epsg,
        gsd_m=item.gsd_m,
        relative_orbit=item.relative_orbit,
        geometry=item.geometry,
        bbox=item.bbox,
        aoi_check=item.aoi_check,  # type: ignore[arg-type]
        assets=[ImageryAssetOut(key=a.key, title=a.title, media_type=a.media_type, roles=a.roles) for a in item.assets],
    )


def _licence_for(provider: StacProvider, items: list[SceneItem]) -> dict[str, str]:
    years = sorted({i.acquisition_time.year for i in items})
    label = "–".join(str(y) for y in dict.fromkeys((years[0], years[-1]))) if years else "<year of acquisition>"
    return {k: v.replace("{year}", label) for k, v in provider.licence.items()}


# --- routes -----------------------------------------------------------------------------------------------------------


@router.get("/imagery/providers", response_model=ImageryProvidersOut, responses=problem_responses(401))
def list_providers(settings: SettingsDep, profile: ProfileDep):
    """The catalogues this installation knows, their collections, and which gates are still closed. Local state only:
    nothing is sent anywhere and no credential is shown."""
    gates = service.gates_for(settings)
    return ImageryProvidersOut(
        providers=[_provider_out(p, gates) for p in build_registry(settings).values()],
        gates=_gates_out(gates),
        note="A search sends a bounding box of your area to the provider, so it needs every gate here, a project that "
        "allows online sources, and your confirmation on each request. Nothing is downloaded or kept.",
    )


@router.post("/projects/{project_id}/scene-search", response_model=SceneSearchOut, responses=SEARCH_REFUSALS)
def scene_search(
    project_id: uuid.UUID,
    body: SceneSearchRequest,
    session: SessionDep,
    settings: SettingsDep,
    hooks: HooksDep,
    profile: ProfileDep,
    request_id: RequestIdDep,
):
    """Find scenes over an area and dates. With `dry_run` it only checks the request and shows what would be sent.
    Without it, the request is refused unless every gate is open, the project allows online sources, and
    `confirm_external_transmission` is true. Nothing is downloaded or stored; page with `next_page_token`."""
    project = session.get(Project, project_id)
    if project is None:
        raise ProblemError(404, "PROJECT_NOT_FOUND", "Project not found")
    try:
        provider = get_provider(settings, body.provider)
        query = SearchQuery(
            provider=provider.id,
            collection=body.collection,
            aoi=parse_aoi(body.aoi),
            start=body.start_date,
            end=body.end_date,
            max_cloud_percent=body.max_cloud_percent,
            limit=body.limit,
            page_token=body.page_token,
        ).check()
        prepared = provider.prepare(query, settings.imagery_aoi_round_deg)
        gates = service.gates_for(settings)
        sent = service.disclosure(provider, query, prepared, settings.imagery_aoi_round_deg)
        if body.dry_run:
            return _answer("dry_run", provider, query, sent, gates, Page([], Filtering(), None, None), None)
        service.ensure_gates_open(gates)
        service.ensure_project_allows(project)
        problem = provider.configuration_problem()
        if problem:
            raise ImageryRefused("IMAGERY_NOT_CONFIGURED", problem)
        if not body.confirm_external_transmission:
            raise ImageryRefused("IMAGERY_CONFIRMATION_REQUIRED", service.confirmation_message(provider, sent))
        page = service.execute(
            provider,
            query,
            prepared,
            transport=hooks.transport,
            context={"project": str(project_id), "profile": profile.display_name, "request_id": request_id},
        )
    except ImageryRefused as exc:
        raise _problem(exc) from None
    return _answer("searched", provider, query, sent, gates, page, datetime.now(UTC))


def _answer(
    status: str,
    provider: StacProvider,
    query: SearchQuery,
    sent: dict,
    gates: service.Gates,
    page: Page,
    retrieved: datetime | None,
) -> SceneSearchOut:
    notes = list(provider.notes)
    if status == "dry_run":
        notes.append("Dry run: nothing was sent. Repeat with dry_run false and confirm_external_transmission true.")
    else:
        notes.append("Scenes were filtered locally against your exact area, so a page can hold fewer than `limit`.")
    notes.extend(page.notes)
    return SceneSearchOut(
        status=status,  # type: ignore[arg-type]
        provider=provider.id,
        collection=query.collection,
        transmission=ImageryTransmission(sent=status == "searched", **sent),
        gates=_gates_out(gates),
        items=[_scene_out(i) for i in page.items],
        returned=len(page.items),
        matched=page.matched,
        next_page_token=page.next_token,
        filtering=None if status == "dry_run" else ImageryFiltering(**vars(page.filtering)),
        retrieved_at=retrieved.isoformat().replace("+00:00", "Z") if retrieved else None,
        licence=_licence_for(provider, page.items),
        notes=notes,
    )
