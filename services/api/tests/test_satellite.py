"""The satellite imagery feasibility prototype, offline.

Everything here runs with no network, no Earth Engine account and no `earthengine-api` package. The Earth Engine provider is
exercised only with a stand-in `ee` module, which checks what our code asks for; it proves nothing about the live service.
The ingestion test uses the scratch database geopoc_test and a scratch folder under pytest's tmp_path.
"""

import hashlib
import importlib
import json
import socket
import sys
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest
import rasterio

from geopoc.config import REPO_ROOT, Settings
from geopoc.earth_engine.errors import EarthEngineRefused
from geopoc.satellite import __main__ as cli
from geopoc.satellite.earthengine import EarthEngineProvider
from geopoc.satellite.export import ExportRefused, ScratchDir, run_export
from geopoc.satellite.gate import EarthEngineDisabled, ProviderError, ProviderUnavailable, ensure_enabled, redact
from geopoc.satellite.mock import MockProvider
from geopoc.satellite.models import (
    COLLECTION_ID,
    AreaOfInterest,
    DateRange,
    ExportLimits,
    RequestRefused,
    SceneInfo,
    SceneQuery,
    plan_export,
    utm_crs_for,
)
from tests import test_assets  # noqa: F401  (upload helper used below)
from tests.test_assets import upload

TODAY = date(2026, 10, 29)
AOI = AreaOfInterest.from_bbox(75.00, 27.00, 75.05, 27.04)  # about 5 km x 4.4 km, UTM zone 43 N
SCENE_ID = f"{COLLECTION_ID}/20260106T053000_MOCK_TABCDE"
SECRET_KEY = (
    "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQC7\n-----END PRIVATE KEY-----\n"
)
SECRET_TOKEN = "ya29.a0AfH6SMBsecretsecretsecret-abc_123"
SIGNED_URL = "https://earthengine.googleapis.com/v1/projects/p/thumbnails/0123456789abcdef:getPixels?token=SECRET"


@contextmanager
def no_network():
    """Any attempt to open a connection fails the test."""

    def refuse(*_a, **_k):
        raise AssertionError("a network connection was attempted")

    with mock.patch.object(socket.socket, "connect", refuse), mock.patch.object(socket, "getaddrinfo", refuse):
        yield


def scene(when="2026-01-06T05:30:00+00:00", cloud=12.5, tile="TABCDE") -> SceneInfo:
    return SceneInfo(SCENE_ID, COLLECTION_ID, datetime.fromisoformat(when), cloud, tile, "05.11")


def settings(**over) -> Settings:
    return Settings(_env_file=None, **over)  # type: ignore[call-arg]


def exported(tmp_path, **kwargs):
    plan = plan_export(SCENE_ID, AOI, **kwargs)
    return plan, run_export(MockProvider(), plan, scene(), ScratchDir.at(tmp_path / "_satellite_t"))


# --- off by default ----------------------------------------------------------------------------------------------------


def test_earth_engine_is_off_by_default_and_the_global_online_default_is_unchanged():
    defaults = settings()
    assert defaults.earth_engine_enabled is False and defaults.online_enabled is False
    assert defaults.earth_engine_project is None and defaults.earth_engine_credentials_file is None
    example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    assert "GEOPOC_ONLINE_ENABLED=false" in example
    active = [line for line in example.splitlines() if line.startswith("GEOPOC_EARTH_ENGINE")]
    assert active == []  # every Earth Engine line in the example is a comment


def test_nothing_is_imported_or_started_by_importing_the_package():
    assert "ee" not in sys.modules
    for name in ("geopoc.satellite", "geopoc.satellite.earthengine", "geopoc.satellite.mock"):
        importlib.import_module(name)
    assert "ee" not in sys.modules and "google.auth" not in sys.modules


