"""Satellite catalogue discovery, Phase 1 (geopoc.imagery). Entirely offline: the catalogue is a mock HTTP transport that
serves fixture files, and no test opens a socket. Nothing is downloaded, written to the database or kept on disk."""

import json
import logging
import re
import socket
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import text

from geopoc.api.main import app
from geopoc.api.routers.imagery import ImageryHooks, get_imagery_settings
from geopoc.api.routers.imagery import get_hooks as get_imagery_hooks
from geopoc.config import Settings, get_settings
from geopoc.db.session import get_engine
from geopoc.imagery import paging
from geopoc.imagery.aoi import fingerprint, parse_aoi, rounded_bbox
from geopoc.imagery.errors import STATUS, ImageryRefused, redact
from geopoc.imagery.models import ImageryLimits, SearchQuery
from geopoc.imagery.providers import build_registry, cdse_provider, get_provider
from geopoc.imagery.stac import StacClient, parse_item, parse_time, validate_base_url

FIXTURES = Path(__file__).parent / "fixtures" / "stac"
BASE = "https://stac.example.invalid/v1"
TOKEN = "TESTTOKEN-abcdef0123456789"
PAGE1 = json.loads((FIXTURES / "s2_l2a_page1.json").read_text(encoding="utf-8"))
PAGE2 = json.loads((FIXTURES / "s2_l2a_page2.json").read_text(encoding="utf-8"))

# Deliberately many decimals: the exact polygon must never appear in what is sent.
AOI = {
    "type": "Polygon",
    "coordinates": [
        [
            [10.0123456, 50.0123456],
            [10.0456789, 50.0123456],
            [10.0456789, 50.0456789],
            [10.0123456, 50.0456789],
            [10.0123456, 50.0123456],
        ]
    ],
}
EXACT_NUMBERS = ("10.0123456", "50.0123456", "10.0456789", "50.0456789")
ROUNDED = [10.01, 50.01, 10.05, 50.05]


# --- helpers --------------------------------------------------------------------------------------------------------------


def make_settings(**over) -> Settings:
    values = {
        "online_enabled": True,
        "imagery_enabled": True,
        "imagery_terms_confirmed": True,
        "imagery_cdse_stac_url": BASE,
    }
    values.update(over)
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


class Catalogue:
    """A mock catalogue. Each request gets the next scripted answer (the last one repeats); every request is recorded."""

    def __init__(self, *answers):
        self.answers = list(answers) or [httpx.Response(200, json=PAGE1)]
        self.requests: list[httpx.Request] = []
        self.transport = httpx.MockTransport(self)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        answer = self.answers[min(len(self.requests) - 1, len(self.answers) - 1)]
        if isinstance(answer, Exception):
            raise answer
        return answer(request) if callable(answer) else answer

    def bodies(self) -> list[dict]:
        return [json.loads(r.content) for r in self.requests]


@contextmanager
def no_network():
    def refuse(*_a, **_k):
        raise AssertionError("a network connection was attempted")

    with mock.patch.object(socket.socket, "connect", refuse), mock.patch.object(socket, "getaddrinfo", refuse):
        yield


def search_body(**over) -> dict:
    body = {
        "aoi": AOI,
        "start_date": "2024-05-20",
        "end_date": "2024-06-20",
        "max_cloud_percent": 50,
        "limit": 20,
        "confirm_external_transmission": True,
    }
    body.update(over)
    return body


def query(**over) -> SearchQuery:
    values = {
        "provider": "cdse",
        "collection": "sentinel-2-l2a",
        "aoi": parse_aoi(AOI),
        "start": date(2024, 5, 20),
        "end": date(2024, 6, 20),
        "max_cloud_percent": 50.0,
        "limit": 20,
    }
    values.update(over)
    return SearchQuery(**values)


TABLES = (
    "audit_event",
    "workspace_user",
    "project",
    "asset",
    "blob",
    "job",
    "raster_metadata",
    "artifact",
    "observation",
)


def db_state() -> dict:
    with get_engine().connect() as conn:
        counts = {t: conn.execute(text(f"SELECT count(*) FROM {t}")).scalar_one() for t in TABLES}
        counts["project_rows"] = [
            tuple(r) for r in conn.execute(text("SELECT id, updated_at, aoi_geom IS NULL FROM project"))
        ]
    return counts


@pytest.fixture
def api(client, headers, project):
    """The real routes with these settings and a mock catalogue; restored afterwards."""
    state = SimpleNamespace(
        settings=make_settings(), catalogue=Catalogue(), client=client, headers=headers, project=project
    )
    app.dependency_overrides[get_imagery_settings] = lambda: state.settings
    app.dependency_overrides[get_imagery_hooks] = lambda: ImageryHooks(transport=state.catalogue.transport)
    yield state
    app.dependency_overrides.pop(get_imagery_settings, None)
    app.dependency_overrides.pop(get_imagery_hooks, None)


def allow_online(api) -> None:
    response = api.client.patch(
        f"/api/v1/projects/{api.project['id']}", json={"source_preference": "allow_online"}, headers=api.headers
    )
    assert response.status_code == 200, response.text


def search(api, **over):
    return api.client.post(
        f"/api/v1/projects/{api.project['id']}/scene-search", json=search_body(**over), headers=api.headers
    )


