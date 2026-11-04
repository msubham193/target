"""The two things the frame pipeline asks of FFmpeg: every frame's presentation time, and one exact frame as a JPEG.

Both run the locally installed ffprobe/ffmpeg as child processes. The video is never read into this process's memory and
nothing is sent anywhere: no network option is used and no URL is ever given to either tool.
"""

import re
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from geopoc.config import Settings
from geopoc.video.sampling import Timeline, TimingUnsupported, timeline_from_pts

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
JPEG_QUALITY = 2  # ffmpeg's mjpeg scale: 2 is high quality (about 95 %)
SEEK_MARGIN_S = 0.0005  # seek half a millisecond early so rounding can never land on the next frame
MATCH_TOLERANCE_S = 0.0006  # the delivered frame's time must match the planned one this closely
_SHOWINFO = re.compile(r"pts_time:(-?[0-9.]+)")


class MediaToolingUnavailable(RuntimeError):
    pass


class FrameExtractionError(RuntimeError):
    """One frame could not be taken. The others may still be fine. `code` is stable."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def tool(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise MediaToolingUnavailable(f"{name} is not installed or not on PATH.")
    return path


def tool_version(name: str) -> str | None:
    """The first line of `<tool> -version`, for the run's provenance. None if it cannot be asked."""
    try:
        done = subprocess.run(
            [tool(name), "-version"], capture_output=True, text=True, timeout=15, creationflags=_NO_WINDOW, check=False
        )
    except (MediaToolingUnavailable, subprocess.TimeoutExpired, OSError):
        return None
    return done.stdout.splitlines()[0].strip() if done.stdout.strip() else None


@dataclass(frozen=True)
class Scan:
    timeline: Timeline
    seek_origin_s: float  # the container's start time: `-ss` is counted from here


def _float(text: str) -> float | None:
    try:
        value = float(text)
    except ValueError:
        return None  # "N/A"
    return value if value == value and abs(value) != float("inf") else None


def scan_timeline(path: Path, settings: Settings) -> Scan:
    """Presentation time of every video packet, without decoding. Streams the tool's output line by line."""
    ffprobe = tool("ffprobe")
    origin = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "format=start_time", "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True,
        text=True,
        timeout=settings.media_tool_timeout_s,
        creationflags=_NO_WINDOW,
        check=False,
    )
    seek_origin = (
        _float(origin.stdout.strip().splitlines()[0]) if origin.returncode == 0 and origin.stdout.strip() else None
    )

    process = subprocess.Popen(
        [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "packet=pts_time",
            "-of",
            "csv=p=0",
            str(path),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        creationflags=_NO_WINDOW,
    )
    timer = threading.Timer(settings.video_packet_scan_timeout_s, process.kill)
    timer.start()
    values: list[float | None] = []
    try:
        assert process.stdout is not None
        for line in process.stdout:
            field = line.strip().split(",")[0]
            if not field:
                continue
            values.append(_float(field))
            if len(values) > settings.video_analysis_max_packets:
                process.kill()
                raise TimingUnsupported(
                    f"The video has more than {settings.video_analysis_max_packets:,} frames; it is too long to "
                    f"analyse here."
                )
        process.wait()
    finally:
        timer.cancel()
        if process.poll() is None:
            process.kill()
    if process.returncode not in (0, None):
        raise FrameExtractionError(
            "VIDEO_TIMING_UNREADABLE", "The video's frame times could not be read (or timed out)."
        )
    timeline = timeline_from_pts(values)
    return Scan(timeline=timeline, seek_origin_s=seek_origin if seek_origin is not None else 0.0)


def extract_frame(
    path: Path, absolute_s: float, seek_origin_s: float, destination: Path, settings: Settings
) -> tuple[int, int, float]:
    """Write the frame presented at `absolute_s` (container time) to `destination` as a JPEG.

    Returns (width, height, presentation time of the frame actually delivered). The delivered frame's own time is
    read back
    from the decoder and compared with the plan: a different frame is an error, never a quietly different result.
    `destination` must not exist.
    """
    ffmpeg = tool("ffmpeg")
    if destination.exists():
        raise FrameExtractionError("FRAME_EXISTS", "The frame file already exists; nothing was overwritten.")
    seek = max(0.0, absolute_s - seek_origin_s - SEEK_MARGIN_S)
    command = [
        ffmpeg, "-nostdin", "-hide_banner", "-v", "info", "-copyts", "-ss", f"{seek:.6f}", "-i", str(path),
        "-map", "0:v:0", "-frames:v", "1", "-vf", "showinfo", "-q:v", str(JPEG_QUALITY), "-n", "-f", "image2",
        str(destination),
    ]  # fmt: skip
    try:
        done = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=settings.media_tool_timeout_s,
            creationflags=_NO_WINDOW,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        _discard(destination)
        raise FrameExtractionError("FRAME_TIMEOUT", "Taking the frame took longer than the time limit.") from exc
    if done.returncode != 0 or not destination.is_file() or destination.stat().st_size == 0:
        _discard(destination)
        raise FrameExtractionError("FRAME_EXTRACTION_FAILED", "The frame could not be decoded from the video.")
    match = _SHOWINFO.search(done.stderr)
    if not match:
        _discard(destination)
        raise FrameExtractionError(
            "FRAME_TIME_UNKNOWN", "The decoder did not report the time of the frame it delivered."
        )
    delivered = float(match.group(1))
    if abs(delivered - absolute_s) > MATCH_TOLERANCE_S:
        _discard(destination)
        raise FrameExtractionError(
            "FRAME_TIMESTAMP_MISMATCH",
            f"The decoder delivered the frame at {delivered:.4f} s, not the planned {absolute_s:.4f} s.",
        )
    try:
        with Image.open(destination) as picture:
            picture.load()
            size = picture.size
    except (UnidentifiedImageError, OSError) as exc:
        _discard(destination)
        raise FrameExtractionError("FRAME_UNREADABLE", "The extracted frame is not a readable image.") from exc
    return size[0], size[1], delivered


def _discard(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)  # our own partial output, created a moment ago
    except OSError:
        pass