@pytest.mark.parametrize(
    ("over", "mentions"),
    [
        ({}, ["GEOPOC_ONLINE_ENABLED", "GEOPOC_EARTH_ENGINE_ENABLED"]),
        ({"online_enabled": True}, ["GEOPOC_EARTH_ENGINE_ENABLED"]),
        ({"earth_engine_enabled": True}, ["GEOPOC_ONLINE_ENABLED"]),
    ],
)
def test_the_real_provider_is_refused_unless_both_switches_are_on_and_nothing_is_imported(over, mentions):
    spy = mock.Mock(side_effect=AssertionError("earth engine was imported"))
    with mock.patch("importlib.import_module", spy), pytest.raises(EarthEngineDisabled) as caught:
        EarthEngineProvider.from_settings(settings(earth_engine_project="p", **over))
    for name in mentions:
        assert name in str(caught.value)
    spy.assert_not_called()
    with pytest.raises(EarthEngineDisabled):
        ensure_enabled(settings(**over))


def test_both_switches_still_need_a_project_and_since_gee_1_a_control_folder_before_anything_is_imported():
    on = {"online_enabled": True, "earth_engine_enabled": True}
    with pytest.raises(ProviderUnavailable, match="GEOPOC_EARTH_ENGINE_PROJECT"):
        EarthEngineProvider.from_settings(settings(**on))
    spy = mock.Mock(side_effect=AssertionError("earth engine was imported"))
    with mock.patch("importlib.import_module", spy), pytest.raises(EarthEngineRefused) as caught:
        EarthEngineProvider.from_settings(settings(earth_engine_project="p", **on))  # no control folder is configured
    assert caught.value.code == "EE_NOT_CONFIGURED"
    spy.assert_not_called()
    assert "ee" not in sys.modules  # the package is installed since GEE-1, but this route never imports it


# --- requests and limits -----------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("build", "code"),
    [
        (lambda: AreaOfInterest.from_bbox(75, 27, 75, 27.1), "AOI_INVALID"),
        (lambda: AreaOfInterest.from_bbox(75, 27, float("nan"), 27.1), "AOI_INVALID"),
        (lambda: AreaOfInterest.from_bbox(170, 27, 190, 28), "AOI_RANGE"),
        (lambda: AreaOfInterest.from_bbox(75, 27, 76, 95), "AOI_RANGE"),
        (lambda: AreaOfInterest.from_bbox(-179, 27, 179, 28), "AOI_ANTIMERIDIAN"),
        (lambda: AreaOfInterest.from_geojson({"type": "Point", "coordinates": [75, 27]}), "AOI_INVALID"),
        (
            lambda: AreaOfInterest.from_geojson(
                {"type": "Polygon", "coordinates": [[[75, 27], [75.1, 27.1], [75.1, 27], [75, 27.1], [75, 27]]]}
            ),
            "AOI_INVALID",
        ),
        (
            lambda: AreaOfInterest.from_geojson(
                {"type": "Polygon", "coordinates": [[["a", 27], [75.1, 27], [75.1, 27.1], [75, 27]]]}
            ),
            "AOI_INVALID",
        ),
    ],
)
def test_areas_of_interest_are_validated(build, code):
    with pytest.raises(RequestRefused) as caught:
        build()
    assert caught.value.code == code


def test_a_geojson_polygon_is_accepted_and_the_region_is_its_bounding_box():
    aoi = AreaOfInterest.from_geojson(
        {"type": "Polygon", "coordinates": [[[75, 27], [75.04, 27], [75.02, 27.03], [75, 27]]]}
    )
    assert aoi.bounds == (75.0, 27.0, 75.04, 27.03)
    assert (
        aoi.bbox_area_km2 > aoi.area_km2 > 0
    )  # the export region is bigger than the triangle, and the plan counts the box


@pytest.mark.parametrize(
    ("start", "end", "code"),
    [
        (date(2026, 2, 1), date(2026, 1, 1), "DATES_INVALID"),
        (date(2026, 10, 1), date(2026, 11, 30), "DATES_FUTURE"),
        (date(2016, 1, 1), date(2016, 2, 1), "DATES_TOO_EARLY"),
        (date(2025, 1, 1), date(2026, 6, 1), "DATES_TOO_LONG"),
    ],
)
def test_date_ranges_are_validated(start, end, code):
    with pytest.raises(RequestRefused) as caught:
        SceneQuery(AOI, DateRange(start, end)).check(today=TODAY)
    assert caught.value.code == code


