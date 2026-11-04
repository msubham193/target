"""Smoke-test client for a running API:  python -m geopoc.smoke --url http://127.0.0.1:8020 --mode scratch

Two modes, and the difference is enforced in code, not by convention:

  scratch  (default)  The target must report a scratch database (geopoc_<something>, never "geopoc") in GET /health.
                      Only then may it send writes. An API that does not report its database is refused.
  working             Strictly read-only. Every request other than GET/HEAD raises WriteRefused before it is sent.

The client only talks to loopback addresses, always sets a timeout, and turns network failures into one-line messages.
It never prints credentials (it has none: the API identifies callers by a local profile id, which is not a secret).
"""

import argparse
import hashlib
import io
import json
import re
import sys
import uuid
from dataclasses import dataclass, field
from urllib.parse import urlparse

import httpx
from PIL import Image

SCRATCH_NAME = re.compile(r"^geopoc_[a-z0-9_]+$")
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
READ_METHODS = {"GET", "HEAD"}


class SmokeError(Exception):
    """A failure the person running the smoke test should read: the message is complete on its own."""


class WriteRefused(SmokeError):
    pass


class TargetIdentityError(SmokeError):
    pass


@dataclass
class Identity:
    name: str | None
    environment: str | None
    reported: bool


def is_scratch_name(name: str | None) -> bool:
    return bool(name) and bool(SCRATCH_NAME.match(name))


class GuardedClient:
    """An HTTP client that cannot write unless the target has proven to be a scratch database."""

    def __init__(self, http: httpx.Client, mode: str, timeout_s: float = 30.0) -> None:
        if mode not in ("scratch", "working"):
            raise SmokeError(f"Unknown mode {mode!r}: use 'scratch' or 'working'.")
        self.http = http
        self.mode = mode
        self.timeout_s = timeout_s
        self.identity: Identity | None = None
        self._writes_allowed = False
        host = urlparse(str(http.base_url)).hostname
        if host not in LOOPBACK_HOSTS:
            raise SmokeError(f"Refusing target host {host!r}: the smoke test only talks to this machine (127.0.0.1).")

    # --- requests -----------------------------------------------------------------------------------------------
    @staticmethod
    def _require_relative(url: str) -> None:
        """Only paths on the verified base URL are allowed: an absolute or scheme-relative URL would skip the checks."""
        parsed = urlparse(str(url))
        if parsed.scheme or parsed.netloc or not str(url).startswith("/"):
            raise SmokeError(
                f"Refusing {str(url)!r}: requests must be a path on the checked target (for example /health)."
            )

    def request(self, method: str, url: str, **kwargs) -> httpx.Response:
        method = method.upper()
        self._require_relative(url)
        if method not in READ_METHODS:
            if self.mode == "working":
                raise WriteRefused(f"{method} {url} refused: working mode is read-only (only GET and HEAD are sent).")
            if not self._writes_allowed:
                raise WriteRefused(
                    f"{method} {url} refused: the target has not been verified as a scratch database. "
                    "Call verify_identity() first."
                )
        kwargs.setdefault("timeout", self.timeout_s)
        try:
            return self.http.request(method, url, **kwargs)
        except httpx.TimeoutException as exc:
            raise SmokeError(f"{method} {url} timed out after {self.timeout_s:g} s ({type(exc).__name__}).") from exc
        except httpx.ConnectError as exc:
            raise SmokeError(
                f"Cannot connect to {self.http.base_url} for {method} {url}. Is the API running? ({exc})"
            ) from exc
        except httpx.HTTPError as exc:
            raise SmokeError(f"{method} {url} failed: {type(exc).__name__}: {exc}") from exc

    def get(self, url: str, **kwargs) -> httpx.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs) -> httpx.Response:
        return self.request("POST", url, **kwargs)

    def patch(self, url: str, **kwargs) -> httpx.Response:
        return self.request("PATCH", url, **kwargs)

    # --- identity -----------------------------------------------------------------------------------------------
    def verify_identity(self) -> Identity:
        response = self.get("/health")
        try:
            body = response.json()
            database = body["database"]
        except (ValueError, KeyError, TypeError) as exc:
            raise TargetIdentityError(
                f"GET /health did not return the expected document (HTTP {response.status_code}); "
                "the target is not trusted."
            ) from exc
        name = database.get("name")
        environment = database.get("environment")
        identity = Identity(name=name, environment=environment, reported=name is not None)
        self.identity = identity
        if self.mode == "scratch":
            if not identity.reported:
                raise TargetIdentityError(
                    "This API does not report which database it uses (it predates the identity fields), so it cannot "
                    "be confirmed as a scratch database. Writes are refused. Use scripts\\smoke.ps1, which starts its "
                    "own "
                    "scratch API."
                )
            if not is_scratch_name(name) or name == "geopoc" or environment != "scratch":
                raise TargetIdentityError(
                    f"Target reports database {name!r} ({environment}); only scratch databases (geopoc_<name>) are "
                    "allowed "
                    "for write checks. Nothing was sent."
                )
            self._writes_allowed = True
        else:
            if identity.reported and environment == "scratch":
                raise TargetIdentityError(
                    f"Working mode was requested but the target reports a scratch database ({name!r}). "
                    "Use --mode scratch."
                )
        return identity


