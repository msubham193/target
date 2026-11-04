import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { lazy, Suspense, type ReactNode, useEffect, useMemo, useState } from "react";

import {
  cancelIndexRun,
  getLayers,
  type IndexRun,
  type IndexRunResult,
  type IndexScene,
  listIndexInputs,
  listIndexRuns,
  listIndices,
  startIndexRun,
} from "../api/client";
import { IconCheck, IconClock, IconLeaf } from "../components/icons";
import type { RasterDisplay } from "../components/MapCanvas";
import {
  Badge,
  Breadcrumbs,
  Button,
  DescriptionList,
  EmptyState,
  ErrorNotice,
  LinkButton,
  Loading,
  Notice,
  PageHeader,
  Panel,
  selectClass,
} from "../components/ui";
import {
  formatAcquisition,
  formatBytes,
  formatCount,
  formatValue,
  isRunActive,
  latestResult,
  type Legend,
  legendGradient,
  legendPosition,
  ndviTileQuery,
  parseLegend,
  PHASE_LABELS,
  PHASE_TONES,
  POLL_MS,
  runPhase,
  SETUP_PENDING_LABEL,
  SETUP_PENDING_MESSAGE,
  SETUP_PENDING_REASSURANCE,
  setupState,
  shortHash,
  tileUrl,
} from "../lib/indices";
import { useHealth } from "../lib/queries";
import { projectCrumbs, useProject } from "./ProjectLayout";

const MapCanvas = lazy(() => import("../components/MapCanvas").then((module) => ({ default: module.MapCanvas })));

const INDEX = "ndvi";
// Phones get 26 rem so the centred empty-state card, its ramp and the status chip all fit; from 640 px it is 28 rem.
const MAP_HEIGHT = "h-[26rem] sm:h-[28rem]";

// --- small building blocks ----------------------------------------------------------------------------------------

/** Panel titles at section-heading size (the shared Panel defaults to a smaller one). */
const sectionTitle = (text: string) => <span className="text-base">{text}</span>;

/** A grouped fact: a small label, then the value on its own line, so a narrow column never squeezes the text. */
function InfoRow({ label, children, wide = false }: { label: string; children: ReactNode; wide?: boolean }) {
  // In a panel at least 480 px wide the rows sit in two columns (no rules between them); narrower, they stack with rules.
  return (
    <div
      className={`border-t border-line py-3 first:border-t-0 first:pt-0 last:pb-0 @min-[480px]:border-t-0 @min-[480px]:py-0 @min-[480px]:first:pt-0 @min-[480px]:last:pb-0 ${wide ? "@min-[480px]:col-span-2" : ""}`}
    >
      <dt className="text-xs font-medium uppercase tracking-wide text-fg-subtle">{label}</dt>
      <dd className="mt-1 text-[13px] leading-snug text-fg">{children}</dd>
    </div>
  );
}

function Fact({ label, children, wide = false }: { label: string; children: ReactNode; wide?: boolean }) {
  return (
    <div className={`${wide ? "col-span-2" : ""} @min-[480px]:contents`}>
      <dt className="text-xs text-fg-subtle">{label}</dt>
      <dd className="mt-0.5 text-[13px] text-fg @min-[480px]:mt-0">{children}</dd>
    </div>
  );
}

// The two bands are told apart by colour as well as by name: red for the red band, the accent blue for near-infrared.
const ROLE_STYLE: Record<string, { title: string; edge: string; chip: string }> = {
  red: { title: "Red band", edge: "border-l-danger", chip: "bg-danger-soft text-danger" },
  nir: { title: "Near-infrared band", edge: "border-l-accent", chip: "bg-accent-soft text-accent" },
};

// --- inputs -------------------------------------------------------------------------------------------------------