def test_query_limits():
    ok = DateRange(date(2026, 1, 1), date(2026, 1, 31))
    with pytest.raises(RequestRefused, match="between 0 and 100"):
        SceneQuery(AOI, ok, max_cloud_percent=120).check(today=TODAY)
    with pytest.raises(RequestRefused, match="between 1 and 100"):
        SceneQuery(AOI, ok, limit=500).check(today=TODAY)
    with pytest.raises(RequestRefused) as caught:
        SceneQuery(AreaOfInterest.from_bbox(70, 20, 80, 30), ok).check(today=TODAY)
    assert caught.value.code == "AOI_TOO_LARGE"
    assert DateRange(date(2026, 1, 1), date(2026, 1, 31)).end_exclusive == date(
        2026, 2, 1
    )  # Earth Engine's end is exclusive


def test_a_plan_states_the_grid_size_and_the_crs_before_anything_is_requested():
    plan = plan_export(SCENE_ID, AOI)
    assert plan.crs == "EPSG:32643" and plan.scale_m == 10 and plan.bands == ("B4", "B3", "B2")
    assert 480 <= plan.width_px <= 520 and 430 <= plan.height_px <= 460
    assert plan.estimated_bytes == plan.pixels * 3 * 2
    assert plan.as_dict()["region_bbox_epsg4326"]["type"] == "Polygon"
    assert utm_crs_for(75, 27) == "EPSG:32643" and utm_crs_for(-58.4, -34.6) == "EPSG:32721"


@pytest.mark.parametrize(
    ("aoi", "kwargs", "code"),
    [
        (AreaOfInterest.from_bbox(75, 27, 75.2, 27.2), {}, "AOI_TOO_LARGE"),  # about 440 km2
        (AreaOfInterest.from_bbox(75, 27, 75.4, 27.01), {}, "TOO_WIDE"),  # long and thin: small area, 3900 px wide
        (AOI, {"bands": ("B4", "B3", "B2", "B8", "B11")}, "TOO_MANY_BANDS"),
        (AOI, {"bands": ("B4", "B4")}, "BANDS_INVALID"),
        (AOI, {"bands": ()}, "BANDS_INVALID"),
        (AOI, {"bands": ("B99",)}, "BANDS_INVALID"),
        (AOI, {"scale_m": 15}, "SCALE_INVALID"),
        (AOI, {"bands": ("B5",), "scale_m": 10}, "SCALE_FINER_THAN_BAND"),
        (AOI, {"limits": ExportLimits(max_pixels=1000)}, "TOO_MANY_PIXELS"),
        (AOI, {"limits": ExportLimits(max_bytes=1024)}, "TOO_LARGE"),
    ],
)
def test_every_export_limit_refuses_with_a_specific_code(aoi, kwargs, code):
    with pytest.raises(RequestRefused) as caught:
        plan_export(SCENE_ID, aoi, **kwargs)
    assert caught.value.code == code


def test_a_scene_id_must_belong_to_the_collection_and_limits_cannot_exceed_earth_engines_own_caps():
    for bad in ("", "LANDSAT/LC08/C02/T1_L2/LC08_1", f"{COLLECTION_ID}"):
        with pytest.raises(RequestRefused) as caught:
            plan_export(bad, AOI)
        assert caught.value.code == "SCENE_INVALID"
    with pytest.raises(ValueError, match="32 MB"):
        ExportLimits(max_bytes=64 * 1024**2)
    with pytest.raises(ValueError):
        ExportLimits(max_side_px=20_000)
    assert plan_export(f"{COLLECTION_ID}/x", AOI, bands=("B5", "B6"), scale_m=20).bands == ("B5", "B6")


# --- the mock provider -------------------------------------------------------------------------------------------------


