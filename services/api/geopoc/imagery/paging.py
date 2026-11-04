"""Stateless page tokens. Nothing is stored: the token carries the provider's own cursor and a fingerprint of the
search it belongs to, so a cursor cannot be replayed against a different area, window, cloud limit or page size."""

import base64
import hashlib
import json

from geopoc.imagery.errors import ImageryRefused
from geopoc.imagery.stac import valid_provider_token

VERSION = 1
INVALID = "The page token does not belong to this search. Start again from the first page."


def query_fingerprint(
    provider: str, collection: str, bbox: list[float], window: str, max_cloud: float | None, limit: int
) -> str:
    canonical = json.dumps([provider, collection, bbox, window, max_cloud, limit], separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def encode(provider: str, collection: str, fingerprint: str, provider_token: str) -> str:
    payload = {"v": VERSION, "p": provider, "c": collection, "f": fingerprint, "t": provider_token}
    return base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")


def decode(token: str, provider: str, collection: str, fingerprint: str) -> str:
    """The provider's cursor, or ImageryRefused if the token is malformed or was made for another search."""
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
        payload = json.loads(raw)
    except (ValueError, TypeError):
        raise ImageryRefused("IMAGERY_PAGE_TOKEN_INVALID", INVALID) from None
    if (
        not isinstance(payload, dict)
        or payload.get("v") != VERSION
        or payload.get("p") != provider
        or payload.get("c") != collection
        or payload.get("f") != fingerprint
        or not isinstance(payload.get("t"), str)
        or not valid_provider_token(payload["t"])
    ):
        raise ImageryRefused("IMAGERY_PAGE_TOKEN_INVALID", INVALID)
    return payload["t"]
