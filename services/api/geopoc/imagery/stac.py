"""A thin STAC API client (item search only) and tolerant parsing of what a catalogue answers.

The client is the only code in this package that opens a connection. It is built from an address checked by
`validate_base_url`, never follows a redirect, never follows a link taken from a response, bounds the size of the
answer, and puts the optional bearer token only on requests to that one address. Errors are redacted and never carry
the token.
"""

import json
import math
import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx

from geopoc.config import LOOPBACK_HOSTS
from geopoc.imagery.errors import ImageryRefused, clean_text
from geopoc.imagery.models import AssetRef, SceneItem

USER_AGENT = "geopoc-imagery/0.1 (local research workbench)"
MAX_ASSETS = 60
_TOKEN_RE = re.compile(r"^[A-Za-z0-9:_\-./=+%~]{1,1024}$")
_FRACTION_RE = re.compile(r"(\.\d{6})\d+")


def validate_base_url(url: str | None) -> str:
    """The catalogue address, or ImageryRefused. HTTPS only (plain HTTP only to this machine, for local fixtures)."""
    if not url or not isinstance(url, str):
        raise ImageryRefused("IMAGERY_NOT_CONFIGURED", "No catalogue address is configured")
    parts = urlsplit(url.strip())
    secure = parts.scheme == "https"
    local = parts.scheme == "http" and (parts.hostname or "") in LOOPBACK_HOSTS
    if not (secure or local) or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
        raise ImageryRefused(
            "IMAGERY_NOT_CONFIGURED",
            "The catalogue address must be an https:// address with no credentials, query or fragment",
        )
    return url.strip().rstrip("/")


def host_of(url: str) -> str:
    parts = urlsplit(url)
    return parts.netloc.rsplit("@", 1)[-1]