def test_the_mock_lists_dated_scenes_with_cloud_metadata_deterministically_and_filters_by_cloud():
    query = SceneQuery(AOI, DateRange(date(2026, 1, 1), date(2026, 3, 1)))
    with no_network():
        first, second = MockProvider().search(query), MockProvider().search(query)
    assert first == second and len(first) == 12  # every five days, 1 Jan to 25 Feb
    assert all(s.collection == COLLECTION_ID and s.acquisition_time.tzinfo is not None for s in first)
    assert all(0 <= s.cloud_percent <= 100 for s in first)
    assert [s.acquisition_time for s in first] == sorted(s.acquisition_time for s in first)
    clear = MockProvider().search(SceneQuery(AOI, query.dates, max_cloud_percent=30))
    assert 0 < len(clear) < len(first) and all(s.cloud_percent <= 30 for s in clear)
    assert len(MockProvider().search(SceneQuery(AOI, query.dates, limit=3))) == 3


# --- the export, its provenance and its safety --------------------------------------------------------------------------


def test_a_mock_export_is_a_georeferenced_geotiff_of_the_planned_size_with_a_full_provenance_record(tmp_path):
    with no_network():
        plan, result = exported(tmp_path)
    with rasterio.open(result.tif_path) as src:
        assert src.crs.to_string() == "EPSG:32643" and (src.width, src.height) == (plan.width_px, plan.height_px)
        assert (abs(src.transform.a), abs(src.transform.e)) == (10, 10) and src.count == 3 and src.dtypes[0] == "uint16"
        assert list(src.descriptions) == ["B4", "B3", "B2"]
    m = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert m == result.manifest
    assert m["synthetic"] is True and m["provider"]["name"] == "mock"
    assert m["source"]["collection"] == COLLECTION_ID and m["source"]["scene_id"] == SCENE_ID
    assert m["source"]["acquisition_time"] == "2026-01-06T05:30:00Z" and m["source"]["cloud_percent"] == 12.5
    assert m["file"]["sha256"] == hashlib.sha256(result.tif_path.read_bytes()).hexdigest()
    assert m["file"]["crs"] == "EPSG:32643" and m["file"]["pixel_size_x"] == 10 and m["file"]["band_count"] == 3
    assert m["request"]["scale_m"] == 10 and m["request"]["bands"] == ["B4", "B3", "B2"]
    assert m["request"]["area_of_interest_epsg4326"]["type"] == "Polygon"
    assert m["processing"]["cloud_masking"] == "none" and "no compositing" in m["processing"]["scene_selection"]
    assert "digital numbers" in m["processing"]["scale_factor"]
    # synthetic pixels are never credited to Copernicus
    assert not any("Copernicus Sentinel data 2026" in line for line in m["attribution"])
    assert any("SYNTHETIC" in line for line in m["attribution"]) and m["licence"]["status"] == "synthetic"
    notes = " ".join(m["notes"])
    assert "not a basemap tile" in notes and "SYNTHETIC" in notes
    assert sorted(p.name for p in result.tif_path.parent.iterdir()) == sorted(
        [result.tif_path.name, result.manifest_path.name]
    )


def test_the_same_request_gives_the_same_bytes(tmp_path):
    _, a = exported(tmp_path / "one")
    _, b = exported(tmp_path / "two")
    assert a.manifest["file"]["sha256"] == b.manifest["file"]["sha256"]


def test_an_export_never_overwrites_and_leaves_nothing_behind_when_it_is_refused(tmp_path):
    plan, first = exported(tmp_path)
    before = first.tif_path.read_bytes(), first.manifest_path.read_bytes()
    with pytest.raises(ExportRefused) as caught:
        run_export(MockProvider(), plan, scene(), ScratchDir.at(tmp_path / "_satellite_t"))
    assert caught.value.code == "DESTINATION_EXISTS"
    assert (first.tif_path.read_bytes(), first.manifest_path.read_bytes()) == before
    assert not list(first.tif_path.parent.glob("*.part"))


class Broken(MockProvider):
    """A provider whose file is wrong in one way, to prove the delivered file is checked and nothing is kept."""

    def __init__(self, fault: str) -> None:
        self.fault = fault

    def export_geotiff(self, plan, scene_, destination):
        if self.fault == "not_tiff":
            destination.write_bytes(b"PK\x03\x04 a zip, not a tiff")
        elif self.fault == "oversize":
            destination.write_bytes(b"II*\x00" + b"0" * (plan.limits.max_bytes + 1))
        else:
            from dataclasses import replace

            bent = {
                "grid": replace(plan, width_px=plan.width_px * 2),
                "bands": replace(plan, bands=plan.bands[:1]),
                "crs": replace(plan, crs="EPSG:32644"),
            }[self.fault]
            return super().export_geotiff(bent, scene_, destination)
        return {}


