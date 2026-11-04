"""Which frames of a video are analysed, and what "the time of a frame" means.

Pure functions: no file, tool or database is touched here.

SAMPLING POLICY (uniform, bounded, never silently truncated)
  * Time is media time relative to the video's first presented frame (the first frame is at 0 s), whatever offset the
    container's own timestamps start at. This is the clock a player shows.
  * A sample is wanted every `interval_s` seconds from `start_s` (default 0) up to `end_s` (default: the last frame).
  * For each wanted time the frame analysed is the one on screen at that time: the last frame whose presentation time is
    at or before it (before the first frame, the first frame). Several wanted times can land on one frame (a slow video
    or a short interval); that frame is analysed once and the repeats are counted, not hidden.
  * `interval_s` is at least the configured minimum and `max_frames` at most the configured ceiling. If the wanted times
    would exceed `max_frames` the request is refused and says what to change. Nothing is dropped to make it fit.
  * A frame number is the frame's 0-based position in presentation order. It is only given when every frame's
    presentation time is known and distinct; otherwise the video is refused rather than numbered by guesswork.

What a sample cannot do: an event that begins and ends between two sampled frames is never seen. The interval is
chosen by
the person, and every result says so.
"""

import bisect
import math
from dataclasses import dataclass
from typing import Any

# Presentation times are printed with 6 decimals by ffprobe; this absorbs rounding without ever merging two frames.
EPSILON_S = 1e-6
DEFAULT_INTERVAL_S = 5.0
DEFAULT_MAX_FRAMES = 60
MAX_INTERVAL_S = 3600.0

# Image detectors are the only models that exist and a frame is an image, so these manifest pipelines may analyse
# frames.
FRAME_MODEL_PIPELINES = {"ground_image"}

POLICY_TEXT = (
    "Frames are taken at fixed time steps and analysed one by one. An event shorter than the step, or one that falls "
    "between two sampled frames, is not seen. A detection in one frame says nothing about any other frame: nothing is "
    "tracked, and the same class in two frames is not claimed to be the same object."
)


class SamplingError(ValueError):
    """The request cannot be turned into a bounded set of frames. `code` is stable."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class TimingUnsupported(SamplingError):
    """The video's frame times are missing or ambiguous, so frame numbers and timestamps cannot be stated honestly."""

    def __init__(self, message: str) -> None:
        super().__init__("VIDEO_TIMING_UNSUPPORTED", message)


@dataclass(frozen=True)
class SamplingPolicy:
    interval_s: float = DEFAULT_INTERVAL_S
    max_frames: int = DEFAULT_MAX_FRAMES
    start_s: float = 0.0
    end_s: float | None = None

    def validate(self, *, min_interval_s: float, frames_ceiling: int) -> "SamplingPolicy":
        for name, value in (("interval_s", self.interval_s), ("start_s", self.start_s), ("end_s", self.end_s)):
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value)
            ):
                raise SamplingError("SAMPLING_INVALID", f"{name} must be a finite number")
        if not min_interval_s <= self.interval_s <= MAX_INTERVAL_S:
            raise SamplingError(
                "SAMPLING_INTERVAL_RANGE",
                f"The interval must be between {min_interval_s:g} and {MAX_INTERVAL_S:g} seconds",
            )
        if not 1 <= self.max_frames <= frames_ceiling:
            raise SamplingError(
                "SAMPLING_FRAMES_RANGE", f"The maximum number of frames must be between 1 and {frames_ceiling}"
            )
        if self.start_s < 0:
            raise SamplingError("SAMPLING_START_RANGE", "The start time cannot be negative")
        if self.end_s is not None and self.end_s <= self.start_s:
            raise SamplingError("SAMPLING_END_RANGE", "The end time must be after the start time")
        return self

    def as_dict(self) -> dict[str, Any]:
        return {
            "interval_s": self.interval_s,
            "max_frames": self.max_frames,
            "start_s": self.start_s,
            "end_s": self.end_s,
        }


