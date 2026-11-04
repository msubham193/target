"""Video-frame analysis end to end: real clips written by the local ffmpeg, the stub detector, the real job runner.

What is checked: an explicit start, the sampling policy and its limits, accurate frame numbers and timestamps
(B-frames, a
container start offset, 29.97 fps), evidence integrity (the stored frame, its checksum, the source video), failures that
leave nothing behind, project isolation, human review of a frame finding, progress, no network, and reopening a result
without running anything again.

Scratch database geopoc_test and scratch data folder only (see conftest.py).
"""

import hashlib
import json
import socket
import subprocess
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

import pytest
from sqlalchemy import text

from geopoc.config import get_settings
from geopoc.db.session import get_engine
from geopoc.jobs.handlers import JobCancelled, JobContext
from geopoc.storage import store
from geopoc.video import media
from tests import media as media_helpers
from tests.test_assets import rows, upload
from tests.test_ground_pipeline import STUB, analyze

SAMPLING = {"interval_s": 1.0, "max_frames": 10}
UNKNOWN_UUID = "00000000-0000-4000-8000-00000000dead"


# --- helpers --------------------------------------------------------------------------------------------------------


def clip(path: Path, *, seconds=6, rate="10", offset=None, size="96x72") -> bytes:
    """A test-pattern clip with B-frames (so storage order differs from presentation order), optionally offset in "
    "time."""
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc=size={size}:rate={rate}:duration={seconds}",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-bf", "2", str(path)],
        check=True, capture_output=True,
    )  # fmt: skip
    if offset is not None:
        shifted = path.with_name("shifted" + path.suffix)
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-itsoffset", str(offset), "-i", str(path), "-c", "copy", str(shifted)],
            check=True, capture_output=True,
        )  # fmt: skip
        return shifted.read_bytes()
    return path.read_bytes()


@pytest.fixture(scope="module")
def clips(tmp_path_factory):
    folder = tmp_path_factory.mktemp("clips")
    return {
        "plain": clip(folder / "plain.mp4"),
        "offset": clip(folder / "off.mp4", offset=2.5),
        "ntsc": clip(folder / "ntsc.mp4", rate="30000/1001", seconds=3),
        "slow": clip(folder / "slow.mp4", rate="2", seconds=3),
    }


def put_video(client, project, headers, run_jobs, data, name="clip.mp4"):
    asset = upload(client, project, headers, name, data, "video").json()["asset"]
    run_jobs()  # the automatic preview job
    return asset


def start(client, project, headers, asset, sampling=SAMPLING, model=STUB, **extra):
    return analyze(client, project, headers, asset["id"], model=model, pipeline="video", sampling=sampling, **extra)


def run_to_end(client, project, headers, run_jobs, asset, **kwargs):
    response = start(client, project, headers, asset, **kwargs)
    assert response.status_code == 202, response.text
    job_id = response.json()["id"]
    run_jobs()
    return client.get(f"/api/v1/jobs/{job_id}").json()


def video_url(project, asset, tail=""):
    return f"/api/v1/projects/{project['id']}/assets/{asset['id']}/video-analysis{tail}"


def frames_of(client, project, asset, job):
    response = client.get(video_url(project, asset, f"/runs/{job['result']['run_id']}/frames"))
    assert response.status_code == 200, response.text
    return response.json()


def findings(client, project, run_id, **params):
    response = client.get(
        f"/api/v1/projects/{project['id']}/observations", params={"run_id": run_id, "limit": 500, **params}
    )
    assert response.status_code == 200, response.text
    return response.json()


def counts():
    with get_engine().connect() as conn:
        return {
            t: conn.execute(text(f"SELECT count(*) FROM {t}")).scalar_one()
            for t in ("job", "pipeline_run", "observation", "artifact", "audit_event", "observation_revision", "asset")
        }


def frame_files() -> list[Path]:
    root = store.data_root() / "video_frames"
    return [p for p in root.rglob("*") if p.is_file()] if root.exists() else []


@contextmanager
def only_the_database_network():
    """Any connection other than the local database fails the test."""
    settings = get_settings()
    real = socket.socket.connect

    def guarded(self, address, *args, **kwargs):
        if self.family == socket.AF_INET and tuple(address)[:2] == (settings.db_host, settings.db_port):
            return real(self, address, *args, **kwargs)
        if self.family != socket.AF_INET:
            return real(self, address, *args, **kwargs)  # local pipes
        raise AssertionError(f"a network connection was attempted: {address}")

    def refuse_lookup(*_a, **_k):
        raise AssertionError("a name lookup was attempted")

    with mock.patch.object(socket.socket, "connect", guarded), mock.patch.object(socket, "getaddrinfo", refuse_lookup):
        yield


# --- starting an analysis -------------------------------------------------------------------------------------------


def test_uploading_a_video_does_not_start_an_analysis_and_it_must_be_asked_for_with_a_sampling_choice(
    client, project, headers, run_jobs, clips
):
    asset = put_video(client, project, headers, run_jobs, clips["plain"])
    assert rows("SELECT count(*) FROM job WHERE kind = 'analyze'")[0][0] == 0
    assert rows("SELECT count(*) FROM pipeline_run")[0][0] == 0

    missing = analyze(client, project, headers, asset["id"], pipeline="video")
    assert (missing.status_code, missing.json()["code"]) == (
        422,
        "SAMPLING_REQUIRED",
    )  # nothing is chosen for the person
    assert rows("SELECT count(*) FROM job WHERE kind = 'analyze'")[0][0] == 0
    assert start(client, project, headers, asset).status_code == 202
    assert rows("SELECT count(*) FROM job WHERE kind = 'analyze'")[0][0] == 1