function BandCard({ band, acquisition }: { band: IndexScene["bands"][number]; acquisition: string | null }) {
  const ok = band.found && band.readable && band.problems.length === 0;
  const style = ROLE_STYLE[band.role] ?? { title: band.role, edge: "border-l-line-strong", chip: "bg-overlay text-fg-muted" };
  return (
    <li className={`rounded-lg border border-l-4 border-line bg-raised p-4 @min-[480px]:p-3.5 ${style.edge}`}>
      <div className="flex items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-3">
          <span className={`grid size-9 shrink-0 place-items-center rounded-md text-xs font-semibold ${style.chip}`}>
            {band.band}
          </span>
          <p className="min-w-0 text-sm font-medium text-fg">{style.title}</p>
        </div>
        <Badge size="md" tone={ok ? "ok" : "danger"}>{ok ? "Readable" : band.found ? "Problem" : "Missing"}</Badge>
      </div>
      <p className="mt-3 break-all font-mono text-xs text-fg-muted">{band.file_name ?? "no matching file"}</p>
      {ok ? (
        <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2.5 border-t border-line pt-3 @min-[480px]:grid-cols-[auto_minmax(0,1fr)] @min-[480px]:gap-x-3 @min-[480px]:gap-y-1.5">
          <Fact label="Resolution">{band.pixel_size_m?.[0]} m</Fact>
          <Fact label="Dimensions">
            {band.width?.toLocaleString("en-GB")} × {band.height?.toLocaleString("en-GB")} px
          </Fact>
          <Fact label="CRS">{band.crs}</Fact>
          <Fact label="Pixel type">{band.dtype}</Fact>
          <Fact label="Acquired" wide>
            {formatAcquisition(acquisition)}
          </Fact>
        </dl>
      ) : null}
      {band.problems.map((problem) => (
        <p key={problem} className="mt-2 text-xs text-danger">
          {problem}
        </p>
      ))}
    </li>
  );
}

/** The verdict on whether the two bands can be combined, kept at the top of the panel so it is seen first. */
function CompatibilityBanner({ scene }: { scene: IndexScene }) {
  if (!scene.compatible) {
    return (
      <Notice tone="warning" title="These bands cannot be combined">
        <ul className="list-disc pl-4">
          {[...scene.problems, ...scene.bands.flatMap((b) => b.problems)].map((p) => (
            <li key={p}>{p}</li>
          ))}
        </ul>
      </Notice>
    );
  }
  const grid = scene.bands.find((b) => b.readable);
  return (
    <div role="status" className="flex items-start gap-3 rounded-lg border border-ok/35 bg-ok-soft p-3.5">
      <IconCheck size={20} className="mt-0.5 shrink-0 text-ok" />
      <div>
        <p className="text-[15px] font-semibold text-ok">Compatible</p>
        <p className="mt-0.5 text-[13px] text-fg-muted">
          Same grid{grid ? ` (${grid.pixel_size_m?.[0]} m, ${grid.crs})` : ""}, so no resampling is needed.
        </p>
      </div>
    </div>
  );
}

function InputsPanel({
  scenes,
  sceneId,
  onSelect,
}: {
  scenes: IndexScene[];
  sceneId: string | null;
  onSelect: (id: string) => void;
}) {
  const scene = scenes.find((s) => s.scene_id === sceneId) ?? null;
  const ordered = scene ? [...scene.bands].sort((a, b) => (a.role === "red" ? -1 : b.role === "red" ? 1 : 0)) : [];
  return (
    <Panel title={sectionTitle("Input bands")} description="Sentinel-2 L2A, from this computer's band folder.">
      {scenes.length > 1 ? (
        <label className="mb-4 block text-xs text-fg-muted">
          Scene
          <select className={`${selectClass} mt-1 w-full`} value={sceneId ?? ""} onChange={(e) => onSelect(e.target.value)}>
            {scenes.map((s) => (
              <option key={s.scene_id} value={s.scene_id}>
                {formatAcquisition(s.acquisition_start)} · {s.tile_id ?? "tile unknown"}
              </option>
            ))}
          </select>
        </label>
      ) : null}
      {scene ? (
        <div className="@container space-y-4">
          <CompatibilityBanner scene={scene} />
          <ul aria-label="Input bands" className="grid gap-3 @min-[480px]:grid-cols-2">
            {ordered.map((band) => (
              <BandCard key={band.role} band={band} acquisition={scene.acquisition_start} />
            ))}
          </ul>
          <p className="text-xs text-fg-subtle">
            Tile {scene.tile_id ?? "unknown"} · baseline {scene.processing_baseline ?? "unknown"} ·{" "}
            {scene.spacecraft ?? "spacecraft unknown"}
          </p>
        </div>
      ) : null}
    </Panel>
  );
}

// --- processing ---------------------------------------------------------------------------------------------------

