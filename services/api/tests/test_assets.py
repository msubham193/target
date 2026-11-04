"""Phase 2: asset ingestion. Media is generated in memory; videos are written by the local ffmpeg."""

import hashlib
import os
import shutil
import stat
import uuid

import pytest
from fastapi.testclient import TestClient
from rasterio.transform import Affine, from_origin
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from geopoc.api.main import app, create_app
from geopoc.config import get_settings
from geopoc.db.session import get_engine, get_session, reset_engine
from geopoc.storage import store
from tests import media

needs_ffmpeg = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg/ffprobe not installed"
)


# --- helpers ---------------------------------------------------------------------------------------------


def upload(client, project, headers, name, data, modality="ground_image", **kwargs):
    return client.post(
        f"/api/v1/projects/{project['id']}/assets",
        params={"modality": modality, "filename": name},
        content=data,
        headers=headers,
        **kwargs,
    )


def rows(sql: str, **params):
    with get_engine().connect() as conn:
        return conn.execute(text(sql), params).all()


def stored_files() -> list:
    return [p for p in (store.data_root() / "originals").rglob("*") if p.is_file()]


def temp_files() -> list:
    return list(store.uploads_dir().glob("*"))


def assert_nothing_stored():
    assert stored_files() == []
    assert temp_files() == []
    assert rows("SELECT count(*) FROM asset")[0][0] == 0
    assert rows("SELECT count(*) FROM blob")[0][0] == 0


def rejections() -> list[dict]:
    return [r[0] for r in rows("SELECT details FROM audit_event WHERE action = 'asset.upload_rejected' ORDER BY at")]


@pytest.fixture(scope="session")
def videos(tmp_path_factory):
    folder = tmp_path_factory.mktemp("videos")
    return {ext: media.make_video(folder / f"clip.{ext}") for ext in ("mp4", "mov", "mkv")}


# --- supported categories -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "fmt", "detected", "media_type"),
    [
        ("photo.jpg", "JPEG", "JPEG", "image/jpeg"),
        ("photo.JPEG", "JPEG", "JPEG", "image/jpeg"),
        ("photo.png", "PNG", "PNG", "image/png"),
        ("photo.webp", "WEBP", "WEBP", "image/webp"),
    ],
)
def test_ground_photograph_is_stored_unmodified_with_metadata(
    client, project, headers, profile, name, fmt, detected, media_type
):
    data = media.image_bytes(fmt)
    response = upload(client, project, headers, name, data)
    assert response.status_code == 201, response.text
    body = response.json()
    asset = body["asset"]

    assert body["duplicate"] is False and body["stored_original_reused"] is False
    assert asset["project_id"] == project["id"]
    assert asset["original_filename"] == name
    assert asset["modality"] == "ground_image"
    assert asset["detected_format"] == detected and asset["media_type"] == media_type
    assert asset["size_bytes"] == len(data)
    assert asset["sha256"] == hashlib.sha256(data).hexdigest()
    assert asset["status"] == "validated"
    assert asset["uploaded_by"]["id"] == profile["id"]
    assert (asset["metadata"]["width"], asset["metadata"]["height"]) == (64, 48)
    sharpness = asset["metadata"]["quality_indicators"]["sharpness"]
    assert sharpness["value"] > 0 and sharpness["threshold"] is None

    # A ground photograph gets no location, and says so.
    assert "NOT_GEOREFERENCED" in asset["quality_flags"]
    assert "no location is inferred" in asset["unknowns"]["geolocation"]
    assert asset["acquisition_time"] is None and "acquisition_time" in asset["unknowns"]
    assert asset["raster"] is None and asset["video"] is None

    # The original is on disk exactly as uploaded, under a content-derived path, and read-only.
    (stored,) = stored_files()
    assert stored.name == asset["sha256"]
    assert stored.read_bytes() == data
    assert not os.access(stored, os.W_OK)
    assert temp_files() == []

    # ... and comes back byte-identical.
    content = client.get(f"/api/v1/assets/{asset['id']}/content")
    assert content.status_code == 200 and content.content == data
    partial = client.get(f"/api/v1/assets/{asset['id']}/content", headers={"Range": "bytes=0-9"})
    assert partial.status_code == 206 and partial.content == data[:10]