@pytest.mark.parametrize(
    ("sampling", "code"),
    [
        ({"interval_s": 0.05, "max_frames": 10}, "SAMPLING_INTERVAL_RANGE"),
        ({"interval_s": 1.0, "max_frames": 10, "start_s": 5, "end_s": 3}, "SAMPLING_END_RANGE"),
    ],
)
def test_a_sampling_outside_the_server_limits_is_refused_before_a_job_exists(
    client, project, headers, run_jobs, clips, sampling, code
):
    asset = put_video(client, project, headers, run_jobs, clips["plain"])
    response = start(client, project, headers, asset, sampling=sampling)
    assert (response.status_code, response.json()["code"]) == (422, code)
    assert rows("SELECT count(*) FROM job WHERE kind = 'analyze'")[0][0] == 0


@pytest.mark.parametrize(
    "sampling",
    [
        {"interval_s": 1.0, "max_frames": 301},
        {"interval_s": 1.0, "max_frames": 0},
        {"interval_s": -1, "max_frames": 5},
        {"interval_s": 1.0, "max_frames": 5, "extra": 1},
        {"interval_s": "fast", "max_frames": 5},
    ],
)
def test_malformed_sampling_is_a_validation_error(client, project, headers, run_jobs, clips, sampling):
    asset = put_video(client, project, headers, run_jobs, clips["plain"])
    assert start(client, project, headers, asset, sampling=sampling).status_code == 422


def test_the_frame_ceiling_is_a_setting_and_cannot_be_exceeded_by_a_request(
    client, project, headers, run_jobs, clips, monkeypatch
):
    asset = put_video(client, project, headers, run_jobs, clips["plain"])
    monkeypatch.setattr(get_settings(), "video_analysis_max_frames", 5)
    response = start(client, project, headers, asset, sampling={"interval_s": 1.0, "max_frames": 6})
    assert (response.status_code, response.json()["code"]) == (422, "SAMPLING_FRAMES_RANGE")


def test_the_wrong_kind_of_input_or_model_is_refused(client, project, headers, run_jobs, clips):
    asset = put_video(client, project, headers, run_jobs, clips["plain"])
    photo = upload(client, project, headers, "p.jpg", media_helpers.image_bytes("JPEG", seed=3)).json()["asset"]
    run_jobs()
    as_photo = analyze(client, project, headers, photo["id"], pipeline="video", sampling=SAMPLING)
    assert (as_photo.status_code, as_photo.json()["code"]) == (422, "PIPELINE_INCOMPATIBLE")
    ground_on_video = analyze(client, project, headers, asset["id"], pipeline="ground_image")
    assert (ground_on_video.status_code, ground_on_video.json()["code"]) == (422, "PIPELINE_INCOMPATIBLE")
    sampled_photo = analyze(client, project, headers, photo["id"], sampling=SAMPLING)
    assert sampled_photo.status_code == 422 and sampled_photo.json()["code"] == "SAMPLING_NOT_APPLICABLE"
    unknown = start(client, project, headers, asset, model={"name": "no-such-model", "version": "1"})
    assert (unknown.status_code, unknown.json()["code"]) == (422, "MODEL_UNKNOWN")
    assert start(client, project, headers, asset, model={"name": "cloud-detector", "version": "test"}).status_code in (
        409,
        422,
    )
    assert rows("SELECT count(*) FROM job WHERE kind = 'analyze'")[0][0] == 0


def test_the_same_analysis_cannot_be_queued_twice_while_active(client, project, headers, run_jobs, clips):
    asset = put_video(client, project, headers, run_jobs, clips["plain"])
    assert start(client, project, headers, asset).status_code == 202
    again = start(client, project, headers, asset)
    assert (again.status_code, again.json()["code"]) == (409, "JOB_ALREADY_ACTIVE")
    assert (
        start(client, project, headers, asset, sampling={"interval_s": 2.0, "max_frames": 10}).status_code == 202
    )  # a different choice


def test_job_creation_is_audited_with_the_sampling_that_was_chosen(client, project, headers, run_jobs, clips):
    asset = put_video(client, project, headers, run_jobs, clips["plain"])
    start(client, project, headers, asset, sampling={"interval_s": 2.0, "max_frames": 7, "start_s": 1.0})
    details = rows("SELECT details FROM audit_event WHERE action = 'job.create' AND details->>'pipeline' = 'video'")[0][
        0
    ]
    assert details["sampling"] == {"interval_s": 2.0, "max_frames": 7, "start_s": 1.0, "end_s": None}
    assert details["model"] == {"name": "stub-detector", "version": "test"} and details["asset_id"] == asset["id"]


# --- the plan -------------------------------------------------------------------------------------------------------


