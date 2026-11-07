"""Which way a photograph's camera faced, read from its EXIF on request: a hint shown to a person, never stored.

Run against the separate test database and scratch data folder only (see conftest.py). Media is generated in memory.
"""

import pytest
from PIL import Image

from geopoc.ingestion.camera_view import read_camera_view
from tests import media
from tests.test_assets import upload

NAN = float("nan")


class Blocks:
    """Stands in for a parsed Exif: `get_ifd` answers with the GPS or camera-settings block."""

    def __init__(self, gps: dict | None = None, settings: dict | None = None) -> None:
        self.blocks = {0x8825: gps or {}, 0x8769: settings or {}}

    def get_ifd(self, tag):
        return self.blocks[tag]


# --- reading the tags -----------------------------------------------------------------------------------------------


def test_direction_and_lens_are_read_as_the_file_states_them():
    view = read_camera_view(Blocks({16: "T", 17: 123.456}, {0xA405: 26, 0x920A: 5.4}))
    assert (view.heading_deg, view.heading_reference) == (123.46, "true_north")
    assert (view.focal_length_35mm_mm, view.focal_length_mm) == (26.0, 5.4)
    assert view.problems == ()


@pytest.mark.parametrize(
    ("ref", "expected"), [("M", "magnetic_north"), ("T", "true_north"), (None, "not_stated"), ("X", "not_stated")]
)
def test_the_north_reference_is_reported_as_given(ref, expected):
    gps = {17: 90.0} | ({16: ref} if ref else {})
    assert read_camera_view(Blocks(gps)).heading_reference == expected


def test_a_photograph_without_a_direction_says_so_and_guesses_nothing():
    view = read_camera_view(Blocks({1: "N", 2: (20.0, 0.0, 0.0)}))
    assert view.heading_deg is None and view.heading_reference is None
    assert view.problems == ("HEADING_ABSENT", "FOCAL_35MM_ABSENT")


@pytest.mark.parametrize("value", [NAN, -5.0, 400.0, "north", (1, 0)])
def test_an_impossible_direction_is_refused(value):
    view = read_camera_view(Blocks({17: value}))
    assert view.heading_deg is None and "HEADING_INVALID" in view.problems


def test_360_degrees_is_north():
    assert read_camera_view(Blocks({17: 360.0})).heading_deg == 0.0


def test_a_zero_or_negative_focal_length_is_not_used():
    view = read_camera_view(Blocks({17: 10.0}, {0xA405: 0, 0x920A: -1}))
    assert view.focal_length_35mm_mm is None and view.focal_length_mm is None
    assert view.problems == ("FOCAL_35MM_ABSENT",)


def test_a_damaged_block_does_not_raise():
    class Broken:
        def get_ifd(self, _tag):
            raise ValueError("corrupt")

    assert read_camera_view(Broken()).problems == ("EXIF_UNREADABLE",)


# --- the endpoint ---------------------------------------------------------------------------------------------------


def exif_with_direction() -> Image.Exif:
    exif = media.gps_exif(t16="T", t17=212.5)
    exif[0x8769] = {0xA405: 26, 0x920A: 5.4}
    return exif


def test_the_endpoint_reads_the_stored_original_and_stores_nothing(client, project, headers):
    data = media.image_bytes("JPEG", exif=exif_with_direction())
    asset = upload(client, project, headers, "facing.jpg", data).json()["asset"]
    before = client.get(f"/api/v1/assets/{asset['id']}/location").json()
    response = client.get(f"/api/v1/assets/{asset['id']}/camera-view")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["heading_deg"] == 212.5 and body["heading_reference"] == "true_north"
    assert body["focal_length_35mm_mm"] == 26.0
    assert body["problems"] == []
    assert "not checked" in body["note"]
    assert client.get(f"/api/v1/assets/{asset['id']}/location").json() == before


def test_a_photograph_without_a_direction_gets_the_reasons(client, project, headers):
    asset = upload(client, project, headers, "plain.jpg", media.image_bytes("JPEG", exif=media.gps_exif())).json()[
        "asset"
    ]
    body = client.get(f"/api/v1/assets/{asset['id']}/camera-view").json()
    assert body["heading_deg"] is None
    assert [p["code"] for p in body["problems"]] == ["HEADING_ABSENT", "FOCAL_35MM_ABSENT"]


def test_only_ground_photographs_have_a_camera_view(client, project, headers):
    raster = upload(client, project, headers, "r.tif", media.geotiff_bytes(), "overhead_raster").json()["asset"]
    response = client.get(f"/api/v1/assets/{raster['id']}/camera-view")
    assert response.status_code == 409 and response.json()["code"] == "NOT_A_PHOTOGRAPH"
    assert client.get("/api/v1/assets/00000000-0000-4000-8000-000000000000/camera-view").status_code == 404