function ProcessingPanel({
  scene,
  formula,
  range,
  busy,
  active,
  setup,
  onStart,
  startError,
}: {
  scene: IndexScene | null;
  formula: string;
  range: number[] | null;
  busy: boolean;
  active: boolean;
  setup: ReturnType<typeof setupState>;
  onStart: () => void;
  startError: unknown;
}) {
  type Scaling = { add_offset?: number; quantification_value?: number; nodata_dn?: number; saturated_dn?: number };
  const scalings = (scene?.bands ?? []).map((b) => ({ ...((b.scaling ?? {}) as Scaling), band: b.band })); // the card's name, B04
  const first = scalings[0];
  const pending = setup === "pending";
  const reason = pending
    ? SETUP_PENDING_LABEL
    : !scene
      ? "There is no scene to process."
      : !scene.compatible
        ? "The bands of this scene cannot be combined."
        : active
          ? "A run for this project is already in progress."
          : null;
  return (
    <Panel
      title={sectionTitle("Processing")}
      actions={pending ? <Badge size="md" tone="warn">{SETUP_PENDING_LABEL}</Badge> : undefined}
      description="What a run does."
    >
      <div className="@container">
        <dl className="@min-[480px]:grid @min-[480px]:grid-cols-2 @min-[480px]:gap-x-6 @min-[480px]:gap-y-3">
          <InfoRow label="NDVI formula" wide>
            <span className="wrap-anywhere font-mono text-[13px]">NDVI = {formula}</span>
          </InfoRow>
          <InfoRow label="Reflectance scaling" wide>
            <span className="wrap-anywhere font-mono text-[13px]">
              reflectance = (DN + offset) ÷ {first?.quantification_value ? formatCount(first.quantification_value) : "quantification value"}
            </span>
            <span className="mt-1.5 flex flex-wrap gap-1.5">
              {scalings.map((s) => (
                <Badge size="md" key={s.band} tone="neutral">
                  {s.band}: offset {s.add_offset ?? "?"}
                </Badge>
              ))}
            </span>
            <span className="mt-1.5 block text-xs text-fg-muted">Read per band from the product metadata, not assumed.</span>
          </InfoRow>
          <InfoRow label="Data validity">
            DN {first?.nodata_dn ?? 0} (NODATA) or {formatCount(first?.saturated_dn ?? 65535)} (SATURATED) in either band, and
            near-zero denominators, are excluded.
          </InfoRow>
          <InfoRow label="Out-of-range masking">
            {range
              ? `Values outside [${range[0]}, ${range[1]}] become nodata. They are masked, not clamped; negative values inside the range are kept.`
              : "Every finite result is kept."}
          </InfoRow>
          <InfoRow label="Processing resolution" wide>
            10 m, the bands' own grid. No resampling.
          </InfoRow>
        </dl>
      </div>

      {pending ? (
        <div className="mt-4">
          <Notice tone="warning" title={SETUP_PENDING_LABEL}>
            <p>
              {SETUP_PENDING_MESSAGE}
              {scene?.compatible ? ` ${SETUP_PENDING_REASSURANCE}` : ""}
            </p>
            <p className="text-xs text-fg-subtle">You can still inspect the selected inputs. No run was requested.</p>
          </Notice>
        </div>
      ) : null}

      <div className="mt-4 space-y-2">
        <Button className="w-full" onClick={onStart} disabled={reason !== null || busy}>
          {busy ? "Starting…" : "Calculate NDVI"}
        </Button>
        {reason ? <p className="text-center text-xs text-fg-muted">{reason}</p> : null}
      </div>
      {startError ? (
        <div className="mt-3">
          <ErrorNotice error={startError} title="The run could not be started" />
        </div>
      ) : null}
    </Panel>
  );
}

// --- progress -----------------------------------------------------------------------------------------------------