@pytest.mark.parametrize(
    ("fault", "code"),
    [
        ("not_tiff", "NOT_A_GEOTIFF"),
        ("oversize", "FILE_TOO_LARGE"),
        ("grid", "GRID_MISMATCH"),
        ("bands", "BANDS_MISMATCH"),
        ("crs", "CRS_MISMATCH"),
    ],
)
def test_a_delivered_file_that_is_not_what_was_asked_for_is_refused_and_not_kept(tmp_path, fault, code):
    scratch = ScratchDir.at(tmp_path / "_satellite_t")
    with pytest.raises(ExportRefused) as caught:
        run_export(Broken(fault), plan_export(SCENE_ID, AOI), scene(), scratch)
    assert caught.value.code == code
    assert list(scratch.path.iterdir()) == []


def test_the_export_folder_must_be_a_dedicated_scratch_folder(tmp_path):
    for bad in (tmp_path / "exports", tmp_path / "_pytest", tmp_path / "originals" / "_satellite_x"):
        with pytest.raises(ExportRefused) as caught:
            ScratchDir.at(bad)
        assert caught.value.code == "NOT_SCRATCH"
    assert not (tmp_path / "exports").exists()
    assert ScratchDir.at(tmp_path / "_satellite_ok").path.is_dir()


def test_a_plan_for_one_scene_cannot_export_another_and_other_collections_are_refused(tmp_path):
    scratch = ScratchDir.at(tmp_path / "_satellite_t")
    other = SceneInfo(SCENE_ID + "X", COLLECTION_ID, scene().acquisition_time, None)
    with pytest.raises(ExportRefused) as caught:
        run_export(MockProvider(), plan_export(SCENE_ID, AOI), other, scratch)
    assert caught.value.code == "SCENE_MISMATCH"
    landsat = SceneInfo(SCENE_ID, "LANDSAT/LC08/C02/T1_L2", scene().acquisition_time, None)
    with pytest.raises(ExportRefused) as caught:
        run_export(MockProvider(), plan_export(SCENE_ID, AOI), landsat, scratch)
    assert caught.value.code == "COLLECTION_MISMATCH"


# --- the Earth Engine provider, against a stand-in `ee` ------------------------------------------------------------------


class FakeEE:
    """Records what our code asks Earth Engine for. It is not Earth Engine."""

    def __init__(self, features=None, url=SIGNED_URL, fail_with: Exception | None = None):
        self.calls: list[tuple] = []
        self.features, self.url, self.fail_with = features if features is not None else [], url, fail_with
        outer = self

        class Collection:
            def __init__(self, id_):
                outer.calls.append(("ImageCollection", id_))

            def filterBounds(self, geometry):
                outer.calls.append(("filterBounds", geometry))
                return self

            def filterDate(self, start, end):
                outer.calls.append(("filterDate", start, end))
                return self

            def filter(self, condition):
                outer.calls.append(("filter", condition))
                return self

            def sort(self, key):
                outer.calls.append(("sort", key))
                return self

            def limit(self, n):
                outer.calls.append(("limit", n))
                return self

            def getInfo(self):
                if outer.fail_with:
                    raise outer.fail_with
                return {"features": outer.features}

        class Image:
            def __init__(self, id_):
                outer.calls.append(("Image", id_))

            def select(self, bands):
                outer.calls.append(("select", bands))
                return self

            def getDownloadURL(self, params):
                outer.calls.append(("getDownloadURL", params))
                if outer.fail_with:
                    raise outer.fail_with
                return outer.url

        self.ImageCollection, self.Image = Collection, Image
        self.Geometry = lambda geojson: ("geometry", geojson)
        self.Filter = SimpleNamespace(lte=lambda name, value: ("lte", name, value))

    def Initialize(self, credentials=None, project=None):  # noqa: N802
        self.calls.append(("Initialize", credentials, project))

    def ServiceAccountCredentials(self, email, key_file):  # noqa: N802
        self.calls.append(("ServiceAccountCredentials", email, key_file))
        return ("credentials-for", email)


