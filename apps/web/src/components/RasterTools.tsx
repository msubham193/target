import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import {
  type DisplayQuery,
  getDisplay,
  type Measurement,
  type RasterTools,
  requestDisplayCopy,
  requestStatistics,
} from "../api/client";
import { formatDateTime } from "../lib/format";
import { formatArea, formatLength } from "../lib/geo";
import { JOB_STATUS_LABELS, jobTone } from "../lib/jobs";
import { Badge, Button, DescriptionList, ErrorNotice, inputClass, Loading, muted, Notice, Panel, selectClass } from "./ui";

type Band = RasterTools["bands"][number];

/** "Band 4 — near infrared (declared by the file)" or "Band 2 — no description in the file". Never inferred. */
export function bandLabel(band: Band): string {
  const description = band.description as string | null | undefined;
  const colour = band.color_interpretation as string | null | undefined;
  const declared = description || (colour && !["undefined", "gray", "grey"].includes(colour) ? `colour: ${colour}` : null);
  return `Band ${String(band.band)} — ${declared ? `${declared} (as the file declares)` : "no description in the file"}`;
}

const STRETCHES: { value: string; label: string; eightBitOnly?: boolean; needsStatistics?: boolean }[] = [
  { value: "default", label: "Default" },
  { value: "none", label: "None (values as stored, 8-bit only)", eightBitOnly: true },
  { value: "minmax", label: "Minimum to maximum", needsStatistics: true },
  { value: "p0.5-99.5", label: "0.5–99.5 percentile", needsStatistics: true },
  { value: "p1-99", label: "1–99 percentile", needsStatistics: true },
  { value: "p2-98", label: "2–98 percentile", needsStatistics: true },
  { value: "p5-95", label: "5–95 percentile", needsStatistics: true },
  { value: "manual", label: "Manual low and high values" },
];

/**
 * Band and contrast controls. They change only how pixels are drawn: stored values, statistics, measurements and
 * exports are not affected. Bands are offered by number with the file's own description, never guessed names.
 */
export function DisplayControls({
  assetId,
  tools,
  value,
  onChange,
  idPrefix = "display",
}: {
  assetId: string;
  tools: RasterTools;
  value: DisplayQuery;
  onChange: (next: DisplayQuery) => void;
  idPrefix?: string;
}) {
  const initialBands = (value.bands ?? (tools.band_count >= 3 ? "1,2,3" : "1")).split(",").map(Number);
  const [single, setSingle] = useState(initialBands.length === 1);
  const [bands, setBands] = useState<number[]>(initialBands.length === 3 ? initialBands : [1, Math.min(2, tools.band_count), Math.min(3, tools.band_count)]);
  const [grey, setGrey] = useState(initialBands.length === 1 ? initialBands[0]! : 1);
  const [stretch, setStretch] = useState(value.stretch ?? "default");
  const [lows, setLows] = useState<string[]>((value.low ?? "").split(",").filter(Boolean));
  const [highs, setHighs] = useState<string[]>((value.high ?? "").split(",").filter(Boolean));
  // Checked again once statistics exist: a stretch that needed them becomes possible.
  const resolved = useQuery({
    queryKey: ["display", assetId, value, tools.statistics !== null],
    queryFn: () => getDisplay(assetId, value),
    retry: false,
  });

  const chosen = single ? [grey] : bands;
  const eightBit = chosen.every((b) => tools.dtypes[b - 1] === "uint8");
  const hasStatistics = tools.statistics !== null;

  const apply = () => {
    onChange({
      bands: chosen.join(","),
      stretch: stretch === "default" ? undefined : stretch,
      low: stretch === "manual" ? chosen.map((_, i) => lows[i] ?? "").join(",") : undefined,
      high: stretch === "manual" ? chosen.map((_, i) => highs[i] ?? "").join(",") : undefined,
    });
  };

  const bandSelect = (id: string, label: string, current: number, set: (n: number) => void) => (
    <div key={id}>
      <label htmlFor={id} className="mb-1 block text-xs text-fg-muted">
        {label}
      </label>
      <select id={id} className={selectClass} value={current} onChange={(e) => set(Number(e.target.value))}>
        {tools.bands.map((band) => (
          <option key={String(band.band)} value={Number(band.band)}>
            {bandLabel(band)}
          </option>
        ))}
      </select>
    </div>
  );

  return (
    <Panel title="Bands and contrast" description="Display only. Stored values are never changed.">
      <div className="space-y-3">
        {tools.band_count >= 3 ? (
          <div className="flex gap-3 text-xs" role="radiogroup" aria-label="Composition">
            <label className="flex items-center gap-1.5">
              <input type="radio" className="accent-accent" name={`${idPrefix}-mode`} checked={!single} onChange={() => setSingle(false)} />
              Three bands as red, green, blue
            </label>
            <label className="flex items-center gap-1.5">
              <input type="radio" className="accent-accent" name={`${idPrefix}-mode`} checked={single} onChange={() => setSingle(true)} />
              One band in grey
            </label>
          </div>
        ) : null}
        {single || tools.band_count < 3 ? (
          bandSelect(`${idPrefix}-grey`, "Band shown in grey", grey, setGrey)
        ) : (
          <div className="grid grid-cols-1 gap-2">
            {(["Shown as red", "Shown as green", "Shown as blue"] as const).map((label, i) =>
              bandSelect(`${idPrefix}-rgb-${i}`, label, bands[i]!, (n) => setBands((b) => b.map((v, j) => (j === i ? n : v)))),
            )}
          </div>
        )}
        <div>
          <label htmlFor={`${idPrefix}-stretch`} className="mb-1 block text-xs text-fg-muted">
            Contrast stretch
          </label>
          <select id={`${idPrefix}-stretch`} className={selectClass} value={stretch} onChange={(e) => setStretch(e.target.value)}>
            {STRETCHES.map((option) => (
              <option
                key={option.value}
                value={option.value}
                disabled={(option.eightBitOnly && !eightBit) || (option.needsStatistics && !hasStatistics)}
              >
                {option.label}
                {option.needsStatistics && !hasStatistics ? " (needs band statistics)" : ""}
              </option>
            ))}
          </select>
        </div>
        {stretch === "manual" ? (
          <div className="grid grid-cols-2 gap-2">
            {chosen.map((band, i) => (
              <div key={`${band}-${i}`} className="contents">
                <div>
                  <label htmlFor={`${idPrefix}-low-${i}`} className="mb-1 block text-xs text-fg-muted">
                    Band {band} low
                  </label>
                  <input id={`${idPrefix}-low-${i}`} inputMode="decimal" className={inputClass} value={lows[i] ?? ""} onChange={(e) => setLows((l) => { const c = [...l]; c[i] = e.target.value; return c; })} />
                </div>
                <div>
                  <label htmlFor={`${idPrefix}-high-${i}`} className="mb-1 block text-xs text-fg-muted">
                    Band {band} high
                  </label>
                  <input id={`${idPrefix}-high-${i}`} inputMode="decimal" className={inputClass} value={highs[i] ?? ""} onChange={(e) => setHighs((h) => { const c = [...h]; c[i] = e.target.value; return c; })} />
                </div>
              </div>
            ))}
          </div>
        ) : null}
        <Button size="sm" onClick={apply}>
          Apply
        </Button>
        {resolved.isError ? <ErrorNotice error={resolved.error} /> : null}
        {resolved.data ? (
          <p className="text-xs text-fg-muted" role="status" aria-label="Display in use">
            Showing band{resolved.data.bands.length > 1 ? "s" : ""} {resolved.data.bands.join(", ")}: {resolved.data.description}.
          </p>
        ) : null}
      </div>
    </Panel>
  );
}

