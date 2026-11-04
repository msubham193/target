"""The gates in front of a catalogue search, what is disclosed before one, and the search itself.

Order of the checks for a real search (each failure stops it before anything is sent):
  1. the request is valid (area, dates, cloud limit, page size, provider, collection, page token);
  2. the application-wide gates are open: GEOPOC_ONLINE_ENABLED, GEOPOC_IMAGERY_ENABLED and
     GEOPOC_IMAGERY_TERMS_CONFIRMED (the account owner has read the provider's terms);
  3. the project allows online sources (`source_preference == "allow_online"`) and is not archived;
  4. the person has confirmed THIS request after seeing the disclosure (`confirm_external_transmission`).
A dry run applies step 1 only and sends nothing. Results are returned and kept nowhere.
"""

import logging
from dataclasses import dataclass
from typing import Any

import httpx

from geopoc.config import Settings
from geopoc.imagery.aoi import fingerprint
from geopoc.imagery.errors import ImageryRefused
from geopoc.imagery.models import Page, SearchQuery
from geopoc.imagery.providers import Prepared, StacProvider
from geopoc.imagery.stac import host_of

logger = logging.getLogger("geopoc.imagery")

ENV_NAMES = {
    "online_enabled": "GEOPOC_ONLINE_ENABLED",
    "imagery_enabled": "GEOPOC_IMAGERY_ENABLED",
    "terms_confirmed": "GEOPOC_IMAGERY_TERMS_CONFIRMED",
}


@dataclass(frozen=True)
class Gates:
    online_enabled: bool
    imagery_enabled: bool
    terms_confirmed: bool

    @property
    def missing(self) -> list[str]:
        return [ENV_NAMES[name] for name in ENV_NAMES if not getattr(self, name)]

    @property
    def all_open(self) -> bool:
        return not self.missing


def gates_for(settings: Settings) -> Gates:
    return Gates(settings.online_enabled, settings.imagery_enabled, settings.imagery_terms_confirmed)


def ensure_gates_open(gates: Gates) -> None:
    if not gates.all_open:
        raise ImageryRefused(
            "IMAGERY_DISABLED",
            f"Catalogue search is off ({', '.join(gates.missing)} is not true). It sends a bounding box of the area of "
            "interest to a third-party service and needs the account owner's approval; nothing was sent.",
        )


def ensure_project_allows(project: Any) -> None:
    if project.archived_at is not None:
        raise ImageryRefused("IMAGERY_PROJECT_ARCHIVED", "This project is archived; restore it before searching.")
    if project.source_preference != "allow_online":
        raise ImageryRefused(
            "IMAGERY_PROJECT_LOCAL_ONLY",
            "This project is set to local-only sources. Change its source preference to allow online sources before a "
            "search; nothing was sent.",
        )


def disclosure(provider: StacProvider, query: SearchQuery, prepared: Prepared, round_deg: float) -> dict[str, Any]:
    """Exactly what a real search would send, shown to the person before they confirm."""
    return {
        "provider": provider.id,
        "host": host_of(provider.base_url),
        "method": "POST",
        "path": "/search",
        "aoi_sent": {
            "kind": "bounding box",
            "bbox": prepared.bbox,
            "rounded_outward_to_degrees": round_deg,
            "exact_polygon_sent": False,
            "note": "Your exact area stays on this machine and is used to filter the results locally.",
        },
        "request_body": prepared.request_body(),
        "credential_header": provider.access_token is not None,
        "not_sent": ["project name or id", "profile name", "any file or image", "the exact polygon"],
    }


def confirmation_message(provider: StacProvider, sent: dict[str, Any]) -> str:
    box = sent["aoi_sent"]["bbox"]
    return (
        f"A search sends a bounding box [{box[0]}, {box[1]}, {box[2]}, {box[3]}] (west, south, east, north), the date "
        f"window and the cloud limit to {sent['host']}, which is outside this machine. Repeat the request with "
        "confirm_external_transmission set to true to proceed; nothing was sent."
    )


def execute(
    provider: StacProvider,
    query: SearchQuery,
    prepared: Prepared,
    *,
    transport: httpx.BaseTransport | None,
    context: dict[str, str],
) -> Page:
    """Send the search. Called only after every gate and the confirmation. Logs no coordinates or credentials."""
    client = provider.client(transport)
    page = provider.search(query, prepared, client)
    logger.info(
        "catalogue search provider=%s collection=%s box=%s project=%s profile=%s returned=%d request_id=%s",
        provider.id,
        query.collection,
        fingerprint(prepared.bbox),
        context.get("project", "-"),
        context.get("profile", "-"),
        len(page.items),
        context.get("request_id", "-"),
    )
    return page