def test_the_plan_says_how_many_frames_a_choice_would_take_without_running_anything(
    client, project, headers, run_jobs, clips
):
    asset = put_video(client, project, headers, run_jobs, clips["plain"])
    before = counts()
    plan = client.get(video_url(project, asset, "/plan"), params={"interval_s": 2.0, "max_frames": 10}).json()
    # from the container's duration (6 s at 10 fps: the last frame is at 5.9 s)
    assert plan["planned_frames"] == 3 and plan["wanted_ms"] == [0, 2000, 4000]
    exact = client.get(video_url(project, asset, "/plan"), params={"interval_s": 1.0, "max_frames": 10}).json()
    assert exact["planned_frames"] == 6  # the same number the job then takes
    assert plan["sampling"] == {"interval_s": 2.0, "max_frames": 10, "start_s": 0.0, "end_s": None}
    assert "container" in plan["basis"] and "not seen" in plan["policy"] and plan["max_frames_ceiling"] == 300
    capped = client.get(video_url(project, asset, "/plan"), params={"interval_s": 0.5, "max_frames": 5})
    assert (capped.status_code, capped.json()["code"]) == (422, "SAMPLING_EXCEEDS_LIMIT")
    assert counts() == before


def test_the_plan_refuses_what_the_job_would_refuse_and_needs_a_video_of_this_project(
    client, project, headers, run_jobs, clips
):
    asset = put_video(client, project, headers, run_jobs, clips["plain"])
    bad = client.get(video_url(project, asset, "/plan"), params={"interval_s": 0.01, "max_frames": 5})
    assert (bad.status_code, bad.json()["code"]) == (422, "SAMPLING_INTERVAL_RANGE")
    photo = upload(client, project, headers, "p.jpg", media_helpers.image_bytes("JPEG", seed=3)).json()["asset"]
    not_video = client.get(video_url(project, photo, "/plan"), params={"interval_s": 1, "max_frames": 5})
    assert (not_video.status_code, not_video.json()["code"]) == (409, "NOT_A_VIDEO")


# --- accurate frames and times --------------------------------------------------------------------------------------


def test_a_run_stores_the_sampled_frames_with_exact_numbers_times_and_findings(
    client, project, headers, run_jobs, clips
):
    asset = put_video(client, project, headers, run_jobs, clips["plain"])
    job = run_to_end(client, project, headers, run_jobs, asset)
    assert job["status"] == "needs_review" and job["error"] is None
    assert (
        job["result"]["frames_analysed"] == 6
        and job["result"]["frames_failed"] == 0
        and job["result"]["observations"] == 12
    )

    out = frames_of(client, project, asset, job)
    # 6 s at 10 fps = frames 0..59; one sample a second: frames 0, 10, 20, 30, 40, 50 at 0, 1000, ... ms
    assert [(f["frame_index"], f["timestamp_ms"], f["sample_target_ms"]) for f in out["frames"]] == [
        (i * 10, i * 1000, i * 1000) for i in range(6)
    ]
    assert all(f["finding_count"] == 2 and (f["width"], f["height"]) == (96, 72) for f in out["frames"])
    assert out["findings_total"] == 12 and out["failed"] == [] and out["frames_in_file"] == 60
    assert out["source_sha256"] == asset["sha256"] and out["sampling"]["interval_s"] == 1.0
    assert (
        "not seen" in out["policy"]
        and "Frame number" in out["frame_numbering"]
        and "Box coordinates" in out["coordinates"]
    )
    assert "not confirmed findings" in out["note"]


def test_a_container_start_offset_does_not_leak_into_timestamps_and_the_container_time_is_kept(
    client, project, headers, run_jobs, clips
):
    asset = put_video(client, project, headers, run_jobs, clips["offset"])  # the container's first frame is at 2.5 s
    job = run_to_end(client, project, headers, run_jobs, asset, sampling={"interval_s": 2.0, "max_frames": 10})
    assert job["status"] == "needs_review", job
    out = frames_of(client, project, asset, job)
    assert [f["timestamp_ms"] for f in out["frames"]] == [0, 2000, 4000]  # the clock a player shows
    details = rows("SELECT details FROM artifact WHERE kind = 'video_frame' ORDER BY (details->>'frame_index')::int")
    assert [round(d[0]["container_pts_s"], 3) for d in details] == [2.5, 4.5, 6.5]
    assert [d[0]["relative_time_s"] for d in details] == [0.0, 2.0, 4.0]


def test_frame_numbers_follow_the_frames_not_a_frame_rate_estimate(client, project, headers, run_jobs, clips):
    asset = put_video(
        client, project, headers, run_jobs, clips["ntsc"]
    )  # 29.97 fps: 1 s falls between frames 29 and 30
    job = run_to_end(client, project, headers, run_jobs, asset, sampling={"interval_s": 1.0, "max_frames": 10})
    frames = frames_of(client, project, asset, job)["frames"]
    # The frame on screen at 1.000 s is frame 29 (at 0.9677 s), not 30 (1.0010 s): "the frame at or before", never
    # later.
    assert [(f["frame_index"], f["timestamp_ms"]) for f in frames] == [(0, 0), (29, 968), (59, 1969)]
    assert [f["sample_target_ms"] for f in frames] == [0, 1000, 2000]


def test_a_frame_standing_for_several_wanted_times_is_analysed_once(client, project, headers, run_jobs, clips):
    asset = put_video(client, project, headers, run_jobs, clips["slow"])  # 2 fps: a frame every 0.5 s
    job = run_to_end(client, project, headers, run_jobs, asset, sampling={"interval_s": 0.25, "max_frames": 20})
    out = frames_of(client, project, asset, job)
    assert [f["frame_index"] for f in out["frames"]] == [0, 1, 2, 3, 4, 5]
    # 0.5 s per frame, wanted every 0.25 s: each frame also stands for the wanted time 0.25 s after it
    assert [f["also_stands_for_ms"] for f in out["frames"]] == [[250], [750], [1250], [1750], [2250], []]
    assert job["result"]["frames_planned"] == 6
    run = rows("SELECT summary FROM pipeline_run")[0][0]
    assert run["frames_wanted_times"] == 11 and run["frames_analysed"] == 6