def wanted_times(policy: SamplingPolicy, last_time_s: float) -> list[float]:
    """The times (seconds, media time) the policy asks for, up to the end or the last frame. Refuses an oversized "
    "plan."""
    end = last_time_s if policy.end_s is None else min(policy.end_s, last_time_s)
    if end < policy.start_s - EPSILON_S:
        raise SamplingError("SAMPLING_NO_FRAMES", "The chosen range starts after the last frame of the video")
    count = int(math.floor((end - policy.start_s) / policy.interval_s + 1e-9)) + 1
    if count > policy.max_frames:
        raise SamplingError(
            "SAMPLING_EXCEEDS_LIMIT",
            f"An interval of {policy.interval_s:g} s over {policy.start_s:g}-{end:g} s needs {count} frames, more "
            f"than the "
            f"limit of {policy.max_frames}. Use a longer interval, a shorter range, or a higher limit (at most as "
            f"allowed).",
        )
    return [round(policy.start_s + k * policy.interval_s, 6) for k in range(count)]


@dataclass(frozen=True)
class Timeline:
    """Every frame's presentation time, in presentation order, relative to the first frame."""

    first_pts_s: float  # the container's own time of the first frame (not subtracted anywhere else)
    relative_s: tuple[float, ...]

    def __post_init__(self) -> None:
        if not self.relative_s:
            raise TimingUnsupported("The video has no frames with a presentation time.")

    @property
    def frame_count(self) -> int:
        return len(self.relative_s)

    @property
    def last_time_s(self) -> float:
        return self.relative_s[-1]

    def absolute_s(self, index: int) -> float:
        return self.first_pts_s + self.relative_s[index]


def timeline_from_pts(pts_values: list[float | None]) -> Timeline:
    """Build the timeline from packet presentation times in any order. Refuses missing or repeated times."""
    if any(v is None for v in pts_values):
        raise TimingUnsupported(
            "Some frames of this video have no presentation time, so frame numbers would be guesses."
        )
    ordered = sorted(float(v) for v in pts_values)  # type: ignore[arg-type]
    if not ordered:
        raise TimingUnsupported("The video has no frames with a presentation time.")
    if any(b - a < EPSILON_S for a, b in zip(ordered, ordered[1:], strict=False)):
        raise TimingUnsupported(
            "Two frames of this video share one presentation time, so they cannot be told apart by time."
        )
    first = ordered[0]
    return Timeline(first_pts_s=first, relative_s=tuple(round(v - first, 6) for v in ordered))


@dataclass(frozen=True)
class SelectedFrame:
    frame_index: int
    target_s: float  # the first wanted time that this frame stands for
    relative_s: float  # the frame's own presentation time
    absolute_s: float  # the container's own time, used to seek to it
    timestamp_ms: int  # relative_s in whole milliseconds
    also_stands_for_s: tuple[float, ...] = ()  # later wanted times that fell on the same frame


def select_frames(policy: SamplingPolicy, timeline: Timeline) -> list[SelectedFrame]:
    """The distinct frames to analyse. A frame hit by several wanted times appears once."""
    targets = wanted_times(policy, timeline.last_time_s)
    chosen: dict[int, list[float]] = {}
    for target in targets:
        index = max(0, bisect.bisect_right(timeline.relative_s, target + EPSILON_S) - 1)
        chosen.setdefault(index, []).append(target)
    frames = []
    for index, hits in sorted(chosen.items()):
        relative = timeline.relative_s[index]
        frames.append(
            SelectedFrame(
                frame_index=index,
                target_s=hits[0],
                relative_s=relative,
                absolute_s=timeline.absolute_s(index),
                timestamp_ms=int(math.floor(relative * 1000 + 0.5)),
                also_stands_for_s=tuple(hits[1:]),
            )
        )
    return frames
