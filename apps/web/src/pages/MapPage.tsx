import { keepPreviousData, useMutation, useQueries, useQuery } from "@tanstack/react-query";
import { lazy, Suspense, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import {
  ApiError,
  type CrsTransform,
  type DisplayQuery,
  displayParams,
  getLayers,
  getPixel,
  getRasterTools,
  type Layers,
  listAnnotations,
  listPhotoLocations,
  measurePoints,
  type PhotoLocationItem,
  type PhotoLocations,
  type RasterLayer,
  transformPoint,
} from "../api/client";
import { AccuracyAcknowledgement } from "../components/AccuracyAcknowledgement";
import { AlignmentPanel } from "../components/AlignmentPanel";
import { BasemapPanel } from "../components/BasemapPanel";
import { AnnotationPanel } from "../components/AnnotationPanel";
import { IconLayers, IconMap } from "../components/icons";
import type { AnnotationShape, PhotoMarker, RasterDisplay } from "../components/MapCanvas";
import { PhotoMapPanel } from "../components/PhotoMapPanel";
import { DisplayControls, MeasurementResult } from "../components/RasterTools";
import {
  Badge,
  Breadcrumbs,
  Button,
  DescriptionList,
  EmptyState,
  ErrorNotice,
  Loading,
  Notice,
  PageHeader,
  Panel,
  selectClass,
} from "../components/ui";
import { Workspace } from "../components/Workspace";
import { boundsOf, type Drawing } from "../lib/annotations";
import { type Basemap, type BasemapStatus, DEFAULT_LABELS, type LabelState, readMapboxToken } from "../lib/mapbox";
import { axisHeading } from "../lib/axes";
import { formatDecimalDegrees, formatDms } from "../lib/geo";
import { SERIES_SHORT } from "../lib/mapsheet";
import {
  ACK_REQUIRED,
  type AcknowledgementNeed,
  acknowledgementFromContext,
  accuracyText,
  describeOperation,
} from "../lib/transformation";
import { projectCrumbs, useProject } from "./ProjectLayout";

// The map library is large and needs WebGL, so it is loaded only on this screen.
const MapCanvas = lazy(() => import("../components/MapCanvas").then((module) => ({ default: module.MapCanvas })));

const EXCLUSION_LABELS: Record<string, string> = {
  NOT_GEOREFERENCED: "No location information",
  CRS_MISSING: "The file does not say which coordinate system it uses",
  CRS_INVALID: "The coordinate system in the file can't be used",
  NO_GEOTRANSFORM: "The file has no position data",
  INVALID_GEOREFERENCING: "The location in the file looks unreliable",
  TRANSFORMATION_BLOCKED: "Needs your approval before it can be placed",
  ANTIMERIDIAN_CROSSING_UNSUPPORTED: "Crosses the 180° line, so it can't be drawn",
};

/** Why a file is not on the map, in everyday words. A photo or video has no map position of its own. */
function plainReason(item: { modality?: string; detail: string }): string {
  if (item.modality === "ground_image") return "Photos don't carry a map location. You can set one in Photo Analysis.";
  if (item.modality === "video") return "Videos don't carry a map location.";
  return item.detail;
}

type Bounds = [number, number, number, number];

function union(boxes: Bounds[]): Bounds | null {
  if (boxes.length === 0) return null;
  return boxes.reduce<Bounds>(
    (acc, b) => [Math.min(acc[0], b[0]), Math.min(acc[1], b[1]), Math.max(acc[2], b[2]), Math.max(acc[3], b[3])],
    [Infinity, Infinity, -Infinity, -Infinity],
  );
}

function aoiBounds(geometry: Record<string, unknown> | null | undefined): Bounds | null {
  if (!geometry) return null;
  const points: number[][] = [];
  const walk = (value: unknown) => {
    if (Array.isArray(value) && typeof value[0] === "number" && typeof value[1] === "number") points.push(value as number[]);
    else if (Array.isArray(value)) value.forEach(walk);
  };
  walk(geometry.coordinates);
  if (points.length === 0) return null;
  const lons = points.map((p) => p[0]!);
  const lats = points.map((p) => p[1]!);
  const pad = points.length === 1 ? 0.01 : 0;
  return [Math.min(...lons) - pad, Math.min(...lats) - pad, Math.max(...lons) + pad, Math.max(...lats) + pad];
}

function LayerPanel({
  data,
  display,
  setDisplay,
  showAoi,
  setShowAoi,
  showFootprints,
  setShowFootprints,
  selectedId,
  onSelect,
  onZoom,
  photos,
  photosQuery,
  showPhotos,
  setShowPhotos,
  selectedPhotoId,
  onSelectPhoto,
  chosen,
  onChoose,
  ordered,
  onMove,
}: {
  data: Layers;
  /** Raster layers in stacking order, topmost first. */
  ordered: RasterLayer[];
  onMove: (assetId: string, direction: "up" | "down") => void;
  /** Datum transformations chosen in this view, by asset. Not stored anywhere. */
  chosen: Record<string, string>;
  onChoose: (assetId: string, operationId: string) => void;
  photos: PhotoLocationItem[];
  photosQuery: { isLoading: boolean; isError: boolean; error: unknown; data?: PhotoLocations };
  showPhotos: boolean;
  setShowPhotos: (value: boolean) => void;
  selectedPhotoId: string | null;
  onSelectPhoto: (id: string) => void;
  display: Record<string, { visible: boolean; opacity: number }>;
  setDisplay: (id: string, change: Partial<{ visible: boolean; opacity: number }>) => void;
  showAoi: boolean;
  setShowAoi: (value: boolean) => void;
  showFootprints: boolean;
  setShowFootprints: (value: boolean) => void;
  selectedId: string | null;
  onSelect: (id: string) => void;
  onZoom: (bounds: Bounds) => void;
}) {
  const { id: projectId } = useProject();
  return (
    <div className="space-y-3">
      <Panel
        title="Layers"
        description="Your satellite images and map sheets. The one at the top of the list is drawn on top."
        padded={false}
      >
        <div className="space-y-1 p-2">
          {data.rasters.length === 0 ? (
            <p className="px-2 py-1.5 text-xs text-fg-subtle" data-testid="no-imagery">
              No satellite images with a known location yet. Upload a GeoTIFF image that carries its location to see it here.
            </p>
          ) : (
            <ul aria-label="Raster layers" className="space-y-1">
              {ordered.map((layer, index) => {
                const state = display[layer.asset_id] ?? { visible: true, opacity: 1 };
                const selected = layer.asset_id === selectedId;
                return (
                  <li key={layer.asset_id} className={`rounded-md px-2 py-2 ${selected ? "bg-accent-soft" : "hover:bg-overlay"}`}>
                    <div className="flex items-start gap-2">
                      <input
                        id={`layer-${layer.asset_id}`}
                        type="checkbox"
                        className="mt-0.5 accent-accent"
                        checked={state.visible}
                        disabled={!layer.tile_url}
                        onChange={(event) => setDisplay(layer.asset_id, { visible: event.target.checked })}
                      />
                      <label htmlFor={`layer-${layer.asset_id}`} className="min-w-0 flex-1 text-[13px] text-fg">
                        <span className="block truncate">{layer.title}</span>
                        <span className={`block text-[11px] ${layer.placement === "trusted" ? "text-ok" : "text-accent"}`}>
                          {layer.placement === "trusted" ? "Location read from the file" : "Location entered by a person"}
                        </span>
                        {layer.map_sheet ? (
                          <span className="block text-[11px] text-fg-subtle" data-testid="map-sheet-tag">
                            Map sheet · {SERIES_SHORT[layer.map_sheet.series]}
                            {layer.map_sheet.sheet_label ? ` · ${layer.map_sheet.sheet_label}` : ""} · {layer.map_sheet.georeferencing_basis_label}
                          </span>
                        ) : (
                          <span className="block text-[11px] text-fg-subtle">Satellite image</span>
                        )}
                      </label>
                      <div className="flex shrink-0 flex-col">
                        <button
                          type="button"
                          className="px-1 text-xs leading-none text-fg-subtle hover:text-fg disabled:opacity-30"
                          aria-label={`Move ${layer.title} up`}
                          disabled={index === 0}
                          onClick={() => onMove(layer.asset_id, "up")}
                        >
                          ▲
                        </button>
                        <button
                          type="button"
                          className="px-1 text-xs leading-none text-fg-subtle hover:text-fg disabled:opacity-30"
                          aria-label={`Move ${layer.title} down`}
                          disabled={index === ordered.length - 1}
                          onClick={() => onMove(layer.asset_id, "down")}
                        >
                          ▼
                        </button>
                      </div>
                    </div>
                    {layer.transformation?.selection_required ? (
                      <div className="mt-1.5 space-y-1 pl-5" data-testid="transformation-choice">
                        <p className="text-[11px] font-medium text-warn">Choose how to place this image</p>
                        <label htmlFor={`operation-${layer.asset_id}`} className="sr-only">
                          How to place {layer.title}
                        </label>
                        <select
                          id={`operation-${layer.asset_id}`}
                          className={selectClass}
                          value={chosen[layer.asset_id] ?? ""}
                          onChange={(event) => event.target.value && onChoose(layer.asset_id, event.target.value)}
                        >
                          <option value="">Choose…</option>
                          {layer.transformation.candidates.map((candidate) => (
                            <option key={candidate.id} value={candidate.id}>
                              {candidate.id} · {candidate.name} · accuracy {accuracyText(candidate.classification, candidate.accuracy_m)}
                            </option>
                          ))}
                        </select>
                        <p className="text-[11px] text-fg-subtle">
                          Each option puts the image in a slightly different place. Your choice applies to this view only.{" "}
                          <Link to={`/projects/${projectId}/assets/${layer.asset_id}#crs-assessment`} className="text-accent hover:underline">
                            Compare them
                          </Link>
                        </p>
                      </div>
                    ) : layer.transformation && layer.transformation.classification === "approximate" ? (
                      <p className="mt-1 pl-5 text-[11px] text-warn" data-testid="approximate-note">
                        {describeOperation({
                          id: layer.transformation.operation_id ?? "",
                          classification: "approximate",
                          accuracy_m: layer.transformation.accuracy_m,
                        })}
                      </p>
                    ) : null}
                    {layer.display_unavailable_reason && !layer.transformation?.selection_required ? (
                      <p className="mt-1 text-[11px] text-warn">{layer.display_unavailable_reason}</p>
                    ) : layer.transformation?.selection_required ? null : (
                      <div className="mt-1.5 flex items-center gap-2 pl-5">
                        <label htmlFor={`opacity-${layer.asset_id}`} className="text-[11px] text-fg-subtle">
                          Opacity
                        </label>
                        <input
                          id={`opacity-${layer.asset_id}`}
                          type="range"
                          min={0}
                          max={1}
                          step={0.05}
                          value={state.opacity}
                          onChange={(event) => setDisplay(layer.asset_id, { opacity: Number(event.target.value) })}
                          className="min-w-0 flex-1 accent-accent"
                          aria-valuetext={`${Math.round(state.opacity * 100)} percent`}
                        />
                        <span className="w-9 text-right text-[11px] text-fg-subtle">{Math.round(state.opacity * 100)}%</span>
                      </div>
                    )}
                    <div className="mt-1.5 flex gap-1.5 pl-5">
                      <Button size="sm" variant="ghost" onClick={() => onZoom(layer.bounds_4326 as Bounds)}>
                        Zoom to
                      </Button>
                      <Button size="sm" variant="ghost" aria-pressed={selected} onClick={() => onSelect(layer.asset_id)}>
                        Details
                      </Button>
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
          <div className="space-y-1.5 border-t border-line px-2 pt-2 text-[13px]">
            <label className="flex items-center gap-2 text-fg-muted">
              <input type="checkbox" className="accent-accent" checked={showFootprints} onChange={(e) => setShowFootprints(e.target.checked)} />
              Image outlines
            </label>
            <label className={`flex items-center gap-2 ${data.aoi ? "text-fg-muted" : "text-fg-subtle"}`}>
              <input
                type="checkbox"
                className="accent-accent"
                checked={showAoi && Boolean(data.aoi)}
                disabled={!data.aoi}
                onChange={(e) => setShowAoi(e.target.checked)}
              />
              Project area{data.aoi ? "" : " (not set)"}
            </label>
          </div>
        </div>
      </Panel>

      <Panel
        title="Photographs"
        description="Photos whose location you have confirmed. A position found inside a photo is only a suggestion until you confirm it."
        padded={false}
      >
        <div className="space-y-1 p-2">
          {photosQuery.isLoading ? <Loading label="Loading photo locations…" /> : null}
          {photosQuery.isError ? <ErrorNotice error={photosQuery.error} title="Photo locations could not be loaded" /> : null}
          {photosQuery.data ? (
            <>
              <label className="flex items-center gap-2 px-2 py-1 text-[13px] text-fg-muted">
                <input
                  type="checkbox"
                  className="accent-accent"
                  checked={showPhotos}
                  disabled={photos.length === 0}
                  onChange={(e) => setShowPhotos(e.target.checked)}
                />
                Photos on the map ({photos.length})
              </label>
              {photos.length === 0 ? (
                <p className="px-2 py-1 text-xs text-fg-subtle" data-testid="no-located-photos">
                  No photo has a confirmed location yet. Open a photo in Photo Analysis to use its GPS position or set its location.
                </p>
              ) : (
                <ul aria-label="Located photographs" className="space-y-0.5">
                  {photos.map((photo) => (
                    <li key={photo.asset_id}>
                      <button
                        type="button"
                        aria-pressed={photo.asset_id === selectedPhotoId}
                        onClick={() => onSelectPhoto(photo.asset_id)}
                        className={`flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-[13px] ${photo.asset_id === selectedPhotoId ? "bg-accent-soft text-fg" : "text-fg-muted hover:bg-overlay hover:text-fg"}`}
                      >
                        <span className="min-w-0 flex-1 truncate">{photo.filename}</span>
                        <span className="text-[11px] text-fg-subtle">{photo.observations} finding(s)</span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
              {photosQuery.data.candidates > 0 ? (
                <p className="px-2 pt-1 text-xs text-warn">
                  {photosQuery.data.candidates} photo(s) have a GPS position that is not confirmed yet. They appear on the map after you
                  confirm them in Photo Analysis.
                </p>
              ) : null}
            </>
          ) : null}
        </div>
      </Panel>

      <Panel title="Not on the map" description="Files that can't be shown because their location is missing or unusable." padded={false}>
        {data.excluded.length === 0 ? (
          <p className="p-4 text-xs text-fg-subtle">None.</p>
        ) : (
          <ul aria-label="Files not placed on the map" className="divide-y divide-line">
            {data.excluded.map((item) => (
              <li key={item.asset_id} className="px-4 py-2 text-[13px]">
                <span className="block truncate text-fg-muted">{item.title}</span>
                {item.map_sheet ? (
                  <span className="block text-[11px] text-fg-subtle">
                    Map sheet · {SERIES_SHORT[item.map_sheet.series]}
                    {item.map_sheet.sheet_label ? ` · ${item.map_sheet.sheet_label}` : ""}
                  </span>
                ) : null}
                <span className="block text-[11px] text-warn">{EXCLUSION_LABELS[item.reason] ?? item.reason}</span>
                <span className="block text-[11px] text-fg-subtle">{plainReason(item)}</span>
                {item.georeferencing_can_be_recorded ? (
                  <Link to={`/projects/${projectId}/assets/${item.asset_id}#georeferencing`} className="mt-0.5 mr-3 inline-block text-[11px] text-accent hover:underline">
                    Add location information…
                  </Link>
                ) : null}
                {item.map_sheet ? (
                  <Link
                    to={`/projects/${projectId}/assets/${item.asset_id}#${item.georeferencing_can_be_recorded ? "control-points" : "map-sheet"}`}
                    className="mt-0.5 inline-block text-[11px] text-accent hover:underline"
                  >
                    {item.georeferencing_can_be_recorded ? "Place using reference points…" : "Review the sheet…"}
                  </Link>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </Panel>

    </div>
  );
}

export type MapTool = "pin" | "distance" | "area";

function MeasurePanel({
  tool,
  points,
  onClear,
  onUndo,
}: {
  tool: "distance" | "area";
  points: [number, number][];
  onClear: () => void;
  onUndo: () => void;
}) {
  const measure = useMutation({ mutationFn: () => measurePoints({ kind: tool, space: "lonlat", points }) });
  return (
    <div className="space-y-2">
      <p className="text-xs text-fg-subtle">Click the map to add points ({points.length} so far). Measured along the Earth's surface.</p>
      <div className="flex flex-wrap gap-2">
        <Button size="sm" disabled={points.length < (tool === "area" ? 3 : 2) || measure.isPending} onClick={() => measure.mutate()}>
          Measure
        </Button>
        <Button size="sm" variant="secondary" disabled={points.length === 0} onClick={() => { measure.reset(); onUndo(); }}>
          Remove last point
        </Button>
        <Button size="sm" variant="ghost" disabled={points.length === 0} onClick={() => { measure.reset(); onClear(); }}>
          Clear
        </Button>
      </div>
      {measure.isError ? <ErrorNotice error={measure.error} /> : null}
      {measure.data ? <MeasurementResult result={measure.data} /> : null}
    </div>
  );
}

function RasterValuesAt({
  point,
  layers,
  chosen,
}: {
  point: { lon: number; lat: number };
  layers: RasterLayer[];
  chosen: Record<string, string>;
}) {
  const under = layers.filter((l) => {
    const [w, s, e, n] = l.bounds_4326 as [number, number, number, number];
    return point.lon >= w && point.lon <= e && point.lat >= s && point.lat <= n;
  });
  const results = useQueries({
    queries: under.map((layer) => ({
      queryKey: ["pixel-at", layer.asset_id, layer.georef_version, point.lon, point.lat, chosen[layer.asset_id] ?? null],
      queryFn: () => getPixel(layer.asset_id, { lon: point.lon, lat: point.lat, operation: chosen[layer.asset_id] }),
      retry: false,
    })),
  });
  if (under.length === 0) return <p className="text-xs text-fg-subtle">No image on the map covers this point.</p>;
  return (
    <ul className="space-y-1.5 text-xs" aria-label="Image values at the pinned point">
      {under.map((layer, i) => {
        const result = results[i];
        return (
          <li key={layer.asset_id}>
            <span className="text-fg">{layer.title}</span>
            {result?.data ? (
              <span className="text-fg-muted">
                {" "}
                · pixel {result.data.col}, {result.data.row}:{" "}
                <span className="font-mono">
                  {result.data.values.map((v) => (v.nodata ? "nodata" : String(v.raw))).join(", ")}
                </span>
              </span>
            ) : result?.isError ? (
              <span className="text-fg-subtle">
                {result.error instanceof ApiError && result.error.code.startsWith("TRANSFORMATION_")
                  ? ` · not looked up: ${result.error.message}`
                  : " · outside the image"}
              </span>
            ) : (
              <span className="text-fg-subtle"> · reading…</span>
            )}
          </li>
        );
      })}
    </ul>
  );
}

function Readout({
  pointer,
  picked,
  crsChoices,
  tool,
  setTool,
  sketch,
  setSketch,
  layers,
  chosen,
}: {
  pointer: { lon: number; lat: number } | null;
  picked: { lon: number; lat: number } | null;
  crsChoices: string[];
  tool: MapTool;
  setTool: (tool: MapTool) => void;
  sketch: [number, number][];
  setSketch: (points: [number, number][]) => void;
  layers: RasterLayer[];
  chosen: Record<string, string>;
}) {
  const [target, setTarget] = useState(crsChoices.find((c) => c !== "EPSG:4326") ?? "EPSG:4326");
  useEffect(() => {
    if (!crsChoices.includes(target)) setTarget(crsChoices[0] ?? "EPSG:4326");
  }, [crsChoices, target]);
  // A layer in a CRS with a registered datum shift is read out through that same shift, so the readout and the picture agree.
  const operation = layers.find((layer) => layer.crs_id === target && layer.transformation?.classification === "approximate")?.transformation
    ?.operation_id;
  // A readout may use a datum shift worse than 10 m only if the person acknowledges it for this readout. That is not stored
  // anywhere and does not apply to any raster: it is tied to the operation and accuracy that were refused.
  const [readoutAck, setReadoutAck] = useState<{ operation: string; accuracy: number | null } | null>(null);
  useEffect(() => {
    setReadoutAck(null);
  }, [target, picked?.lon, picked?.lat]);
  const converted = useQuery({
    queryKey: ["transform", target, picked?.lon, picked?.lat, operation, readoutAck],
    queryFn: () =>
      transformPoint({
        source: "EPSG:4326",
        target,
        x: picked!.lon,
        y: picked!.lat,
        ...(operation ? { operation } : {}),
        ...(readoutAck
          ? { acknowledge_operation: readoutAck.operation, ...(readoutAck.accuracy != null ? { acknowledge_accuracy_m: readoutAck.accuracy } : {}) }
          : {}),
      }),
    enabled: picked !== null && target !== "EPSG:4326",
    retry: false,
  });
  return (
    <Panel title="Coordinates and measuring" description="Pin a point to read its position, or measure a distance or an area.">
      <div className="space-y-3 text-[13px]">
        <div className="flex flex-wrap gap-1.5" role="group" aria-label="Map tools">
          {(
            [
              ["pin", "Pin a point"],
              ["distance", "Measure distance"],
              ["area", "Measure area"],
            ] as const
          ).map(([name, label]) => (
            <Button key={name} size="sm" variant={tool === name ? "primary" : "secondary"} aria-pressed={tool === name} onClick={() => { setTool(name); setSketch([]); }}>
              {label}
            </Button>
          ))}
        </div>
        {tool !== "pin" ? <MeasurePanel key={tool} tool={tool} points={sketch} onClear={() => setSketch([])} onUndo={() => setSketch(sketch.slice(0, -1))} /> : null}
        <p className="font-mono text-xs text-fg-muted" aria-live="off">
          {pointer
            ? `Cursor: ${formatDecimalDegrees(pointer.lat, "lat", 5)}, ${formatDecimalDegrees(pointer.lon, "lon", 5)}`
            : "Cursor: not over the map"}
        </p>
        {picked ? (
          <div className="space-y-2" role="status" aria-label="Pinned point">
            <DescriptionList
              labelWidth="sm"
              rows={[
                ["Latitude", <span className="font-mono text-xs">{formatDecimalDegrees(picked.lat, "lat")} · {formatDms(picked.lat, "lat")}</span>],
                ["Longitude", <span className="font-mono text-xs">{formatDecimalDegrees(picked.lon, "lon")} · {formatDms(picked.lon, "lon")}</span>],
                ["Coordinate system", "GPS (WGS 84, EPSG:4326)"],
              ]}
            />
            {crsChoices.length > 1 ? (
              <div>
                <label htmlFor="readout-crs" className="mb-1 block text-xs text-fg-muted">
                  Also show in
                </label>
                <select id="readout-crs" className={selectClass} value={target} onChange={(e) => setTarget(e.target.value)}>
                  {crsChoices.map((choice) => (
                    <option key={choice} value={choice}>
                      {choice}
                    </option>
                  ))}
                </select>
              </div>
            ) : null}
            {target !== "EPSG:4326" ? <Converted query={converted} onAcknowledge={(need) => setReadoutAck({ operation: need.operationId, accuracy: need.accuracyM })} /> : null}
            <div className="border-t border-line pt-2">
              <p className="mb-1 text-xs font-medium text-fg-muted">Image values at this point</p>
              <RasterValuesAt point={picked} layers={layers} chosen={chosen} />
            </div>
          </div>
        ) : (
          <p className="text-xs text-fg-subtle">No point pinned.</p>
        )}
        <p className="text-[11px] text-fg-subtle">
          The position is simply where you clicked on the map.
        </p>
      </div>
    </Panel>
  );
}

function Converted({
  query,
  onAcknowledge,
}: {
  query: { isLoading: boolean; isError: boolean; error: unknown; data?: CrsTransform };
  onAcknowledge: (need: AcknowledgementNeed) => void;
}) {
  const [accepted, setAccepted] = useState(false);
  if (query.isLoading) return <Loading label="Converting…" />;
  if (query.isError) {
    const need = query.error instanceof ApiError && query.error.code === ACK_REQUIRED ? acknowledgementFromContext(query.error.context) : null;
    if (!need) return <ErrorNotice error={query.error} />;
    return (
      <div className="space-y-2">
        <AccuracyAcknowledgement
          need={need}
          checked={accepted}
          onChange={setAccepted}
          idPrefix="readout"
          where="For this readout only. Nothing is stored, and it does not apply to any raster or other result."
        />
        <Button size="sm" disabled={!accepted} onClick={() => onAcknowledge(need)}>
          Show the readout with this shift
        </Button>
      </div>
    );
  }
  const data = query.data;
  if (!data) return null;
  // x is the first coordinate of the CRS, whichever way that axis runs: it is not always east.
  const [first, second] = data.xy_axes.length === 2 ? data.xy_axes : data.target_axes;
  const unit = first?.unit ?? "";
  return (
    <div className="space-y-1.5">
      <DescriptionList
        labelWidth="sm"
        rows={[
          [axisHeading(first, "x"), <span className="font-mono text-xs">{data.x.toFixed(3)} {unit}</span>],
          [axisHeading(second, "y"), <span className="font-mono text-xs">{data.y.toFixed(3)} {second?.unit ?? unit}</span>],
          ["CRS", data.target_crs_id],
          ["Accuracy", data.transformation.accuracy_m == null ? "not stated by PROJ" : `${String(data.transformation.accuracy_m)} m`],
        ]}
      />
      {data.warnings.includes("OUTSIDE_AREA_OF_USE") ? (
        <Notice tone="warning" title="Outside this CRS's area of use">
          The numbers are computed, but the CRS is not meant to be used here.
        </Notice>
      ) : null}
      <p className="text-[11px] text-fg-subtle">{data.order_note} PROJ {String(data.transformation.proj_version)}, network off.</p>
    </div>
  );
}

function LayerDisplay({ layer, value, onChange }: { layer: RasterLayer; value: DisplayQuery; onChange: (q: DisplayQuery) => void }) {
  const tools = useQuery({ queryKey: ["raster-tools", layer.asset_id], queryFn: () => getRasterTools(layer.asset_id) });
  if (!tools.data) return tools.isError ? <ErrorNotice error={tools.error} /> : <Loading />;
  return <DisplayControls key={layer.asset_id} idPrefix={`map-${layer.asset_id}`} assetId={layer.asset_id} tools={tools.data} value={value} onChange={onChange} />;
}

function LayerDetails({
  layer,
  displayQuery,
  setDisplayQuery,
}: {
  layer: RasterLayer | null;
  displayQuery: DisplayQuery;
  setDisplayQuery: (q: DisplayQuery) => void;
}) {
  const project = useProject();
  if (!layer) {
    return (
      <Panel title="Layer details">
        <p className="text-[13px] text-fg-subtle">Choose Details on a layer.</p>
      </Panel>
    );
  }
  const transformation = layer.footprint_transformation as { definition?: string; accuracy_m?: number | null; proj_version?: string } | null;
  return (
    <>
    <Panel
      title="Layer details"
      actions={
        layer.placement === "trusted" ? (
          <Badge tone="ok">Location read from the file</Badge>
        ) : (
          <Badge tone="accent">Location entered by a person</Badge>
        )
      }
    >
      <div className="space-y-3">
        <p className="wrap-anywhere text-[13px] font-medium text-fg">{layer.title}</p>
        <DescriptionList
          rows={[
            ...(layer.map_sheet
              ? ([
                  ["Kind", `Map sheet · ${layer.map_sheet.series_label}`],
                  ["Sheet", [layer.map_sheet.sheet_label, layer.map_sheet.scale_denominator ? `1 : ${layer.map_sheet.scale_denominator}` : null, layer.map_sheet.edition].filter(Boolean).join(" · ") || "not stated"],
                  ["Position established by", layer.map_sheet.georeferencing_basis_label],
                ] as [string, string][])
              : []),
            ["Coordinate system", `${layer.crs_id ?? "no code"} — ${layer.crs_name ?? ""}`],
            ["Detail per pixel", `${layer.pixel_size.map((v) => v.toPrecision(4)).join(" × ")} ${layer.pixel_units ?? ""}`],
            ["Area covered (latitude, longitude)", <span className="font-mono text-xs">{layer.bounds_4326.map((v) => v.toFixed(6)).join(", ")}</span>],
                        ["Colour channels shown", layer.bands_shown.join(", ")],
            [
              "Display",
              typeof layer.display_stretch === "string"
                ? layer.display_stretch
                : "Brightness adjusted so it is easier to see; the stored values are not changed",
            ],
            [
              "How the position was converted",
              layer.transformation?.classification
                ? describeOperation({
                    id: layer.transformation.operation_id ?? "",
                    classification: layer.transformation.classification,
                    accuracy_m: layer.transformation.accuracy_m,
                  })
                : "none chosen yet",
            ],
            ["Outline conversion", transformation?.definition ?? "not recorded"],
            ["Accuracy", transformation?.accuracy_m == null ? "not stated" : `${transformation.accuracy_m} m`],
          ]}
        />
        {layer.map_sheet ? (
          <Link to={`/projects/${project.id}/assets/${layer.asset_id}#map-sheet`} className="block text-xs text-accent hover:underline">
            Map sheet details
          </Link>
        ) : null}
        <Link to={`/projects/${project.id}/assets/${layer.asset_id}#crs-assessment`} className="block text-xs text-accent hover:underline">
          Coordinate system details and options
        </Link>
        {layer.placement === "user_asserted" ? (
          <p className="text-xs text-fg-muted">
            Its location was entered by a person (version {layer.georef_version}), not read from the file. The software cannot
            check that it is correct.{" "}
            <Link to={`/projects/${project.id}/assets/${layer.asset_id}#georeferencing`} className="text-accent hover:underline">
              See the record
            </Link>
          </p>
        ) : null}
        <Link to={`/projects/${project.id}/assets/${layer.asset_id}/overhead`} className="text-xs text-accent hover:underline">
          Open in Satellite Images
        </Link>
      </div>
    </Panel>
    <LayerDisplay layer={layer} value={displayQuery} onChange={setDisplayQuery} />
    </>
  );
}

export function MapPage() {
  const project = useProject();
  // Datum transformations chosen for rasters where several apply. They live in this view only; nothing is stored.
  const [chosen, setChosen] = useState<Record<string, string>>({});
  const layers = useQuery({
    queryKey: ["layers", project.id, chosen],
    queryFn: () => getLayers(project.id, Object.entries(chosen).map(([assetId, operationId]) => `${assetId}:${operationId}`)),
    placeholderData: keepPreviousData,
    // A raster uploaded a moment ago is placed before its picture is ready (the worker is still preparing it).
    refetchInterval: (query) =>
      query.state.dataUpdateCount < 60 && query.state.data?.rasters.some((layer) => !layer.tile_url && layer.display_unavailable_reason) ? 3000 : false,
  });
  const [display, setDisplayState] = useState<Record<string, { visible: boolean; opacity: number }>>({});
  // The stacking order a person chose, asset ids topmost first. Layers not listed follow in the default order (newest on top).
  const [stack, setStack] = useState<string[]>([]);
  const [showAoi, setShowAoi] = useState(true);
  const [showFootprints, setShowFootprints] = useState(true);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [pointer, setPointer] = useState<{ lon: number; lat: number } | null>(null);
  const [picked, setPicked] = useState<{ lon: number; lat: number } | null>(null);
  const [tool, setTool] = useState<MapTool>("pin");
  const [sketch, setSketch] = useState<[number, number][]>([]);
  const [displayQueries, setDisplayQueries] = useState<Record<string, DisplayQuery>>({});
  const [fitTo, setFitTo] = useState<Bounds | null>(null);
  // The basemap. Mapbox Standard Satellite when a public token is configured (the person chose to set one up), otherwise the
  // offline map. Either can be chosen by hand.
  const [basemap, setBasemap] = useState<Basemap>(() => (readMapboxToken().ok ? "mapbox" : "offline"));
  const [mapboxLabels, setMapboxLabels] = useState<LabelState>(DEFAULT_LABELS);
  const [basemapStatus, setBasemapStatus] = useState<BasemapStatus | null>(null);
  const [goTo, setGoTo] = useState<{ lon: number; lat: number; zoom?: number } | null>(null);
  const [showPhotos, setShowPhotos] = useState(true);
  const [selectedPhotoId, setSelectedPhotoId] = useState<string | null>(null);
  // Photographs a map click could have meant, when markers overlap. Nothing is opened until the person chooses.
  const [choice, setChoice] = useState<string[] | null>(null);
  // The person's own annotations: a shape being drawn, the one selected, and whether they are drawn at all.
  const [drawing, setDrawing] = useState<Drawing | null>(null);
  const [selectedAnnotationId, setSelectedAnnotationId] = useState<string | null>(null);
  const [showAnnotations, setShowAnnotations] = useState(true);
  const annotations = useQuery({
    queryKey: ["annotations", project.id, false],
    queryFn: () => listAnnotations(project.id, false),
    retry: false,
  });
  const photoLocations = useQuery({ queryKey: ["photo-locations", project.id], queryFn: () => listPhotoLocations(project.id) });
  const data = layers.data;

  // Only photographs with a person-confirmed location have coordinates here; the API sends none for the rest.
  const located = useMemo(
    () => (photoLocations.data?.items ?? []).filter((item) => item.state === "confirmed" && item.latitude != null && item.longitude != null),
    [photoLocations.data],
  );
  const markers: PhotoMarker[] = useMemo(
    () => located.map((item) => ({ id: item.asset_id, lon: item.longitude!, lat: item.latitude!, label: item.filename })),
    [located],
  );
  const selectedPhoto = located.find((item) => item.asset_id === selectedPhotoId) ?? null;
  // Only active annotations are drawn; deleted ones are never on the map.
  const annotationShapes: AnnotationShape[] = useMemo(
    () =>
      (annotations.data?.items ?? [])
        .filter((item) => item.status === "active")
        .map((item) => ({ id: item.annotation_id, geometry: item.geometry })),
    [annotations.data],
  );
  const annotationBounds = useMemo(
    () => union(annotationShapes.map((shape) => boundsOf(shape.geometry as never))),
    [annotationShapes],
  );
  // A drawing in progress is shown with the same sketch and pin the measurement tool uses (blue), not as a saved mark.
  const accepting = drawing?.active === true;

  const everything = useMemo(
    () =>
      data
        ? (union([
            ...data.rasters.map((r) => r.bounds_4326 as Bounds),
            ...[aoiBounds(data.aoi?.geometry_4326)].filter((b): b is Bounds => b !== null),
            ...markers.map((m): Bounds => [m.lon - 0.01, m.lat - 0.01, m.lon + 0.01, m.lat + 0.01]),
          ]) ?? annotationBounds)
        : null,
    [data, markers, annotationBounds],
  );
  useEffect(() => {
    if (everything) setFitTo(everything);
  }, [everything]);

  const ordered: RasterLayer[] = useMemo(() => {
    const newestFirst = [...(data?.rasters ?? [])].reverse();
    const known = stack.map((id) => newestFirst.find((l) => l.asset_id === id)).filter((l): l is RasterLayer => Boolean(l));
    return [...known, ...newestFirst.filter((l) => !known.includes(l))];
  }, [data, stack]);
  const moveLayer = (assetId: string, direction: "up" | "down") => {
    const ids = ordered.map((l) => l.asset_id);
    const at = ids.indexOf(assetId);
    const to = direction === "up" ? at - 1 : at + 1;
    if (at < 0 || to < 0 || to >= ids.length) return;
    [ids[at], ids[to]] = [ids[to]!, ids[at]!];
    setStack(ids);
  };
  // The map draws its first layer at the bottom, so the top-first list is reversed for it.
  const rasters: RasterDisplay[] = useMemo(
    () =>
      [...ordered].reverse().map((layer) => ({
        layer,
        ...(display[layer.asset_id] ?? { visible: true, opacity: 1 }),
        query: displayParams(displayQueries[layer.asset_id] ?? {}),
      })),
    [ordered, display, displayQueries],
  );
  const crsChoices = useMemo(() => {
    const ids = new Set<string>(["EPSG:4326"]);
    for (const layer of data?.rasters ?? []) if (layer.crs_id) ids.add(layer.crs_id);
    if (data?.aoi?.entered_crs_id) ids.add(data.aoi.entered_crs_id);
    return [...ids];
  }, [data]);

  const setDisplay = (id: string, change: Partial<{ visible: boolean; opacity: number }>) =>
    setDisplayState((current) => ({ ...current, [id]: { ...(current[id] ?? { visible: true, opacity: 1 }), ...change } }));

  const nothingToDraw = data && data.rasters.length === 0 && !data.aoi && markers.length === 0 && annotationShapes.length === 0;

  return (
    <div>
      <Breadcrumbs items={projectCrumbs(project, { label: "Map" })} />
      <div className="mt-2">
        <PageHeader
          title="Map"
          description="See your satellite images and photos on a map. Only files with a known location are shown."
          meta={data ? <Badge tone="neutral">{data.rasters.length} on the map · {data.excluded.length} not on the map</Badge> : undefined}
          actions={
            everything ? (
              <Button variant="secondary" onClick={() => setFitTo([...everything])}>
                <IconLayers size={15} />
                Fit to layers
              </Button>
            ) : undefined
          }
        />
      </div>
      {layers.isLoading ? <Loading label="Loading layers…" /> : null}
      {layers.isError ? <ErrorNotice error={layers.error} /> : null}
      {data ? (
        <Workspace
          left={
            <LayerPanel
              data={data}
              ordered={ordered}
              onMove={moveLayer}
              chosen={chosen}
              onChoose={(assetId, operationId) => setChosen((current) => ({ ...current, [assetId]: operationId }))}
              display={display}
              setDisplay={setDisplay}
              showAoi={showAoi}
              setShowAoi={setShowAoi}
              showFootprints={showFootprints}
              setShowFootprints={setShowFootprints}
              selectedId={selectedId}
              onSelect={(id) => setSelectedId(id === selectedId ? null : id)}
              onZoom={(bounds) => setFitTo([...bounds])}
              photos={located}
              photosQuery={photoLocations}
              showPhotos={showPhotos}
              setShowPhotos={setShowPhotos}
              selectedPhotoId={selectedPhotoId}
              onSelectPhoto={(id) => {
                setSelectedPhotoId(id);
                const photo = located.find((item) => item.asset_id === id);
                if (photo) setFitTo([photo.longitude! - 0.01, photo.latitude! - 0.01, photo.longitude! + 0.01, photo.latitude! + 0.01]);
              }}
            />
          }
          main={
            <div className="space-y-3">
              {nothingToDraw ? (
                <EmptyState icon={<IconMap size={28} />} title="Nothing to show on the map yet">
                  Add a satellite image that carries its location, or give the project an area, to see it here. Files that can't be shown
                  are listed on the left with the reason.
                </EmptyState>
              ) : null}
              <Suspense fallback={<Loading label="Loading the map…" />}>
                <MapCanvas
                  rasters={rasters}
                  aoi={data.aoi?.geometry_4326 ?? null}
                  showAoi={showAoi}
                  showFootprints={showFootprints}
                  selectedRasterId={selectedId}
                  fitTo={fitTo}
                  basemap={basemap}
                  mapboxLabels={mapboxLabels}
                  goTo={goTo}
                  onBasemapStatus={setBasemapStatus}
                  onPointer={setPointer}
                  onPick={(point) => {
                    if (drawing) {
                      // While an annotation is being drawn, clicks add its positions and nothing else.
                      if (drawing.active) {
                        const next: [number, number] = [point.lon, point.lat];
                        setDrawing({ ...drawing, points: drawing.kind === "point" ? [next] : [...drawing.points, next] });
                      }
                      return;
                    }
                    if (tool === "pin") setPicked(point);
                    else setSketch((current) => [...current, [point.lon, point.lat]]);
                  }}
                  pinned={drawing ? (drawing.kind === "point" && drawing.points[0] ? { lon: drawing.points[0][0], lat: drawing.points[0][1] } : null) : picked}
                  sketch={
                    drawing
                      ? drawing.kind === "point" || drawing.points.length === 0
                        ? null
                        : { kind: drawing.kind === "line" ? "distance" : "area", points: drawing.points }
                      : tool === "pin"
                        ? null
                        : { kind: tool, points: sketch }
                  }
                  annotations={annotationShapes}
                  showAnnotations={showAnnotations}
                  selectedAnnotationId={selectedAnnotationId}
                  onAnnotationSelect={!drawing && tool === "pin" ? setSelectedAnnotationId : undefined}
                  photos={markers}
                  showPhotos={showPhotos}
                  selectedPhotoId={selectedPhotoId}
                  onPhotoSelect={
                    accepting
                      ? undefined
                      : (id) => {
                          setChoice(null);
                          setSelectedPhotoId(id);
                        }
                  }
                  onPhotoChoice={accepting ? undefined : (ids) => setChoice(ids)}
                />
              </Suspense>
              {choice ? (
                <Notice tone="warning" title="Several photographs are close together here">
                  <p className="text-xs">Their markers overlap at this zoom. Choose one, or zoom in.</p>
                  <ul aria-label="Photographs near the click" className="mt-1 flex flex-wrap gap-1.5">
                    {choice.map((id) => {
                      const photo = located.find((item) => item.asset_id === id);
                      return photo ? (
                        <li key={id}>
                          <Button
                            size="sm"
                            variant="secondary"
                            onClick={() => {
                              setChoice(null);
                              setSelectedPhotoId(id);
                            }}
                          >
                            {photo.filename}
                          </Button>
                        </li>
                      ) : null;
                    })}
                    <li>
                      <Button size="sm" variant="ghost" onClick={() => setChoice(null)}>
                        Dismiss
                      </Button>
                    </li>
                  </ul>
                </Notice>
              ) : null}
              <p className="text-xs text-fg-subtle">
                {data.observations.with_location} finding(s) have a known location and{" "}
                {data.observations.without_location} do not. Findings from photos mark a spot inside the picture, not a place on
                the map, so you will see them in Photo Analysis.
              </p>
              {data.aoi ? <p className="text-xs text-fg-subtle">Dashed outline: {data.aoi.note}</p> : null}
            </div>
          }
          right={
            <div className="space-y-4">
              <BasemapPanel
                basemap={basemap}
                onBasemap={setBasemap}
                labels={mapboxLabels}
                onLabels={setMapboxLabels}
                status={basemapStatus}
                onGo={(position) => setGoTo({ ...position })}
              />
              {selectedPhoto ? (
                <PhotoMapPanel key={selectedPhoto.asset_id} projectId={project.id} item={selectedPhoto} onClose={() => setSelectedPhotoId(null)} />
              ) : null}
              <AnnotationPanel
                projectId={project.id}
                drawing={drawing}
                onDrawing={setDrawing}
                selectedId={selectedAnnotationId}
                onSelect={setSelectedAnnotationId}
                showOnMap={showAnnotations}
                onShowOnMap={setShowAnnotations}
                onZoom={(bounds) => setFitTo([...bounds])}
              />
              <Readout
                pointer={pointer}
                picked={picked}
                crsChoices={crsChoices}
                tool={tool}
                setTool={setTool}
                sketch={sketch}
                setSketch={setSketch}
                layers={data.rasters.filter((r) => (display[r.asset_id]?.visible ?? true))}
                chosen={chosen}
              />
              <AlignmentPanel projectId={project.id} layers={ordered} />
              <LayerDetails
                layer={data.rasters.find((r) => r.asset_id === selectedId) ?? null}
                displayQuery={(selectedId && displayQueries[selectedId]) || {}}
                setDisplayQuery={(q) => selectedId && setDisplayQueries((current) => ({ ...current, [selectedId]: q }))}
              />
            </div>
          }
          leftLabel="Layers"
          rightLabel="Coordinates and image details"
        />
      ) : null}
    </div>
  );
}