def test_valid_geotiff_keeps_crs_geotransform_and_gets_a_footprint(client, project, headers):
    data = media.geotiff_bytes()
    response = upload(client, project, headers, "scene.tif", data, "overhead_raster")
    assert response.status_code == 201, response.text
    asset = response.json()["asset"]
    raster = asset["raster"]

    assert asset["status"] == "validated"
    assert asset["detected_format"] == "GTiff"
    assert (raster["width"], raster["height"], raster["band_count"]) == (40, 30, 1)
    assert raster["dtypes"] == ["uint8"]
    assert raster["crs_status"] == "valid"
    assert raster["crs_identifier"] == "EPSG:32643"
    assert raster["crs_name"] == "WGS 84 / UTM zone 43N"
    assert raster["georeferenced"] is True
    assert raster["geotransform"] == [500000.0, 10.0, 0.0, 3000000.0, 0.0, -10.0]  # verbatim, GDAL order
    assert (raster["pixel_size_x"], raster["pixel_size_y"], raster["pixel_units"]) == (10.0, 10.0, "metre")
    assert raster["bounds_native"] == {
        "left": 500000.0,
        "bottom": 2999700.0,
        "right": 500400.0,
        "top": 3000000.0,
    }

    ring = raster["footprint_4326"]["coordinates"][0]
    assert ring[0][0] == pytest.approx(75.0, abs=1e-6)  # the western edge lies on the central meridian
    assert all(74.99 < lon < 75.01 and 27.0 < lat < 27.2 for lon, lat in ring)
    transformation = asset["metadata"]["footprint_transformation"]
    assert transformation["target_crs_id"] == "EPSG:4326" and transformation["definition"]

    assert "MISSING_CRS" not in asset["quality_flags"] and "NOT_GEOREFERENCED" not in asset["quality_flags"]
    assert "crs" not in asset["unknowns"]


def test_rotated_geotiff_reports_true_pixel_size(client, project, headers):
    transform = Affine.translation(500000, 3000000) @ Affine.rotation(30) @ Affine.scale(10, -10)
    asset = upload(
        client, project, headers, "rot.tiff", media.geotiff_bytes(transform=transform), "overhead_raster"
    ).json()["asset"]
    assert asset["raster"]["georeferenced"] is True
    assert asset["raster"]["pixel_size_x"] == pytest.approx(10.0)
    assert asset["raster"]["pixel_size_y"] == pytest.approx(10.0)


@pytest.mark.parametrize("transform", [media.NORTH_UP, None], ids=["with-geotransform", "plain-tiff"])
def test_raster_without_crs_is_accepted_but_never_given_coordinates(client, project, headers, transform):
    data = media.geotiff_bytes(crs=None, transform=transform)
    response = upload(client, project, headers, "nocrs.tif", data, "overhead_raster")
    assert response.status_code == 201, response.text
    asset = response.json()["asset"]
    raster = asset["raster"]

    assert asset["status"] == "validated_with_warnings"
    assert raster["crs_status"] == "missing"
    assert raster["crs_identifier"] is None and raster["crs_wkt"] is None
    assert raster["georeferenced"] is False
    assert raster["footprint_4326"] is None and raster["bounds_native"] is None
    assert raster["pixel_units"] is None
    assert {"MISSING_CRS", "NOT_GEOREFERENCED"} <= set(asset["quality_flags"])
    assert "None is assumed" in asset["unknowns"]["crs"]
    assert "pixel_units" in asset["unknowns"]
    assert "footprint_transformation" not in asset["metadata"]
    if transform is None:
        assert raster["geotransform"] is None and raster["pixel_size_x"] is None
        assert "geotransform" in asset["unknowns"]
    else:
        # The numbers in the file are kept verbatim, but without a CRS they have no unit and no place.
        assert raster["geotransform"][1] == 10.0 and raster["pixel_size_x"] == 10.0
    assert (raster["width"], raster["height"]) == (40, 30)  # still inspectable in image space


