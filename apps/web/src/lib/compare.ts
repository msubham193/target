/**
 * Comparing several photographs of one project, from records the API already stores: the capture time the file
 * declared, the person-confirmed camera position, and the model's findings with their review state.
 *
 * Nothing here is invented. A missing time or position stays missing and is listed as a gap. A camera position is where
 * a photograph was recorded as taken; it is never an object's position. No relationship between findings in different
 * photographs is computed: a shared label, or two nearby camera positions, are not evidence of the same object.
 */
import type { Asset, ObservationView, PhotoLocationItem } from "../api/client";
import { categoryLabel } from "./jobs";

export type CaptureTime = { state: "known"; iso: string } | { state: "unknown"; reason: string };

export type CameraPosition =
  | {
      state: "confirmed";
      lat: number;
      lon: number;
      source: string | null;
      confirmedBy: string | null;
      confirmedAt: string | null;
    }
  | { state: "candidate" }
  | { state: "none" };

export interface FindingsSummary {
  total: number;
  /** The run the figures describe: the most recent analysis. */
  runAt: string;
  earlierRuns: number;
  byStatus: Record<string, number>;
  reviewed: number;
  unreviewed: number;
  categories: { category: string; count: number }[];
}

/** verified, rejected or inconclusive: a person concluded something. Same rule as the findings report. */
export const REVIEWED_STATUSES = ["verified", "rejected", "inconclusive"];

export function captureTime(asset: Asset): CaptureTime {
  if (asset.acquisition_time) return { state: "known", iso: asset.acquisition_time };
  const reason = (asset.unknowns as Record<string, unknown> | undefined)?.acquisition_time;
  return { state: "unknown", reason: typeof reason === "string" && reason ? reason : "No capture time is stored for this photograph." };
}

export function cameraPosition(item: PhotoLocationItem | undefined): CameraPosition {
  if (!item) return { state: "none" };
  if (item.state === "confirmed" && item.latitude != null && item.longitude != null) {
    return {
      state: "confirmed",
      lat: item.latitude,
      lon: item.longitude,
      source: item.source,
      confirmedBy: item.confirmed_by,
      confirmedAt: item.confirmed_at,
    };
  }
  if (item.state === "candidate") return { state: "candidate" };
  return { state: "none" };
}

/** The most recent analysis run among a photograph's findings, with its findings, or null when none has run. */
export function latestRun(views: ObservationView[]): { runId: string; at: string; views: ObservationView[]; runs: number } | null {
  let best: { runId: string; at: string } | null = null;
  const runs = new Set<string>();
  for (const view of views) {
    const runId = view.observation.model?.run_id;
    if (!runId) continue;
    runs.add(runId);
    const at = view.observation.provenance.created_at;
    if (!best || at > best.at) best = { runId, at };
  }
  if (!best) return null;
  const chosen = best;
  return { runId: chosen.runId, at: chosen.at, views: views.filter((v) => v.observation.model?.run_id === chosen.runId), runs: runs.size };
}

/** null: this photograph has not been analysed (no findings stored). */
export function summarizeFindings(views: ObservationView[]): FindingsSummary | null {
  const run = latestRun(views);
  if (!run) return null;
  const byStatus: Record<string, number> = {};
  const counts = new Map<string, number>();
  for (const { current } of run.views) {
    byStatus[current.status] = (byStatus[current.status] ?? 0) + 1;
    const label = categoryLabel(current.category);
    counts.set(label, (counts.get(label) ?? 0) + 1);
  }
  const reviewed = run.views.filter((v) => REVIEWED_STATUSES.includes(v.current.status)).length;
  return {
    total: run.views.length,
    runAt: run.at,
    earlierRuns: run.runs - 1,
    byStatus,
    reviewed,
    unreviewed: run.views.length - reviewed,
    categories: [...counts.entries()].map(([category, count]) => ({ category, count })).sort((a, b) => b.count - a.count || a.category.localeCompare(b.category)),
  };
}

