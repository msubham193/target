import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import {
  assetPreviewUrl,
  createAnnotation,
  describeScene,
  getCameraView,
  getReportSummaryStatus,
  listObservations,
  type ObservationView,
  type PhotoLocationItem,
} from "../api/client";
import { distanceMetres, latestRun } from "../lib/compare";
import { fitGround, footPoint, type GroundFit, type GroundPair, type Placement } from "../lib/groundMatch";
import { formatLatLon } from "../lib/coordinates";
import { formatDateTime } from "../lib/format";
import { formatLength } from "../lib/geo";
import { categoryLabel, REVIEW_STATUS_LABELS, reviewTone } from "../lib/jobs";
import { useAsset } from "../lib/queries";
import {
  bearingInPicture,
  bearingTo,
  classColourHex,
  DEFAULT_LINE_LENGTH_M,
  destination,
  formatBearing,
  type LonLat,
  TYPICAL_VIEW_WIDTH_DEG,
  viewOutline,
  viewWidthFromFocal,
} from "../lib/sightLines";
import { AssetPreview } from "./AssetPreview";
import { boxes, DetectionOverlay } from "./DetectionOverlay";
import { EvidenceCrop, type EvidenceRegion } from "./EvidenceCrop";
import type { SightLines } from "./MapCanvas";
import { Badge, Button, ErrorNotice, Loading, Notice } from "./ui";

/** One object the model found in the photograph, numbered as its box is numbered on the picture. */
export interface PhotoObject {
  id: string;
  index: number;
  label: string;
  colour: string;
  status: string;
  score: number | null;
  /** The box's centre column in the picture's own pixels, which gives its direction from the camera. */
  centreX: number | null;
  imageWidth: number | null;
  imageHeight: number | null;
  region: EvidenceRegion | null;
}

export function photoObjects(views: ObservationView[]): PhotoObject[] {
  return views.map(({ observation, current }, position) => {
    const region = (observation.evidence[0]?.region_px ?? null) as EvidenceRegion | null;
    let centreX: number | null = null;
    if (region?.type === "bbox_xyxy") {
      const [x0, , x1] = region.coordinates as number[];
      if (x0 !== undefined && x1 !== undefined) centreX = (x0 + x1) / 2;
    } else if (region && Array.isArray(region.coordinates)) {
      const xs = (region.coordinates as number[][]).map((p) => p[0]).filter((x): x is number => typeof x === "number");
      if (xs.length > 0) centreX = (Math.min(...xs) + Math.max(...xs)) / 2;
    }
    const label = categoryLabel(current.category);
    return {
      id: observation.observation_id,
      index: position + 1,
      label,
      colour: classColourHex(label),
      status: current.status,
      score: observation.score.raw_value ?? null,
      centreX,
      imageWidth: region?.image_width ?? null,
      imageHeight: region?.image_height ?? null,
      region,
    };
  });
}

/** Classes found by the latest analysis, most frequent first. Objects a reviewer rejected are not counted. */
export function analysisCounts(objects: PhotoObject[]): { label: string; colour: string; count: number }[] {
  const counts = new Map<string, { label: string; colour: string; count: number }>();
  for (const object of objects) {
    if (object.status === "rejected") continue;
    const entry = counts.get(object.label) ?? { label: object.label, colour: object.colour, count: 0 };
    entry.count += 1;
    counts.set(object.label, entry);
  }
  return [...counts.values()].sort((a, b) => b.count - a.count || a.label.localeCompare(b.label));
}

/** "What the AI found": one coloured chip per class with its count. */
export function AnalysisSummary({ objects, at, compact = false }: { objects: PhotoObject[]; at?: string | null; compact?: boolean }) {
  const counts = analysisCounts(objects);
  if (counts.length === 0) return <p className="text-xs text-fg-subtle">The AI analysis found nothing in this photo.</p>;
  const total = counts.reduce((sum, entry) => sum + entry.count, 0);
  return (
    <div className="space-y-1" aria-label="What the AI found">
      <p className="text-xs text-fg-muted">
        <span className="font-medium text-fg">AI analysis:</span> {total} object(s)
        {at && !compact ? ` · ${formatDateTime(at)}` : ""}
      </p>
      <div className="flex flex-wrap gap-1">
        {counts.map((entry) => (
          <span key={entry.label} className="flex items-center gap-1 rounded-full border border-line px-1.5 py-px text-[11px] text-fg">
            <span className="size-2 rounded-full" style={{ background: entry.colour }} />
            {entry.label} {entry.count}
          </span>
        ))}
      </div>
    </div>
  );
}

/** The cached AI description of one photograph (it is written once per session, when the photo panel is opened). */
const sceneKey = (assetId: string) => ["scene-description", assetId] as const;

/**
 * "What the AI sees": a draft description of the scene and what is going on, written by the local vision model from the
 * photo and the detector's counts. It is written when the panel opens (once per photo per session) if the model is ready.
 */