def test_raster_whose_coordinates_do_not_fit_its_crs_is_not_trusted(client, project, headers):
    data = media.geotiff_bytes(transform=from_origin(9e15, 9e15, 10, 10))
    asset = upload(client, project, headers, "bad.tif", data, "overhead_raster").json()["asset"]
    raster = asset["raster"]
    assert raster["crs_status"] == "valid"  # the CRS definition itself is fine
    assert raster["georeferenced"] is False
    assert raster["footprint_4326"] is None and raster["pixel_units"] is None
    assert {"INVALID_GEOREFERENCING", "NOT_GEOREFERENCED"} <= set(asset["quality_flags"])
    assert asset["status"] == "validated_with_warnings"


def test_raster_with_crs_but_no_geotransform_is_not_georeferenced(client, project, headers):
    asset = upload(
        client, project, headers, "crs_only.tif", media.geotiff_bytes(transform=None), "overhead_raster"
    ).json()["asset"]
    assert asset["raster"]["crs_status"] == "valid"
    assert asset["raster"]["georeferenced"] is False and asset["raster"]["footprint_4326"] is None
    assert "geotransform" in asset["unknowns"]


def test_raster_with_non_earth_crs_is_flagged_invalid(client, project, headers):
    local = 'LOCAL_CS["site grid",UNIT["metre",1]]'
    try:
        data = media.geotiff_bytes(crs=local)
    except Exception as exc:  # depends on what the bundled GDAL will write
        pytest.skip(f"could not build an engineering-CRS fixture: {exc}")
    asset = upload(client, project, headers, "local.tif", data, "overhead_raster").json()["asset"]
    if asset["raster"]["crs_status"] == "missing":
        pytest.skip("the bundled GDAL did not persist the engineering CRS in the GeoTIFF")
    assert asset["raster"]["crs_status"] == "invalid"
    assert asset["raster"]["georeferenced"] is False and asset["raster"]["footprint_4326"] is None
    assert "INVALID_CRS" in asset["quality_flags"]


@needs_ffmpeg
@pytest.mark.parametrize(
    ("ext", "detected", "media_type", "container"),
    [
        ("mp4", "MP4", "video/mp4", "mp4"),
        ("mov", "QuickTime", "video/quicktime", "mov"),
        ("mkv", "Matroska", "video/x-matroska", "matroska"),
    ],
)
def test_video_metadata_and_duration(client, project, headers, videos, ext, detected, media_type, container):
    response = upload(client, project, headers, f"clip.{ext}", videos[ext], "video")
    assert response.status_code == 201, response.text
    asset = response.json()["asset"]
    video = asset["video"]

    assert asset["detected_format"] == detected and asset["media_type"] == media_type
    assert asset["sha256"] == hashlib.sha256(videos[ext]).hexdigest()
    assert container in video["container"].split(",")
    assert video["video_codec"] == "mpeg4"
    assert (video["width"], video["height"]) == (64, 48)
    assert video["duration_ms"] == pytest.approx(1000, abs=150)
    assert video["frame_rate"] == pytest.approx(10.0)
    assert video["has_audio"] is False
    assert video["time_base"]
    assert asset["status"] == "validated"
    assert "NOT_GEOREFERENCED" in asset["quality_flags"]
    assert "no location is inferred" in asset["unknowns"]["geolocation"]
    assert asset["acquisition_time"] is None
    if video["frame_count"] is None:
        assert "frame_count" in asset["unknowns"]
    else:
        assert video["frame_count"] == 10


# --- rejected at the door -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "modality"),
    [
        ("animation.gif", "ground_image"),
        ("scan.tif", "ground_image"),  # TIFF is a raster format here, not a ground photograph
        ("photo.jpg", "overhead_raster"),
        ("image.jp2", "overhead_raster"),
        ("clip.webm", "video"),
        ("clip.avi", "video"),
        ("noextension", "ground_image"),
        ("archive.jpg.exe", "ground_image"),
    ],
)
def test_unsupported_extension_is_rejected_with_a_clear_message(client, project, headers, name, modality):
    response = upload(client, project, headers, name, media.image_bytes("JPEG"), modality)
    assert response.status_code == 415
    body = response.json()
    assert body["code"] == "UNSUPPORTED_FILE_TYPE"
    assert "accepts" in body["detail"]
    assert_nothing_stored()
    assert rejections()[-1]["code"] == "UNSUPPORTED_FILE_TYPE"
    assert rejections()[-1]["filename"] == name