export interface CompareInput {
  asset: Asset;
  location: PhotoLocationItem | undefined;
  /** undefined while the findings are still loading. */
  observations: ObservationView[] | undefined;
}

export interface ComparedPhoto {
  assetId: string;
  filename: string;
  asset: Asset;
  time: CaptureTime;
  position: CameraPosition;
  /** undefined: not loaded yet. null: loaded and the photograph has not been analysed. */
  findings: FindingsSummary | null | undefined;
  /** What is missing or unresolved, in words. */
  gaps: string[];
}

export interface TimeInterval {
  fromId: string;
  toId: string;
  seconds: number;
}

export interface CameraDistance {
  aId: string;
  bId: string;
  metres: number;
}

export interface Comparison {
  photos: ComparedPhoto[];
  /** Photographs with a stored capture time, earliest first. */
  timeline: { assetId: string; iso: string }[];
  /** Between consecutive photographs in the timeline. Only photographs with a stored time take part. */
  intervals: TimeInterval[];
  /** Between the confirmed camera positions of every pair that has both. */
  distances: CameraDistance[];
}

const EARTH_RADIUS_M = 6_371_008.8;

/** Great-circle distance between two WGS 84 points, in metres. A spherical approximation, accurate to ~0.5 %. */
export function distanceMetres(a: { lat: number; lon: number }, b: { lat: number; lon: number }): number {
  const rad = (degrees: number) => (degrees * Math.PI) / 180;
  const dLat = rad(b.lat - a.lat);
  const dLon = rad(b.lon - a.lon);
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(rad(a.lat)) * Math.cos(rad(b.lat)) * Math.sin(dLon / 2) ** 2;
  return 2 * EARTH_RADIUS_M * Math.asin(Math.min(1, Math.sqrt(h)));
}

export function comparePhotos(inputs: CompareInput[]): Comparison {
  const photos: ComparedPhoto[] = inputs.map(({ asset, location, observations }) => {
    const time = captureTime(asset);
    const position = cameraPosition(location);
    const findings = observations === undefined ? undefined : summarizeFindings(observations);
    const gaps: string[] = [];
    if (time.state === "unknown") gaps.push(`No capture time: ${time.reason}`);
    if (position.state === "candidate") gaps.push("A GPS position in the file has not been confirmed, so no location is used.");
    if (position.state === "none") gaps.push("No location is recorded.");
    if (findings === null) gaps.push("Not analysed yet.");
    if (findings && findings.unreviewed > 0) gaps.push(`${findings.unreviewed} finding(s) not yet reviewed.`);
    return { assetId: asset.id, filename: asset.original_filename, asset, time, position, findings, gaps };
  });

  const timeline = photos
    .flatMap((p) => (p.time.state === "known" ? [{ assetId: p.assetId, iso: p.time.iso }] : []))
    .sort((a, b) => new Date(a.iso).getTime() - new Date(b.iso).getTime() || a.assetId.localeCompare(b.assetId));
  const intervals: TimeInterval[] = [];
  for (let i = 1; i < timeline.length; i++) {
    const from = timeline[i - 1]!;
    const to = timeline[i]!;
    intervals.push({ fromId: from.assetId, toId: to.assetId, seconds: (new Date(to.iso).getTime() - new Date(from.iso).getTime()) / 1000 });
  }

  const distances: CameraDistance[] = [];
  for (let i = 0; i < photos.length; i++) {
    for (let j = i + 1; j < photos.length; j++) {
      const a = photos[i]!;
      const b = photos[j]!;
      if (a.position.state === "confirmed" && b.position.state === "confirmed") {
        distances.push({ aId: a.assetId, bId: b.assetId, metres: distanceMetres(a.position, b.position) });
      }
    }
  }
  return { photos, timeline, intervals, distances };
}

/** "3 min 20 s" / "2 h 5 min" / "0 s". */
export function formatInterval(seconds: number): string {
  const total = Math.round(Math.abs(seconds));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h > 0) return `${h} h ${m} min`;
  if (m > 0) return `${m} min ${s} s`;
  return `${s} s`;
}