FEATURES = [
    {
        "id": f"{COLLECTION_ID}/20260106T053000_20260106T053100_T43RFM",
        "properties": {
            "system:time_start": 1767677400000,
            "CLOUDY_PIXEL_PERCENTAGE": 3.25,
            "MGRS_TILE": "43RFM",
            "PROCESSING_BASELINE": "05.11",
        },
    },
    {
        "id": f"{COLLECTION_ID}/20260111T053000_x",
        "properties": {"system:time_start": 1768109400000, "MGRS_TILE": "43RFM"},
    },  # no cloud figure
    {"id": f"{COLLECTION_ID}/nodate", "properties": {"CLOUDY_PIXEL_PERCENTAGE": 1}},  # no time: cannot be recorded
]


def provider(ee, downloader=None):
    return EarthEngineProvider(ee, project="my-project", downloader=downloader or (lambda *_: None))


def test_a_search_asks_for_the_right_collection_area_dates_and_cloud_filter_and_reads_the_metadata():
    ee = FakeEE(FEATURES)
    query = SceneQuery(AOI, DateRange(date(2026, 1, 1), date(2026, 1, 31)), max_cloud_percent=20, limit=10)
    found = provider(ee).search(query)
    assert ("ImageCollection", "COPERNICUS/S2_SR_HARMONIZED") in ee.calls
    assert ("filterDate", "2026-01-01", "2026-02-01") in ee.calls  # inclusive end becomes Earth Engine's exclusive end
    assert ("filter", ("lte", "CLOUDY_PIXEL_PERCENTAGE", 20)) in ee.calls and ("limit", 10) in ee.calls
    bounds = next(c for c in ee.calls if c[0] == "filterBounds")[1][1]
    assert bounds["type"] == "Polygon"
    assert [s.cloud_percent for s in found] == [
        3.25,
        None,
    ]  # a missing figure stays missing; the undated scene is left out
    assert found[0].acquisition_time == datetime(2026, 1, 6, 5, 30, tzinfo=UTC) and found[0].tile == "43RFM"
    assert found[0].processing_baseline == "05.11" and found[1].processing_baseline is None


def test_an_export_requests_exactly_the_planned_bands_region_scale_and_crs_and_records_it(tmp_path):
    ee = FakeEE()
    plan = plan_export(SCENE_ID, AOI)
    downloaded = {}

    def deliver(url, destination, max_bytes):
        downloaded.update(url=url, max_bytes=max_bytes)
        MockProvider().export_geotiff(plan, scene(), destination)  # stands in for the bytes Google would send

    result = run_export(provider(ee, deliver), plan, scene(), ScratchDir.at(tmp_path / "_satellite_t"))
    params = next(c for c in ee.calls if c[0] == "getDownloadURL")[1]
    assert params == {
        "bands": ["B4", "B3", "B2"],
        "region": plan.aoi.bbox_geojson(),
        "scale": 10,
        "crs": "EPSG:32643",
        "format": "GEO_TIFF",
    }
    assert ("select", ["B4", "B3", "B2"]) in ee.calls
    assert downloaded == {"url": SIGNED_URL, "max_bytes": plan.limits.max_bytes}
    m = result.manifest
    assert m["synthetic"] is False and m["provider"] == {
        "name": "earthengine",
        "endpoint": "earthengine.googleapis.com",
        "cloud_project": "my-project",
    }
    assert "Contains modified Copernicus Sentinel data 2026." in m["attribution"]
    assert (
        m["licence"]["attribution"].startswith("Contains modified Copernicus")
        and "not legal advice" in m["licence"]["status"]
    )
    assert m["request"]["provider_request"]["method"] == "ee.Image.getDownloadURL"
    # the signed link is a credential: it is used once and is in no record
    assert "SECRET" not in json.dumps(m) and "getPixels" not in json.dumps(m)


