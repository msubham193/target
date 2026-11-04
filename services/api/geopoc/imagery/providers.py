"""Catalogue providers. The application depends on `StacProvider`, never on a particular service.

Phase 1 knows one: the Copernicus Data Space Ecosystem STAC catalogue, Sentinel-2 Level-2A. The facts below about its
address, collection name and queryable fields come from its public documentation as remembered when this was written
and have NOT been checked against the live service (no live request is made in Phase 1). The cloud filter is therefore
also applied again locally, so a result is correct whether or not the catalogue honours it.
"""

from dataclasses import dataclass, field, replace
from typing import Any

import httpx
from shapely.geometry import shape

from geopoc.config import Settings
from geopoc.imagery import paging
from geopoc.imagery.aoi import rounded_bbox
from geopoc.imagery.errors import ImageryRefused
from geopoc.imagery.models import Filtering, Page, SceneItem, SearchQuery
from geopoc.imagery.stac import StacClient, matched_count, next_page, parse_item, validate_base_url

CDSE_ID = "cdse"
SORT = [{"field": "properties.datetime", "direction": "desc"}]


@dataclass(frozen=True)
class CollectionInfo:
    id: str
    title: str
    level: str
    mission: str
    cloud_property: str | None = "eo:cloud_cover"


@dataclass(frozen=True)
class Prepared:
    """A search worked out and checked before anything is sent. `body` is what would be sent (plus the cursor)."""

    body: dict[str, Any]
    bbox: list[float]
    window: str
    fingerprint: str
    provider_token: str | None

    def request_body(self) -> dict[str, Any]:
        return {**self.body, "token": self.provider_token} if self.provider_token else dict(self.body)


@dataclass(frozen=True)
class StacProvider:
    id: str
    title: str
    base_url: str
    collections: tuple[CollectionInfo, ...]
    licence: dict[str, str]
    access_token: str | None = field(default=None, repr=False)
    timeout_s: float = 20.0
    max_response_bytes: int = 5 * 1024**2
    # None means "believed so, not verified".
    search_needs_credentials: bool | None = None
    notes: tuple[str, ...] = ()

    def collection(self, collection_id: str) -> CollectionInfo:
        for info in self.collections:
            if info.id == collection_id:
                return info
        raise ImageryRefused(
            "IMAGERY_UNSUPPORTED_COLLECTION",
            f"{self.title} supports: {', '.join(c.id for c in self.collections)}; got {collection_id!r}",
        )

    def configuration_problem(self) -> str | None:
        try:
            validate_base_url(self.base_url)
        except ImageryRefused as exc:
            return str(exc)
        return None

    def prepare(self, query: SearchQuery, round_deg: float) -> Prepared:
        info = self.collection(query.collection)
        bbox = rounded_bbox(query.aoi.bounds, round_deg)
        window = f"{query.start.isoformat()}T00:00:00Z/{query.end.isoformat()}T23:59:59Z"
        body: dict[str, Any] = {
            "collections": [info.id],
            "bbox": bbox,
            "datetime": window,
            "limit": query.limit,
            "sortby": SORT,
        }
        if query.max_cloud_percent is not None and info.cloud_property:
            body["query"] = {info.cloud_property: {"lte": query.max_cloud_percent}}
        fingerprint = paging.query_fingerprint(self.id, info.id, bbox, window, query.max_cloud_percent, query.limit)
        token = paging.decode(query.page_token, self.id, info.id, fingerprint) if query.page_token else None
        return Prepared(body, bbox, window, fingerprint, token)

    def client(self, transport: httpx.BaseTransport | None = None) -> StacClient:
        return StacClient(
            self.base_url,
            timeout_s=self.timeout_s,
            max_bytes=self.max_response_bytes,
            token=self.access_token,
            transport=transport,
        )

    def search(self, query: SearchQuery, prepared: Prepared, client: StacClient) -> Page:
        info = self.collection(query.collection)
        answer = client.search(prepared.request_body())
        features = answer["features"]
        filtering = Filtering(server_returned=len(features))
        notes: list[str] = []
        if len(features) > query.limit:
            notes.append(
                f"The catalogue returned more than the {query.limit} scenes asked for; the extra were dropped."
            )
            features = features[: query.limit]
        items: list[SceneItem] = []
        for raw in features:
            item = parse_item(raw, provider=self.id, collection=info.id, cloud_property=info.cloud_property or "")
            if item is None:
                filtering.malformed += 1
                continue
            if query.max_cloud_percent is not None:
                if item.cloud_percent is None:
                    filtering.unknown_cloud += 1
                    continue
                if item.cloud_percent > query.max_cloud_percent:
                    filtering.above_cloud_limit += 1
                    continue
            checked = _against_area(item, query)
            if checked is None:
                filtering.outside_aoi += 1
                continue
            items.append(checked)
        filtering.kept = len(items)
        offered, token = next_page(answer)
        next_token = paging.encode(self.id, info.id, prepared.fingerprint, token) if token else None
        if offered and token is None:
            notes.append("The catalogue offers a further page that this client cannot follow.")
        return Page(items, filtering, matched_count(answer), next_token, notes)


def _against_area(item: SceneItem, query: SearchQuery) -> SceneItem | None:
    """The item with its footprint checked against the EXACT area, or None if it lies outside. An item with no usable
    footprint is kept and marked as not verified."""
    if item.geometry is None:
        return item
    try:
        footprint = shape(item.geometry)
        if footprint.is_empty or not footprint.is_valid:
            return item
        return replace(item, aoi_check="intersects") if footprint.intersects(query.aoi.polygon) else None
    except Exception:  # noqa: BLE001 - shapely raises several error types for odd geometry
        return item


CDSE_LICENCE = {
    "data": "Copernicus Sentinel data is released under the Copernicus Sentinel Data Terms and Conditions.",
    "attribution": "Contains modified Copernicus Sentinel data {year}.",
    "service": "Access through the Copernicus Data Space Ecosystem has its own terms, quotas and retention rules.",
    "status": "Summary for orientation, not legal advice. The account owner must confirm the terms before use; "
    "GEOPOC_IMAGERY_TERMS_CONFIRMED records that they did. Commercial use is not assumed to be permitted.",
}


def cdse_provider(settings: Settings) -> StacProvider:
    token = settings.imagery_cdse_access_token
    return StacProvider(
        id=CDSE_ID,
        title="Copernicus Data Space Ecosystem (STAC)",
        base_url=settings.imagery_cdse_stac_url,
        collections=(
            CollectionInfo(
                id="sentinel-2-l2a",
                title="Sentinel-2 MSI Level-2A (surface reflectance)",
                level="L2A",
                mission="Sentinel-2",
            ),
        ),
        licence=CDSE_LICENCE,
        access_token=token.get_secret_value() if token is not None and token.get_secret_value() else None,
        timeout_s=settings.imagery_timeout_s,
        max_response_bytes=settings.imagery_max_response_bytes,
        search_needs_credentials=None,
        notes=(
            "Catalogue address, collection name and queryable fields are from public documentation and are not "
            "verified against the live service.",
            "Cloud cover is the catalogue's figure for the whole granule, not for your area.",
        ),
    )


def build_registry(settings: Settings) -> dict[str, StacProvider]:
    providers = [cdse_provider(settings)]
    return {p.id: p for p in providers}


def get_provider(settings: Settings, provider_id: str) -> StacProvider:
    registry = build_registry(settings)
    if provider_id not in registry:
        raise ImageryRefused(
            "IMAGERY_UNKNOWN_PROVIDER", f"Unknown provider {provider_id!r}; available: {', '.join(sorted(registry))}"
        )
    return registry[provider_id]