def test_a_plan_over_the_limit_fails_the_job_with_nothing_kept(client, project, headers, run_jobs, clips):
    asset = put_video(client, project, headers, run_jobs, clips["plain"])
    job = run_to_end(client, project, headers, run_jobs, asset, sampling={"interval_s": 0.2, "max_frames": 10})
    assert job["status"] == "failed" and job["error"]["code"] == "SAMPLING_EXCEEDS_LIMIT"
    assert "limit of 10" in job["error"]["message"]
    assert rows("SELECT count(*) FROM pipeline_run")[0][0] == 0 and rows("SELECT count(*) FROM observation")[0][0] == 0
    assert frame_files() == []


# --- evidence integrity and provenance ------------------------------------------------------------------------------


def test_every_finding_names_its_frame_and_the_frame_checksum_and_the_source_video(
    client, project, headers, run_jobs, clips
):
    asset = put_video(client, project, headers, run_jobs, clips["plain"])
    job = run_to_end(client, project, headers, run_jobs, asset)
    run_id = job["result"]["run_id"]
    out = frames_of(client, project, asset, job)
    by_index = {f["frame_index"]: f for f in out["frames"]}

    for view in findings(client, project, run_id):
        o = view["observation"]
        evidence = o["evidence"][0]
        frame = by_index[evidence["frame_index"]]
        assert o["pipeline"] == "video" and o["asset_id"] == asset["id"] and o["model"]["run_id"] == run_id
        assert o["model"] == {"name": "stub-detector", "version": "test", "run_id": run_id}
        assert evidence["timestamp_ms"] == frame["timestamp_ms"] and evidence["asset_id"] == asset["id"]
        assert evidence["source_excerpt_or_metadata_key"] == f"video_frame_sha256:{frame['sha256']}"
        region = evidence["region_px"]
        assert (region["image_width"], region["image_height"]) == (frame["width"], frame["height"])
        x0, y0, x1, y1 = region["coordinates"]
        assert 0 <= x0 < x1 <= frame["width"] and 0 <= y0 < y1 <= frame["height"]
        assert o["provenance"]["source_checksum"] == asset["sha256"]  # the video, not the frame
        step = o["provenance"]["transformations"][0]
        assert (step["type"], step["frame_index"], step["output_sha256"]) == (
            "frame_extraction",
            evidence["frame_index"],
            frame["sha256"],
        )
        assert "UNCALIBRATED_SCORE" in o["quality_flags"] and "NOT_GEOREFERENCED" in o["quality_flags"]
        assert o["geospatial"]["geometry"] is None  # a video finding never gets a location
        assert "same object" in o["claim"]["description"] and "other frame" in o["claim"]["description"]
        assert view["current"]["status"] == "needs_review" and o["score"]["calibrated"] is False


def test_the_stored_frame_is_the_image_that_was_analysed_and_matches_its_recorded_checksum(
    client, project, headers, run_jobs, clips
):
    asset = put_video(client, project, headers, run_jobs, clips["plain"])
    job = run_to_end(client, project, headers, run_jobs, asset)
    run_id = job["result"]["run_id"]
    for frame in frames_of(client, project, asset, job)["frames"]:
        image = client.get(video_url(project, asset, f"/runs/{run_id}/frames/{frame['frame_index']}/image"))
        assert image.status_code == 200 and image.headers["content-type"] == "image/jpeg"
        assert hashlib.sha256(image.content).hexdigest() == frame["sha256"] == image.headers["etag"].strip('"')
        assert len(image.content) == frame["size_bytes"] and image.content[:2] == b"\xff\xd8"
    missing = client.get(video_url(project, asset, f"/runs/{run_id}/frames/7/image"))
    assert (missing.status_code, missing.json()["code"]) == (404, "FRAME_NOT_FOUND")


def test_the_run_records_the_model_the_policy_the_tool_and_how_frames_are_numbered(
    client, project, headers, run_jobs, clips
):
    asset = put_video(client, project, headers, run_jobs, clips["plain"])
    run_to_end(client, project, headers, run_jobs, asset)
    (run,) = rows("SELECT pipeline, model_name, model_version, settings, summary, library_versions FROM pipeline_run")
    assert (run[0], run[1], run[2]) == ("video", "stub-detector", "test")
    assert run[3]["sampling"] == {"interval_s": 1.0, "max_frames": 10, "start_s": 0.0, "end_s": None}
    assert "not seen" in run[3]["sampling_policy"] and "Frame number" in run[3]["frame_numbering"]
    assert run[4]["video_frames_in_file"] == 60 and run[4]["frames_planned"] == 6
    assert run[5]["ffmpeg"] and "ffmpeg" in run[5]["ffmpeg"].lower()


def test_a_tampered_stored_video_is_not_analysed(client, project, headers, run_jobs, clips):
    asset = put_video(client, project, headers, run_jobs, clips["plain"])
    path = store.absolute_path(rows("SELECT storage_path FROM blob")[0][0])
    path.chmod(0o666)
    with open(path, "ab") as handle:
        handle.write(b"tampered")
    job = run_to_end(client, project, headers, run_jobs, asset)
    assert job["status"] == "failed" and job["error"]["code"] == "STORAGE_ERROR"
    assert rows("SELECT count(*) FROM pipeline_run")[0][0] == 0 and frame_files() == []