def test_a_zip_answer_is_refused_and_nothing_is_kept(tmp_path):
    scratch = ScratchDir.at(tmp_path / "_satellite_t")
    zip_bytes = lambda _url, dest, _max: dest.write_bytes(b"PK\x03\x04zip")  # noqa: E731
    with pytest.raises(ProviderError, match="other than a GeoTIFF"):
        run_export(provider(FakeEE(), zip_bytes), plan_export(SCENE_ID, AOI), scene(), scratch)
    assert list(scratch.path.iterdir()) == []


def test_errors_from_the_library_are_redacted_so_no_token_key_or_signed_url_reaches_a_message_or_a_log(
    tmp_path, caplog
):
    leak = Exception(f"403 Bearer {SECRET_TOKEN} for {SIGNED_URL}\n{SECRET_KEY}")
    caplog.set_level("DEBUG")
    with pytest.raises(ProviderError) as search_error:
        provider(FakeEE(fail_with=leak)).search(SceneQuery(AOI, DateRange(date(2026, 1, 1), date(2026, 1, 31))))
    with pytest.raises(ProviderError) as export_error:
        run_export(
            provider(FakeEE(fail_with=leak)),
            plan_export(SCENE_ID, AOI),
            scene(),
            ScratchDir.at(tmp_path / "_satellite_t"),
        )
    for text in (str(search_error.value), str(export_error.value), caplog.text):
        for secret in (SECRET_TOKEN, "SECRET", "MIIEvQ", "BEGIN PRIVATE KEY", "0123456789abcdef"):
            assert secret not in text
    assert "Exception" in str(search_error.value)  # still says what kind of failure it was


def test_settings_hold_only_the_path_of_a_key_file_never_its_contents(tmp_path):
    # Since GEE-1 authentication lives in geopoc.earth_engine.client (organisation-owned identities; tests in
    # tests/test_earth_engine.py). The prototype's own key-file and initialisation code was removed with the route.
    key = tmp_path / "key.json"
    key.write_text(json.dumps({"client_email": "svc@proj.iam.gserviceaccount.com", "private_key": SECRET_KEY}))
    assert "private_key" not in repr(settings(earth_engine_credentials_file=key))


@pytest.mark.parametrize(
    "text",
    [
        SECRET_TOKEN,
        f"Authorization: Bearer {SECRET_TOKEN}",
        '{"private_key": "-----BEGIN PRIVATE KEY-----\\nabc\\n-----END PRIVATE KEY-----\\n", "client_email": "a@b"}',
        SECRET_KEY,
        "refresh_token=1//0gAbCdEfGhIjKlMnOpQrStUvWxYz",
        '{"access_token": "abcdef123456"}',
        "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.c2lnbmF0dXJl",
        f"fetching {SIGNED_URL} failed",
    ],
)
def test_redaction_removes_every_shape_of_credential(text):
    cleaned = redact(text)
    for fragment in (
        "secretsecret",
        "BEGIN PRIVATE",
        "abcdef123456",
        "0gAbCdEf",
        "c2lnbmF0dXJl",
        "SECRET",
        "abc\\n",
        "0123456789abcdef",
    ):
        assert fragment not in cleaned
    assert redact("an ordinary message") == "an ordinary message"


# --- the whole offline path never touches the network --------------------------------------------------------------------


def test_the_complete_mock_flow_runs_with_the_network_blocked_and_without_earth_engine(tmp_path):
    with no_network():
        found = MockProvider().search(SceneQuery(AOI, DateRange(date(2026, 1, 1), date(2026, 1, 31))))
        plan = plan_export(found[0].scene_id, AOI)
        result = run_export(MockProvider(), plan, found[0], ScratchDir.at(tmp_path / "_satellite_t"))
    assert result.tif_path.exists() and "ee" not in sys.modules


# --- command line -----------------------------------------------------------------------------------------------------------


BBOX = ["--bbox", "75.0", "27.0", "75.05", "27.04"]