@pytest.mark.parametrize(
    ("name", "modality", "data", "actual"),
    [
        ("really_png.jpg", "ground_image", media.image_bytes("PNG"), "PNG image"),
        ("really_jpeg.png", "ground_image", media.image_bytes("JPEG"), "JPEG image"),
        ("really_jpeg.webp", "ground_image", media.image_bytes("JPEG"), "JPEG image"),
        ("text.tif", "overhead_raster", b"this is plain text, not a raster" * 4, "not a recognised format"),
        ("photo.tif", "overhead_raster", media.image_bytes("JPEG"), "JPEG image"),
        ("program.mp4", "video", b"MZ\x90\x00" + b"\x00" * 200, "not a recognised format"),
        ("image.mkv", "video", media.image_bytes("PNG"), "PNG image"),
        ("tiny.png", "ground_image", b"\x89PN", "not a recognised format"),
    ],
)
def test_content_that_contradicts_the_file_name_is_rejected(client, project, headers, name, modality, data, actual):
    response = upload(client, project, headers, name, data, modality)
    assert response.status_code == 415
    body = response.json()
    assert body["code"] == "SIGNATURE_MISMATCH"
    assert actual in body["detail"]
    assert_nothing_stored()
    assert rejections()[-1]["code"] == "SIGNATURE_MISMATCH"


def test_oversized_upload_is_rejected_before_and_during_streaming(client, project, headers, monkeypatch):
    monkeypatch.setattr(get_settings(), "max_ground_image_bytes", 2000)
    big = media.image_bytes("PNG", size=(200, 200))
    assert len(big) > 2000

    declared = upload(client, project, headers, "big.png", big)
    assert declared.status_code == 413
    assert declared.json()["code"] == "FILE_TOO_LARGE"
    assert "limit" in declared.json()["detail"]
    assert_nothing_stored()

    # No Content-Length (chunked): the limit is enforced while streaming and the partial file is removed.
    def chunks():
        for start in range(0, len(big), 512):
            yield big[start : start + 512]

    streamed = upload(client, project, headers, "big.png", chunks())
    assert streamed.status_code == 413
    assert_nothing_stored()
    assert [r["code"] for r in rejections()] == ["FILE_TOO_LARGE", "FILE_TOO_LARGE"]

    # Exactly at the limit is accepted.
    monkeypatch.setattr(get_settings(), "max_ground_image_bytes", len(big))
    assert upload(client, project, headers, "big.png", big).status_code == 201


def test_default_limits_are_the_approved_ones(client):
    body = client.get("/api/v1/ingestion/rules").json()
    by_modality = {rule["modality"]: rule for rule in body["rules"]}
    assert by_modality["ground_image"]["max_bytes"] == 50 * 1024**2
    assert by_modality["ground_image"]["extensions"] == [".jpg", ".jpeg", ".png", ".webp"]
    assert by_modality["overhead_raster"]["max_bytes"] == 1024**3
    assert by_modality["overhead_raster"]["extensions"] == [".tif", ".tiff"]
    assert by_modality["video"]["max_bytes"] == 1024**3
    assert by_modality["video"]["extensions"] == [".mp4", ".mov", ".mkv"]


def test_empty_file_is_rejected(client, project, headers):
    response = upload(client, project, headers, "empty.jpg", b"")
    assert response.status_code == 422 and response.json()["code"] == "EMPTY_FILE"
    assert_nothing_stored()


# --- corrupt or unreadable --------------------------------------------------------------------------------------


