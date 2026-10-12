"""Video validation and metadata through the locally installed ffprobe / ffmpeg. Nothing is decoded in-process."""

import json
import shutil
import subprocess
import sys
from datetime import datetime
from fractions import Fraction
from pathlib import Path
from typing import Any

from geopoc.config import Settings
from geopoc.ingestion.rules import Inspection, ToolingUnavailable, UnreadableAsset, json_safe

# container names ffprobe reports for each signature family
CONTAINER_NAMES = {"isobmff": {"mp4", "mov"}, "matroska": {"matroska"}}
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def tool_path(name: str) -> str | None:
    return shutil.which(name)


def _run(args: list[str], timeout: int) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(args, capture_output=True, timeout=timeout, creationflags=_NO_WINDOW, check=False)
    except subprocess.TimeoutExpired as exc:
        raise UnreadableAsset("ASSET_UNREADABLE", "The video could not be read within the time limit.") from exc
    except OSError as exc:
        raise ToolingUnavailable(str(exc)) from exc


def _rate(text: Any) -> float | None:
    try:
        value = Fraction(str(text))
    except (ValueError, ZeroDivisionError):
        return None
    return float(value) if value > 0 else None


def _milliseconds(*candidates: Any) -> int | None:
    for candidate in candidates:
        try:
            seconds = float(candidate)
        except (TypeError, ValueError):
            continue
        if seconds > 0:
            return round(seconds * 1000)
    return None


def _timestamp(text: Any) -> datetime | None:
    if not isinstance(text, str):
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else None


def inspect_video(path: Path, family: str, settings: Settings) -> Inspection:
    ffprobe, ffmpeg = tool_path("ffprobe"), tool_path("ffmpeg")
    if not ffprobe or not ffmpeg:
        raise ToolingUnavailable("ffprobe and ffmpeg must be installed and on PATH to validate video.")

    unreadable = UnreadableAsset("ASSET_UNREADABLE", "The file is corrupt or not a readable video.")
    probe = _run(
        [ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        settings.media_tool_timeout_s,
    )
    if probe.returncode != 0:
        raise unreadable
    try:
        info = json.loads(probe.stdout.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as exc:
        raise unreadable from exc

    container = info.get("format") or {}
    names = set(str(container.get("format_name", "")).split(","))
    if not names & CONTAINER_NAMES[family]:
        raise UnreadableAsset("ASSET_UNREADABLE", "The file's container does not match its type.")
    streams = info.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None or not video.get("width") or not video.get("height"):
        raise UnreadableAsset("ASSET_UNREADABLE", "The file contains no readable video stream.")

    # Decoding one frame is the readability proof; the whole file is never decoded during ingestion.
    decode = _run(
        [
            ffmpeg,
            "-v",
            "error",
            "-xerror",
            "-i",
            str(path),
            "-map",
            "0:v:0",
            "-frames:v",
            "1",
            "-f",
            "null",
            "-",
        ],
        settings.media_tool_timeout_s,
    )
    if decode.returncode != 0:
        raise UnreadableAsset("ASSET_UNREADABLE", "The video stream could not be decoded.")

    tags = container.get("tags") or {}
    if family == "matroska":
        detected, media_type = "Matroska", "video/x-matroska"
    elif str(tags.get("major_brand", "")).strip() == "qt":
        detected, media_type = "QuickTime", "video/quicktime"
    else:
        detected, media_type = "MP4", "video/mp4"

    duration_ms = _milliseconds(container.get("duration"), video.get("duration"))
    frame_count = int(video["nb_frames"]) if str(video.get("nb_frames", "")).isdigit() else None
    creation_time = _timestamp(tags.get("creation_time"))

    result = Inspection(detected_format=detected, media_type=media_type)
    result.video = {
        "container": str(container.get("format_name", "")),
        "video_codec": video.get("codec_name"),
        "width": int(video["width"]),
        "height": int(video["height"]),
        "duration_ms": duration_ms,
        "avg_frame_rate": video.get("avg_frame_rate"),
        "frame_rate": _rate(video.get("avg_frame_rate")),
        "time_base": video.get("time_base"),
        "frame_count": frame_count,
        "creation_time": creation_time,
        "has_audio": any(s.get("codec_type") == "audio" for s in streams),
    }
    result.metadata = json_safe(
        {
            "stream_count": len(streams),
            "pixel_format": video.get("pix_fmt"),
            "nominal_frame_rate": video.get("r_frame_rate"),
            "container_tags": {k: v for k, v in list(tags.items())[:30]},
        }
    )

    if duration_ms is None:
        result.unknowns["duration"] = "The container does not state a duration."
        result.flag("METADATA_INCOMPLETE")
    if frame_count is None:
        result.unknowns["frame_count"] = (
            "The container does not state a frame count; frames are not counted during ingestion."
        )
    if result.video["frame_rate"] is None:
        result.unknowns["frame_rate"] = "The container does not state a usable frame rate."
        result.flag("METADATA_INCOMPLETE")
    result.unknowns["acquisition_time"] = (
        "The container creation time is kept as metadata; it is not proof of when the video was captured."
        if creation_time
        else "The file carries no creation or capture time."
    )
    result.flag("TIMESTAMP_UNKNOWN")
    result.unknowns["geolocation"] = (
        "No camera pose, calibration or geospatial metadata is used; no location is inferred."
    )
    result.flag("NOT_GEOREFERENCED")
    return result