# --- the area of interest ---------------------------------------------------------------------------------------------------


def test_a_valid_polygon_is_accepted_and_measured():
    aoi = parse_aoi(AOI)
    assert 0 < aoi.area_km2 < 20 and aoi.bounds == (10.0123456, 50.0123456, 10.0456789, 50.0456789)


@pytest.mark.parametrize(
    ("geometry", "code"),
    [
        (None, "AOI_INVALID"),
        ("POLYGON((0 0,1 0,1 1,0 0))", "AOI_INVALID"),
        ({"type": "Point", "coordinates": [10, 50]}, "AOI_INVALID"),
        ({"type": "MultiPolygon", "coordinates": []}, "AOI_INVALID"),
        ({"type": "Polygon"}, "AOI_INVALID"),
        ({"type": "Polygon", "coordinates": []}, "AOI_INVALID"),
        ({"type": "Polygon", "coordinates": [[[0, 0], [1, 0]]]}, "AOI_INVALID"),
        ({"type": "Polygon", "coordinates": [[[0, 0, 5], [1, 0, 5], [1, 1, 5], [0, 0, 5]]]}, "AOI_INVALID"),
        ({"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, "1"], [0, 0]]]}, "AOI_INVALID"),
        ({"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [True, 1], [0, 0]]]}, "AOI_INVALID"),
        ({"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [float("nan"), 1], [0, 0]]]}, "AOI_INVALID"),
        ({"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [float("inf"), 1], [0, 0]]]}, "AOI_INVALID"),
        ({"type": "Polygon", "coordinates": [[[0, 0], [1, 1], [2, 2], [0, 0]]]}, "AOI_INVALID"),  # no area
        ({"type": "Polygon", "coordinates": [[[0, 0], [2, 2], [2, 0], [0, 2], [0, 0]]]}, "AOI_INVALID"),  # bow-tie
        ({"type": "Polygon", "coordinates": [[[200, 0], [201, 0], [201, 1], [200, 0]]]}, "AOI_RANGE"),
        ({"type": "Polygon", "coordinates": [[[0, 95], [1, 95], [1, 96], [0, 95]]]}, "AOI_RANGE"),
        ({"type": "Polygon", "coordinates": [[[-179, 0], [179, 0], [179, 1], [-179, 0]]]}, "AOI_ANTIMERIDIAN"),
    ],
)
def test_an_invalid_area_is_rejected_with_a_stable_code(geometry, code):
    with pytest.raises(ImageryRefused) as caught:
        parse_aoi(geometry)
    assert caught.value.code == code and caught.value.status == 422


def test_an_over_complex_polygon_is_refused_before_any_geometry_is_built():
    ring = [[10 + i * 1e-6, 50 + (i % 2) * 1e-6] for i in range(1500)]
    with pytest.raises(ImageryRefused) as caught:
        parse_aoi({"type": "Polygon", "coordinates": [ring + [ring[0]]]})
    assert caught.value.code == "AOI_TOO_COMPLEX"


@pytest.mark.parametrize("step", [0.01, 0.05, 0.25, 1.0, 0.0, -3.0, float("nan")])
def test_the_box_that_is_sent_always_contains_the_area_and_never_leaves_the_world(step):
    for bounds in [
        (10.0123456, 50.0123456, 10.0456789, 50.0456789),
        (-179.999, -89.999, -179.5, -89.5),
        (179.9, 89.9, 180, 90),
    ]:
        west, south, east, north = rounded_bbox(bounds, step)
        assert west <= bounds[0] and south <= bounds[1] and east >= bounds[2] and north >= bounds[3]
        assert -180 <= west < east <= 180 and -90 <= south < north <= 90


def test_the_rounded_box_is_on_the_grid_and_the_fingerprint_is_not_the_coordinates():
    assert rounded_bbox((10.0123456, 50.0123456, 10.0456789, 50.0456789), 0.01) == ROUNDED
    assert all(part not in fingerprint(ROUNDED) for part in ("10.01", "50.05"))


# --- the request -------------------------------------------------------------------------------------------------------------


def test_dates_cloud_limit_and_area_size_are_checked():
    today = date(2026, 11, 2)
    ok = query()
    assert ok.check(today=today) is ok
    cases = {
        "DATES_INVALID": query(start=date(2024, 6, 2), end=date(2024, 6, 1)),
        "DATES_FUTURE": query(end=today + timedelta(days=1)),
        "DATES_TOO_EARLY": query(start=date(2015, 6, 22), end=date(2015, 7, 1)),
        "DATES_TOO_LONG": query(start=date(2023, 1, 1), end=date(2024, 6, 1)),
        "CLOUD_RANGE": query(max_cloud_percent=100.5),
        "LIMIT_RANGE": query(limit=0),
    }
    for code, bad in cases.items():
        with pytest.raises(ImageryRefused) as caught:
            bad.check(today=today)
        assert caught.value.code == code, code
    big = parse_aoi({"type": "Polygon", "coordinates": [[[0, 0], [3, 0], [3, 3], [0, 3], [0, 0]]]})
    with pytest.raises(ImageryRefused) as caught:
        query(aoi=big).check(ImageryLimits(max_aoi_km2=1000), today=today)
    assert caught.value.code == "AOI_TOO_LARGE"
    with pytest.raises(ImageryRefused):
        query(limit=101).check(today=today)


def test_every_refusal_code_has_a_status_and_none_is_a_server_error():
    assert all(status < 500 or status in (502, 504) for status in STATUS.values())
    assert (
        STATUS["IMAGERY_DISABLED"] == 409 and STATUS["AOI_INVALID"] == 422 and STATUS["IMAGERY_UPSTREAM_TIMEOUT"] == 504
    )


# --- provider selection and the search request ----------------------------------------------------------------------------


def test_provider_selection():
    settings = make_settings()
    assert list(build_registry(settings)) == ["cdse"]
    provider = get_provider(settings, "cdse")
    assert provider.id == "cdse" and [c.id for c in provider.collections] == ["sentinel-2-l2a"]
    with pytest.raises(ImageryRefused) as caught:
        get_provider(settings, "elsewhere")
    assert caught.value.code == "IMAGERY_UNKNOWN_PROVIDER"
    with pytest.raises(ImageryRefused) as caught:
        provider.collection("sentinel-1-grd")
    assert caught.value.code == "IMAGERY_UNSUPPORTED_COLLECTION"


def test_the_search_request_is_built_exactly_and_carries_no_exact_coordinates():
    provider = cdse_provider(make_settings())
    prepared = provider.prepare(query(), 0.01)
    assert prepared.body == {
        "collections": ["sentinel-2-l2a"],
        "bbox": ROUNDED,
        "datetime": "2024-05-20T00:00:00Z/2024-06-20T23:59:59Z",
        "limit": 20,
        "sortby": [{"field": "properties.datetime", "direction": "desc"}],
        "query": {"eo:cloud_cover": {"lte": 50.0}},
    }
    sent = json.dumps(prepared.request_body())
    assert (
        not any(number in sent for number in EXACT_NUMBERS) and "coordinates" not in sent and "intersects" not in sent
    )
    without_cloud = provider.prepare(query(max_cloud_percent=None), 0.01)
    assert "query" not in without_cloud.body


def test_the_cursor_is_added_only_on_a_later_page_and_only_for_the_same_search():
    provider = cdse_provider(make_settings())
    first = provider.prepare(query(), 0.01)
    token = paging.encode("cdse", "sentinel-2-l2a", first.fingerprint, "next:ABC-123_x")
    second = provider.prepare(query(page_token=token), 0.01)
    assert second.request_body()["token"] == "next:ABC-123_x" and "token" not in first.request_body()
    for other in (
        query(page_token=token, limit=21),
        query(page_token=token, max_cloud_percent=10.0),
        query(page_token=token, end=date(2024, 6, 19)),
    ):
        with pytest.raises(ImageryRefused) as caught:
            provider.prepare(other, 0.01)
        assert caught.value.code == "IMAGERY_PAGE_TOKEN_INVALID"
    for junk in ("", "abc", "!!!!", paging.encode("cdse", "sentinel-2-l2a", "0" * 16, "next:x"), "e30"):
        with pytest.raises(ImageryRefused):
            provider.prepare(query(page_token=junk or "x"), 0.01)


def test_a_cursor_with_unsafe_characters_is_refused():
    provider = cdse_provider(make_settings())
    fp = provider.prepare(query(), 0.01).fingerprint
    for bad in ("next:a b", "x" * 2000, "a\nb", "a;b", '{"a":1}'):
        with pytest.raises(ImageryRefused):
            provider.prepare(query(page_token=paging.encode("cdse", "sentinel-2-l2a", fp, bad)), 0.01)


# --- the client and parsing -------------------------------------------------------------------------------------------------


def client_for(catalogue: Catalogue, token: str | None = None, **over) -> StacClient:
    return StacClient(BASE, transport=catalogue.transport, token=token, **over)


def test_the_provider_page_is_filtered_by_cloud_by_the_exact_area_and_for_unusable_items():
    provider = cdse_provider(make_settings())
    catalogue = Catalogue(httpx.Response(200, json=PAGE1))
    prepared = provider.prepare(query(), 0.01)
    page = provider.search(query(), prepared, client_for(catalogue))
    assert [i.cloud_percent for i in page.items] == [12.5, 45.0]
    assert all(i.aoi_check == "intersects" for i in page.items)
    f = page.filtering
    assert (f.server_returned, f.above_cloud_limit, f.outside_aoi, f.malformed, f.unknown_cloud, f.kept) == (
        6,
        1,
        1,
        1,
        1,
        2,
    )
    assert page.matched == 37 and page.next_token


def test_without_a_cloud_limit_a_scene_with_unknown_cloud_is_kept_and_stays_unknown():
    provider = cdse_provider(make_settings())
    q = query(max_cloud_percent=None)
    page = provider.search(q, provider.prepare(q, 0.01), client_for(Catalogue(httpx.Response(200, json=PAGE1))))
    assert [i.cloud_percent for i in page.items] == [12.5, 45.0, 85.0, None]
    assert page.filtering.unknown_cloud == 0


def test_scene_fields_come_from_the_catalogue_and_nothing_is_invented():
    item = parse_item(PAGE1["features"][0], provider="cdse", collection="sentinel-2-l2a")
    assert (
        item.scene_id.startswith("S2B_MSIL2A_20240612")
        and item.acquisition_time.isoformat() == "2024-06-12T10:15:59.024000+00:00"
    )
    assert (item.platform, item.constellation, item.instruments, item.processing_level) == (
        "sentinel-2b",
        "sentinel-2",
        ["msi"],
        "L2A",
    )
    assert (item.mgrs_tile, item.epsg, item.gsd_m, item.relative_orbit, item.processing_baseline) == (
        "32UNA",
        32632,
        10.0,
        65,
        "05.10",
    )
    bare = parse_item({"id": "x", "properties": {"datetime": "2024-01-01T00:00:00Z"}}, provider="cdse", collection="c")
    assert (
        bare.cloud_percent is None
        and bare.platform is None
        and bare.epsg is None
        and bare.geometry is None
        and bare.assets == []
    )


def test_asset_names_are_kept_but_never_their_addresses():
    item = parse_item(PAGE1["features"][0], provider="cdse", collection="sentinel-2-l2a")
    assert {a.key for a in item.assets} == {"B02_10m", "SCL_20m", "thumbnail"}
    assert "s3://" not in repr(item) and "SECRETSIG" not in repr(item) and "href" not in repr(item)


@pytest.mark.parametrize(
    "raw",
    [
        None,
        [],
        {},
        {"id": ""},
        {"id": 7, "properties": {"datetime": "2024-01-01T00:00:00Z"}},
        {"id": "x", "properties": {}},
        {"id": "x", "properties": {"datetime": "2024-01-01T00:00:00"}},  # no zone: not trusted
        {"id": "x", "properties": {"datetime": "yesterday"}},
    ],
)
def test_items_that_cannot_be_recorded_honestly_are_skipped(raw):
    assert parse_item(raw, provider="cdse", collection="c") is None


def test_odd_values_become_unknown_not_guesses():
    props = {
        "datetime": "2024-01-01T00:00:00Z",
        "eo:cloud_cover": 140,
        "proj:code": "EPSG:32633",
        "gsd": float("nan"),
        "sat:relative_orbit": True,
    }
    item = parse_item(
        {"id": "a\x00b\x1b[31m", "properties": props, "geometry": {"type": "Point"}, "bbox": [1, 2, 3]},
        provider="cdse",
        collection="c",
    )
    assert item.cloud_percent is None and item.epsg == 32633 and item.gsd_m is None and item.relative_orbit is None
    assert item.geometry is None and item.bbox is None and all(ch.isprintable() for ch in item.scene_id)
    assert parse_time("2024-01-01T00:00:00.123456789Z").microsecond == 123456
    assert parse_time("2024-01-01T02:00:00+02:00").hour == 0


@pytest.mark.parametrize(
    "url",
    [
        "",
        None,
        "http://stac.example.invalid/v1",
        "ftp://x/y",
        "https://u:p@x.invalid/v1",
        "https://x.invalid/v1?a=1",
        "https:///v1",
    ],
)
def test_only_a_plain_https_address_is_accepted(url):
    with pytest.raises(ImageryRefused) as caught:
        validate_base_url(url)
    assert caught.value.code == "IMAGERY_NOT_CONFIGURED"
    assert validate_base_url("https://stac.example.invalid/v1/") == BASE
    assert validate_base_url("http://127.0.0.1:9000/v1") == "http://127.0.0.1:9000/v1"


@pytest.mark.parametrize(
    ("answer", "code", "status"),
    [
        (httpx.ReadTimeout("slow"), "IMAGERY_UPSTREAM_TIMEOUT", 504),
        (httpx.ConnectTimeout("slow"), "IMAGERY_UPSTREAM_TIMEOUT", 504),
        (httpx.ConnectError("refused"), "IMAGERY_UPSTREAM_UNREACHABLE", 502),
        (httpx.RemoteProtocolError("broken"), "IMAGERY_UPSTREAM_UNREACHABLE", 502),
        (httpx.Response(429, headers={"Retry-After": "30"}, json={}), "IMAGERY_UPSTREAM_RATE_LIMITED", 429),
        (httpx.Response(401, json={}), "IMAGERY_UPSTREAM_REJECTED", 502),
        (httpx.Response(403, text="no"), "IMAGERY_UPSTREAM_REJECTED", 502),
        (httpx.Response(400, json={"description": "sortby is not supported"}), "IMAGERY_UPSTREAM_BAD_REQUEST", 502),
        (httpx.Response(500, text="boom"), "IMAGERY_UPSTREAM_ERROR", 502),
        (httpx.Response(503, json={"code": "Unavailable"}), "IMAGERY_UPSTREAM_ERROR", 502),
        (httpx.Response(302, headers={"Location": "https://elsewhere.invalid/"}), "IMAGERY_UPSTREAM_ERROR", 502),
        (httpx.Response(200, text="<html>not json</html>"), "IMAGERY_BAD_RESPONSE", 502),
        (httpx.Response(200, json=[1, 2]), "IMAGERY_BAD_RESPONSE", 502),
        (httpx.Response(200, json={"features": "none"}), "IMAGERY_BAD_RESPONSE", 502),
    ],
)
def test_provider_failures_become_stable_refusals(answer, code, status):
    catalogue = Catalogue(answer)
    with pytest.raises(ImageryRefused) as caught:
        client_for(catalogue).search({"collections": ["sentinel-2-l2a"]})
    assert caught.value.code == code and caught.value.status == status
    assert len(catalogue.requests) == 1  # no retry, and a redirect is not followed


def test_the_answer_size_is_bounded_whether_declared_or_streamed():
    huge = b'{"features": [' + b'{"id": "x"},' * 2000 + b"{}]}"
    declared = httpx.Response(200, content=huge)
    streamed = httpx.Response(200, content=huge)
    del streamed.headers["content-length"]  # only the bytes themselves reveal the size
    assert "content-length" in declared.headers
    for response in (declared, streamed):
        with pytest.raises(ImageryRefused) as caught:
            client_for(Catalogue(response), max_bytes=2048).search({})
        assert caught.value.code == "IMAGERY_BAD_RESPONSE"


def test_the_token_goes_only_in_the_authorization_header_to_the_configured_address():
    catalogue = Catalogue(httpx.Response(200, json=PAGE2))
    client_for(catalogue, token=TOKEN).search({"collections": ["c"]})
    request = catalogue.requests[0]
    assert request.headers["authorization"] == f"Bearer {TOKEN}" and str(request.url) == f"{BASE}/search"
    assert TOKEN not in request.content.decode() and TOKEN not in str(request.url)
    anonymous = Catalogue(httpx.Response(200, json=PAGE2))
    client_for(anonymous).search({})
    assert "authorization" not in anonymous.requests[0].headers


def test_links_in_an_answer_are_never_followed():
    evil = {
        **PAGE1,
        "links": [
            {"rel": "next", "method": "POST", "href": "https://evil.invalid/steal", "body": {"token": "next:ok"}}
        ],
    }
    provider = cdse_provider(make_settings(imagery_cdse_access_token=SecretStr(TOKEN)))
    catalogue = Catalogue(httpx.Response(200, json=evil))
    provider.search(query(), provider.prepare(query(), 0.01), provider.client(catalogue.transport))
    assert [str(r.url) for r in catalogue.requests] == [f"{BASE}/search"]


def test_a_next_link_this_client_cannot_follow_is_reported_not_hidden():
    answer = {**PAGE2, "links": [{"rel": "next", "href": "https://stac.example.invalid/v1/search?page=2"}]}
    provider = cdse_provider(make_settings())
    page = provider.search(
        query(), provider.prepare(query(), 0.01), client_for(Catalogue(httpx.Response(200, json=answer)))
    )
    assert page.next_token is None and any("cannot follow" in n for n in page.notes)
    by_query = {**PAGE2, "links": [{"rel": "next", "href": "https://stac.example.invalid/v1/search?token=next%3Aq1"}]}
    page = provider.search(
        query(), provider.prepare(query(), 0.01), client_for(Catalogue(httpx.Response(200, json=by_query)))
    )
    assert (
        page.next_token
        and paging.decode(page.next_token, "cdse", "sentinel-2-l2a", provider.prepare(query(), 0.01).fingerprint)
        == "next:q1"
    )


def test_redaction_removes_credentials_and_exact_secrets():
    samples = [
        f"Bearer {TOKEN}",
        "Authorization: Basic dXNlcjpwYXNzd29yZA==",
        "https://x.invalid/a.jp2?X-Amz-Signature=deadbeef1234&X-Amz-Credential=AKIAABCDEFGHIJKLMNOP",
        'token="abc123def456"',
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.c2ln",
        "client_secret=hunter2hunter2",
        "AKIAABCDEFGHIJKLMNOP",
    ]
    for sample in samples:
        out = redact(sample)
        assert not any(
            s in out
            for s in (
                "dXNlcjpwYXNzd29yZA",
                "deadbeef1234",
                "abc123def456",
                "c2ln",
                "hunter2",
                "AKIAABCDEFGHIJKLMNOP",
                TOKEN,
            )
        ), (sample, out)
    assert "mysecretvalue" not in redact("it said mysecretvalue", secrets=("mysecretvalue",))
    assert str(ImageryRefused("X", f"oops {TOKEN}", secrets=(TOKEN,))) == "oops [removed]"


# --- the API: providers -------------------------------------------------------------------------------------------------------


def test_providers_need_a_profile(client):
    assert client.get("/api/v1/imagery/providers").status_code == 401


def test_by_default_everything_is_off_and_nothing_is_ready(client, headers):
    with no_network():
        body = client.get("/api/v1/imagery/providers", headers=headers).json()
    assert body["gates"]["missing"] == [
        "GEOPOC_ONLINE_ENABLED",
        "GEOPOC_IMAGERY_ENABLED",
        "GEOPOC_IMAGERY_TERMS_CONFIRMED",
    ]
    (provider,) = body["providers"]
    assert provider["id"] == "cdse" and provider["ready"] is False and provider["credential_configured"] is False
    assert provider["collections"][0]["id"] == "sentinel-2-l2a" and provider["search_needs_credentials"] is None


def test_providers_report_readiness_but_never_a_credential(api):
    api.settings = make_settings(imagery_cdse_access_token=SecretStr(TOKEN))
    response = api.client.get("/api/v1/imagery/providers", headers=api.headers)
    body = response.json()
    assert body["gates"]["missing"] == [] and body["providers"][0]["ready"] is True
    assert (
        body["providers"][0]["credential_configured"] is True and body["providers"][0]["host"] == "stac.example.invalid"
    )
    assert TOKEN not in response.text
    api.settings = make_settings(imagery_cdse_stac_url="http://not-local.invalid/v1")
    body = api.client.get("/api/v1/imagery/providers", headers=api.headers).json()
    assert body["providers"][0]["ready"] is False and body["providers"][0]["configuration_problem"]
    assert api.catalogue.requests == []


# --- the API: gates and confirmation ----------------------------------------------------------------------------------------


def test_nothing_is_sent_while_any_gate_is_closed_and_the_network_is_never_touched(api):
    allow_online(api)
    cases = [
        ({"online_enabled": False}, "GEOPOC_ONLINE_ENABLED"),
        ({"imagery_enabled": False}, "GEOPOC_IMAGERY_ENABLED"),
        ({"imagery_terms_confirmed": False}, "GEOPOC_IMAGERY_TERMS_CONFIRMED"),
        (
            {"online_enabled": False, "imagery_enabled": False, "imagery_terms_confirmed": False},
            "GEOPOC_ONLINE_ENABLED",
        ),
    ]
    with no_network(), mock.patch.object(httpx.Client, "__init__", side_effect=AssertionError("a client was created")):
        for over, expected in cases:
            api.settings = make_settings(**over)
            response = search(api)
            assert response.status_code == 409, response.text
            problem = response.json()
            assert (
                problem["code"] == "IMAGERY_DISABLED"
                and expected in problem["detail"]
                and "nothing was sent" in problem["detail"]
            )
    assert api.catalogue.requests == []


def test_a_project_set_to_local_only_is_refused_even_with_every_gate_open_and_confirmation(api):
    response = search(api)
    assert response.status_code == 409 and response.json()["code"] == "IMAGERY_PROJECT_LOCAL_ONLY"
    assert api.catalogue.requests == []


def test_an_archived_project_is_refused(api):
    allow_online(api)
    assert (
        api.client.patch(
            f"/api/v1/projects/{api.project['id']}", json={"archived": True}, headers=api.headers
        ).status_code
        == 200
    )
    response = search(api)
    assert (
        response.status_code == 409
        and response.json()["code"] == "IMAGERY_PROJECT_ARCHIVED"
        and api.catalogue.requests == []
    )


@pytest.mark.parametrize("flag", [False, None])
def test_the_area_is_not_sent_without_explicit_confirmation(api, flag):
    allow_online(api)
    over = {"confirm_external_transmission": flag} if flag is not None else {}
    body = search_body(**over)
    if flag is None:
        body.pop("confirm_external_transmission")
    response = api.client.post(f"/api/v1/projects/{api.project['id']}/scene-search", json=body, headers=api.headers)
    problem = response.json()
    assert response.status_code == 409 and problem["code"] == "IMAGERY_CONFIRMATION_REQUIRED"
    assert (
        "stac.example.invalid" in problem["detail"]
        and "10.01" in problem["detail"]
        and "nothing was sent" in problem["detail"]
    )
    assert api.catalogue.requests == []


def test_a_dry_run_shows_exactly_what_would_be_sent_and_sends_nothing_even_when_gates_are_closed(api):
    api.settings = make_settings(online_enabled=False)  # closed gates, local-only project, no confirmation
    with no_network(), mock.patch.object(httpx.Client, "__init__", side_effect=AssertionError("a client was created")):
        response = search(api, dry_run=True, confirm_external_transmission=False)
    body = response.json()
    assert (
        response.status_code == 200
        and body["status"] == "dry_run"
        and body["items"] == []
        and body["filtering"] is None
    )
    sent = body["transmission"]
    assert sent["sent"] is False and sent["host"] == "stac.example.invalid" and sent["path"] == "/search"
    assert sent["aoi_sent"]["bbox"] == ROUNDED and sent["aoi_sent"]["exact_polygon_sent"] is False
    assert sent["request_body"]["bbox"] == ROUNDED and sent["credential_header"] is False
    assert not any(n in response.text for n in EXACT_NUMBERS)
    assert body["gates"]["online_enabled"] is False and body["gates"]["missing"] == ["GEOPOC_ONLINE_ENABLED"]
    assert api.catalogue.requests == []


def test_a_dry_run_still_rejects_an_invalid_request(api):
    response = search(api, dry_run=True, start_date="2000-01-01", end_date="2000-02-01")
    assert response.status_code == 422 and response.json()["code"] == "DATES_TOO_EARLY"


# --- the API: searching -------------------------------------------------------------------------------------------------------


def test_a_confirmed_search_returns_filtered_scenes_and_sends_only_a_coarse_box(api):
    allow_online(api)
    response = search(api)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "searched" and body["returned"] == 2 and body["matched"] == 37
    assert [s["cloud_percent"] for s in body["items"]] == [12.5, 45.0]
    first = body["items"][0]
    assert first["acquisition_time"] == "2024-06-12T10:15:59.024000Z" and first["aoi_check"] == "intersects"
    assert (
        first["mgrs_tile"] == "32UNA"
        and first["epsg"] == 32632
        and {a["key"] for a in first["assets"]} == {"B02_10m", "SCL_20m", "thumbnail"}
    )
    assert body["filtering"] == {
        "server_returned": 6,
        "outside_aoi": 1,
        "above_cloud_limit": 1,
        "unknown_cloud": 1,
        "malformed": 1,
        "kept": 2,
    }
    assert body["next_page_token"] and body["transmission"]["sent"] is True and body["retrieved_at"].endswith("Z")
    assert (
        "Copernicus Sentinel data 2024" in body["licence"]["attribution"]
        and "not legal advice" in body["licence"]["status"]
    )
    (request,) = api.catalogue.requests
    assert str(request.url) == f"{BASE}/search" and request.method == "POST" and "authorization" not in request.headers
    sent = api.catalogue.bodies()[0]
    assert sent["bbox"] == ROUNDED and sent["datetime"] == "2024-05-20T00:00:00Z/2024-06-20T23:59:59Z"
    assert (
        sent["query"] == {"eo:cloud_cover": {"lte": 50.0}}
        and sent["limit"] == 20
        and sent["collections"] == ["sentinel-2-l2a"]
    )
    on_the_wire = request.content.decode()
    assert not any(n in on_the_wire for n in EXACT_NUMBERS) and api.project["id"] not in on_the_wire
    assert "s3://" not in response.text and "SECRETSIG" not in response.text and "href" not in response.text


def test_pagination_follows_the_providers_cursor_and_ends(api):
    allow_online(api)
    api.catalogue = Catalogue(httpx.Response(200, json=PAGE1), httpx.Response(200, json=PAGE2))
    first = search(api).json()
    second = search(api, page_token=first["next_page_token"])
    assert second.status_code == 200, second.text
    page = second.json()
    assert [s["scene_id"][:24] for s in page["items"]] == ["S2A_MSIL2A_20240522T1016"] and page[
        "next_page_token"
    ] is None
    assert api.catalogue.bodies()[1]["token"] == "next:ABC-123_x" and "token" not in api.catalogue.bodies()[0]
    # the same cursor on a different search is refused before anything is sent
    before = len(api.catalogue.requests)
    other = search(api, page_token=first["next_page_token"], limit=10)
    assert other.status_code == 422 and other.json()["code"] == "IMAGERY_PAGE_TOKEN_INVALID"
    other = search(api, page_token="not-a-token")
    assert other.status_code == 422 and len(api.catalogue.requests) == before


def test_without_a_cloud_limit_the_filter_is_not_sent(api):
    allow_online(api)
    body = search(api, max_cloud_percent=None).json()
    assert [s["cloud_percent"] for s in body["items"]] == [12.5, 45.0, 85.0, None]
    assert "query" not in api.catalogue.bodies()[0]


@pytest.mark.parametrize(
    ("over", "code"),
    [
        ({"aoi": {"type": "Point", "coordinates": [1, 2]}}, "AOI_INVALID"),
        ({"aoi": {"type": "Polygon", "coordinates": [[[0, 0], [2, 2], [2, 0], [0, 2], [0, 0]]]}}, "AOI_INVALID"),
        ({"aoi": {"type": "Polygon", "coordinates": [[[200, 0], [201, 0], [201, 1], [200, 0]]]}}, "AOI_RANGE"),
        ({"start_date": "2024-06-20", "end_date": "2024-05-20"}, "DATES_INVALID"),
        ({"end_date": "2999-01-01"}, "DATES_FUTURE"),
        ({"start_date": "2020-01-01", "end_date": "2024-01-01"}, "DATES_TOO_LONG"),
        ({"provider": "elsewhere"}, "IMAGERY_UNKNOWN_PROVIDER"),
        ({"collection": "sentinel-1-grd"}, "IMAGERY_UNSUPPORTED_COLLECTION"),
    ],
)
def test_an_invalid_request_is_a_422_before_any_gate_and_sends_nothing(api, over, code):
    api.settings = make_settings(online_enabled=False)  # gates closed: validation still comes first
    response = search(api, **over)
    assert response.status_code == 422 and response.json()["code"] == code, response.text
    assert api.catalogue.requests == []


@pytest.mark.parametrize(
    "over",
    [
        {"max_cloud_percent": 150},
        {"max_cloud_percent": -1},
        {"limit": 0},
        {"limit": 101},
        {"start_date": "soon"},
        {"unknown": 1},
        {"provider": "Bad Name"},
    ],
)
def test_field_level_validation_is_a_422(api, over):
    allow_online(api)
    response = search(api, **over)
    assert (
        response.status_code == 422 and response.json()["code"] == "VALIDATION_ERROR" and api.catalogue.requests == []
    )


def test_a_search_over_an_unknown_project_or_without_a_profile_is_refused(api):
    unknown = api.client.post(
        "/api/v1/projects/00000000-0000-4000-8000-000000000009/scene-search", json=search_body(), headers=api.headers
    )
    assert unknown.status_code == 404 and unknown.json()["code"] == "PROJECT_NOT_FOUND"
    no_profile = api.client.post(f"/api/v1/projects/{api.project['id']}/scene-search", json=search_body())
    assert no_profile.status_code == 401 and no_profile.json()["code"] == "PROFILE_REQUIRED"
    assert api.catalogue.requests == []


@pytest.mark.parametrize(
    ("answer", "code", "status"),
    [
        (httpx.ReadTimeout("slow"), "IMAGERY_UPSTREAM_TIMEOUT", 504),
        (httpx.ConnectError("refused"), "IMAGERY_UPSTREAM_UNREACHABLE", 502),
        (httpx.Response(429, headers={"Retry-After": "12"}, json={}), "IMAGERY_UPSTREAM_RATE_LIMITED", 429),
        (httpx.Response(403, json={}), "IMAGERY_UPSTREAM_REJECTED", 502),
        (httpx.Response(400, json={"description": "bad"}), "IMAGERY_UPSTREAM_BAD_REQUEST", 502),
        (httpx.Response(500, text="boom"), "IMAGERY_UPSTREAM_ERROR", 502),
        (httpx.Response(200, text="not json"), "IMAGERY_BAD_RESPONSE", 502),
    ],
)
def test_provider_failures_are_documented_problem_answers_not_server_errors(api, answer, code, status):
    allow_online(api)
    api.catalogue = Catalogue(answer)
    response = search(api)
    assert response.status_code == status and response.headers["content-type"].startswith("application/problem+json")
    problem = response.json()
    assert problem["code"] == code and problem["status"] == status and problem["request_id"]


def test_the_credential_is_sent_to_the_catalogue_and_nowhere_else(api, caplog):
    allow_online(api)
    api.settings = make_settings(imagery_cdse_access_token=SecretStr(TOKEN))
    api.catalogue = Catalogue(httpx.Response(200, json=PAGE2))
    caplog.set_level(logging.DEBUG)
    response = search(api, max_cloud_percent=None)
    assert response.status_code == 200 and response.json()["transmission"]["credential_header"] is True
    assert api.catalogue.requests[0].headers["authorization"] == f"Bearer {TOKEN}"
    assert TOKEN not in response.text and TOKEN not in caplog.text and TOKEN not in repr(api.settings)
    assert TOKEN not in api.client.get("/openapi.json").text


def test_a_provider_that_echoes_the_credential_cannot_leak_it(api, caplog):
    allow_online(api)
    api.settings = make_settings(imagery_cdse_access_token=SecretStr(TOKEN))
    echo = lambda request: httpx.Response(400, json={"description": f"bad header {request.headers['authorization']}"})  # noqa: E731
    api.catalogue = Catalogue(echo)
    caplog.set_level(logging.DEBUG)
    response = search(api)
    assert response.status_code == 502 and TOKEN not in response.text and TOKEN not in caplog.text
    assert "[removed]" in response.json()["detail"]


def test_the_log_records_that_a_search_happened_without_coordinates_or_credentials(api, caplog):
    allow_online(api)
    api.settings = make_settings(imagery_cdse_access_token=SecretStr(TOKEN))
    caplog.set_level(logging.INFO, logger="geopoc.imagery")
    assert search(api).status_code == 200
    lines = [r.getMessage() for r in caplog.records if r.name == "geopoc.imagery"]
    assert len(lines) == 1 and "provider=cdse" in lines[0] and api.project["id"] in lines[0]
    assert not any(n in lines[0] for n in (*EXACT_NUMBERS, "10.01", "50.05", TOKEN))


# --- nothing is written ------------------------------------------------------------------------------------------------------


def test_searching_changes_no_database_row_and_writes_no_file(api):
    data_root = get_settings().data_root

    allow_online(api)
    before_db = db_state()
    before_files = sorted(str(p) for p in data_root.rglob("*"))
    api.catalogue = Catalogue(httpx.Response(200, json=PAGE1), httpx.Response(200, json=PAGE2))
    first = search(api)
    assert first.status_code == 200 and search(api, page_token=first.json()["next_page_token"]).status_code == 200
    assert search(api, dry_run=True).status_code == 200
    assert search(api, aoi={"type": "Point"}).status_code == 422
    api.catalogue = Catalogue(httpx.ReadTimeout("slow"))
    assert search(api).status_code == 504
    assert db_state() == before_db
    assert sorted(str(p) for p in data_root.rglob("*")) == before_files


def test_the_discovery_code_has_no_way_to_write_and_does_not_touch_the_prototype_packages():
    root = Path(__file__).resolve().parents[1] / "geopoc"
    files = [*(root / "imagery").glob("*.py"), root / "api" / "routers" / "imagery.py"]
    assert len(files) >= 9
    for path in files:
        source = path.read_text(encoding="utf-8")
        for forbidden in (
            "session.add",
            ".commit(",
            ".flush(",
            "record_audit",
            "INSERT",
            "UPDATE ",
            "DELETE ",
            "write_text",
            "write_bytes",
        ):
            assert forbidden not in source, f"{path.name} contains {forbidden!r}"
        assert not re.search(r"(?<![\w.])open\(", source), f"{path.name} opens a file"
        imported = re.findall(r"^\s*(?:from|import)\s+(geopoc\.[\w.]+)", source, re.MULTILINE)
        for module in imported:
            assert not module.startswith(("geopoc.satellite", "geopoc.earth_engine", "geopoc.geo")), (path.name, module)