@dataclass
class Report:
    results: list[tuple[str, bool, str]] = field(default_factory=list)

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        self.results.append((name, bool(ok), detail))
        print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""))
        return bool(ok)

    @property
    def failed(self) -> int:
        return sum(1 for _, ok, _ in self.results if not ok)


# --- read-only checks (both modes) ------------------------------------------------------------------------------


def _json(response: httpx.Response):
    try:
        return response.json()
    except ValueError:
        return None


def read_only_checks(client: GuardedClient, report: Report) -> None:
    health = _json(client.get("/health")) or {}
    database = health.get("database", {})
    report.check("health is ok", health.get("status") == "ok")
    report.check("database reachable", database.get("reachable") is True, f"revision {database.get('schema_revision')}")
    report.check(
        "online access is off, bound to loopback",
        health.get("online", {}).get("enabled") is False and health.get("bind_host") in LOOPBACK_HOSTS,
    )
    if client.identity is not None and client.identity.reported:
        report.check("identity reported", True, f"database {client.identity.name} ({client.identity.environment})")
    else:
        report.check(
            "identity reported", True, "this API version does not report its database name (read-only checks only)"
        )

    models = _json(client.get("/health/models")) or {}
    available = [m.get("name") for m in models.get("models", []) if m.get("available")]
    report.check("model health readable", "models" in models, f"available: {', '.join(available) or 'none'}")

    spec = _json(client.get("/openapi.json")) or {}
    paths = spec.get("paths", {})
    for route in (
        "/api/v1/projects",
        "/api/v1/assets/{asset_id}/location",
        "/api/v1/projects/{project_id}/photo-locations",
    ):
        report.check(f"route documented: {route}", route in paths)

    missing = client.get("/api/v1/assets/00000000-0000-4000-8000-000000000000/location")
    problem = _json(missing) or {}
    report.check(
        "unknown asset gives a readable 404", missing.status_code == 404 and problem.get("code") == "ASSET_NOT_FOUND"
    )
    malformed = client.get("/api/v1/assets/not-a-uuid/location")
    report.check(
        "malformed id gives a readable 422", malformed.status_code == 422 and "code" in (_json(malformed) or {})
    )

    projects = (_json(client.get("/api/v1/projects")) or {}).get("items", [])
    report.check("projects listable", isinstance(projects, list), f"{len(projects)} active project(s)")
    for project in projects:
        pid = project["id"]
        label = project["name"][:40]
        summary = client.get(f"/api/v1/projects/{pid}/summary")
        report.check(f"summary readable: {label}", summary.status_code == 200)
        listing = _json(client.get(f"/api/v1/projects/{pid}/photo-locations")) or {}
        items = listing.get("items", [])
        report.check(
            f"coordinates only for confirmed photographs: {label}",
            all((i.get("latitude") is not None) == (i.get("state") == "confirmed") for i in items),
            f"{len(items)} photo(s), {listing.get('confirmed', 0)} confirmed, "
            f"{listing.get('candidates', 0)} candidate(s)",
        )
        layers = client.get(f"/api/v1/projects/{pid}/layers")
        report.check(f"map layers readable: {label}", layers.status_code == 200)