def test_a_missing_stored_video_fails_cleanly(client, project, headers, run_jobs, clips):
    asset = put_video(client, project, headers, run_jobs, clips["plain"])
    path = store.absolute_path(rows("SELECT storage_path FROM blob")[0][0])
    path.chmod(0o666)
    path.unlink()
    job = run_to_end(client, project, headers, run_jobs, asset)
    assert job["status"] == "failed" and job["error"]["code"] == "STORAGE_ERROR"


def test_the_video_is_never_read_into_memory(client, project, headers, run_jobs, clips):
    asset = put_video(client, project, headers, run_jobs, clips["plain"])
    original = store.absolute_path(rows("SELECT storage_path FROM blob")[0][0])
    real = Path.read_bytes

    def guarded(self, *args, **kwargs):
        assert self != original, "the whole video was read into memory"
        return real(self, *args, **kwargs)

    with mock.patch.object(Path, "read_bytes", guarded):
        job = run_to_end(client, project, headers, run_jobs, asset)
    assert job["status"] == "needs_review"


# --- failures -------------------------------------------------------------------------------------------------------


def test_missing_ffmpeg_is_a_readable_failure_and_keeps_nothing(client, project, headers, run_jobs, clips):
    asset = put_video(client, project, headers, run_jobs, clips["plain"])
    with mock.patch.object(media.shutil, "which", lambda _name: None):
        job = run_to_end(client, project, headers, run_jobs, asset)
    assert job["status"] == "failed" and job["error"]["code"] == "MEDIA_TOOLING_UNAVAILABLE"
    assert "ffmpeg" in job["error"]["message"] and rows("SELECT count(*) FROM pipeline_run")[0][0] == 0


def test_a_video_whose_frame_times_cannot_be_trusted_is_refused_rather_than_numbered_by_guesswork(
    client, project, headers, run_jobs, clips
):
    asset = put_video(client, project, headers, run_jobs, clips["plain"])
    from geopoc.video.sampling import TimingUnsupported

    with mock.patch.object(
        media, "scan_timeline", side_effect=TimingUnsupported("Some frames have no presentation time.")
    ):
        job = run_to_end(client, project, headers, run_jobs, asset)
    assert job["status"] == "failed" and job["error"]["code"] == "VIDEO_TIMING_UNSUPPORTED"
    assert "presentation time" in job["error"]["message"] and rows("SELECT count(*) FROM observation")[0][0] == 0


def test_a_frame_that_cannot_be_taken_is_reported_and_the_others_are_kept(client, project, headers, run_jobs, clips):
    asset = put_video(client, project, headers, run_jobs, clips["plain"])
    real = media.extract_frame

    def flaky(path, absolute_s, origin, destination, settings):
        if abs(absolute_s - 2.0) < 1e-3:
            raise media.FrameExtractionError(
                "FRAME_EXTRACTION_FAILED", "The frame could not be decoded from the video."
            )
        return real(path, absolute_s, origin, destination, settings)

    with mock.patch.object(media, "extract_frame", flaky):
        job = run_to_end(client, project, headers, run_jobs, asset)
    assert job["status"] == "completed_with_warnings" and job["result"]["frames_failed"] == 1
    assert "1 of 6 frame(s) could not be taken" in rows("SELECT message FROM job_event ORDER BY id DESC LIMIT 1")[0][0]
    out = frames_of(client, project, asset, job)
    assert [f["frame_index"] for f in out["frames"]] == [0, 10, 30, 40, 50]
    assert out["failed"] == [
        {
            "frame_index": 20,
            "timestamp_ms": 2000,
            "code": "FRAME_EXTRACTION_FAILED",
            "message": "The frame could not be decoded from the video.",
        }
    ]
    assert out["findings_total"] == 10 and job["result"]["frames_failed"] == 1


def test_when_no_frame_can_be_taken_the_job_fails_and_leaves_nothing(client, project, headers, run_jobs, clips):
    asset = put_video(client, project, headers, run_jobs, clips["plain"])
    boom = media.FrameExtractionError(
        "FRAME_TIMESTAMP_MISMATCH", "The decoder delivered the frame at 9.0 s, not the planned 1.0 s."
    )
    with mock.patch.object(media, "extract_frame", side_effect=boom):
        job = run_to_end(client, project, headers, run_jobs, asset)
    assert job["status"] == "failed" and job["error"]["code"] == "FRAME_EXTRACTION_FAILED"
    assert "FRAME_TIMESTAMP_MISMATCH" in job["error"]["message"]
    assert all(rows(f"SELECT count(*) FROM {t}")[0][0] == 0 for t in ("pipeline_run", "observation"))
    assert rows("SELECT count(*) FROM artifact WHERE kind = 'video_frame'")[0][0] == 0 and frame_files() == []


def test_a_decoder_that_delivers_the_wrong_frame_is_caught(tmp_path, clips):
    """The extractor reads the delivered frame's time back and refuses a different one."""
    path = tmp_path / "c.mp4"
    path.write_bytes(clips["plain"])
    settings = get_settings()
    scan = media.scan_timeline(path, settings)
    absolute = scan.timeline.absolute_s(20)
    width, height, delivered = media.extract_frame(path, absolute, scan.seek_origin_s, tmp_path / "ok.jpg", settings)
    assert (width, height) == (96, 72) and abs(delivered - 2.0) < 1e-3
    with pytest.raises(media.FrameExtractionError) as caught:
        media.extract_frame(
            path, absolute, scan.seek_origin_s + 0.15, tmp_path / "wrong.jpg", settings
        )  # seeks 0.15 s early, so the decoder starts at the previous frame
    assert caught.value.code == "FRAME_TIMESTAMP_MISMATCH" and not (tmp_path / "wrong.jpg").exists()
    with pytest.raises(media.FrameExtractionError) as caught:
        media.extract_frame(path, absolute, scan.seek_origin_s, tmp_path / "ok.jpg", settings)
    assert caught.value.code == "FRAME_EXISTS"  # nothing is overwritten