function ProgressPanel({
  run,
  onCancel,
  cancelling,
  setupPending,
}: {
  run: IndexRun | null;
  onCancel: () => void;
  cancelling: boolean;
  setupPending: boolean;
}) {
  if (!run) {
    return (
      <Panel title={sectionTitle("Progress")} actions={setupPending ? <Badge size="md" tone="warn">{SETUP_PENDING_LABEL}</Badge> : undefined}>
        <p className="text-sm text-fg-muted">
          {setupPending
            ? "No run can start until the database setup is upgraded. Nothing has been submitted."
            : "No run yet. Start one from the processing panel."}
        </p>
      </Panel>
    );
  }
  const phase = runPhase(run.status);
  const percent = run.progress === null ? null : Math.round(run.progress * 100);
  return (
    <Panel
      title={sectionTitle("Progress")}
      actions={<Badge size="md" tone={PHASE_TONES[phase]}>{PHASE_LABELS[phase]}</Badge>}
      description={phase === "running" ? (run.stage ?? "Working") : undefined}
    >
      {phase === "queued" || phase === "running" ? (
        <div>
          <div
            role="progressbar"
            aria-label="NDVI run progress"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={percent ?? undefined}
            aria-valuetext={percent === null ? PHASE_LABELS[phase] : `${percent}%`}
            className="h-2 overflow-hidden rounded-full bg-overlay"
          >
            <div
              className={`h-full rounded-full bg-accent transition-[width] ${percent === null ? "w-1/4 animate-pulse" : ""}`}
              style={percent === null ? undefined : { width: `${percent}%` }}
            />
          </div>
          <p className="mt-1.5 text-xs text-fg-muted">
            {phase === "queued" ? "Waiting for the worker to pick this up." : percent === null ? "Working…" : `${percent}% done`}
          </p>
          <div className="mt-3">
            <Button variant="secondary" size="sm" onClick={onCancel} disabled={cancelling || run.cancel_requested}>
              {run.cancel_requested ? "Stopping…" : "Cancel run"}
            </Button>
          </div>
        </div>
      ) : null}
      {phase === "completed" ? <p className="text-sm text-fg-muted">The result is stored and shown on the map.</p> : null}
      {phase === "cancelled" ? <p className="text-sm text-fg-muted">Cancelled. Nothing from this run was kept.</p> : null}
      {phase === "failed" ? (
        <Notice tone="error" title="The run failed">
          <p>{run.error?.message ?? "The job reported no message."}</p>
          {run.error ? <p className="font-mono text-xs text-fg-subtle">Code: {run.error.code}</p> : null}
        </Notice>
      ) : null}
      <p className="mt-3 text-xs text-fg-subtle">
        Started {run.started_at ? formatAcquisition(run.started_at) : "not yet"}
        {run.finished_at ? ` · finished ${formatAcquisition(run.finished_at)}` : ""}
        {run.attempt > 1 ? ` · attempt ${run.attempt} of ${run.max_attempts}` : ""}
      </p>
    </Panel>
  );
}

// --- the legend ---------------------------------------------------------------------------------------------------

export function NdviLegend({ legend, name = "NDVI legend" }: { legend: Legend; name?: string }) {
  const zero = legendPosition(0, legend);
  return (
    <div className="rounded-md border border-line bg-surface p-3" aria-label={name} role="group">
      <p className="mb-2 text-xs font-medium text-fg-muted">{legend.title} (display colours)</p>
      <div className="h-3 rounded-sm border border-line" style={{ backgroundImage: legendGradient(legend) }} aria-hidden="true" />
      <div className="relative mt-1 h-8 text-xs text-fg-muted">
        <span className="absolute left-0 top-0">
          {formatValue(legend.low, 1)}
          <span className="block text-fg-subtle">negative</span>
        </span>
        <span className="absolute top-0 -translate-x-1/2" style={{ left: `${zero}%` }}>
          0<span className="block text-center text-fg-subtle">zero</span>
        </span>
        <span className="absolute right-0 top-0 text-right">
          {formatValue(legend.high, 1)}
          <span className="block text-fg-subtle">positive</span>
        </span>
      </div>
      <p className="text-xs text-fg-subtle">
        Values beyond the ends use the end colours. Nodata is transparent. The colours are a display choice; the stored
        values are unchanged.
      </p>
    </div>
  );
}

// --- the map ------------------------------------------------------------------------------------------------------