export function SceneDescription({ assetId }: { assetId: string }) {
  const status = useQuery({ queryKey: ["report-summary-status"], queryFn: getReportSummaryStatus, staleTime: 60_000 });
  const ready = status.data?.enabled === true && status.data.model_installed;
  const draft = useQuery({
    queryKey: sceneKey(assetId),
    queryFn: () => describeScene(assetId),
    enabled: ready,
    staleTime: Number.POSITIVE_INFINITY,
    gcTime: Number.POSITIVE_INFINITY,
    retry: false,
    refetchOnWindowFocus: false,
  });
  return (
    <section className="space-y-1.5 rounded-lg border border-line/70 bg-canvas/40 p-2.5" aria-label="What the AI sees">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-[13px] font-medium text-fg">What the AI sees</h3>
        {draft.data || draft.isError ? (
          <Button size="sm" variant="ghost" onClick={() => void draft.refetch()} disabled={draft.isFetching}>
            {draft.isFetching ? "Writing…" : "Describe again"}
          </Button>
        ) : null}
      </div>
      {status.isLoading ? <Loading label="Checking the local AI model…" /> : null}
      {status.data && !ready ? (
        <p className="text-xs text-fg-subtle">The AI description is not available: {status.data.note}</p>
      ) : null}
      {status.isError ? <p className="text-xs text-fg-subtle">The AI description is not available: the API could not be asked.</p> : null}
      {draft.isFetching && !draft.data ? (
        <Loading label="The AI is looking at the photo… This takes up to a minute on this computer." />
      ) : null}
      {draft.isError ? <ErrorNotice error={draft.error} title="The AI could not describe this photo" /> : null}
      {draft.data ? (
        <div className="space-y-1.5 text-[13px]" data-testid="scene-description">
          <p className="text-fg">{draft.data.scene}</p>
          <p className="text-fg-muted">{draft.data.activity}</p>
          {draft.data.details.length > 0 ? (
            <ul className="list-disc space-y-0.5 pl-4 text-xs text-fg-muted">
              {draft.data.details.map((detail, index) => (
                <li key={index}>{detail}</li>
              ))}
            </ul>
          ) : null}
          {draft.data.detector_check ? (
            <p className="text-xs text-fg-muted">
              <span className="text-fg">Detector check:</span> {draft.data.detector_check}
            </p>
          ) : null}
          <p className="text-[11px] text-fg-subtle">
            Draft written by {draft.data.model} on this computer, {formatDateTime(draft.data.generated_at)}. Not reviewed by a person;
            it can be wrong and is not a finding. Not saved.
          </p>
        </div>
      ) : null}
    </section>
  );
}

/** One line of the AI description for the hover popup, when it has already been written for this photo. */
function SceneLine({ assetId }: { assetId: string }) {
  const draft = useQuery({ queryKey: sceneKey(assetId), queryFn: () => describeScene(assetId), enabled: false });
  if (!draft.data) return null;
  return (
    <p className="line-clamp-3 text-xs text-fg-muted" data-testid="hover-scene">
      <span className="text-fg">AI sees:</span> {draft.data.scene} {draft.data.activity}
    </p>
  );
}

/**
 * The photo shown when the pointer is over its marker: the picture with the AI's boxes drawn on it, and what was found.
 * Uses the same cached analysis as the photo panel.
 */
export function PhotoHoverCard({ projectId, item, open }: { projectId: string; item: PhotoLocationItem; open: boolean }) {
  const observations = useQuery({
    queryKey: ["observations", projectId, "asset", item.asset_id],
    queryFn: () => listObservations(projectId, { asset_id: item.asset_id }),
  });
  const latest = useMemo(() => latestRun(observations.data ?? []), [observations.data]);
  const objects = useMemo(() => photoObjects(latest?.views ?? []), [latest]);
  const sized = objects.find((o) => o.imageWidth && o.imageHeight);
  const width = sized?.imageWidth ?? 4;
  const height = sized?.imageHeight ?? 3;
  return (
    <>
      <div className="relative w-full bg-canvas" style={{ aspectRatio: `${width} / ${height}` }}>
        <img src={assetPreviewUrl(item.asset_id, "view")} alt={`Photo taken here: ${item.filename}`} className="block h-full w-full" />
        {sized ? (
          <svg
            className="absolute inset-0 h-full w-full"
            viewBox={`0 0 ${width} ${height}`}
            preserveAspectRatio="none"
            aria-label="AI detections on the photo"
            data-testid="hover-boxes"
          >
            {objects.flatMap((object) => {
              if (object.status === "rejected" || object.region?.type !== "bbox_xyxy") return [];
              const [x0, y0, x1, y1] = object.region.coordinates as number[];
              if (x0 === undefined || y0 === undefined || x1 === undefined || y1 === undefined) return [];
              return [
                <rect
                  key={object.id}
                  x={x0}
                  y={y0}
                  width={x1 - x0}
                  height={y1 - y0}
                  fill="none"
                  stroke={object.colour}
                  strokeWidth={1.5}
                  vectorEffect="non-scaling-stroke"
                />,
              ];
            })}
          </svg>
        ) : null}
      </div>
      <div className="space-y-1.5 px-3 py-2">
        <div>
          <p className="truncate font-medium text-fg">{item.filename}</p>
          <p className="font-mono text-xs text-fg-muted">{formatLatLon(item.latitude!, item.longitude!, 6)}</p>
        </div>
        {observations.isLoading ? <p className="text-xs text-fg-subtle">Loading the AI analysis…</p> : null}
        {observations.isSuccess && !latest ? <p className="text-xs text-fg-subtle">Not analysed yet. Run an analysis in Photo Analysis.</p> : null}
        {latest ? <AnalysisSummary objects={objects} compact /> : null}
        <SceneLine assetId={item.asset_id} />
        <p className="text-[11px] text-fg-subtle">{open ? "Open on the right." : "Click the marker to open it."} Model predictions until reviewed.</p>
      </div>
    </>
  );
}

/** What the person set for one photograph, kept in this browser only (never sent to the server). */
interface Settings {
  heading: number | null;
  viewWidth: number | null;
  length: number;
  marks: Record<string, [number, number]>;
  /** Spots matched between the photo (pixels in the boxes' grid) and the map. */
  controls: GroundPair[];
}

const EMPTY_SETTINGS: Settings = { heading: null, viewWidth: null, length: DEFAULT_LINE_LENGTH_M, marks: {}, controls: [] };

function isPair(value: unknown): value is GroundPair {
  const p = value as GroundPair;
  return Boolean(p) && [p.u, p.v, p.lon, p.lat].every((n) => typeof n === "number" && Number.isFinite(n));
}
const storageKey = (assetId: string) => `photo-on-map:${assetId}`;

function loadSettings(assetId: string): Settings {
  try {
    const raw = window.localStorage.getItem(storageKey(assetId));
    if (!raw) return EMPTY_SETTINGS;
    const parsed = JSON.parse(raw) as Partial<Settings>;
    return {
      heading: typeof parsed.heading === "number" ? parsed.heading : null,
      viewWidth: typeof parsed.viewWidth === "number" ? parsed.viewWidth : null,
      length: typeof parsed.length === "number" ? parsed.length : DEFAULT_LINE_LENGTH_M,
      marks: parsed.marks && typeof parsed.marks === "object" ? parsed.marks : {},
      controls: Array.isArray(parsed.controls) ? parsed.controls.filter(isPair) : [],
    };
  } catch {
    return EMPTY_SETTINGS;
  }
}