def test_an_unreadable_video_gives_a_failure_not_a_crash(tmp_path):
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"not a video at all")
    from geopoc.video.sampling import TimingUnsupported

    with pytest.raises((media.FrameExtractionError, TimingUnsupported)):
        media.scan_timeline(bad, get_settings())


def test_a_failing_model_fails_the_job_and_leaves_no_findings_or_frame_files(client, project, headers, run_jobs, clips):
    asset = put_video(client, project, headers, run_jobs, clips["plain"])
    job = run_to_end(client, project, headers, run_jobs, asset, model={"name": "stub-failing", "version": "test"})
    assert job["status"] == "failed" and job["error"]["code"] == "INFERENCE_FAILED"
    assert "frame 0" in job["error"]["message"]
    assert all(rows(f"SELECT count(*) FROM {t}")[0][0] == 0 for t in ("pipeline_run", "observation"))
    assert rows("SELECT count(*) FROM artifact WHERE kind = 'video_frame'")[0][0] == 0
    assert frame_files() == []  # the frames written before the failure are removed with it


def test_a_model_that_finds_nothing_is_a_valid_result(client, project, headers, run_jobs, clips):
    asset = put_video(client, project, headers, run_jobs, clips["plain"])
    job = run_to_end(client, project, headers, run_jobs, asset, model={"name": "stub-empty", "version": "test"})
    assert job["status"] == "completed" and job["result"]["observations"] == 0 and job["result"]["frames_analysed"] == 6
    out = frames_of(client, project, asset, job)
    assert len(out["frames"]) == 6 and out["findings_total"] == 0  # the sampled frames are still kept and shown


def test_a_model_output_that_breaks_the_contract_is_reported_not_stored(client, project, headers, run_jobs, clips):
    asset = put_video(client, project, headers, run_jobs, clips["plain"])
    job = run_to_end(
        client, project, headers, run_jobs, asset, model={"name": "stub-invalid-output", "version": "test"}
    )
    assert job["status"] == "completed_with_warnings" and job["result"]["rejected_outputs"] >= 1
    assert rows("SELECT count(*) FROM observation")[0][0] == job["result"]["observations"]


# --- interruption ---------------------------------------------------------------------------------------------------


def test_a_cancelled_job_keeps_no_findings_and_removes_its_frame_files(client, project, headers, run_jobs, clips):
    asset = put_video(client, project, headers, run_jobs, clips["plain"])
    calls = {"n": 0}
    real = JobContext.progress

    def cancel_after_three_frames(self, fraction):
        calls["n"] += 1
        if calls["n"] > 3:
            raise JobCancelled
        return real(self, fraction)

    with mock.patch.object(JobContext, "progress", cancel_after_three_frames):
        job = run_to_end(client, project, headers, run_jobs, asset)
    assert job["status"] == "cancelled" and job["has_partial_outputs"] is False
    assert all(rows(f"SELECT count(*) FROM {t}")[0][0] == 0 for t in ("pipeline_run", "observation"))
    assert rows("SELECT count(*) FROM artifact WHERE kind = 'video_frame'")[0][0] == 0
    assert frame_files() == []


def test_a_stop_after_the_results_were_written_still_removes_the_frame_files(client, project, headers, run_jobs, clips):
    """The window between the handler returning and the job being finished: the rolled-back run must not leave files."""
    asset = put_video(client, project, headers, run_jobs, clips["plain"])
    from geopoc.jobs import run_job

    with mock.patch.object(run_job.queue, "finish", side_effect=[JobCancelled(), None, None]):
        job = run_to_end(client, project, headers, run_jobs, asset)
    assert all(rows(f"SELECT count(*) FROM {t}")[0][0] == 0 for t in ("pipeline_run", "observation"))
    assert frame_files() == [] and job["status"] in ("queued", "processing", "validating", "cancelled")


# --- bounded processing and progress --------------------------------------------------------------------------------


def test_progress_rises_once_per_frame_and_never_leaves_the_bounds(client, project, headers, run_jobs, clips):
    asset = put_video(client, project, headers, run_jobs, clips["plain"])
    seen: list[float] = []
    real = JobContext.progress

    def record(self, fraction):
        seen.append(fraction)
        return real(self, fraction)

    with mock.patch.object(JobContext, "progress", record):
        job = run_to_end(client, project, headers, run_jobs, asset)
    assert len(seen) == 6 and seen == sorted(seen) and 0 < seen[0] < seen[-1] < 1  # one report per sampled frame
    events = rows("SELECT message FROM job_event ORDER BY at, id")
    assert any("distinct frame(s) chosen" in e[0] for e in events)
    assert job["progress"] is not None and rows("SELECT stage FROM job_event WHERE stage IS NOT NULL")


