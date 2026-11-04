"""Frame sampling and timestamp rules. Pure functions: no video, tool or database is used."""

import pytest

from geopoc.video.sampling import (
    SamplingError,
    SamplingPolicy,
    Timeline,
    TimingUnsupported,
    select_frames,
    timeline_from_pts,
    wanted_times,
)

LIMITS = {"min_interval_s": 0.2, "frames_ceiling": 300}


def policy(**over) -> SamplingPolicy:
    return SamplingPolicy(**{"interval_s": 1.0, "max_frames": 60, **over})


def uniform(count: int, rate: float = 10.0, first: float = 0.0) -> Timeline:
    return timeline_from_pts([first + i / rate for i in range(count)])


# --- the policy -----------------------------------------------------------------------------------------------------


def test_a_valid_policy_is_accepted_and_reports_itself():
    p = policy(interval_s=2.5, max_frames=10, start_s=1.0, end_s=20.0).validate(**LIMITS)
    assert p.as_dict() == {"interval_s": 2.5, "max_frames": 10, "start_s": 1.0, "end_s": 20.0}


@pytest.mark.parametrize(
    ("over", "code"),
    [
        ({"interval_s": 0.1}, "SAMPLING_INTERVAL_RANGE"),
        ({"interval_s": 0.0}, "SAMPLING_INTERVAL_RANGE"),
        ({"interval_s": 3601.0}, "SAMPLING_INTERVAL_RANGE"),
        ({"interval_s": float("nan")}, "SAMPLING_INVALID"),
        ({"interval_s": float("inf")}, "SAMPLING_INVALID"),
        ({"interval_s": True}, "SAMPLING_INVALID"),
        ({"max_frames": 0}, "SAMPLING_FRAMES_RANGE"),
        ({"max_frames": 301}, "SAMPLING_FRAMES_RANGE"),
        ({"start_s": -1.0}, "SAMPLING_START_RANGE"),
        ({"start_s": float("nan")}, "SAMPLING_INVALID"),
        ({"start_s": 5.0, "end_s": 5.0}, "SAMPLING_END_RANGE"),
        ({"start_s": 5.0, "end_s": 2.0}, "SAMPLING_END_RANGE"),
    ],
)
def test_a_policy_outside_the_configured_bounds_is_refused_with_a_code(over, code):
    with pytest.raises(SamplingError) as caught:
        policy(**over).validate(**LIMITS)
    assert caught.value.code == code


def test_the_ceilings_come_from_settings_and_can_be_lowered():
    with pytest.raises(SamplingError) as caught:
        policy(max_frames=11).validate(min_interval_s=0.2, frames_ceiling=10)
    assert caught.value.code == "SAMPLING_FRAMES_RANGE"
    with pytest.raises(SamplingError):
        policy(interval_s=0.5).validate(min_interval_s=1.0, frames_ceiling=300)


# --- the wanted times -----------------------------------------------------------------------------------------------


def test_wanted_times_are_a_fixed_step_from_the_start_up_to_the_last_frame():
    assert wanted_times(policy(interval_s=2.0), 9.0) == [0.0, 2.0, 4.0, 6.0, 8.0]
    assert wanted_times(policy(interval_s=2.0, start_s=1.0), 9.0) == [1.0, 3.0, 5.0, 7.0, 9.0]  # 9.0 is reached exactly
    assert wanted_times(policy(interval_s=2.0, end_s=5.0), 9.0) == [0.0, 2.0, 4.0]  # the end time cuts the range
    assert wanted_times(policy(interval_s=2.0, end_s=50.0), 9.0) == [
        0.0,
        2.0,
        4.0,
        6.0,
        8.0,
    ]  # but never past the video


def test_a_step_that_does_not_divide_evenly_does_not_drift():
    times = wanted_times(policy(interval_s=0.1, max_frames=300), 29.9)
    assert len(times) == 300 and times[-1] == 29.9 and times[3] == 0.3  # 0.1 + 0.1 + 0.1 style error never accumulates


def test_a_plan_over_the_frame_limit_is_refused_not_cut_short():
    with pytest.raises(SamplingError) as caught:
        wanted_times(policy(interval_s=1.0, max_frames=10), 60.0)
    assert caught.value.code == "SAMPLING_EXCEEDS_LIMIT"
    assert "61 frames" in str(caught.value) and "limit of 10" in str(caught.value)
    assert len(wanted_times(policy(interval_s=1.0, max_frames=61), 60.0)) == 61  # exactly at the limit is fine


def test_a_start_after_the_last_frame_has_nothing_to_analyse():
    with pytest.raises(SamplingError) as caught:
        wanted_times(policy(start_s=10.0), 5.0)
    assert caught.value.code == "SAMPLING_NO_FRAMES"


