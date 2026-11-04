import type { Job, ObservationView, VideoFrame } from "../api/client";
import {
  describeSampling,
  formatTimestamp,
  frameOf,
  isVideoJob,
  nearestFrame,
  NOT_RETRYABLE_VIDEO,
  percentAlong,
  timelineSpan,
} from "./video";

const frame = (index: number, ms: number): VideoFrame =>
  ({ frame_index: index, timestamp_ms: ms, finding_count: 0, image_url: "/x", sha256: "a".repeat(64) }) as unknown as VideoFrame;

describe("timestamps", () => {
  it("shows the clock a player shows, with milliseconds", () => {
    expect(formatTimestamp(0)).toBe("0:00.000");
    expect(formatTimestamp(2000)).toBe("0:02.000");
    expect(formatTimestamp(12_340)).toBe("0:12.340");
    expect(formatTimestamp(61_005)).toBe("1:01.005");
    expect(formatTimestamp(3_723_500)).toBe("1:02:03.500");
  });

  it("never shows a negative or fractional-millisecond time", () => {
    expect(formatTimestamp(-5)).toBe("0:00.000");
    expect(formatTimestamp(1968.6)).toBe("0:01.969");
  });
});

describe("the timeline", () => {
  it("places a time along the track and keeps every mark on it", () => {
    expect(percentAlong(0, 10_000)).toBe(0);
    expect(percentAlong(2500, 10_000)).toBe(25);
    expect(percentAlong(10_000, 10_000)).toBe(100);
    expect(percentAlong(99_000, 10_000)).toBe(100);
    expect(percentAlong(-5, 10_000)).toBe(0);
    expect(percentAlong(5, 0)).toBe(0); // an unknown span puts nothing off the track
  });

  it("spans the video's duration, or the last sampled frame when the duration is unknown", () => {
    const frames = [frame(0, 0), frame(30, 3000)];
    expect(timelineSpan(6000, frames)).toBe(6000);
    expect(timelineSpan(null, frames)).toBe(3000);
    expect(timelineSpan(1000, frames)).toBe(3000); // a frame is never beyond the end of the track
  });

  it("finds the sampled frame nearest to a playback position, if one is close enough", () => {
    const frames = [frame(0, 0), frame(10, 1000), frame(20, 2000)];
    expect(nearestFrame(frames, 1400)?.frame_index).toBe(10);
    expect(nearestFrame(frames, 1600)?.frame_index).toBe(20);
    expect(nearestFrame(frames, 5000, 500)).toBeNull();
    expect(nearestFrame([], 10)).toBeNull();
  });
});

describe("which frame a finding is in", () => {
  const evidence = (frame_index: number | null, timestamp_ms: number | null) =>
    ({ observation: { evidence: [{ frame_index, timestamp_ms }] } }) as unknown as ObservationView;

  it("comes from the finding's own evidence and is null for anything else", () => {
    expect(frameOf(evidence(20, 2000))).toEqual({ frameIndex: 20, timestampMs: 2000 });
    expect(frameOf(evidence(0, 0))).toEqual({ frameIndex: 0, timestampMs: 0 }); // frame 0 at 0 ms is a real frame
    expect(frameOf(evidence(null, null))).toBeNull(); // a photograph finding
    expect(frameOf(evidence(3, null))).toBeNull();
    expect(frameOf({ observation: { evidence: [] } } as unknown as ObservationView)).toBeNull();
  });
});

describe("jobs and sampling in words", () => {
  it("recognises a video analysis job by its payload", () => {
    expect(isVideoJob({ kind: "analyze", payload: { pipeline: "video" } } as unknown as Job)).toBe(true);
    expect(isVideoJob({ kind: "analyze", payload: { pipeline: "ground_image" } } as unknown as Job)).toBe(false);
    expect(isVideoJob({ kind: "preview", payload: {} } as unknown as Job)).toBe(false);
  });

  it("describes the sampling so a person can check it", () => {
    expect(describeSampling({ interval_s: 5, max_frames: 60 })).toBe("one frame every 5 s (at most 60 frames)");
    expect(describeSampling({ interval_s: 2, max_frames: 10, start_s: 4, end_s: 20 })).toBe("one frame every 2 s from 4 s to 20 s (at most 10 frames)");
  });

  it("lists failures that running again would repeat", () => {
    expect(NOT_RETRYABLE_VIDEO).toContain("SAMPLING_EXCEEDS_LIMIT");
    expect(NOT_RETRYABLE_VIDEO).toContain("VIDEO_TIMING_UNSUPPORTED");
  });
});
