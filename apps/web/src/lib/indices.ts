import type { IndexRun } from "../api/client";

/** How often a run's state is re-read while one is queued or running. */
export const POLL_MS = 1500;

const ACTIVE = ["queued", "validating", "processing"];
export const isRunActive = (run: Pick<IndexRun, "status">): boolean => ACTIVE.includes(run.status);

/** The four states the Studio shows, from the job's own status. Nothing here is estimated. */
export type RunPhase = "queued" | "running" | "completed" | "failed" | "cancelled";

export function runPhase(status: string): RunPhase {
  if (status === "queued" || status === "draft") return "queued";
  if (status === "validating" || status === "processing") return "running";
  if (status === "completed" || status === "completed_with_warnings" || status === "needs_review") return "completed";
  if (status === "cancelled") return "cancelled";
  return "failed";
}

export const PHASE_LABELS: Record<RunPhase, string> = {
  queued: "Queued",
  running: "Running",
  completed: "Completed",
  failed: "Failed",
  cancelled: "Cancelled",
};

export const PHASE_TONES: Record<RunPhase, "neutral" | "accent" | "ok" | "danger"> = {
  queued: "neutral",
  running: "accent",
  completed: "ok",
  failed: "danger",
  cancelled: "neutral",
};

// --- the colour ramp ----------------------------------------------------------------------------------------------

export interface Legend {
  colormap: string;
  title: string;
  low: number;
  high: number;
  stops: { value: number; color: string }[];
}

/** The legend the API describes, or null when it is missing or malformed (the map then shows no ramp). */
export function parseLegend(display: unknown): Legend | null {
  if (!display || typeof display !== "object") return null;
  const d = display as Record<string, unknown>;
  const stops = Array.isArray(d.stops)
    ? d.stops.filter(
        (s): s is { value: number; color: string } =>
          !!s && typeof (s as { value?: unknown }).value === "number" && typeof (s as { color?: unknown }).color === "string",
      )
    : [];
  if (typeof d.colormap !== "string" || typeof d.low !== "number" || typeof d.high !== "number") return null;
  if (!(d.low < d.high) || stops.length < 2) return null;
  return { colormap: d.colormap, title: typeof d.title === "string" ? d.title : d.colormap, low: d.low, high: d.high, stops };
}

/** Where a value sits along the legend bar, 0 to 100, held inside the bar. */
export function legendPosition(value: number, legend: Pick<Legend, "low" | "high">): number {
  const share = ((value - legend.low) / (legend.high - legend.low)) * 100;
  return Math.min(100, Math.max(0, share));
}

/** A CSS gradient from the same stops the tile renderer uses, so the bar and the map agree. */
export function legendGradient(legend: Legend): string {
  const parts = legend.stops.map((s) => `${s.color} ${legendPosition(s.value, legend).toFixed(1)}%`);
  return `linear-gradient(to right, ${parts.join(", ")})`;
}

/** Display settings for the tile route. They choose colours only; stored values are never changed. */
export function ndviTileQuery(legend: Pick<Legend, "colormap" | "low" | "high">): string {
  return `colormap=${encodeURIComponent(legend.colormap)}&colormap_range=${legend.low},${legend.high}`;
}

/** The existing asset tile route; the colour ramp is added by the map as a query. */
export const tileUrl = (assetId: string, georefVersion: number): string =>
  `/api/v1/assets/${assetId}/tiles/{z}/{x}/{y}.png?v=${georefVersion}`;

// --- numbers ------------------------------------------------------------------------------------------------------

export function formatValue(value: number | null | undefined, digits = 4): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "not available";
  const text = value.toFixed(digits);
  return /^-0\.0*$/.test(text) ? text.slice(1) : text; // never "-0.0000"
}

export const formatCount = (value: number | null | undefined): string =>
  value === null || value === undefined ? "not available" : value.toLocaleString("en-GB");

export const shortHash = (hash: string | null | undefined): string => (hash ? hash.slice(0, 12) : "not recorded");

export function formatAcquisition(iso: string | null | undefined): string {
  if (!iso) return "unknown";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "unknown";
  return `${date.toISOString().slice(0, 10)} ${date.toISOString().slice(11, 19)} UTC`;
}

export function formatBytes(bytes: number): string {
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(2)} GiB`;
  if (bytes >= 1024 ** 2) return `${(bytes / 1024 ** 2).toFixed(1)} MiB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KiB`;
}

/** The newest run that finished with a stored result. */
export function latestResult(runs: IndexRun[]): IndexRun | undefined {
  return runs.find((run) => run.result !== null && run.result !== undefined && runPhase(run.status) === "completed");
}

// --- whether the database can accept a run ------------------------------------------------------------------------

/**
 * The first schema revision that accepts the `derived_index` job kind (migration 0010). The API reports the database's
 * current revision in /health, so the page can tell this specific state without asking the run route anything.
 */
export const REQUIRED_SCHEMA_REVISION = 10;

/** "ready" when the reported revision is new enough, "pending" when it is older, "unknown" when it cannot be read. */
export type SetupState = "ready" | "pending" | "unknown";

export function setupState(revision: string | null | undefined): SetupState {
  if (!revision || !/^\d+$/.test(revision)) return "unknown"; // never block on something that cannot be established
  return Number(revision) >= REQUIRED_SCHEMA_REVISION ? "ready" : "pending";
}

export const SETUP_PENDING_LABEL = "Processing setup pending";
export const SETUP_PENDING_MESSAGE =
  "NDVI processing is temporarily unavailable because the database job configuration has not yet been upgraded.";
export const SETUP_PENDING_REASSURANCE = "Your selected imagery is compatible and no source data has been modified.";