def test_the_command_line_plans_searches_and_exports_offline_with_the_mock(tmp_path, capsys):
    assert cli.main(["plan", *BBOX, "--scene", SCENE_ID]) == 0
    assert json.loads(capsys.readouterr().out)["crs"] == "EPSG:32643"
    assert cli.main(["search", *BBOX, "--start", "2026-01-01", "--end", "2026-01-31"]) == 0
    listing = json.loads(capsys.readouterr().out)
    assert listing and listing[0]["cloud_percent"] is not None
    args = [
        "export",
        *BBOX,
        "--scene",
        listing[0]["scene_id"],
        "--acquired",
        listing[0]["acquisition_time"],
        "--scratch",
        str(tmp_path / "_satellite_cli"),
    ]
    assert cli.main(args) == 0
    out = json.loads(capsys.readouterr().out)
    assert Path(out["tif"]).exists() and Path(out["manifest"]).exists()
    assert cli.main(args) == 1  # a second run refuses rather than overwriting
    assert "DESTINATION_EXISTS" in capsys.readouterr().err


def test_the_command_line_refuses_earth_engine_unless_switched_on_and_a_bad_folder_or_time(tmp_path, capsys):
    with mock.patch("geopoc.satellite.__main__.get_settings", lambda: settings()):
        assert (
            cli.main(["search", *BBOX, "--start", "2026-01-01", "--end", "2026-01-31", "--provider", "earthengine"])
            == 1
        )
    assert "EarthEngineDisabled" in capsys.readouterr().err and "ee" not in sys.modules
    assert (
        cli.main(
            [
                "export",
                *BBOX,
                "--scene",
                SCENE_ID,
                "--acquired",
                "2026-01-06T05:30:00Z",
                "--scratch",
                str(tmp_path / "plain"),
            ]
        )
        == 1
    )
    assert "NOT_SCRATCH" in capsys.readouterr().err and not (tmp_path / "plain").exists()
    assert (
        cli.main(
            [
                "export",
                *BBOX,
                "--scene",
                SCENE_ID,
                "--acquired",
                "2026-01-06T05:30:00",
                "--scratch",
                str(tmp_path / "_satellite_z"),
            ]
        )
        == 1
    )
    assert "ACQUIRED_NO_ZONE" in capsys.readouterr().err
    assert (
        cli.main(["plan", "--bbox", "75", "27", "75.5", "27.5", "--scene", SCENE_ID]) == 1
    )  # 2,900 km2 is over the limit


# --- ingesting an export into a scratch project -----------------------------------------------------------------------------


def test_an_export_is_ingested_into_a_scratch_project_unchanged_and_is_placed_on_the_map(
    client, project, headers, tmp_path
):
    from geopoc.config import get_settings

    assert get_settings().db_name == "geopoc_test"  # a scratch database, never the working one
    _, result = exported(tmp_path)
    original = result.tif_path.read_bytes()
    response = upload(client, project, headers, result.tif_path.name, original, modality="overhead_raster")
    assert response.status_code == 201, response.text
    asset = response.json()["asset"]
    assert asset["modality"] == "overhead_raster" and asset["project_id"] == project["id"]
    assert asset["raster"]["crs_status"] == "valid" and asset["raster"]["georeferenced"] is True
    assert asset["raster"]["crs_identifier"] == "EPSG:32643" and asset["raster"]["band_count"] == 3
    assert asset["sha256"] == result.manifest["file"]["sha256"]  # the record matches the stored original
    assert result.tif_path.read_bytes() == original  # the export file itself was not touched
    layers = client.get(f"/api/v1/projects/{project['id']}/layers").json()
    assert [layer["asset_id"] for layer in layers["rasters"]] == [asset["id"]]


def test_no_migration_was_added_for_the_prototype():
    versions = sorted(p.name for p in (REPO_ROOT / "services" / "api" / "migrations" / "versions").glob("0*.py"))
    # Nothing was added for the prototype. The only migration after 0009 is the one approved for derived-index jobs
    # (0010); any other new migration must be named here on purpose. (This used to require 0009 to be the newest.)
    after_0009 = [v for v in versions if v[:4] > "0009"]
    assert after_0009 == ["0010_derived_index_job_kind.py"]
    assert not any("satellite" in v or "earth" in v for v in versions)
