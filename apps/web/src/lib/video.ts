/**
 * Video-frame analysis in the browser: how the sampled frames and their findings are laid out and described.
 *
 * Nothing here decides anything about a video. A frame number and a timestamp are what the server stored from the video's
 * own frame times; the timeline only places them. A finding belongs to one frame, and no helper here relates findings in
 * different frames to each other.
 */
import type { Job, ObservationView, VideoFrame } from "../api/client";

export const DEFAULT_SAMPLING = { interval_s: 5, max_frames: 60 } as const;

/** "0:12.340" or "1:02:03.500": the clock a player shows. */
export function formatTimestamp(ms: number): string {
  const total = Math.max(0, Math.round(ms));
  const hours = Math.floor(total / 3_600_000);
  const minutes = Math.floor((total % 3_600_000) / 60_000);
  const seconds = (total % 60_000) / 1000;
  const sec = seconds.toFixed(3).padStart(6, "0");
  return hours > 0 ? `${hours}:${String(minutes).padStart(2, "0")}:${sec}` : `${minutes}:${sec}`;
}

/** Position on the timeline, 0-100, for a time within a span. Clamped, so a mark never leaves the track. */
export function percentAlong(ms: number, spanMs: number): number {
  if (!(spanMs > 0)) return 0;
  return Math.min(100, Math.max(0, (ms / spanMs) * 100));
}

/** The length the timeline represents: the video's duration, or failing that the last sampled frame. */
export function timelineSpan(durationMs: number | null | undefined, frames: Pick<VideoFrame, "timestamp_ms">[]): number {
  const last = frames.reduce((max, frame) => Math.max(max, frame.timestamp_ms), 0);
  return Math.max(durationMs ?? 0, last);
}

/** The sampled frame closest in time to a playback position, or null when none is within `withinMs`. */
export function nearestFrame(frames: VideoFrame[], timeMs: number, withinMs = Number.POSITIVE_INFINITY): VideoFrame | null {
  let best: VideoFrame | null = null;
  for (const frame of frames) {
    const distance = Math.abs(frame.timestamp_ms - timeMs);
    if (distance <= withinMs && (best === null || distance < Math.abs(best.timestamp_ms - timeMs))) best = frame;
  }
  return best;
}

/** Which frame a finding is in, from the finding's own evidence. Null for anything that is not a frame finding. */
export function frameOf(view: ObservationView): { frameIndex: number; timestampMs: number } | null {
  const evidence = view.observation.evidence[0];
  if (!evidence || evidence.frame_index == null || evidence.timestamp_ms == null) return null;
  return { frameIndex: evidence.frame_index, timestampMs: evidence.timestamp_ms };
}

export function isVideoJob(job: Pick<Job, "kind" | "payload">): boolean {
  return job.kind === "analyze" && (job.payload as { pipeline?: string } | null)?.pipeline === "video";
}

export function describeSampling(sampling: { interval_s: number; max_frames: number; start_s?: number | null; end_s?: number | null }): string {
  const range = `${sampling.start_s ? `from ${sampling.start_s} s ` : ""}${sampling.end_s ? `to ${sampling.end_s} s ` : ""}`;
  return `one frame every ${sampling.interval_s} s ${range}(at most ${sampling.max_frames} frames)`.replace(/\s+\(/, " (");
}

export const SAMPLING_WARNING =
  "Only the sampled frames were analysed. Anything that happens between two sampled frames, or lasts less than the interval, was not looked at.";
export const CONFIDENCE_WARNING =
  "Scores are the model's own values, not probabilities. They are not calibrated and no threshold has been validated, so every finding needs a person's review.";
export const NO_TRACKING_WARNING =
  "Frames are analysed one by one. Nothing is tracked, and the same class in two frames is not claimed to be the same object.";
export const NO_LOCATION_WARNING = "Boxes are positions in the frame image. No geographic location of the camera or of anything seen is established.";

/** Failures that depend on the request or the file: running the same job again ends the same way. */
export const NOT_RETRYABLE_VIDEO = [
  "SAMPLING_EXCEEDS_LIMIT",
  "SAMPLING_INVALID",
  "SAMPLING_NO_FRAMES",
  "SAMPLING_INTERVAL_RANGE",
  "VIDEO_TIMING_UNSUPPORTED",
  "VIDEO_TIMING_UNREADABLE",
];