function Histogram({ band }: { band: Band }) {
  const histogram = band.histogram as { counts: number[] } | null;
  if (!histogram) return null;
  const peak = Math.max(...histogram.counts, 1);
  return (
    <svg viewBox={`0 0 ${histogram.counts.length} 20`} preserveAspectRatio="none" className="h-8 w-full" role="img" aria-label={`Histogram of band ${String(band.band)}`}>
      {histogram.counts.map((count, i) => (
        <rect key={i} x={i} y={20 - (20 * count) / peak} width={0.9} height={(20 * count) / peak} className="fill-accent/70" />
      ))}
    </svg>
  );
}

const number = (value: unknown) => (typeof value === "number" ? (Number.isInteger(value) ? String(value) : value.toPrecision(6)) : "—");

/** Per-band statistics, with how they were computed (every pixel, or a recorded sample). */
export function BandStatistics({ assetId, tools }: { assetId: string; tools: RasterTools }) {
  const queryClient = useQueryClient();
  const run = useMutation({
    mutationFn: () => requestStatistics(assetId),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["raster-tools", assetId] }),
  });
  const stats = tools.statistics as { method: string; method_note: string; bands: Band[] } | null;
  const job = tools.statistics_job;
  const active = job && ["queued", "validating", "processing"].includes(job.status);
  return (
    <Panel
      title="Band statistics"
      description={stats ? stats.method_note : "Computed in the background from the original."}
      actions={job ? <Badge tone={jobTone(job.status)}>{JOB_STATUS_LABELS[job.status] ?? job.status}</Badge> : undefined}
    >
      <div className="space-y-3">
        {job?.status === "failed" ? <Notice tone="error" title={job.error_message ?? "Statistics could not be computed"}>Code: {job.error_code}</Notice> : null}
        {stats ? (
          <ul className="space-y-3" aria-label="Statistics per band">
            {stats.bands.map((band) => {
              const p = band.percentiles as Record<string, number>;
              return (
                <li key={String(band.band)} className="space-y-1 text-xs">
                  <p className="font-medium text-fg">{bandLabel(band)}</p>
                  <DescriptionList
                    labelWidth="sm"
                    rows={[
                      ["Type", String(band.dtype)],
                      ["Minimum / maximum", `${number(band.min)} / ${number(band.max)}`],
                      ["Mean ± std", `${number(band.mean)} ± ${number(band.std)}`],
                      ["2nd / 98th percentile", p ? `${number(p["2"])} / ${number(p["98"])}` : "—"],
                      ["Valid pixels read", `${number(band.valid)} of ${number(band.pixels_read)}`],
                      ["Nodata or masked", number(band.nodata_or_masked)],
                      ["Units", (band.units as string | null) ?? muted],
                    ]}
                  />
                  <Histogram band={band} />
                </li>
              );
            })}
          </ul>
        ) : active ? (
          <Loading label="Computing statistics…" />
        ) : null}
        <Button size="sm" variant="secondary" disabled={Boolean(active) || run.isPending} onClick={() => run.mutate()}>
          {stats ? "Recompute statistics" : "Compute statistics"}
        </Button>
        {run.isError ? <ErrorNotice error={run.error} /> : null}
      </div>
    </Panel>
  );
}