function storeSettings(assetId: string, settings: Settings) {
  try {
    window.localStorage.setItem(storageKey(assetId), JSON.stringify(settings));
  } catch {
    // Private windows and blocked storage: the settings simply last until the page is left.
  }
}

/**
 * What the next click means: set the camera direction, mark one object, or match spots (a click in the photo, then the
 * same spot on the map).
 */
export type Aim =
  | { kind: "direction" }
  | { kind: "object"; id: string }
  | { kind: "match"; pending: { u: number; v: number } | null }
  | null;

/**
 * The state behind one photograph on the satellite map: its objects, which way the camera faced (from the file, or set
 * by the person), the direction lines to draw, and what a map click means right now.
 */
export function usePhotoOnMap(projectId: string, item: PhotoLocationItem | null) {
  const assetId = item?.asset_id ?? null;
  const camera: LonLat | null = item && item.latitude != null && item.longitude != null ? { lon: item.longitude, lat: item.latitude } : null;
  const observations = useQuery({
    queryKey: ["observations", projectId, "asset", assetId],
    queryFn: () => listObservations(projectId, { asset_id: assetId! }),
    enabled: assetId !== null,
  });
  const cameraView = useQuery({ queryKey: ["camera-view", assetId], queryFn: () => getCameraView(assetId!), enabled: assetId !== null });
  const latest = useMemo(() => latestRun(observations.data ?? []), [observations.data]);
  const objects = useMemo(() => photoObjects(latest?.views ?? []), [latest]);

  // Settings belong to one photograph: choosing another starts from what was set for that one.
  const [state, setState] = useState<{ assetId: string | null; settings: Settings }>(() => ({
    assetId,
    settings: assetId ? loadSettings(assetId) : EMPTY_SETTINGS,
  }));
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [selected, setSelected] = useState<string | null>(null);
  const [aim, setAim] = useState<Aim>(null);
  const [hint, setHint] = useState<string | null>(null);
  const [showLines, setShowLines] = useState(false);
  if (state.assetId !== assetId) {
    setState({ assetId, settings: assetId ? loadSettings(assetId) : EMPTY_SETTINGS });
    setSelected(null);
    setAim(null);
    setHidden(new Set());
  }
  const settings = state.assetId === assetId ? state.settings : EMPTY_SETTINGS;
  const update = (change: Partial<Settings>) =>
    setState((current) => {
      const next = { ...current.settings, ...change };
      if (current.assetId) storeSettings(current.assetId, next);
      return { ...current, settings: next };
    });

  const fileHeading = cameraView.data?.heading_deg ?? null;
  const heading = settings.heading ?? fileHeading;
  const headingSource: "person" | "file" | null = settings.heading != null ? "person" : fileHeading != null ? "file" : null;
  const sized = objects.find((o) => o.imageWidth && o.imageHeight);
  const focal = cameraView.data?.focal_length_35mm_mm ?? null;
  const lensWidth = focal && sized ? viewWidthFromFocal(focal, sized.imageWidth!, sized.imageHeight!) : null;
  const viewWidth = settings.viewWidth ?? lensWidth ?? TYPICAL_VIEW_WIDTH_DEG;
  const widthSource: "person" | "lens" | "typical" = settings.viewWidth != null ? "person" : lensWidth != null ? "lens" : "typical";

  const bearings = useMemo(() => {
    const result = new Map<string, number>();
    if (heading == null) return result;
    for (const object of objects) {
      if (object.centreX != null && object.imageWidth) {
        result.set(object.id, bearingInPicture(heading, viewWidth, object.centreX, object.imageWidth));
      }
    }
    return result;
  }, [objects, heading, viewWidth]);

  // The photo-to-ground mapping from the matched spots, and where it puts each object's foot point.
  const fit: GroundFit | null = useMemo(
    () => (camera && settings.controls.length > 0 ? fitGround(camera, settings.controls) : null),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [camera?.lon, camera?.lat, settings.controls],
  );
  const placements = useMemo(() => {
    const result = new Map<string, Placement>();
    if (!fit?.ok) return result;
    for (const object of objects) {
      const foot = object.region ? footPoint(object.region) : null;
      if (foot) result.set(object.id, fit.project(foot.u, foot.v));
    }
    return result;
  }, [fit, objects]);
  const fitted = fit?.ok === true;

  // Objects a reviewer rejected are not drawn: the model was wrong about them.
  const drawn = useMemo(() => objects.filter((o) => o.status !== "rejected" && !hidden.has(o.label)), [objects, hidden]);

  const sightLines: SightLines | null = useMemo(() => {
    if (!camera) return null;
    const from: [number, number] = [camera.lon, camera.lat];
    const lines = !fitted || showLines;
    return {
      fan: heading != null && lines ? viewOutline(camera, heading, viewWidth, settings.length) : null,
      rays: (lines ? drawn : []).flatMap((object) => {
        const bearing = bearings.get(object.id);
        if (bearing === undefined) return [];
        const end = destination(camera, bearing, settings.length);
        return [{ id: object.id, colour: object.colour, coordinates: [from, [end.lon, end.lat]] as [number, number][] }];
      }),
      marked: drawn.flatMap((object) => {
        const at = settings.marks[object.id];
        return at ? [{ id: object.id, colour: object.colour, at, from }] : [];
      }),
      // A person's own mark wins over the estimate for that object.
      estimated: drawn.flatMap((object) => {
        const placed = placements.get(object.id);
        if (settings.marks[object.id] || !placed?.ok) return [];
        return [{ id: object.id, colour: object.colour, at: [placed.lon, placed.lat] as [number, number], extrapolated: placed.extrapolated }];
      }),
      controls: settings.controls.map((pair, index) => ({ id: `control-${index + 1}`, at: [pair.lon, pair.lat] as [number, number] })),
      selectedId: selected,
    };
    // `camera` is rebuilt from the item each render; its numbers are what matter.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [camera?.lon, camera?.lat, heading, viewWidth, settings.length, settings.marks, settings.controls, drawn, bearings, selected, placements, fitted, showLines]);

  /** The photo's pixel grid (the boxes' grid), which matched photo spots are recorded in. */
  const grid = sized ? { width: sized.imageWidth!, height: sized.imageHeight! } : null;

  /** A click in the photo while matching: the photo half of the next pair. */
  const pickPhoto = ({ fx, fy }: { fx: number; fy: number }) => {
    if (aim?.kind !== "match" || !grid) return;
    setHint(null);
    setAim({ kind: "match", pending: { u: fx * grid.width, v: fy * grid.height } });
  };

  /** Handles a map click while aiming. Returns false when the click was not meant for this photograph. */
  const pick = (position: LonLat): boolean => {
    if (!camera || !aim) return false;
    if (aim.kind === "match") {
      if (!aim.pending) {
        setHint("Click the spot in the photo first, then the same spot on the map.");
        return true;
      }
      update({ controls: [...settings.controls, { ...aim.pending, lon: position.lon, lat: position.lat }] });
      setHint(null);
      setAim({ kind: "match", pending: null });
      return true;
    }
    if (aim.kind === "direction") {
      update({ heading: Math.round(bearingTo(camera, position) * 10) / 10 });
    } else {
      update({ marks: { ...settings.marks, [aim.id]: [position.lon, position.lat] } });
      setSelected(aim.id);
    }
    setAim(null);
    return true;
  };

  return {
    item,
    camera,
    observations,
    cameraView,
    latest,
    objects,
    heading,
    headingSource,
    fileHeading,
    viewWidth,
    widthSource,
    lensWidth,
    length: settings.length,
    marks: settings.marks,
    controls: settings.controls,
    fit,
    placements,
    grid,
    hint,
    showLines,
    setShowLines,
    pickPhoto,
    removeControl: (index: number) => update({ controls: settings.controls.filter((_, i) => i !== index) }),
    clearControls: () => update({ controls: [] }),
    bearings,
    hidden,
    setHidden,
    selected,
    setSelected,
    aim,
    setAim,
    sightLines,
    pick,
    setHeading: (value: number | null) => update({ heading: value }),
    setViewWidth: (value: number | null) => update({ viewWidth: value }),
    setLength: (value: number) => update({ length: value }),
    clearMarks: () => update({ marks: {} }),
    unmark: (id: string) => {
      const marks = { ...settings.marks };
      delete marks[id];
      update({ marks });
    },
  };
}

export type PhotoOnMapState = ReturnType<typeof usePhotoOnMap>;

const SOURCE_LABELS: Record<string, string> = {
  exif: "GPS position from the photo file, confirmed by a person",
  manual_entry: "Coordinates typed in by a person",
  map_selection: "Point chosen on the map by a person",
};

const REFERENCE_LABELS: Record<string, string> = {
  true_north: "measured from true north",
  magnetic_north: "measured from magnetic north (within about 1° of true north in India; more elsewhere)",
  not_stated: "the file does not say from which north",
};

function Slider({
  label,
  value,
  min,
  max,
  step,
  unit,
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  unit: string;
  onChange: (value: number) => void;
}) {
  return (
    <label className="block space-y-1">
      <span className="flex justify-between text-xs text-fg-muted">
        <span>{label}</span>
        <span className="font-mono text-fg">
          {Math.round(value)}
          {unit}
        </span>
      </span>
      <input
        type="range"
        className="w-full accent-accent"
        aria-label={label}
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
      />
    </label>
  );
}

/** "about ±3 m", or how to get an accuracy figure. */
export function accuracyText(fit: GroundFit | null): string {
  if (!fit?.ok) return "";
  if (fit.checkM == null) return "Add a 5th spot to check the accuracy.";
  return `About ±${fit.checkM < 10 ? fit.checkM.toFixed(1) : Math.round(fit.checkM)} m where you matched spots (each spot checked against the others).`;
}

/** What a placement rests on, in words, for notes and exports. */
function methodText(fit: GroundFit | null): string {
  if (!fit?.ok) return "";
  const accuracy = fit.checkM == null ? "accuracy not checked (4 spots)" : `about ±${fit.checkM.toFixed(1)} m`;
  return `estimated from ${fit.pairs} ground spots matched between the photo and the satellite map, ${accuracy}; flat ground assumed`;
}

/** The objects with a map position (a person's mark, or an estimate) as GeoJSON, for download. Rejected objects are left out. */
export function objectsGeoJSON(state: Pick<PhotoOnMapState, "objects" | "placements" | "marks" | "fit" | "item">) {
  const features = state.objects.flatMap((object) => {
    if (object.status === "rejected") return [];
    const mark = state.marks[object.id];
    const placed = state.placements.get(object.id);
    const at = mark ?? (placed?.ok ? [placed.lon, placed.lat] : null);
    if (!at) return [];
    return [
      {
        type: "Feature",
        geometry: { type: "Point", coordinates: [Number(at[0]!.toFixed(7)), Number(at[1]!.toFixed(7))] },
        properties: {
          object_number: object.index,
          class: object.label,
          model_score: object.score,
          review_status: object.status,
          observation_id: object.id,
          photo: state.item?.filename ?? null,
          photo_asset_id: state.item?.asset_id ?? null,
          method: mark ? "marked by a person on the satellite map" : methodText(state.fit),
          extrapolated: mark ? false : placed?.ok ? placed.extrapolated : false,
        },
      },
    ];
  });
  return { type: "FeatureCollection", features };
}

/** Numbered matched spots (white) and the half-made pair (amber), drawn over the photo in its own pixel grid. */
function SpotsOverlay({ state, scale }: { state: PhotoOnMapState; scale: number }) {
  if (!state.grid) return null;
  const pending = state.aim?.kind === "match" ? state.aim.pending : null;
  const r = 7 / scale;
  return (
    <svg
      className="pointer-events-none absolute inset-0 h-full w-full"
      viewBox={`0 0 ${state.grid.width} ${state.grid.height}`}
      preserveAspectRatio="none"
      aria-hidden
    >
      {state.controls.map((pair, index) => (
        <g key={index}>
          <circle cx={pair.u} cy={pair.v} r={r} fill="#ffffff" stroke="#11141a" strokeWidth={2 / scale} />
          <text x={pair.u} y={pair.v + 3.5 / scale} textAnchor="middle" fontSize={10 / scale} fontWeight={700} fill="#11141a">
            {index + 1}
          </text>
        </g>
      ))}
      {pending ? (
        <circle cx={pending.u} cy={pending.v} r={r * 1.3} fill="none" stroke="#e6b450" strokeWidth={3 / scale} data-testid="pending-spot" />
      ) : null}
    </svg>
  );
}

/**
 * One photograph on the satellite map: where it was taken, the objects found in it, which way the camera faced, and a line
 * from the camera towards each object. A photograph shows the direction to an object, never its distance, so an object is
 * on the map only where a person marks it.
 */
export function PhotoOnMapPanel({ projectId, state, onClose }: { projectId: string; state: PhotoOnMapState; onClose: () => void }) {
  const { item, camera } = state;
  const asset = useAsset(item?.asset_id);
  const queryClient = useQueryClient();
  const [savedNotes, setSavedNotes] = useState<Record<string, true>>({});
  useEffect(() => setSavedNotes({}), [item?.asset_id]);
  const saveNote = useMutation({
    mutationFn: ({ objectId, at, note }: { objectId: string; at: [number, number]; note: string }) =>
      createAnnotation(projectId, { kind: "point", geometry: { type: "Point", coordinates: at }, crs_id: "EPSG:4326", note }).then(() => objectId),
    onSuccess: (objectId) => {
      setSavedNotes((current) => ({ ...current, [objectId]: true }));
      void queryClient.invalidateQueries({ queryKey: ["annotations", projectId] });
    },
  });
  const [confirmSave, setConfirmSave] = useState(false);
  const [includeExtrapolated, setIncludeExtrapolated] = useState(false);
  const [savedAll, setSavedAll] = useState<number | null>(null);
  useEffect(() => {
    setConfirmSave(false);
    setSavedAll(null);
  }, [item?.asset_id, state.controls]);
  const toSave = state.objects.flatMap((object) => {
    const placed = state.placements.get(object.id);
    if (object.status === "rejected" || state.hidden.has(object.label) || state.marks[object.id] || !placed?.ok) return [];
    if (placed.extrapolated && !includeExtrapolated) return [];
    return [{ object, at: [placed.lon, placed.lat] as [number, number], extrapolated: placed.extrapolated }];
  });
  const saveAll = useMutation({
    mutationFn: async () => {
      for (const { object, at, extrapolated } of toSave) {
        await createAnnotation(projectId, {
          kind: "point",
          geometry: { type: "Point", coordinates: at },
          crs_id: "EPSG:4326",
          note: `${object.label} (#${object.index}) in photo ${item?.filename ?? ""}. Position ${methodText(state.fit)}${
            extrapolated ? "; outside the matched area, less reliable" : ""
          }.`,
        });
      }
      return toSave.length;
    },
    onSuccess: (count) => {
      setSavedAll(count);
      setConfirmSave(false);
      void queryClient.invalidateQueries({ queryKey: ["annotations", projectId] });
    },
  });
  if (!item || !camera) return null;

  const download = () => {
    const blob = new Blob([JSON.stringify(objectsGeoJSON(state), null, 2)], { type: "application/geo+json" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `${item.filename.replace(/\.[^.]+$/, "")}-objects.geojson`;
    link.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  const matching = state.aim?.kind === "match";
  const pending = state.aim?.kind === "match" ? state.aim.pending : null;
  const placedCount = state.objects.filter((o) => o.status !== "rejected" && state.placements.get(o.id)?.ok).length;
  const extrapolatedCount = state.objects.filter((o) => {
    const placed = state.placements.get(o.id);
    return o.status !== "rejected" && placed?.ok === true && placed.extrapolated;
  }).length;
  const unplaceable = state.objects.filter((o) => o.status !== "rejected" && state.placements.get(o.id)?.ok === false).length;

  const selectedObject = state.objects.find((o) => o.id === state.selected) ?? null;
  const categories = [...new Map(state.objects.map((o) => [o.label, o.colour])).entries()].map(([label, colour]) => ({
    label,
    colour,
    count: state.objects.filter((o) => o.label === label).length,
  }));
  const rejected = state.objects.filter((o) => o.status === "rejected").length;
  const aimingAt = state.aim?.kind === "object" ? state.objects.find((o) => o.id === (state.aim as { id: string }).id) : null;

  return (
    <div
      role="region"
      aria-label="Photo on the map"
      className="pointer-events-auto flex max-h-full flex-col overflow-hidden rounded-xl border border-line bg-surface/95 shadow-[0_12px_32px_rgb(0_0_0/0.45)] backdrop-blur-md"
    >
      <div className="flex items-start justify-between gap-2 border-b border-line/70 px-3 py-2">
        <div className="min-w-0">
          <p className="text-xs text-fg-subtle">Photo taken here</p>
          <p className="wrap-anywhere text-[13px] font-medium text-fg">{item.filename}</p>
        </div>
        <Button size="sm" variant="ghost" onClick={onClose}>
          Close
        </Button>
      </div>

      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-3 text-[13px]">
        <div>
          <p className="font-mono text-xs text-fg" aria-label="Where the photo was taken">
            {formatLatLon(camera.lat, camera.lon, 6)}
          </p>
          <p className="text-xs text-fg-muted">
            {SOURCE_LABELS[item.source ?? ""] ?? "Recorded by a person"}
            {item.confirmed_by ? ` · ${item.confirmed_by}` : ""}. The green marker on the map is this position.
          </p>
        </div>

        {asset.isLoading ? <Loading label="Loading the photo…" /> : null}
        {asset.isError ? <ErrorNotice error={asset.error} title="The photo could not be loaded" /> : null}
        {asset.data ? (
          <AssetPreview
            asset={asset.data}
            large={matching}
            onPointPick={matching ? state.pickPhoto : undefined}
            overlay={(scale) => (
              <>
                <DetectionOverlay
                  items={boxes(state.latest?.views ?? [])}
                  selected={selectedObject?.index ?? null}
                  scale={scale}
                  onSelect={(index) => {
                    const object = state.objects.find((o) => o.index === index);
                    state.setSelected(object && object.id !== state.selected ? object.id : null);
                  }}
                />
                <SpotsOverlay state={state} scale={scale} />
              </>
            )}
          />
        ) : null}

        {state.latest ? <AnalysisSummary objects={state.objects} at={state.latest.at} /> : null}
        <SceneDescription assetId={item.asset_id} />

        {state.latest && state.grid ? (
          <section className="space-y-2" aria-label="Place all objects on the map">
            <h3 className="text-[13px] font-medium text-fg">Place all objects on the map</h3>
            <p className="text-xs text-fg-muted">
              Match spots on the ground that you can see both in the photo and on the satellite map: the end of a lane marking, a kerb
              corner, the edge of the median. With 4 or more, every object is placed where it touches the ground.
            </p>
            <div className="flex flex-wrap gap-1.5">
              <Button
                size="sm"
                variant={matching ? "primary" : "secondary"}
                onClick={() => state.setAim(matching ? null : { kind: "match", pending: null })}
              >
                {matching ? "Done matching" : state.controls.length > 0 ? "Match more spots" : "Match spots"}
              </Button>
              {state.controls.length > 0 ? (
                <Button size="sm" variant="ghost" onClick={state.clearControls}>
                  Remove all spots
                </Button>
              ) : null}
            </div>
            {matching ? (
              <Notice tone="info" title={pending ? "Now click the same spot on the satellite map" : "Click a spot on the ground in the photo"}>
                <p>
                  {pending
                    ? "The amber ring in the photo is the spot you chose. Click exactly that place on the map (zoom in for precision), or click the photo again to choose a different spot."
                    : "Choose 6–8 spots spread from near to far and from left to right, all on the flat ground. Never use roofs, poles, signs or vehicles: they are not on the ground, or they move."}
                </p>
                {state.hint ? <p className="mt-1 font-medium">{state.hint}</p> : null}
              </Notice>
            ) : null}

            {state.controls.length > 0 ? (
              <ol className="space-y-0.5 text-xs" aria-label="Matched spots">
                {state.controls.map((pair, index) => {
                  const residual = state.fit?.ok ? state.fit.residuals[index] : undefined;
                  return (
                    <li key={index} className="flex items-center gap-2 rounded-md px-2 py-0.5 hover:bg-overlay">
                      <span className="flex size-4.5 shrink-0 items-center justify-center rounded-full bg-white text-[10px] font-bold text-[#11141a]">
                        {index + 1}
                      </span>
                      <span className="min-w-0 flex-1 truncate font-mono text-fg-muted">{formatLatLon(pair.lat, pair.lon, 6)}</span>
                      {residual !== undefined && Number.isFinite(residual) ? (
                        <span className={`font-mono ${residual > 3 ? "text-warn" : "text-fg-subtle"}`} title="How far the fit is from this spot">
                          {residual.toFixed(1)} m
                        </span>
                      ) : null}
                      <Button size="sm" variant="ghost" aria-label={`Remove spot ${index + 1}`} onClick={() => state.removeControl(index)}>
                        Remove
                      </Button>
                    </li>
                  );
                })}
              </ol>
            ) : null}

            {state.fit && !state.fit.ok ? (
              <Notice tone="warning" title="The objects cannot be placed yet">
                <p data-testid="fit-problem">{state.fit.reason}</p>
              </Notice>
            ) : null}
            {state.fit?.ok ? (
              <div className="space-y-2 rounded-lg border border-line/70 p-2 text-xs" data-testid="fit-result">
                <p className="text-fg">
                  {placedCount} object(s) placed from {state.fit.pairs} matched spots.{" "}
                  <span className="text-fg-muted">{accuracyText(state.fit)}</span>
                </p>
                {extrapolatedCount > 0 ? (
                  <p className="text-fg-muted">
                    {extrapolatedCount} of them are outside the matched area (hollow dots) and less reliable: add spots near them.
                  </p>
                ) : null}
                {unplaceable > 0 ? (
                  <p className="text-fg-muted">{unplaceable} could not be placed: their bottom edge is at or above the horizon.</p>
                ) : null}
                <label className="flex items-center gap-2 text-fg-muted">
                  <input type="checkbox" checked={state.showLines} onChange={(event) => state.setShowLines(event.target.checked)} />
                  Also show the direction lines
                </label>
                <div className="flex flex-wrap gap-1.5">
                  <Button size="sm" variant="secondary" onClick={download}>
                    Download positions (GeoJSON)
                  </Button>
                  <Button size="sm" variant="secondary" onClick={() => setConfirmSave(true)} disabled={confirmSave}>
                    Save positions as map notes
                  </Button>
                </div>
                {confirmSave ? (
                  <div className="space-y-1.5 rounded-md border border-line p-2" role="group" aria-label="Confirm saving map notes">
                    <p className="text-fg">
                      Save {toSave.length} map note(s) to the project, one per object shown? Each note says the position is an estimate and
                      how it was found. Objects you marked yourself are saved from their own row.
                    </p>
                    <label className="flex items-center gap-2 text-fg-muted">
                      <input type="checkbox" checked={includeExtrapolated} onChange={(event) => setIncludeExtrapolated(event.target.checked)} />
                      Include objects outside the matched area
                    </label>
                    <div className="flex gap-1.5">
                      <Button size="sm" onClick={() => saveAll.mutate()} disabled={saveAll.isPending || toSave.length === 0}>
                        {saveAll.isPending ? "Saving…" : `Save ${toSave.length}`}
                      </Button>
                      <Button size="sm" variant="ghost" onClick={() => setConfirmSave(false)} disabled={saveAll.isPending}>
                        Cancel
                      </Button>
                    </div>
                  </div>
                ) : null}
                {savedAll !== null ? <p className="text-ok">{savedAll} map note(s) saved. They show on the Map page.</p> : null}
                {saveAll.isError ? <ErrorNotice error={saveAll.error} title="Not all map notes were saved" /> : null}
              </div>
            ) : null}
          </section>
        ) : null}

        <section className="space-y-2" aria-label="Which way the camera faced">
          <h3 className="text-[13px] font-medium text-fg">Which way the camera faced</h3>
          {state.cameraView.isLoading ? <Loading label="Reading the photo file…" /> : null}
          {state.headingSource === "file" ? (
            <p className="text-xs text-fg-muted" data-testid="heading-source">
              From the photo's own compass: <span className="font-mono text-fg">{formatBearing(state.heading!)}</span>,{" "}
              {REFERENCE_LABELS[state.cameraView.data?.heading_reference ?? "not_stated"]}. Not checked: a phone compass can be off by
              tens of degrees.
            </p>
          ) : state.headingSource === "person" ? (
            <p className="text-xs text-fg-muted" data-testid="heading-source">
              Set by you: <span className="font-mono text-fg">{formatBearing(state.heading!)}</span>. Kept in this browser only.
              {state.fileHeading != null ? (
                <>
                  {" "}
                  <button type="button" className="text-accent hover:underline" onClick={() => state.setHeading(null)}>
                    Use the photo's compass ({formatBearing(state.fileHeading)})
                  </button>
                </>
              ) : null}
            </p>
          ) : state.cameraView.isSuccess ? (
            <p className="text-xs text-fg-muted" data-testid="heading-source">
              This photo file does not record which way the camera faced, so the objects' directions are not known yet. Set it: click{" "}
              <span className="text-fg">Set on the map</span>, then click the satellite map at something you can see in the middle of the
              photo.
            </p>
          ) : null}
          {state.cameraView.isError ? <ErrorNotice error={state.cameraView.error} title="The photo file could not be read" /> : null}

          <div className="flex flex-wrap gap-1.5">
            <Button
              size="sm"
              variant={state.aim?.kind === "direction" ? "primary" : "secondary"}
              onClick={() => state.setAim(state.aim?.kind === "direction" ? null : { kind: "direction" })}
            >
              {state.aim?.kind === "direction" ? "Cancel" : "Set on the map"}
            </Button>
            {state.headingSource === "person" && state.fileHeading == null ? (
              <Button size="sm" variant="ghost" onClick={() => state.setHeading(null)}>
                Clear direction
              </Button>
            ) : null}
          </div>
          {state.aim?.kind === "direction" ? (
            <Notice tone="info" title="Click the map">
              <p>Click the satellite map at something you can see in the middle of the photo. The camera direction is set from the green marker towards that point.</p>
            </Notice>
          ) : null}

          {state.heading != null ? (
            <div className="space-y-2 rounded-lg border border-line/70 p-2">
              <Slider label="Camera direction (° from north)" value={state.heading} min={0} max={359} step={1} unit="°" onChange={state.setHeading} />
              <Slider label="Width of view" value={state.viewWidth} min={20} max={120} step={1} unit="°" onChange={state.setViewWidth} />
              <p className="text-[11px] text-fg-subtle">
                {state.widthSource === "lens"
                  ? "Width from the lens data in the file."
                  : state.widthSource === "typical"
                    ? `The file has no lens data: ${state.viewWidth}° is typical of a phone camera.`
                    : "Width set by you."}{" "}
                Adjust both until the lines run through what you see in the photo.
                {state.widthSource === "person" ? (
                  <>
                    {" "}
                    <button type="button" className="text-accent hover:underline" onClick={() => state.setViewWidth(null)}>
                      Reset width
                    </button>
                  </>
                ) : null}
              </p>
              <Slider label="Line length" value={state.length} min={50} max={1000} step={10} unit=" m" onChange={state.setLength} />
            </div>
          ) : null}
        </section>

        <section className="space-y-2" aria-label="Objects in this photo">
          <h3 className="text-[13px] font-medium text-fg">Objects in this photo</h3>
          {state.observations.isLoading ? <Loading label="Loading the objects…" /> : null}
          {state.observations.isError ? <ErrorNotice error={state.observations.error} title="The objects could not be loaded" /> : null}
          {state.observations.isSuccess && !state.latest ? (
            <p className="text-xs text-fg-subtle">This photo has not been analysed yet. Run an analysis in Photo Analysis to find objects.</p>
          ) : null}
          {state.latest ? (
            <>
              <p className="text-xs text-fg-subtle">
                {state.objects.length} object(s) from the latest analysis
                {rejected > 0 ? `; ${rejected} rejected by a reviewer are not drawn` : ""}. Select one to see it.
              </p>
              {Object.keys(state.marks).length > 0 ? (
                <div className="flex items-center gap-2 text-xs text-fg-muted">
                  <span>{Object.keys(state.marks).length} object(s) marked by you on the map.</span>
                  <Button size="sm" variant="ghost" onClick={state.clearMarks}>
                    Remove all marks
                  </Button>
                </div>
              ) : null}
              <div className="flex flex-wrap gap-1" aria-label="Show on the map">
                {categories.map((category) => {
                  const shown = !state.hidden.has(category.label);
                  return (
                    <button
                      key={category.label}
                      type="button"
                      aria-pressed={shown}
                      onClick={() => {
                        const next = new Set(state.hidden);
                        if (shown) next.add(category.label);
                        else next.delete(category.label);
                        state.setHidden(next);
                      }}
                      className={`flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs ${shown ? "border-line text-fg" : "border-line/50 text-fg-subtle line-through"}`}
                    >
                      <span className="size-2 rounded-full" style={{ background: category.colour }} />
                      {category.label} ({category.count})
                    </button>
                  );
                })}
              </div>

              {aimingAt ? (
                <Notice tone="info" title={`Click the map where #${aimingAt.index} (${aimingAt.label}) is`}>
                  <p>
                    Look at the photo and the satellite picture, and click the spot on the map.{" "}
                    <button type="button" className="text-accent hover:underline" onClick={() => state.setAim(null)}>
                      Cancel
                    </button>
                  </p>
                </Notice>
              ) : null}

              {selectedObject?.region && item ? (
                <div className="flex gap-3" aria-label="Selected object">
                  <EvidenceCrop
                    src={`/api/v1/assets/${item.asset_id}/content`}
                    fallback={assetPreviewUrl(item.asset_id, "view")}
                    region={selectedObject.region}
                    alt={`Object ${selectedObject.index}: ${selectedObject.label}`}
                    openTo={`/projects/${projectId}/assets/${item.asset_id}/ground`}
                    className="w-36 shrink-0"
                  />
                  <div className="min-w-0 space-y-1 text-xs">
                    <p className="text-[13px] font-medium text-fg">
                      #{selectedObject.index} {selectedObject.label}
                    </p>
                    <ObjectWhere state={state} object={selectedObject} />
                  </div>
                </div>
              ) : null}

              <ul className="space-y-0.5" aria-label="Objects">
                {state.objects.map((object) => {
                  const active = state.selected === object.id;
                  const bearing = state.bearings.get(object.id);
                  const mark = state.marks[object.id];
                  const placed = state.placements.get(object.id);
                  return (
                    <li key={object.id} className={`rounded-md ${active ? "bg-accent-soft" : ""}`}>
                      <div className="flex items-center gap-2 px-2 py-1">
                        <button
                          type="button"
                          aria-pressed={active}
                          onClick={() => state.setSelected(active ? null : object.id)}
                          className="flex min-w-0 flex-1 items-center gap-2 text-left"
                        >
                          <span className="w-6 shrink-0 text-right text-xs text-fg-subtle">{object.index}</span>
                          <span className="size-2.5 shrink-0 rounded-full" style={{ background: object.colour }} />
                          <span className={`min-w-0 flex-1 truncate ${object.status === "rejected" ? "text-fg-subtle line-through" : "text-fg"}`}>
                            {object.label}
                          </span>
                          <span className="shrink-0 font-mono text-[11px] text-fg-muted">
                            {mark && camera
                              ? `${formatLength(distanceMetres(camera, { lon: mark[0], lat: mark[1] }))}`
                              : placed?.ok
                                ? `${formatLength(placed.distanceM)}${placed.extrapolated ? " ?" : ""}`
                                : bearing !== undefined
                                  ? formatBearing(bearing)
                                  : ""}
                          </span>
                        </button>
                        <Button
                          size="sm"
                          variant="ghost"
                          aria-label={`Mark #${object.index} ${object.label} on the map`}
                          disabled={object.status === "rejected"}
                          onClick={() => state.setAim(state.aim?.kind === "object" && state.aim.id === object.id ? null : { kind: "object", id: object.id })}
                        >
                          {mark ? "Move" : "Mark"}
                        </Button>
                      </div>
                      {active && mark ? (
                        <div className="flex flex-wrap items-center gap-2 px-2 pb-1.5 pl-10 text-xs">
                          <Button
                            size="sm"
                            variant="secondary"
                            disabled={savedNotes[object.id] === true || saveNote.isPending}
                            onClick={() =>
                              saveNote.mutate({
                                objectId: object.id,
                                at: mark,
                                note: `${object.label} (#${object.index}) seen in photo ${item.filename}. Position marked by a person on the satellite map.`,
                              })
                            }
                          >
                            {savedNotes[object.id] ? "Saved as a map note" : "Save as a map note"}
                          </Button>
                          <Button size="sm" variant="ghost" onClick={() => state.unmark(object.id)}>
                            Remove mark
                          </Button>
                          <Badge tone={reviewTone(object.status)}>{REVIEW_STATUS_LABELS[object.status] ?? object.status}</Badge>
                        </div>
                      ) : null}
                    </li>
                  );
                })}
              </ul>
              {saveNote.isError ? <ErrorNotice error={saveNote.error} title="The map note was not saved" /> : null}
            </>
          ) : null}
        </section>

        <Notice tone="warning" title="Directions, not positions">
          <p>
            A photo shows which way each object is from the camera, not how far. Each coloured line means: the object is somewhere along
            this line. An object is placed on the map only where you mark it, by comparing the photo with the satellite picture. Marks are
            kept in this browser until you save one as a map note. Objects are model predictions until reviewed.
          </p>
        </Notice>

        <Link to={`/projects/${projectId}/assets/${item.asset_id}/ground`} className="inline-block text-xs text-accent hover:underline">
          Open in Photo Analysis (review the objects, change the location)
        </Link>
      </div>
    </div>
  );
}

function ObjectWhere({ state, object }: { state: PhotoOnMapState; object: PhotoObject }) {
  const bearing = state.bearings.get(object.id);
  const mark = state.marks[object.id];
  const placed = state.placements.get(object.id);
  return (
    <>
      <p className="text-fg-muted">
        {object.score == null ? "No score" : `Model score ${object.score.toFixed(2)}`} ·{" "}
        {REVIEW_STATUS_LABELS[object.status] ?? object.status}
      </p>
      {bearing !== undefined ? (
        <p className="text-fg-muted">
          Direction from the camera: <span className="font-mono text-fg">{formatBearing(bearing)}</span> (somewhere along its line)
        </p>
      ) : (
        <p className="text-fg-subtle">Direction unknown until the camera direction is set.</p>
      )}
      {!mark && placed?.ok ? (
        <p className="text-fg-muted" data-testid="estimated-position">
          Estimated at <span className="font-mono text-fg">{formatLatLon(placed.lat, placed.lon, 6)}</span>, about{" "}
          {formatLength(placed.distanceM)} from the camera ({state.fit?.ok ? `from ${state.fit.pairs} matched spots` : ""}
          {placed.extrapolated ? "; outside the matched area, less reliable" : ""}).
        </p>
      ) : null}
      {!mark && placed && !placed.ok ? <p className="text-fg-subtle">{placed.reason}</p> : null}
      {mark && state.camera ? (
        <p className="text-fg-muted">
          Marked at <span className="font-mono text-fg">{formatLatLon(mark[1], mark[0], 6)}</span>, about{" "}
          {formatLength(distanceMetres(state.camera, { lon: mark[0], lat: mark[1] }))} from the camera (marked by you).
        </p>
      ) : (
        <p className="text-fg-subtle">Not marked on the map. Use Mark, then click where it is.</p>
      )}
    </>
  );
}