def test_only_the_chosen_frames_are_taken_not_the_whole_video(client, project, headers, run_jobs, clips):
    asset = put_video(client, project, headers, run_jobs, clips["plain"])
    taken: list[float] = []
    real = media.extract_frame

    def spy(path, absolute_s, origin, destination, settings):
        taken.append(absolute_s)
        return real(path, absolute_s, origin, destination, settings)

    with mock.patch.object(media, "extract_frame", spy):
        run_to_end(client, project, headers, run_jobs, asset)
    assert len(taken) == 6 and len(frame_files()) == 6  # 6 of the 60 frames


# --- no network -----------------------------------------------------------------------------------------------------


def test_the_whole_analysis_makes_no_network_connection_other_than_the_local_database(
    client, project, headers, run_jobs, clips
):
    asset = put_video(client, project, headers, run_jobs, clips["plain"])
    with only_the_database_network():
        job = run_to_end(client, project, headers, run_jobs, asset)
        frames_of(client, project, asset, job)
        client.get(video_url(project, asset, "/plan"), params={"interval_s": 1, "max_frames": 5})
    assert job["status"] == "needs_review"


# --- project isolation ----------------------------------------------------------------------------------------------


def test_another_project_cannot_see_a_runs_frames_plan_or_findings(client, project, headers, run_jobs, clips):
    asset = put_video(client, project, headers, run_jobs, clips["plain"])
    job = run_to_end(client, project, headers, run_jobs, asset)
    run_id = job["result"]["run_id"]
    other = client.post("/api/v1/projects", json={"name": "Other"}, headers=headers).json()

    for tail, params in (
        (f"/runs/{run_id}/frames", None),
        (f"/runs/{run_id}/frames/0/image", None),
        ("/plan", {"interval_s": 1, "max_frames": 5}),
    ):
        through_other = client.get(
            f"/api/v1/projects/{other['id']}/assets/{asset['id']}/video-analysis{tail}", params=params
        )
        assert (through_other.status_code, through_other.json()["code"]) == (404, "ASSET_NOT_FOUND"), tail
    assert findings(client, other, run_id) == []  # the other project's listing of this run is empty

    theirs = put_video(client, other, headers, run_jobs, clips["offset"], name="theirs.mp4")
    # a run id from the first project asked for through the other project's own video is "not found" too
    crossed = client.get(video_url(other, theirs, f"/runs/{run_id}/frames"))
    assert (crossed.status_code, crossed.json()["code"]) == (404, "RUN_NOT_FOUND")
    assert client.get(video_url(project, asset, f"/runs/{UNKNOWN_UUID}/frames")).status_code == 404
    assert (
        client.get(
            f"/api/v1/projects/{UNKNOWN_UUID}/assets/{asset['id']}/video-analysis/plan",
            params={"interval_s": 1, "max_frames": 5},
        ).status_code
        == 404
    )

    # the other project can run its own analysis and sees only its own findings and frames
    own = run_to_end(client, other, headers, run_jobs, theirs, sampling={"interval_s": 2.0, "max_frames": 5})
    assert own["status"] == "needs_review"
    assert {v["observation"]["asset_id"] for v in findings(client, other, own["result"]["run_id"])} == {theirs["id"]}
    assert len(frames_of(client, project, asset, job)["frames"]) == 6


# --- human review ---------------------------------------------------------------------------------------------------


def test_a_frame_finding_is_reviewed_with_the_existing_actions_and_keeps_its_evidence(
    client, project, headers, run_jobs, clips
):
    asset = put_video(client, project, headers, run_jobs, clips["plain"])
    job = run_to_end(client, project, headers, run_jobs, asset)
    run_id = job["result"]["run_id"]
    in_frame = findings(client, project, run_id, frame_index=20)
    assert len(in_frame) == 2 and {v["observation"]["evidence"][0]["frame_index"] for v in in_frame} == {20}
    target = in_frame[0]["observation"]["observation_id"]
    others_before = {
        v["observation"]["observation_id"]: v["current"]["status"] for v in findings(client, project, run_id)
    }

    ok = client.patch(
        f"/api/v1/observations/{target}/review",
        json={"action": "confirm", "expected_revision": 0, "note": "Looks right at 2.0 s"},
        headers=headers,
    )
    assert ok.status_code == 200, ok.text
    after = {v["observation"]["observation_id"]: v for v in findings(client, project, run_id)}
    assert (
        after[target]["current"]["status"] == "verified"
        and after[target]["current"]["last_note"] == "Looks right at 2.0 s"
    )
    # nothing else changed: other findings keep their state, the stored record and its frame evidence are untouched
    assert all(after[i]["current"]["status"] == s for i, s in others_before.items() if i != target)
    record = after[target]["observation"]
    assert record["evidence"][0]["frame_index"] == 20 and record["evidence"][0]["timestamp_ms"] == 2000
    assert record["model"]["run_id"] == run_id and record["claim"]["category"].startswith("test:")
    assert rows("SELECT count(*) FROM observation_revision WHERE observation_id = :o", o=target)[0][0] == 1
    assert rows("SELECT count(*) FROM audit_event WHERE action LIKE 'observation.%'")[0][0] >= 1
    # rejecting needs a reason, as for photographs
    other = in_frame[1]["observation"]["observation_id"]
    refused = client.patch(
        f"/api/v1/observations/{other}/review", json={"action": "reject", "expected_revision": 0}, headers=headers
    )
    assert refused.status_code == 422