# --- the timeline ---------------------------------------------------------------------------------------------------


def test_the_timeline_puts_frames_in_presentation_order_whatever_order_the_container_stores_them():
    decode_order = [0.0, 0.2, 0.1, 0.4, 0.3]  # B-frames: stored out of order
    t = timeline_from_pts(decode_order)
    assert t.relative_s == (0.0, 0.1, 0.2, 0.3, 0.4) and t.frame_count == 5 and t.last_time_s == 0.4


def test_time_is_relative_to_the_first_frame_so_a_container_offset_does_not_leak_into_timestamps():
    t = timeline_from_pts([2.5, 2.6, 2.7])
    assert t.first_pts_s == 2.5 and t.relative_s == (0.0, 0.1, 0.2)
    assert t.absolute_s(2) == pytest.approx(2.7)  # the container's own time is kept for seeking


@pytest.mark.parametrize(
    "values",
    [[], [0.0, None, 0.2], [None], [0.0, 0.1, 0.1], [0.0, 0.0000001]],
)
def test_missing_empty_or_repeated_frame_times_are_refused_rather_than_guessed(values):
    with pytest.raises(TimingUnsupported) as caught:
        timeline_from_pts(values)
    assert caught.value.code == "VIDEO_TIMING_UNSUPPORTED"


# --- choosing frames ------------------------------------------------------------------------------------------------


def test_each_wanted_time_gets_the_frame_on_screen_at_that_time():
    frames = select_frames(policy(interval_s=0.25), uniform(10))  # frames every 0.1 s, 0.0 .. 0.9
    assert [(f.frame_index, f.timestamp_ms) for f in frames] == [(0, 0), (2, 200), (5, 500), (7, 700)]
    # 0.25 s -> the frame at 0.2; 0.5 -> exactly the frame at 0.5; 0.75 -> the frame at 0.7. Never a later frame.
    assert [f.target_s for f in frames] == [0.0, 0.25, 0.5, 0.75]
    assert all(f.relative_s <= f.target_s + 1e-6 for f in frames)


def test_a_frame_hit_by_several_wanted_times_is_analysed_once_and_the_repeats_are_recorded():
    frames = select_frames(policy(interval_s=0.2, max_frames=20), timeline_from_pts([0.0, 0.5, 1.0]))  # a slow video
    assert [f.frame_index for f in frames] == [0, 1, 2]
    assert frames[0].also_stands_for_s == (0.2, 0.4)  # 0.2 and 0.4 are both still showing frame 0
    assert (frames[1].target_s, frames[1].also_stands_for_s) == (0.6, (0.8,))
    assert frames[2].also_stands_for_s == () and frames[2].target_s == 1.0
    assert sum(1 + len(f.also_stands_for_s) for f in frames) == 6  # nothing is lost from the count of wanted times


def test_frame_numbers_count_presentation_order_not_decode_order_and_not_a_frame_rate_guess():
    # an irregular (variable-rate) video: 0, 0.4, 0.5, 0.6, 2.0. At 2.0 s the frame number is 4, not 2.0 * rate.
    t = timeline_from_pts([0.0, 0.5, 0.4, 0.6, 2.0])
    frames = select_frames(policy(interval_s=1.0), t)
    assert [(f.frame_index, f.timestamp_ms) for f in frames] == [(0, 0), (3, 600), (4, 2000)]


def test_a_frame_exactly_on_a_wanted_time_is_found_despite_rounding_noise():
    t = timeline_from_pts([i * (1001 / 30000) for i in range(100)])  # 29.97 fps
    frames = select_frames(policy(interval_s=0.2002, max_frames=60), t)
    for f in frames:
        assert t.relative_s[f.frame_index] == f.relative_s
    assert frames[1].frame_index == 6 and frames[1].timestamp_ms == 200  # frame 6 sits at 6 * 1001 / 30000 s = 0.2002 s


def test_timestamps_are_whole_milliseconds_rounded_half_up_and_never_negative():
    frames = select_frames(policy(interval_s=1.0), timeline_from_pts([0.0, 0.9996, 2.0]))
    assert [f.timestamp_ms for f in frames] == [0, 1000, 2000]  # 999.6 ms rounds to 1000
    assert all(f.timestamp_ms >= 0 for f in frames)


def test_the_first_frame_is_used_for_a_start_before_it_and_the_end_is_respected():
    t = uniform(50)  # 0 .. 4.9 s
    frames = select_frames(policy(interval_s=1.0, start_s=0.0, end_s=2.5), t)
    assert [f.timestamp_ms for f in frames] == [0, 1000, 2000]
    only = select_frames(policy(interval_s=1.0, start_s=4.0), t)
    assert [f.timestamp_ms for f in only] == [4000]