/** What the map area shows before there is a result: where the layer will go and how it will be coloured. No preview. */
function MapPlaceholder({ legend, scene, setupPending }: { legend: Legend | null; scene: IndexScene | null; setupPending: boolean }) {
  const grid = scene?.bands.find((b) => b.readable);
  return (
    <div
      className={`relative grid ${MAP_HEIGHT} place-items-center overflow-hidden bg-raised p-4 sm:p-6`}
      style={{
        backgroundImage:
          "linear-gradient(var(--color-line) 1px, transparent 1px), linear-gradient(90deg, var(--color-line) 1px, transparent 1px)",
        backgroundSize: "48px 48px",
      }}
    >
      <div className="relative w-full max-w-lg rounded-xl border border-line-strong bg-surface p-4 text-center shadow-lg sm:p-6">
        <span className="mx-auto grid size-14 place-items-center rounded-full bg-accent-soft text-accent">
          <IconLeaf size={28} />
        </span>
        <h3 className="mt-4 text-[17px] font-semibold text-fg">NDVI layer</h3>
        <p className="mx-auto mt-1.5 max-w-sm text-[13px] text-fg-muted">
          The analytical NDVI raster will be drawn here once a run completes. No preview is shown before then.
        </p>
        {setupPending ? (
          <p className="mt-3 text-[13px] font-medium text-warn">{SETUP_PENDING_LABEL}: runs cannot start yet.</p>
        ) : null}
        {legend ? (
          <div className="mt-5 text-left">
            <NdviLegend legend={legend} name="NDVI colour ramp preview" />
          </div>
        ) : null}
      </div>
      {scene ? (
        <p className="absolute bottom-3 left-3 rounded-md border border-line bg-surface/90 px-2 py-1 text-xs text-fg-muted">
          Tile {scene.tile_id ?? "unknown"}
          {grid ? ` · ${grid.crs} · ${grid.pixel_size_m?.[0]} m` : ""}
        </p>
      ) : null}
    </div>
  );
}

function MapPanel({
  projectId,
  result,
  legend,
  scene,
  setupPending,
}: {
  projectId: string;
  result: IndexRunResult | null;
  legend: Legend | null;
  scene: IndexScene | null;
  setupPending: boolean;
}) {
  const [visible, setVisible] = useState(true);
  const [opacity, setOpacity] = useState(0.9);
  const layers = useQuery({
    queryKey: ["layers", projectId, "ndvi", result?.derived_asset_id],
    queryFn: () => getLayers(projectId),
    enabled: result !== null,
  });
  const layer = layers.data?.rasters.find((r) => r.asset_id === result?.derived_asset_id) ?? null;
  const rasters = useMemo<RasterDisplay[]>(() => {
    if (!layer || !legend) return [];
    // The layer comes from the ordinary layers route; only the address is replaced so that its colours come from the
    // NDVI ramp and not from the grey default stretch, which would need band statistics first.
    return [{ layer: { ...layer, tile_url: tileUrl(layer.asset_id, layer.georef_version) }, visible, opacity, query: ndviTileQuery(legend) }];
  }, [layer, legend, visible, opacity]);
  const fitTo = useMemo(() => (layer ? (layer.bounds_4326 as [number, number, number, number]) : null), [layer]);

  return (
    <Panel
      title={sectionTitle("Map")}
      padded={false}
      actions={
        <>
          <label className="flex items-center gap-1.5 text-xs text-fg-muted">
            <input type="checkbox" checked={visible} disabled={!layer} onChange={(e) => setVisible(e.target.checked)} />
            NDVI layer
          </label>
          <label className="flex items-center gap-1.5 text-xs text-fg-muted">
            Opacity
            <input
              type="range"
              min={10}
              max={100}
              step={5}
              value={Math.round(opacity * 100)}
              disabled={!layer}
              aria-label="NDVI layer opacity"
              onChange={(e) => setOpacity(Number(e.target.value) / 100)}
            />
          </label>
        </>
      }
    >
      {result === null ? (
        <MapPlaceholder legend={legend} scene={scene} setupPending={setupPending} />
      ) : layers.isLoading ? (
        <div className={`grid ${MAP_HEIGHT} place-items-center`}>
          <Loading label="Loading the layer…" />
        </div>
      ) : layers.isError ? (
        <div className="p-4">
          <ErrorNotice error={layers.error} title="The map layers could not be loaded" />
        </div>
      ) : !layer ? (
        <div className="p-4">
          <Notice tone="warning" title="This raster cannot be placed on the map">
            It is stored, but it does not have a usable CRS and geotransform for the map.
          </Notice>
        </div>
      ) : (
        <Suspense fallback={<Loading label="Loading the map…" className="p-4" />}>
          <MapCanvas
            rasters={rasters}
            aoi={null}
            showAoi={false}
            showFootprints={false}
            selectedRasterId={null}
            fitTo={fitTo}
            className={`${MAP_HEIGHT} w-full`}
          />
        </Suspense>
      )}
      {legend && result ? (
        <div className="border-t border-line p-3">
          <NdviLegend legend={legend} />
        </div>
      ) : null}
    </Panel>
  );
}