def test_video_findings_reach_the_report_and_the_csv_with_their_frame_and_time(
    client, project, headers, run_jobs, clips
):
    asset = put_video(client, project, headers, run_jobs, clips["plain"])
    photo = upload(client, project, headers, "p.jpg", media_helpers.image_bytes("JPEG", seed=5)).json()["asset"]
    run_jobs()
    run_to_end(client, project, headers, run_jobs, asset)
    assert analyze(client, project, headers, photo["id"]).status_code == 202
    run_jobs()

    report = client.get(f"/api/v1/projects/{project['id']}/report", headers=headers).json()
    videos = [o for o in report["observations"] if o["pipeline"] == "video"]
    assert len(videos) == 12 and {o["evidence"][0]["frame_index"] for o in videos} == {0, 10, 20, 30, 40, 50}
    assert all(o["evidence"][0]["timestamp_ms"] is not None and o["geographically_located"] is False for o in videos)
    assert any(r["pipeline"] == "video" and r["settings"]["sampling"]["interval_s"] == 1.0 for r in report["runs"])

    import csv
    import io

    table = list(
        csv.reader(
            io.StringIO(
                client.get(
                    f"/api/v1/projects/{project['id']}/exports/observations.csv", headers=headers
                ).content.decode("utf-8-sig")
            )
        )
    )
    header, body = table[0], table[1:]
    frame_col, time_col, pipe_col = (
        next(i for i, h in enumerate(header) if h.startswith(name))
        for name in ("frame_index", "video_timestamp_ms", "pipeline")
    )
    video_rows = [r for r in body if r[pipe_col] == "video"]
    assert len(video_rows) == 12 and {r[frame_col] for r in video_rows} == {"0", "10", "20", "30", "40", "50"}
    assert all(
        r[frame_col] == "" and r[time_col] == "" for r in body if r[pipe_col] == "ground_image"
    )  # photographs: blank


# --- reopening results ----------------------------------------------------------------------------------------------


def test_reopening_a_result_reads_stored_data_and_never_runs_or_writes_anything(
    client, project, headers, run_jobs, clips
):
    asset = put_video(client, project, headers, run_jobs, clips["plain"])
    job = run_to_end(client, project, headers, run_jobs, asset)
    run_id = job["result"]["run_id"]
    before, files = counts(), sorted(frame_files())

    for _ in range(2):
        frames = frames_of(client, project, asset, job)
        client.get(video_url(project, asset, f"/runs/{run_id}/frames/10/image"))
        client.get(f"/api/v1/projects/{project['id']}/observations", params={"run_id": run_id})
        client.get(f"/api/v1/projects/{project['id']}/observations", params={"run_id": run_id, "frame_index": 10})
        client.get(f"/api/v1/projects/{project['id']}/jobs", params={"asset_id": asset["id"], "kind": "analyze"})
        client.get(f"/api/v1/runs/{run_id}")
        client.get(f"/api/v1/assets/{asset['id']}")
        client.get(video_url(project, asset, "/plan"), params={"interval_s": 1, "max_frames": 10})
    assert counts() == before  # no job, run, finding, frame, review or audit row was added
    assert sorted(frame_files()) == files
    assert frames["findings_total"] == 12
    assert run_jobs() == []  # and nothing is waiting to run


def test_a_second_analysis_is_a_separate_run_and_the_first_stays_readable(client, project, headers, run_jobs, clips):
    asset = put_video(client, project, headers, run_jobs, clips["plain"])
    first = run_to_end(client, project, headers, run_jobs, asset)
    second = run_to_end(client, project, headers, run_jobs, asset, sampling={"interval_s": 2.0, "max_frames": 10})
    assert first["result"]["run_id"] != second["result"]["run_id"]
    a, b = frames_of(client, project, asset, first), frames_of(client, project, asset, second)
    assert [f["frame_index"] for f in a["frames"]] == [0, 10, 20, 30, 40, 50] and [
        f["frame_index"] for f in b["frames"]
    ] == [0, 20, 40]
    shared = {f["frame_index"] for f in b["frames"]}
    assert {f["sha256"] for f in a["frames"] if f["frame_index"] in shared} == {
        f["sha256"] for f in b["frames"]
    }  # same frames
    assert len(frame_files()) == 9  # every run keeps its own frame files
    assert len(findings(client, project, second["result"]["run_id"])) == 6


def test_findings_of_a_video_are_not_linked_across_frames_or_runs(client, project, headers, run_jobs, clips):
    """Nothing says a detection in one frame is the same object as one in another: no links, no conflicts are "
    "created."""
    asset = put_video(client, project, headers, run_jobs, clips["plain"])
    run_to_end(client, project, headers, run_jobs, asset)
    run_to_end(client, project, headers, run_jobs, asset, model={"name": "stub-detector-alt", "version": "test"})
    assert rows("SELECT count(*) FROM link_group")[0][0] == 0 and rows("SELECT count(*) FROM conflict")[0][0] == 0
    items = client.get(f"/api/v1/projects/{project['id']}/observations", params={"limit": 500}).json()
    assert items and all(i["possible_duplicate_group_ids"] == [] and i["open_conflict_ids"] == [] for i in items)


def test_the_run_data_is_valid_json_with_no_secrets_or_paths(client, project, headers, run_jobs, clips):
    asset = put_video(client, project, headers, run_jobs, clips["plain"])
    job = run_to_end(client, project, headers, run_jobs, asset)
    body = json.dumps(frames_of(client, project, asset, job)) + json.dumps(
        client.get(f"/api/v1/runs/{job['result']['run_id']}").json()
    )
    assert str(store.data_root()) not in body and "video_frames/" not in body and "\\" not in body.replace("\\n", "")