/** The verified display copy used for faster drawing of large rasters. */
export function DisplayCopyPanel({ assetId, tools }: { assetId: string; tools: RasterTools }) {
  const queryClient = useQueryClient();
  const run = useMutation({
    mutationFn: () => requestDisplayCopy(assetId),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["raster-tools", assetId] }),
  });
  const copy = tools.display_copy as {
    size_bytes: number;
    build_seconds: number;
    created_at: string;
    verification: { windows_compared: number; overview_levels: number[] };
  } | null;
  const job = tools.display_copy_job;
  const active = job && ["queued", "validating", "processing"].includes(job.status);
  return (
    <Panel
      title="Display copy"
      description="A copy with reduced-resolution levels, used only to draw large rasters faster."
      actions={
        copy ? <Badge tone="ok">In use</Badge> : job ? <Badge tone={jobTone(job.status)}>{JOB_STATUS_LABELS[job.status] ?? job.status}</Badge> : undefined
      }
    >
      <div className="space-y-2 text-xs text-fg-muted">
        {copy ? (
          <DescriptionList
            labelWidth="sm"
            rows={[
              ["Verified", `${copy.verification.windows_compared} full-resolution windows identical to the original`],
              ["Overview levels", copy.verification.overview_levels.join(", ") || "none"],
              ["Size", `${(copy.size_bytes / 1024 ** 2).toFixed(1)} MB`],
              ["Built", `${formatDateTime(copy.created_at)} in ${copy.build_seconds} s`],
            ]}
          />
        ) : (
          <p>
            {tools.display_copy_needed
              ? "Recommended: this raster is large and has no overviews of its own, so zoomed-out views are slow to draw."
              : "Not needed: the raster is small, or the file already has overviews."}
          </p>
        )}
        {job?.status === "failed" ? <Notice tone="error" title={job.error_message ?? "The copy was not kept"}>Code: {job.error_code}</Notice> : null}
        <p>
          Statistics, pixel values, measurements and comparisons always read the original, which is never changed.
        </p>
        {!copy ? (
          <Button size="sm" variant="secondary" disabled={Boolean(active) || run.isPending} onClick={() => run.mutate()}>
            {active ? "Building…" : "Build display copy"}
          </Button>
        ) : null}
        {run.isError ? <ErrorNotice error={run.error} /> : null}
      </div>
    </Panel>
  );
}

/** A measurement result: the value with its method and validity, or why there is none. */
export function MeasurementResult({ result }: { result: Measurement }) {
  const value =
    result.value == null ? null : result.kind === "distance" ? formatLength(result.value) : formatArea(result.value);
  const planar = result.planar as { value: number; method: string } | null;
  return (
    <div className="space-y-1.5 text-xs" role="status" aria-label="Measurement result">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[13px] font-medium text-fg">{value ?? "No ground measurement"}</span>
        <Badge tone={result.validity === "valid" ? "ok" : "warn"}>{result.validity}</Badge>
        {result.flags.includes("CRS_USER_ASSERTED") ? <Badge tone="accent">recorded georeferencing</Badge> : null}
      </div>
      <p className="text-fg-muted">Method: {result.method}</p>
      {result.perimeter_m != null ? <p className="text-fg-muted">Perimeter: {formatLength(result.perimeter_m)}</p> : null}
      {planar ? (
        <p className="text-fg-muted">
          Planar: {result.kind === "distance" ? formatLength(planar.value) : formatArea(planar.value)} — {planar.method}
        </p>
      ) : null}
      {result.pixel_value != null ? (
        <p className="text-fg-muted">
          In the image: {result.pixel_value.toFixed(1)} {result.pixel_unit}
        </p>
      ) : null}
      {result.points_lonlat_note ? (
        <p className="text-warn" data-testid="lonlat-note">
          No longitude/latitude for these points: {result.points_lonlat_note}
        </p>
      ) : null}
      <p className="text-fg-subtle">{result.note}</p>
    </div>
  );
}

/** Keeps a display setting per asset while the page is open. */
export function useDisplayQuery(assetId: string): [DisplayQuery, (next: DisplayQuery) => void] {
  const [value, setValue] = useState<DisplayQuery>({});
  useEffect(() => setValue({}), [assetId]);
  return [value, setValue];
}