// --- results ------------------------------------------------------------------------------------------------------

// What the Results panel will hold once a run has completed, grouped as it will be shown. Categories only: no values.
const WILL_SHOW: { title: string; items: string }[] = [
  { title: "Statistics", items: "Minimum, maximum, mean, standard deviation, percentiles" },
  { title: "Pixel counts", items: "Valid, invalid and masked out-of-range pixels" },
  { title: "Raster", items: "Resolution, CRS, size and acquisition date" },
  { title: "Provenance", items: "Input files, SHA-256 and per-band scaling" },
];

function ChecklistItem({ tone, children }: { tone: "ok" | "warn" | "neutral"; children: ReactNode }) {
  const colour = tone === "ok" ? "text-ok" : tone === "warn" ? "text-warn" : "text-fg-subtle";
  return (
    <li className="flex items-start gap-2.5 text-[13px] text-fg-muted">
      <span className={`mt-0.5 shrink-0 ${colour}`}>{tone === "ok" ? <IconCheck size={16} /> : <IconClock size={16} />}</span>
      <span>{children}</span>
    </li>
  );
}

function ResultsEmpty({ scene, setup, range }: { scene: IndexScene | null; setup: ReturnType<typeof setupState>; range: number[] | null }) {
  return (
    <Panel title={sectionTitle("Results")}>
      <div className="@container">
        <div className="grid gap-6 min-[1180px]:grid-cols-2 @min-[640px]:grid-cols-2">
          <div>
            <h3 className="text-[15px] font-semibold text-fg">No analytical NDVI result is registered yet</h3>
            <p className="mt-1.5 text-[13px] text-fg-muted">
              Statistics and provenance appear here after a successful run. Nothing is shown until there is a real result.
            </p>
            <ul aria-label="Readiness" className="mt-4 space-y-2.5">
              <ChecklistItem tone={scene?.compatible ? "ok" : "warn"}>
                {scene?.compatible
                  ? "Input imagery has already passed the compatibility checks."
                  : "Input imagery has not passed the compatibility checks."}
              </ChecklistItem>
              <ChecklistItem tone={setup === "pending" ? "warn" : setup === "ready" ? "ok" : "neutral"}>
                {setup === "pending"
                  ? `${SETUP_PENDING_LABEL}: the database job configuration is not upgraded yet.`
                  : setup === "ready"
                    ? "Processing setup is ready. Waiting for a run to be started."
                    : "Waiting for a run to be started."}
              </ChecklistItem>
              {range ? (
                <ChecklistItem tone="neutral">
                  Values outside [{range[0]}, {range[1]}] will be masked as nodata, not clamped.
                </ChecklistItem>
              ) : null}
            </ul>
          </div>
          <div>
            <h3 className="text-xs font-semibold uppercase tracking-wide text-fg-subtle">This panel will show</h3>
            <div className="@container mt-3">
              <ul aria-label="What the results will show" className="grid grid-cols-1 gap-2.5 @min-[360px]:grid-cols-2">
                {WILL_SHOW.map((group) => (
                  <li key={group.title} className="rounded-lg border border-line bg-raised p-3">
                    <p className="text-[13px] font-medium text-fg">{group.title}</p>
                    <p className="mt-0.5 text-xs text-fg-muted">{group.items}</p>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </div>
      </div>
    </Panel>
  );
}

function ResultsPanel({ projectId, result, run }: { projectId: string; result: IndexRunResult | null; run: IndexRun | null }) {
  if (!result || !run) return null;
  const stats = result.statistics;
  const counts = result.counts;
  const p = result.product as { acquisition_start?: string; tile_id?: string; processing_baseline?: string; uri?: string };
  const total = counts.pixels ?? 0;
  const share = (n: number | undefined) => (total && n !== undefined ? ` (${((n / total) * 100).toFixed(2)} %)` : "");
  return (
    <Panel
      title={sectionTitle("Results")}
      actions={
        <LinkButton to={`/projects/${projectId}/assets/${result.derived_asset_id}`} variant="secondary" size="sm">
          Open the stored raster
        </LinkButton>
      }
    >
      <div className="grid gap-5 xl:grid-cols-2">
        <section aria-label="Statistics">
          <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-fg-subtle">Statistics of valid pixels</h3>
          <DescriptionList
            rows={[
              ["Minimum", formatValue(stats.min)],
              ["Maximum", formatValue(stats.max)],
              ["Mean", formatValue(stats.mean)],
              ["Std deviation", formatValue(stats.std)],
              ["Median", formatValue(stats.percentiles?.["50"])],
              ["1st–99th pct", `${formatValue(stats.percentiles?.["1"])} to ${formatValue(stats.percentiles?.["99"])}`],
            ]}
          />
          <h3 className="mb-2 mt-5 text-xs font-semibold uppercase tracking-wide text-fg-subtle">Pixels</h3>
          <DescriptionList
            labelWidth="md"
            rows={[
              ["Valid", `${formatCount(counts.valid)}${share(counts.valid)}`],
              ["Invalid (nodata)", `${formatCount(counts.invalid)}${share(counts.invalid)}`],
              ["Masked: outside the range", formatCount(counts.masked_out_of_range)],
              ["Input nodata", formatCount(counts.input_nodata)],
              ["Near-zero denominator", formatCount(counts.denominator_near_zero)],
              ["Saturated inputs", formatCount(counts.input_saturated)],
            ]}
          />
        </section>
        <section aria-label="Raster and provenance">
          <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-fg-subtle">Raster</h3>
          <DescriptionList
            rows={[
              ["Resolution", `${result.resolution_m} m`],
              ["CRS", result.crs ?? "unknown"],
              ["Size", `${formatCount(result.width)} × ${formatCount(result.height)} px · ${formatBytes(result.size_bytes)}`],
              ["Nodata value", String(result.nodata)],
              ["Acquisition", formatAcquisition(p.acquisition_start)],
              ["Tile", p.tile_id ?? "unknown"],
            ]}
          />
          <h3 className="mb-2 mt-5 text-xs font-semibold uppercase tracking-wide text-fg-subtle">Input provenance</h3>
          <ul aria-label="Input provenance" className="space-y-2">
            {result.inputs.map((input) => (
              <li key={input.role} className="rounded-md border border-line bg-raised p-2 text-xs">
                <p className="text-fg">
                  {input.role === "red" ? "Red" : "Near-infrared"} · {input.band}
                </p>
                <p className="break-all font-mono text-fg-muted">{input.file_name}</p>
                <p className="text-fg-subtle" title={input.sha256 ?? undefined}>
                  SHA-256 {shortHash(input.sha256)}…
                </p>
              </li>
            ))}
          </ul>
          <p className="mt-2 wrap-anywhere text-xs text-fg-subtle">
            Formula {result.formula}. Product {p.uri ?? "unknown"}, baseline {p.processing_baseline ?? "unknown"}.
          </p>
          {result.duplicate_of_asset_id ? (
            <Notice tone="info" className="mt-2">
              Identical to an earlier result in this project, which is linked as its original.
            </Notice>
          ) : null}
        </section>
      </div>
    </Panel>
  );
}

// --- the page -----------------------------------------------------------------------------------------------------

export function NdviStudioPage() {
  const project = useProject();
  const queryClient = useQueryClient();
  const [sceneId, setSceneId] = useState<string | null>(null);

  const registry = useQuery({ queryKey: ["indices"], queryFn: listIndices });
  const inputs = useQuery({ queryKey: ["index-inputs", project.id, INDEX], queryFn: () => listIndexInputs(project.id, INDEX) });
  const runs = useQuery({
    queryKey: ["index-runs", project.id, INDEX],
    queryFn: () => listIndexRuns(project.id, INDEX),
    refetchInterval: (query) => (query.state.data?.runs.some(isRunActive) ? POLL_MS : false),
  });
  // The database's schema revision, which the API already reports. It tells whether runs can be accepted at all, so
  // the known "setup pending" state is shown before anything is submitted. An unreadable revision never blocks.
  const health = useHealth();
  const revision: unknown = health.data?.database?.schema_revision;
  const setup = setupState(typeof revision === "string" ? revision : null);

  const scenes = useMemo(() => inputs.data?.scenes ?? [], [inputs.data]);
  useEffect(() => {
    if (sceneId && scenes.some((s) => s.scene_id === sceneId)) return;
    const first = scenes.find((s) => s.compatible) ?? scenes[0];
    setSceneId(first?.scene_id ?? null);
  }, [scenes, sceneId]);
  const scene = scenes.find((s) => s.scene_id === sceneId) ?? null;

  const refreshRuns = () => queryClient.invalidateQueries({ queryKey: ["index-runs", project.id, INDEX] });
  const start = useMutation({ mutationFn: (id: string) => startIndexRun(project.id, id, INDEX), onSettled: refreshRuns });
  const cancel = useMutation({ mutationFn: (jobId: string) => cancelIndexRun(project.id, jobId, INDEX), onSettled: refreshRuns });

  const runList = runs.data?.runs ?? [];
  const current = runList[0] ?? null;
  const finished = latestResult(runList) ?? null;
  const definition = registry.data?.indices.find((i) => i.key === INDEX);
  const legend = parseLegend(current?.display ?? finished?.display ?? definition?.display);
  const active = runList.some(isRunActive);
  const range = definition?.analytical_range ?? null;

  const header = (
    <>
      <Breadcrumbs items={projectCrumbs(project, { label: "NDVI Studio" })} />
      <div className="mt-2">
        <PageHeader
          title={<span className="text-2xl">NDVI Studio</span>}
          description="Vegetation index from two Sentinel-2 Level-2A bands already on this computer. Runs in the background; the result is stored as a derived raster."
        />
      </div>
    </>
  );

  if (inputs.isLoading || runs.isLoading) {
    return (
      <>
        {header}
        <Loading label="Looking for Sentinel-2 bands…" />
      </>
    );
  }
  if (inputs.isError || runs.isError) {
    return (
      <>
        {header}
        <ErrorNotice error={inputs.error ?? runs.error} title="NDVI Studio could not load" />
      </>
    );
  }
  if (!inputs.data?.configured) {
    return (
      <>
        {header}
        <EmptyState title="No band folder is configured">
          The server has no folder to look in for Sentinel-2 bands. Set <code>GEOPOC_IMAGERY_BAND_ROOT</code> to a folder that
          holds a product&apos;s <code>MTD_MSIL2A.xml</code> and its B04 and B08 files, then restart the API.
        </EmptyState>
      </>
    );
  }
  if (scenes.length === 0) {
    return (
      <>
        {header}
        <EmptyState title="No Sentinel-2 scene was found">
          The band folder holds no <code>MTD_MSIL2A.xml</code> with matching B04 and B08 files.
        </EmptyState>
      </>
    );
  }

  return (
    <>
      {header}
      {/* Left: the inputs and what a run will do (340-380 px, in px so the root font size cannot change it). Right: the map takes the rest, with the results below it. */}
      <div className="grid items-start gap-4 min-[1180px]:grid-cols-[minmax(340px,380px)_minmax(0,1fr)]">
        <div className="min-w-0 space-y-4">
          <InputsPanel scenes={scenes} sceneId={sceneId} onSelect={setSceneId} />
          <ProcessingPanel
            scene={scene}
            formula={definition?.formula ?? "(NIR − Red) ÷ (NIR + Red)"}
            range={range}
            busy={start.isPending}
            active={active}
            setup={setup}
            onStart={() => setup !== "pending" && sceneId && start.mutate(sceneId)}
            startError={start.error}
          />
          <ProgressPanel
            run={current}
            onCancel={() => current && cancel.mutate(current.job_id)}
            cancelling={cancel.isPending}
            setupPending={setup === "pending"}
          />
          {cancel.error ? <ErrorNotice error={cancel.error} title="The run could not be cancelled" /> : null}
        </div>
        <div className="min-w-0 space-y-4">
          <MapPanel projectId={project.id} result={finished?.result ?? null} legend={legend} scene={scene} setupPending={setup === "pending"} />
          {finished?.result ? (
            <ResultsPanel projectId={project.id} result={finished.result} run={finished} />
          ) : (
            <ResultsEmpty scene={scene} setup={setup} range={range} />
          )}
        </div>
      </div>
    </>
  );
}