def _corrupt_cases():
    jpeg, png, webp = (media.image_bytes(f, size=(300, 300)) for f in ("JPEG", "PNG", "WEBP"))
    tiff = media.geotiff_bytes(size=(600, 600))
    return [
        ("truncated.jpg", "ground_image", jpeg[: len(jpeg) // 2]),
        ("truncated.png", "ground_image", png[: len(png) // 2]),
        ("truncated.webp", "ground_image", webp[: len(webp) // 2]),
        ("garbage.jpg", "ground_image", b"\xff\xd8\xff\xe0" + b"\x13" * 500),
        ("truncated.tif", "overhead_raster", tiff[: len(tiff) // 3]),
        ("garbage.tif", "overhead_raster", b"II*\x00" + b"\x07" * 500),
        ("garbage.mp4", "video", b"\x00\x00\x00\x18ftypisom" + b"\x05" * 800),
        ("garbage.mkv", "video", b"\x1a\x45\xdf\xa3" + b"\x05" * 800),
    ]


@pytest.mark.parametrize(("name", "modality", "data"), _corrupt_cases(), ids=[case[0] for case in _corrupt_cases()])
def test_corrupt_file_fails_safely_and_is_recorded(client, project, headers, name, modality, data):
    """Spec test T-01: ingestion fails safely, the error is recorded, nothing is fabricated."""
    if modality == "video" and not shutil.which("ffprobe"):
        pytest.skip("ffprobe not installed")
    response = upload(client, project, headers, name, data, modality)
    assert response.status_code == 422, response.text
    body = response.json()
    assert body["code"] == "ASSET_UNREADABLE"
    assert "Nothing was stored" in body["detail"]
    assert_nothing_stored()
    recorded = rejections()[-1]
    assert recorded["code"] == "ASSET_UNREADABLE"
    assert recorded["sha256"] == hashlib.sha256(data).hexdigest()
    # The project itself is unharmed.
    assert client.get(f"/api/v1/projects/{project['id']}").status_code == 200
    assert client.get(f"/api/v1/projects/{project['id']}/assets").json()["items"] == []


def test_image_above_the_pixel_cap_is_rejected(client, project, headers, monkeypatch):
    monkeypatch.setattr(get_settings(), "max_image_pixels", 1000)
    response = upload(client, project, headers, "wide.png", media.image_bytes("PNG"))
    assert response.status_code == 422 and response.json()["code"] == "IMAGE_TOO_MANY_PIXELS"
    assert_nothing_stored()


# --- metadata --------------------------------------------------------------------------------------------------


def test_metadata_extraction_failure_keeps_the_asset_and_records_the_gap(client, project, headers, monkeypatch):
    def broken(_image):
        raise RuntimeError("malformed EXIF block")

    monkeypatch.setattr("geopoc.ingestion.image.read_exif", broken)
    response = upload(client, project, headers, "photo.jpg", media.image_bytes("JPEG"))
    assert response.status_code == 201
    asset = response.json()["asset"]
    assert asset["status"] == "validated_with_warnings"
    assert "METADATA_INCOMPLETE" in asset["quality_flags"]
    assert "could not be read" in asset["unknowns"]["exif"]
    assert "exif" not in asset["metadata"]
    assert asset["metadata"]["width"] == 64  # what could be read is still recorded


def test_exif_capture_time_is_only_trusted_with_a_utc_offset(client, project, headers):
    ambiguous = media.image_bytes("JPEG", seed=2, exif=media.exif_block(original="2026:01:02 03:04:05"))
    asset = upload(client, project, headers, "a.jpg", ambiguous).json()["asset"]
    assert asset["acquisition_time"] is None
    assert "no UTC offset" in asset["unknowns"]["acquisition_time"]
    assert asset["metadata"]["exif"]["datetime_original"] == "2026:01:02 03:04:05"  # kept as raw metadata
    assert asset["metadata"]["exif"]["make"] == "TestCam"

    exact = media.image_bytes("JPEG", seed=3, exif=media.exif_block(original="2026:01:02 03:04:05", offset="+05:30"))
    asset = upload(client, project, headers, "b.jpg", exact).json()["asset"]
    assert asset["acquisition_time"].startswith("2026-01-01T21:34:05")
    assert "acquisition_time" not in asset["unknowns"]
    assert "TIMESTAMP_UNKNOWN" not in asset["quality_flags"]


def test_exif_gps_is_noted_but_never_becomes_a_location(client, project, headers):
    data = media.image_bytes("JPEG", exif=media.exif_block(gps=True))
    response = upload(client, project, headers, "gps.jpg", data)
    asset = response.json()["asset"]
    assert asset["metadata"]["exif"]["gps_tags_present"] is True
    # It is held as an unverified candidate: a person must confirm it before it is a location (test_photo_locations).
    assert "unverified candidate" in asset["unknowns"]["geolocation"]
    assert "until a person confirms" in asset["unknowns"]["geolocation"]
    assert "NOT_GEOREFERENCED" in asset["quality_flags"]
    # No coordinate value from the GPS block appears anywhere in the stored asset record.
    for value in ("28.0", "28.6", "77.0", "77.2"):
        assert value not in response.text


@needs_ffmpeg
def test_video_cannot_be_validated_without_ffmpeg(client, project, headers, videos, monkeypatch):
    monkeypatch.setattr("geopoc.ingestion.video.tool_path", lambda _name: None)
    response = upload(client, project, headers, "clip.mp4", videos["mp4"], "video")
    assert response.status_code == 503
    assert response.json()["code"] == "MEDIA_TOOLING_UNAVAILABLE"
    assert_nothing_stored()


# --- duplicates and checksums ------------------------------------------------------------------------------------


def test_duplicate_uploads_are_accepted_linked_and_flagged(client, project, headers):
    data = media.image_bytes("JPEG", seed=7)
    first = upload(client, project, headers, "original.jpg", data).json()
    second = upload(client, project, headers, "copy of original.jpg", data)
    third = upload(client, project, headers, "another copy.jpeg", data).json()
    assert second.status_code == 201
    second = second.json()

    assert first["duplicate"] is False and "DUPLICATE_ASSET" not in first["asset"]["quality_flags"]
    for copy in (second, third):
        assert copy["duplicate"] is True and copy["stored_original_reused"] is True
        assert copy["duplicate_of_asset_id"] == first["asset"]["id"]  # always the first, never a chain
        assert copy["asset"]["duplicate_of_asset_id"] == first["asset"]["id"]
        assert "DUPLICATE_ASSET" in copy["asset"]["quality_flags"]
        assert copy["asset"]["sha256"] == first["asset"]["sha256"]
        assert copy["asset"]["id"] != first["asset"]["id"]
        assert copy["asset"]["status"] == "validated"
        assert "duplicate" in copy["message"]

    # Three upload records for audit, one stored original.
    assert rows("SELECT count(*) FROM asset")[0][0] == 3
    assert rows("SELECT count(*) FROM blob")[0][0] == 1
    assert len(stored_files()) == 1 and temp_files() == []
    # Copies share one content identity, so they can never be counted as independent sources.
    assert rows("SELECT count(DISTINCT blob_sha256) FROM asset")[0][0] == 1

    different = upload(client, project, headers, "different.jpg", media.image_bytes("JPEG", seed=8)).json()
    assert different["duplicate"] is False
    assert different["asset"]["sha256"] != first["asset"]["sha256"]
    assert len(stored_files()) == 2


def test_same_content_in_another_project_reuses_storage_without_being_a_project_duplicate(client, project, headers):
    data = media.image_bytes("PNG", seed=9)
    upload(client, project, headers, "a.png", data)
    other = client.post("/api/v1/projects", json={"name": "Other"}, headers=headers).json()
    result = upload(client, other, headers, "a.png", data).json()
    assert result["duplicate"] is False and result["duplicate_of_asset_id"] is None
    assert result["stored_original_reused"] is True
    assert "DUPLICATE_ASSET" not in result["asset"]["quality_flags"]
    assert len(stored_files()) == 1


def test_a_lost_original_is_restored_by_an_identical_upload(client, project, headers):
    data = media.image_bytes("PNG", seed=10)
    first = upload(client, project, headers, "a.png", data).json()["asset"]
    (stored,) = stored_files()
    os.chmod(stored, stat.S_IWRITE)
    stored.unlink()
    missing = client.get(f"/api/v1/assets/{first['id']}/content")
    assert missing.status_code == 500 and missing.json()["code"] == "STORAGE_ERROR"

    assert upload(client, project, headers, "a again.png", data).status_code == 201
    assert client.get(f"/api/v1/assets/{first['id']}/content").content == data


# --- failures of the database and of storage -------------------------------------------------------------------------


def test_database_unavailable_is_reported_cleanly(client, project):
    dead_engine = create_engine(get_settings().database_url().set(port=1), connect_args={"connect_timeout": 1})

    def dead_session():
        with Session(dead_engine) as session:
            yield session

    app.dependency_overrides[get_session] = dead_session
    try:
        response = client.get(f"/api/v1/projects/{project['id']}/assets")
    finally:
        app.dependency_overrides.clear()
        dead_engine.dispose()
    assert response.status_code == 503
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == "DATABASE_UNAVAILABLE"


def test_database_failure_during_upload_leaves_no_orphan_file(client, project, headers, monkeypatch):
    real_commit = Session.commit
    calls = {"n": 0}

    def failing_commit(self):
        calls["n"] += 1
        if calls["n"] == 1:  # the commit that would persist the asset
            raise OperationalError("COMMIT", None, Exception("connection lost"))
        return real_commit(self)

    monkeypatch.setattr(Session, "commit", failing_commit)
    response = upload(client, project, headers, "photo.jpg", media.image_bytes("JPEG"))
    monkeypatch.undo()

    assert response.status_code == 503
    assert response.json()["code"] == "DATABASE_UNAVAILABLE"
    assert_nothing_stored()


def test_storage_write_failure_is_reported_and_rolled_back(client, project, headers, monkeypatch):
    def failing(_temp, _sha):
        raise store.StorageError("disk error")

    monkeypatch.setattr(store, "commit_original", failing)
    response = upload(client, project, headers, "photo.jpg", media.image_bytes("JPEG"))
    monkeypatch.undo()
    assert response.status_code == 500
    assert response.json()["code"] == "STORAGE_ERROR"
    assert "Nothing was stored" in response.json()["detail"]
    assert_nothing_stored()
    assert rejections()[-1]["code"] == "STORAGE_ERROR"


def test_upload_is_refused_when_disk_space_is_short(client, project, headers, monkeypatch):
    monkeypatch.setattr(store, "free_bytes", lambda: get_settings().storage_reserve_bytes)
    response = upload(client, project, headers, "photo.jpg", media.image_bytes("JPEG"))
    assert response.status_code == 507
    assert response.json()["code"] == "INSUFFICIENT_STORAGE"
    assert_nothing_stored()


# --- association, listing, retrieval -----------------------------------------------------------------------------------


def test_assets_belong_to_their_project_and_can_be_listed_and_filtered(client, project, headers, videos):
    other = client.post("/api/v1/projects", json={"name": "Other"}, headers=headers).json()
    photo = upload(client, project, headers, "p.jpg", media.image_bytes("JPEG", seed=20)).json()["asset"]
    raster = upload(client, project, headers, "r.tif", media.geotiff_bytes(crs=None), "overhead_raster").json()["asset"]
    elsewhere = upload(client, other, headers, "o.png", media.image_bytes("PNG", seed=21)).json()["asset"]

    url = f"/api/v1/projects/{project['id']}/assets"
    listed = client.get(url).json()
    assert [a["id"] for a in listed["items"]] == [raster["id"], photo["id"]]  # newest first
    assert listed["next_cursor"] is None
    assert [a["id"] for a in client.get(f"/api/v1/projects/{other['id']}/assets").json()["items"]] == [elsewhere["id"]]

    assert [a["id"] for a in client.get(url, params={"modality": "overhead_raster"}).json()["items"]] == [raster["id"]]
    assert [a["id"] for a in client.get(url, params={"status": "validated"}).json()["items"]] == [photo["id"]]
    assert client.get(url, params={"modality": "audio"}).status_code == 422

    page1 = client.get(url, params={"limit": 1}).json()
    page2 = client.get(url, params={"limit": 1, "cursor": page1["next_cursor"]}).json()
    assert [page1["items"][0]["id"], page2["items"][0]["id"]] == [raster["id"], photo["id"]]
    assert page2["next_cursor"] is None

    fetched = client.get(f"/api/v1/assets/{photo['id']}")
    assert fetched.status_code == 200 and fetched.json() == photo

    assert client.get(f"/api/v1/projects/{uuid.uuid4()}/assets").json()["code"] == "PROJECT_NOT_FOUND"
    assert client.get(f"/api/v1/assets/{uuid.uuid4()}").json()["code"] == "ASSET_NOT_FOUND"
    assert client.get(f"/api/v1/assets/{uuid.uuid4()}/content").status_code == 404


def test_upload_requires_profile_existing_project_and_unarchived_project(client, project, headers):
    data = media.image_bytes("JPEG")
    assert upload(client, project, {}, "p.jpg", data).json()["code"] == "PROFILE_REQUIRED"
    missing = upload(client, {"id": str(uuid.uuid4())}, headers, "p.jpg", data)
    assert missing.status_code == 404 and missing.json()["code"] == "PROJECT_NOT_FOUND"

    client.patch(f"/api/v1/projects/{project['id']}", json={"archived": True}, headers=headers)
    archived = upload(client, project, headers, "p.jpg", data)
    assert archived.status_code == 409 and archived.json()["code"] == "PROJECT_ARCHIVED"
    assert_nothing_stored()


def test_client_file_name_never_becomes_a_storage_path(client, project, headers):
    asset = upload(client, project, headers, "..\\..\\Windows\\evil.jpg", media.image_bytes("JPEG")).json()["asset"]
    assert asset["original_filename"] == "evil.jpg"
    (stored,) = stored_files()
    assert stored.name == asset["sha256"]
    assert store.data_root() in stored.parents


def test_assets_survive_an_application_restart(client, project, headers):
    """Restart is simulated in-process: the connection pool is discarded and a new application is built."""
    data = media.geotiff_bytes()
    asset = upload(client, project, headers, "scene.tif", data, "overhead_raster").json()["asset"]

    reset_engine()
    with TestClient(create_app()) as restarted:
        again = restarted.get(f"/api/v1/assets/{asset['id']}")
        assert again.status_code == 200 and again.json() == asset
        assert restarted.get(f"/api/v1/assets/{asset['id']}/content").content == data
        listed = restarted.get(f"/api/v1/projects/{project['id']}/assets").json()["items"]
        assert [a["id"] for a in listed] == [asset["id"]]


def test_interrupted_uploads_are_cleaned_up_at_start(client):
    stale = store.uploads_dir() / "stale.upload"
    fresh = store.uploads_dir() / "fresh.upload"
    stale.write_bytes(b"x")
    fresh.write_bytes(b"x")
    old = stale.stat().st_mtime - 2 * 24 * 3600
    os.utime(stale, (old, old))
    assert store.remove_orphaned_uploads() == 1
    assert not stale.exists() and fresh.exists()


# --- audit ---------------------------------------------------------------------------------------------------------


def test_uploads_and_rejections_are_audited(client, project, headers, profile):
    data = media.image_bytes("JPEG", seed=30)
    traced = headers | {"X-Request-ID": "req-audit-1"}
    asset = upload(client, project, traced, "ok.jpg", data).json()["asset"]
    upload(client, project, headers, "copy.jpg", data)
    upload(client, project, headers, "bad.gif", data)

    accepted = rows(
        "SELECT actor_id::text, entity_id::text, request_id, details FROM audit_event "
        "WHERE action = 'asset.upload' ORDER BY at"
    )
    assert len(accepted) == 2
    actor, entity, request_id, details = accepted[0]
    assert actor == profile["id"] and entity == asset["id"] and request_id == "req-audit-1"
    assert details["sha256"] == hashlib.sha256(data).hexdigest()
    assert details["project_id"] == project["id"]
    assert details["duplicate_of_asset_id"] is None and details["stored_original_reused"] is False
    assert accepted[1][3]["duplicate_of_asset_id"] == asset["id"]

    rejected = rows(
        "SELECT actor_id::text, entity_type, entity_id::text, details FROM audit_event "
        "WHERE action = 'asset.upload_rejected'"
    )
    assert len(rejected) == 1
    assert rejected[0][:3] == (profile["id"], "project", project["id"])
    assert rejected[0][3] == {
        "filename": "bad.gif",
        "modality": "ground_image",
        "code": "UNSUPPORTED_FILE_TYPE",
    }
