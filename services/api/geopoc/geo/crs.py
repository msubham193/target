"""CRS identification against the local PROJ database. Works offline; nothing is ever guessed."""

import re
from functools import lru_cache
from typing import Any

import pyproj
from pyproj.database import query_crs_info
from pyproj.exceptions import CRSError

# Never fetch grids or definitions from the network.
pyproj.network.set_network_enabled(False)

IDENTIFIER_RE = re.compile(r"^([A-Za-z][A-Za-z0-9_]*):([A-Za-z0-9_.\-]+)$")

INCOMPLETE_CRS_MESSAGE = (
    "A CRS must be identified by an authority code such as EPSG:4326. A datum name, a projection-method "
    "name, or a combination of them (for example 'WGS84 + LCC') does not define a complete CRS."
)


class CrsResolutionError(ValueError):
    pass


def normalise_identifier(identifier: str) -> str:
    match = IDENTIFIER_RE.match(identifier.strip())
    if not match:
        raise CrsResolutionError(INCOMPLETE_CRS_MESSAGE)
    return f"{match[1].upper()}:{match[2]}"


def resolve(identifier: str) -> pyproj.CRS:
    """Resolve an authority code. Free-text names are rejected on purpose."""
    normalised = normalise_identifier(identifier)
    authority, code = normalised.split(":", 1)
    try:
        return pyproj.CRS.from_authority(authority, code)
    except CRSError as exc:
        raise CrsResolutionError(f"{normalised} is not in the local CRS registry.") from exc


def _kind(crs: pyproj.CRS) -> str:
    if crs.is_compound:
        return "compound"
    if crs.is_geographic:
        return "geographic"
    if crs.is_projected:
        return "projected"
    if crs.is_vertical:
        return "vertical"
    if crs.is_geocentric:
        return "geocentric"
    return "other"


def describe(identifier: str) -> dict[str, Any]:
    crs = resolve(identifier)
    axes = [
        {"abbreviation": axis.abbrev, "direction": axis.direction, "unit": axis.unit_name}
        for axis in crs.axis_info
    ]
    area = crs.area_of_use
    return {
        "identifier": normalise_identifier(identifier),
        "name": crs.name,
        "kind": _kind(crs),
        "units": list(dict.fromkeys(axis["unit"] for axis in axes)),
        "axis_order": axes,
        "area_of_use": (
            {
                "name": area.name,
                "west": area.west,
                "south": area.south,
                "east": area.east,
                "north": area.north,
            }
            if area
            else None
        ),
        "deprecated": crs.is_deprecated,
    }


@lru_cache(maxsize=1)
def _registry() -> tuple[tuple[str, str, str, bool], ...]:
    return tuple((info.auth_name, info.code, info.name, info.deprecated) for info in query_crs_info())


def search(query: str, limit: int = 20) -> list[dict[str, Any]]:
    """Search the registry by authority code or by words in the official CRS name."""
    text = query.strip()
    if not text:
        return []
    lowered = text.lower()
    tokens = lowered.split()
    id_match = IDENTIFIER_RE.match(text)
    wanted_auth = id_match[1].upper() if id_match else None
    wanted_code = id_match[2].lower() if id_match else lowered

    ranked: list[tuple[int, bool, bool, int, str, str]] = []
    for auth, code, name, deprecated in _registry():
        if wanted_auth and auth != wanted_auth:
            continue
        code_l = code.lower()
        if code_l == wanted_code:
            rank = 0
        elif code_l.startswith(wanted_code):
            rank = 1
        elif not wanted_auth and all(token in name.lower() for token in tokens):
            rank = 2
        else:
            continue
        ranked.append((rank, deprecated, auth != "EPSG", len(code), code, f"{auth}:{code}"))

    results: list[dict[str, Any]] = []
    for *_, identifier in sorted(ranked):
        try:
            results.append(describe(identifier))
        except CrsResolutionError:
            continue
        if len(results) >= limit:
            break
    return results