# --- write checks (scratch only) ---------------------------------------------------------------------------------


def synthetic_jpeg(seed: int, gps: dict | None) -> bytes:
    """A small picture with an optional EXIF GPS block. Pillow writes the block; nothing is read from disk."""
    image = Image.new("RGB", (64, 48), ((seed * 37) % 256, (seed * 91) % 256, (seed * 53) % 256))
    for x in range(0, 64, 4):
        image.putpixel((x, (x + seed) % 48), (255, 255, 255))
    exif = Image.Exif()
    exif[271] = "SmokeCam"
    if gps is not None:
        exif[0x8825] = gps
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", exif=exif)
    return buffer.getvalue()


GPS_VALID = {1: "N", 2: (27.0, 6.0, 46.8), 3: "E", 4: (75.0, 0.0, 36.0)}  # 27.113 N, 75.010 E
GPS_VOID = {**GPS_VALID, 9: "V"}


def write_checks(client: GuardedClient, report: Report) -> None:
    tag = uuid.uuid4().hex[:8]
    profile = client.post("/api/v1/workspace/profiles", json={"display_name": f"Smoke {tag}"})
    if not report.check("scratch profile created", profile.status_code == 201):
        return
    headers = {"X-Workspace-Profile": profile.json()["id"]}
    project = client.post("/api/v1/projects", json={"name": f"Smoke project {tag}"}, headers=headers)
    if not report.check("scratch project created", project.status_code == 201):
        return
    pid = project.json()["id"]

    def upload(name: str, data: bytes) -> dict:
        response = client.post(
            f"/api/v1/projects/{pid}/assets",
            params={"modality": "ground_image", "filename": name},
            content=data,
            headers=headers,
        )
        if response.status_code != 201:
            raise SmokeError(f"Upload of {name} failed: HTTP {response.status_code} {response.text[:200]}")
        return response.json()["asset"]

    gps_bytes = synthetic_jpeg(1, GPS_VALID)
    gps = upload("smoke_gps.jpg", gps_bytes)
    plain = upload("smoke_plain.jpg", synthetic_jpeg(2, None))
    void = upload("smoke_void.jpg", synthetic_jpeg(3, GPS_VOID))
    report.check("original stored byte-for-byte", gps["sha256"] == hashlib.sha256(gps_bytes).hexdigest())
    report.check(
        "asset record carries no coordinates", "27.113" not in json.dumps(gps) and "75.01" not in json.dumps(gps)
    )

    location = _json(client.get(f"/api/v1/assets/{gps['id']}/location")) or {}
    exif = location.get("exif", {})
    report.check(
        "EXIF read as an unverified candidate",
        exif.get("state") == "candidate" and (exif.get("latitude"), exif.get("longitude")) == (27.113, 75.01),
    )
    report.check("candidate is not the current location", location.get("current", {}).get("state") == "none")
    report.check(
        "unusable GPS is not a candidate",
        (_json(client.get(f"/api/v1/assets/{void['id']}/location")) or {}).get("exif", {}).get("state") == "unusable",
    )
    geo = _json(client.get(f"/api/v1/projects/{pid}/exports/observations.geojson", headers=headers)) or {}
    report.check("GeoJSON has no photograph before confirmation", geo.get("features") == [])

    def change(asset: dict, **body):
        body.setdefault("confirm", True)
        return client.post(f"/api/v1/assets/{asset['id']}/location", json=body, headers=headers)

    report.check(
        "confirmation flag required",
        change(gps, action="confirm_exif", expected_version=1, confirm=False).status_code == 422,
    )
    report.check("stale version refused", change(gps, action="confirm_exif", expected_version=0).status_code == 409)
    report.check(
        "a profile is required",
        client.post(
            f"/api/v1/assets/{gps['id']}/location",
            json={"action": "confirm_exif", "expected_version": 1, "confirm": True},
        ).status_code
        == 401,
    )
    confirmed = change(gps, action="confirm_exif", expected_version=1)
    report.check(
        "EXIF candidate confirmed by a person",
        confirmed.status_code == 201 and confirmed.json()["current"]["state"] == "confirmed",
    )
    for lat, lon, code in (
        (95, 10, "LOCATION_LATITUDE_RANGE"),
        (10, 200, "LOCATION_LONGITUDE_RANGE"),
        (0, 0, "LOCATION_NULL_ISLAND"),
    ):
        refused = change(
            plain, action="set_position", method="manual_entry", latitude=lat, longitude=lon, expected_version=0
        )
        report.check(
            f"manual entry refused: {lat},{lon}",
            refused.status_code == 422 and (_json(refused) or {}).get("code") == code,
        )
    typed = change(
        plain, action="set_position", method="manual_entry", latitude=27.1135, longitude=75.0105, expected_version=0
    )
    report.check(
        "manual placement recorded", typed.status_code == 201 and typed.json()["current"]["source"] == "manual_entry"
    )
    picked = change(
        void, action="set_position", method="map_selection", latitude=27.1128, longitude=75.0098, expected_version=0
    )
    report.check(
        "map-selection placement recorded",
        picked.status_code == 201 and picked.json()["current"]["source"] == "map_selection",
    )

    listing = _json(client.get(f"/api/v1/projects/{pid}/photo-locations")) or {}
    report.check(
        "map list: three located photographs",
        listing.get("confirmed") == 3 and all(i["latitude"] is not None for i in listing.get("items", [])),
    )
    geo = _json(client.get(f"/api/v1/projects/{pid}/exports/observations.geojson", headers=headers)) or {}
    photos = [f for f in geo.get("features", []) if f.get("properties", {}).get("feature_type") == "photo_location"]
    report.check(
        "GeoJSON: three Point features in longitude, latitude order",
        len(photos) == 3 and [75.01, 27.113] in [f["geometry"]["coordinates"] for f in photos],
    )
    content = client.get(f"/api/v1/assets/{gps['id']}/content")
    report.check(
        "original unchanged after location changes", hashlib.sha256(content.content).hexdigest() == gps["sha256"]
    )
    history = (_json(client.get(f"/api/v1/assets/{gps['id']}/location")) or {}).get("history", [])
    report.check("history is attributed and ordered", [h.get("status") for h in history] == ["candidate", "confirmed"])


def run(client: GuardedClient) -> Report:
    report = Report()
    identity = client.verify_identity()
    print(
        f"Target {client.http.base_url}: database {identity.name!r} "
        f"({identity.environment or 'not reported'}), mode {client.mode}"
    )
    read_only_checks(client, report)
    if client.mode == "scratch":
        write_checks(client, report)
    else:
        print("Working mode: write checks skipped (read-only).")
    print(f"\n{len(report.results) - report.failed} passed, {report.failed} failed")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Smoke test for the Geospatial PoC API.")
    parser.add_argument("--url", required=True, help="API base URL on this machine, for example http://127.0.0.1:8020")
    parser.add_argument("--mode", choices=["scratch", "working"], default="scratch")
    parser.add_argument("--timeout", type=float, default=30.0, help="seconds per request")
    arguments = parser.parse_args(argv)
    try:
        with httpx.Client(base_url=arguments.url) as http:
            client = GuardedClient(http, arguments.mode, timeout_s=arguments.timeout)
            report = run(client)
    except SmokeError as exc:
        print(f"SMOKE TEST STOPPED: {exc}", file=sys.stderr)
        return 2
    return 1 if report.failed else 0


if __name__ == "__main__":
    sys.exit(main())