class StacClient:
    def __init__(
        self,
        base_url: str,
        *,
        timeout_s: float = 20.0,
        max_bytes: int = 5 * 1024**2,
        token: str | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = validate_base_url(base_url)
        self._timeout = max(1.0, min(float(timeout_s), 120.0))
        self._max_bytes = max(1024, int(max_bytes))
        self._token = token or None
        self._transport = transport

    @property
    def sends_credential(self) -> bool:
        return self._token is not None

    def _fail(self, code: str, message: str) -> ImageryRefused:
        return ImageryRefused(code, message, secrets=(self._token,))

    def search(self, body: dict[str, Any]) -> dict[str, Any]:
        """POST {base}/search with `body` and return the parsed answer (an object with a `features` list)."""
        url = f"{self.base_url}/search"
        headers = {"Accept": "application/geo+json, application/json", "User-Agent": USER_AGENT}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        try:
            with httpx.Client(
                transport=self._transport,
                timeout=httpx.Timeout(self._timeout, connect=min(5.0, self._timeout)),
                follow_redirects=False,
            ) as http:
                with http.stream("POST", url, json=body, headers=headers) as response:
                    status = response.status_code
                    declared = response.headers.get("content-length", "")
                    if declared.isdigit() and int(declared) > self._max_bytes:
                        raise self._fail("IMAGERY_BAD_RESPONSE", "The catalogue's answer is larger than the limit")
                    chunks: list[bytes] = []
                    size = 0
                    for chunk in response.iter_bytes(64 * 1024):
                        size += len(chunk)
                        if size > self._max_bytes:
                            raise self._fail("IMAGERY_BAD_RESPONSE", "The catalogue's answer is larger than the limit")
                        chunks.append(chunk)
                    retry_after = response.headers.get("retry-after", "")
        except httpx.TimeoutException:
            raise self._fail(
                "IMAGERY_UPSTREAM_TIMEOUT", f"The catalogue did not answer within {self._timeout:g} seconds"
            ) from None
        except httpx.HTTPError as exc:
            raise self._fail(
                "IMAGERY_UPSTREAM_UNREACHABLE", f"The catalogue could not be reached ({type(exc).__name__})"
            ) from None
        raw = b"".join(chunks)
        if status != 200:
            raise self._status_error(status, raw, retry_after)
        try:
            data = json.loads(raw)
        except ValueError:
            raise self._fail("IMAGERY_BAD_RESPONSE", "The catalogue's answer is not JSON") from None
        if not isinstance(data, dict) or not isinstance(data.get("features"), list):
            raise self._fail("IMAGERY_BAD_RESPONSE", "The catalogue's answer is not an item collection")
        return data

    def _status_error(self, status: int, raw: bytes, retry_after: str) -> ImageryRefused:
        hint = ""
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                text = parsed.get("description") or parsed.get("detail") or parsed.get("message") or parsed.get("code")
                if isinstance(text, str | int | float):
                    hint = ": " + clean_text(text)
        except ValueError:
            pass
        if status == 429:
            wait = f"; retry after {retry_after.strip()[:10]} s" if retry_after.strip().isdigit() else ""
            return self._fail("IMAGERY_UPSTREAM_RATE_LIMITED", f"The catalogue is limiting requests (HTTP 429){wait}")
        if status in (401, 403):
            return self._fail(
                "IMAGERY_UPSTREAM_REJECTED",
                f"The catalogue refused the request (HTTP {status}); its terms or access settings may have changed",
            )
        if 300 <= status < 400:
            return self._fail("IMAGERY_UPSTREAM_ERROR", f"The catalogue answered with a redirect (HTTP {status})")
        if 400 <= status < 500:
            return self._fail(
                "IMAGERY_UPSTREAM_BAD_REQUEST", f"The catalogue did not accept the search (HTTP {status}){hint}"
            )
        return self._fail("IMAGERY_UPSTREAM_ERROR", f"The catalogue reported an error (HTTP {status}){hint}")


# --- parsing ------------------------------------------------------------------------------------------


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        return None
    return float(value)


def _text(value: Any, limit: int = 120) -> str | None:
    return clean_text(value, limit) if isinstance(value, str) and value.strip() else None


def parse_time(value: Any) -> datetime | None:
    """An RFC 3339 time with a zone, as UTC. A time with no zone is not trusted (STAC requires one)."""
    if not isinstance(value, str):
        return None
    text = _FRACTION_RE.sub(r"\1", value.strip())
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    return moment.astimezone(UTC) if moment.tzinfo is not None else None


def _epsg(properties: dict[str, Any]) -> int | None:
    value = properties.get("proj:epsg")
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    code = properties.get("proj:code")
    if isinstance(code, str) and code.upper().startswith("EPSG:") and code[5:].isdigit():
        return int(code[5:])
    return None


def _geometry(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict) and value.get("type") in ("Polygon", "MultiPolygon") and "coordinates" in value:
        return value
    return None


def _bbox(value: Any) -> list[float] | None:
    if isinstance(value, list) and len(value) == 4:
        numbers = [_finite(v) for v in value]
        if all(n is not None for n in numbers):
            return numbers  # type: ignore[return-value]
    return None


def _assets(value: Any) -> list[AssetRef]:
    if not isinstance(value, dict):
        return []
    refs = []
    for key, asset in list(value.items())[:MAX_ASSETS]:
        if not isinstance(asset, dict):
            continue
        roles = asset.get("roles")
        refs.append(
            AssetRef(
                key=clean_text(key, 80),
                title=_text(asset.get("title"), 160),
                media_type=_text(asset.get("type"), 120),
                roles=[clean_text(r, 40) for r in roles[:10] if isinstance(r, str)] if isinstance(roles, list) else [],
            )
        )
    return refs


def parse_item(raw: Any, *, provider: str, collection: str, cloud_property: str = "eo:cloud_cover") -> SceneItem | None:
    """One STAC item as a SceneItem, or None when it lacks an id or an acquisition time (it could not be recorded
    honestly). Every other missing or odd field becomes None."""
    if not isinstance(raw, dict):
        return None
    properties = raw.get("properties") if isinstance(raw.get("properties"), dict) else {}
    scene_id = raw.get("id")
    if not isinstance(scene_id, str) or not scene_id.strip():
        return None
    moment = parse_time(properties.get("datetime")) or parse_time(properties.get("start_datetime"))
    if moment is None:
        return None
    cloud = _finite(properties.get(cloud_property))
    if cloud is not None and not 0 <= cloud <= 100:
        cloud = None
    grid = _text(properties.get("grid:code")) or _text(properties.get("s2:mgrs_tile"))
    if grid and grid.upper().startswith("MGRS-"):
        grid = grid[5:]
    instruments = properties.get("instruments")
    orbit = properties.get("sat:relative_orbit")
    return SceneItem(
        provider=provider,
        collection=_text(raw.get("collection"), 80) or collection,
        scene_id=clean_text(scene_id.strip(), 200),
        acquisition_time=moment,
        cloud_percent=cloud,
        platform=_text(properties.get("platform"), 40),
        constellation=_text(properties.get("constellation"), 40),
        instruments=[clean_text(i, 40) for i in instruments[:10] if isinstance(i, str)]
        if isinstance(instruments, list)
        else [],
        processing_level=_text(properties.get("processing:level"), 20),
        processing_baseline=_text(properties.get("s2:processing_baseline"), 20),
        mgrs_tile=grid,
        epsg=_epsg(properties),
        gsd_m=_finite(properties.get("gsd")),
        relative_orbit=orbit if isinstance(orbit, int) and not isinstance(orbit, bool) else None,
        geometry=_geometry(raw.get("geometry")),
        bbox=_bbox(raw.get("bbox")),
        assets=_assets(raw.get("assets")),
    )


def matched_count(answer: dict[str, Any]) -> int | None:
    for value in (answer.get("numberMatched"), (answer.get("context") or {}).get("matched")):
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
    return None


def next_page(answer: dict[str, Any]) -> tuple[bool, str | None]:
    """(a next page is offered, the provider's token for it). The token is read from the link's body or address; the
    address itself is never followed."""
    for link in answer.get("links") or []:
        if not isinstance(link, dict) or link.get("rel") != "next":
            continue
        body = link.get("body")
        token = body.get("token") if isinstance(body, dict) else None
        if token is None and isinstance(link.get("href"), str):
            values = parse_qs(urlsplit(link["href"]).query).get("token")
            token = values[0] if values else None
        if isinstance(token, str) and _TOKEN_RE.match(token):
            return True, token
        return True, None
    return False, None


def valid_provider_token(token: str) -> bool:
    return bool(_TOKEN_RE.match(token))
