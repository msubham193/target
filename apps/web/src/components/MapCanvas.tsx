import "maplibre-gl/dist/maplibre-gl.css";
import "mapbox-gl/dist/mapbox-gl.css";

import type { GeoJSONSource, LngLatBoundsLike, Map as MapLibreMap, StyleSpecification } from "maplibre-gl";
import { useEffect, useMemo, useRef, useState } from "react";

import type { RasterLayer } from "../api/client";
import { graticule } from "../lib/geo";
import { chooseMarker, HIT_RADIUS_PX, type ScreenMarker } from "../lib/markers";
import {
  type Basemap,
  type BasemapStatus,
  DEFAULT_LABELS,
  type LabelState,
  MAPBOX_STYLE,
  readMapboxToken,
  redactSecrets,
} from "../lib/mapbox";
import { Loading, Notice } from "./ui";

// MapLibre's own GeoJSON type, so no separate GeoJSON type package is needed.
type Data = Exclude<Parameters<GeoJSONSource["setData"]>[0], string>;

export interface RasterDisplay {
  layer: RasterLayer;
  visible: boolean;
  opacity: number;
  /** Display settings as a query string (bands, stretch). Display only. */
  query?: string;
}

/** A photograph with a person-confirmed location. Never an EXIF candidate. */
export interface PhotoMarker {
  id: string;
  lon: number;
  lat: number;
  label: string;
}

/**
 * Direction lines from a photograph's camera position towards the objects found in it. Each object is somewhere along its
 * line; only a position a person marked (`marked`) is a place on the ground.
 */
export interface SightLines {
  /** The camera's view as a closed outline, drawn faintly. */
  fan: [number, number][] | null;
  rays: { id: string; colour: string; coordinates: [number, number][] }[];
  /** Positions a person marked for objects, each joined to the camera by a dashed line. */
  marked: { id: string; colour: string; at: [number, number]; from: [number, number] }[];
  /** Positions estimated from matched ground spots; hollow when outside the matched area (extrapolated). */
  estimated?: { id: string; colour: string; at: [number, number]; extrapolated: boolean }[];
  /** The matched spots on the map, drawn as small white squares. */
  controls?: { id: string; at: [number, number] }[];
  selectedId: string | null;
}

/** A mark the person drew (an annotation). Not a finding and not a photograph location. */
export interface AnnotationShape {
  id: string;
  geometry: Record<string, unknown>;
}

export interface MapCanvasProps {
  rasters: RasterDisplay[];
  /** The project's area of interest in EPSG:4326, or null. */
  aoi: Record<string, unknown> | null;
  showAoi: boolean;
  showFootprints: boolean;
  selectedRasterId: string | null;
  /** [west, south, east, north] to fit; a new array asks for a new fit. */
  fitTo: [number, number, number, number] | null;
  onPointer?: (position: { lon: number; lat: number } | null) => void;
  onPick?: (position: { lon: number; lat: number }) => void;
  /** The point pinned by a click, drawn as a marker. */
  pinned?: { lon: number; lat: number } | null;
  /** Points of a measurement being drawn. */
  sketch?: { kind: "distance" | "area"; points: [number, number][] } | null;
  /** Photographs with a confirmed location, drawn as markers. */
  photos?: PhotoMarker[];
  showPhotos?: boolean;
  selectedPhotoId?: string | null;
  onPhotoSelect?: (id: string) => void;
  /** Called instead of `onPhotoSelect` when a click could mean several overlapping markers, nearest first. */
  onPhotoChoice?: (ids: string[]) => void;
  /**
   * Called while the pointer is over a photograph marker, with the marker's position on screen (pixels from the map's top
   * left), and with null when it leaves or the map moves.
   */
  onPhotoHover?: (hover: { id: string; x: number; y: number } | null) => void;
  /** The person's own annotations (active ones only), drawn apart from photograph markers and findings. */
  annotations?: AnnotationShape[];
  showAnnotations?: boolean;
  selectedAnnotationId?: string | null;
  /** Called when a click lands on an annotation. Leave it out while drawing, so clicks add positions instead. */
  onAnnotationSelect?: (id: string) => void;
  /** Direction lines from a photograph towards the objects in it (see `SightLines`). */
  sightLines?: SightLines | null;
  /** Called when a click lands on a direction line or a marked object. Leave it out while the map is being clicked to aim. */
  onSightLineSelect?: (id: string) => void;
  /** A position being looked at but not saved (for example an unverified EXIF candidate), drawn as a hollow ring. */
  preview?: { lon: number; lat: number } | null;
  /** Tailwind classes for the frame's size. The default suits a full map page. */
  className?: string;
  /**
   * The basemap. "offline" (the default) is the plain local map. "mapbox" is Mapbox Standard Satellite, drawn by Mapbox GL
   * JS, and needs a public token; without one the offline map is drawn and the status says why. Only one map engine
   * exists at a time: changing this replaces the map and carries the view over.
   */
  basemap?: Basemap;
  /** Which label and feature groups of the Mapbox basemap are shown. Ignored by the offline map. */
  mapboxLabels?: LabelState;
  /** Move the view here. A new object asks for a new move; the same object is acted on once. */
  /** Go to a position. `instant` jumps there instead of flying (for a map that has only just opened). */
  goTo?: { lon: number; lat: number; zoom?: number; instant?: boolean } | null;
  /** Where a newly made map starts (used only when it has no earlier view), so it never flashes the whole world first. */
  start?: { lon: number; lat: number; zoom: number } | null;
  /** Progress and problems of the Mapbox basemap (never called for the offline map). */
  onBasemapStatus?: (status: BasemapStatus) => void;
}

type MapLibreLibrary = Pick<typeof import("maplibre-gl"), "Map" | "NavigationControl" | "ScaleControl">;
// Mapbox GL JS and MapLibre share this API for everything drawn here (sources, layers, filters, events, queries), so the
// overlay code below is written once against MapLibre's types. Mapbox-only calls are made through `MapboxExtras`.
interface MapboxExtras {
  setConfigProperty(importId: string, name: string, value: unknown): void;
}

// The style has no online sources: a plain background, a graticule, and layers served by the local API.
// No glyphs or sprites are referenced, so MapLibre never requests fonts or icons either.
function baseStyle(): StyleSpecification {
  return {
    version: 8,
    sources: {
      graticule: { type: "geojson", data: graticule(10) as unknown as Data },
    },
    layers: [
      { id: "background", type: "background", paint: { "background-color": "#11141a" } },
      { id: "graticule", type: "line", source: "graticule", paint: { "line-color": "#2a303b", "line-width": 0.7 } },
    ],
  };
}

const EMPTY = { type: "FeatureCollection", features: [] } as unknown as Data;

function footprints(rasters: RasterDisplay[]): Data {
  return {
    type: "FeatureCollection",
    features: rasters.map(({ layer }) => ({
      type: "Feature",
      properties: { id: layer.asset_id },
      geometry: layer.footprint_4326,
    })),
  } as unknown as Data;
}

function startCentre(start: MapCanvasProps["start"]): [number, number] {
  return start ? [start.lon, start.lat] : [0, 20];
}

function aoiData(aoi: Record<string, unknown> | null): Data {
  if (!aoi) return EMPTY;
  return { type: "FeatureCollection", features: [{ type: "Feature", properties: {}, geometry: aoi }] } as unknown as Data;
}

/**
 * The map. MapLibre is loaded only when this component is shown. Rasters are drawn from the API's tile endpoint,
 * which reprojects each raster from its own CRS; nothing here positions anything by assumption.
 */
export function MapCanvas(props: MapCanvasProps) {
  const container = useRef<HTMLDivElement>(null);
  const map = useRef<MapLibreMap | null>(null);
  const [ready, setReady] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const callbacks = useRef(props);
  callbacks.current = props;
  const token = useMemo(() => readMapboxToken(), []);
  // The Mapbox basemap is drawn only when it was asked for and a usable public token exists.
  const engine: Basemap = props.basemap === "mapbox" && token.ok ? "mapbox" : "offline";
  const labels = props.mapboxLabels ?? DEFAULT_LABELS;
  const labelsNow = useRef(labels);
  labelsNow.current = labels;
  // Where the view was last, so replacing the map (a basemap change) keeps zoom and position.
  const lastView = useRef<{ center: [number, number]; zoom: number; bearing: number } | null>(null);
  const fitted = useRef<unknown>(null);
  const moved = useRef<unknown>(null);

  // Create the map, and create it again only when the basemap engine changes. Never two at once.
  useEffect(() => {
    let cancelled = false;
    let instance: MapLibreMap | null = null;
    setReady(false);
    setFailure(null);
    const report = (status: BasemapStatus) => {
      if (engine === "mapbox") callbacks.current.onBasemapStatus?.(status);
    };
    report({ state: "loading" });
    const loadLibrary = (): Promise<MapLibreLibrary> =>
      engine === "mapbox"
        ? import("mapbox-gl").then((module) => module.default as unknown as MapLibreLibrary)
        : import("maplibre-gl").then((module) => module.default as unknown as MapLibreLibrary);
    void loadLibrary()
      .then((maplibre) => {
        if (cancelled || !container.current) return;
        const view = lastView.current;
        try {
          if (engine === "mapbox" && token.ok) {
            (maplibre as unknown as { accessToken: string }).accessToken = token.token;
            const options: Record<string, unknown> = {
              container: container.current,
              style: MAPBOX_STYLE,
              // Label groups are set when the map is made, so a map made with a label off never draws it first.
              config: { basemap: { ...labelsNow.current } },
              center: view?.center ?? startCentre(callbacks.current.start),
              zoom: view?.zoom ?? (callbacks.current.start?.zoom ?? 1),
              bearing: view?.bearing ?? 0,
              // Mercator, flat: the project's rasters, measurements and coordinates are all Web Mercator / geodetic.
              projection: "mercator",
              attributionControl: true,
              maxZoom: 22,
              pitchWithRotate: false,
              maxPitch: 0,
            };
            instance = new (maplibre.Map as unknown as new (o: Record<string, unknown>) => MapLibreMap)(options);
          } else {
            instance = new maplibre.Map({
              container: container.current,
              style: baseStyle(),
              center: view?.center ?? startCentre(callbacks.current.start),
              zoom: view?.zoom ?? (callbacks.current.start?.zoom ?? 1),
              attributionControl: false,
              maxZoom: 22,
              dragRotate: false,
              pitchWithRotate: false,
            });
          }
        } catch (error) {
          const message = redactSecrets(error instanceof Error ? error.message : "The map could not be started.");
          report({ state: "error", message });
          setFailure(message);
          return;
        }
        instance.addControl(new maplibre.NavigationControl({ showCompass: engine === "mapbox" }), "top-right");
        instance.addControl(new maplibre.ScaleControl({ unit: "metric" }), "bottom-left");
        if (engine === "offline") instance.touchZoomRotate.disableRotation();
        instance.on("moveend", () => {
          if (!instance) return;
          const center = instance.getCenter();
          // The offline map is always north-up, so it never writes a bearing: the Mapbox bearing is kept for the way back.
          const bearing = engine === "mapbox" ? instance.getBearing() : (lastView.current?.bearing ?? 0);
          lastView.current = { center: [center.lng, center.lat], zoom: instance.getZoom(), bearing };
          report({ state: "ready", zoom: instance.getZoom() });
        });
        instance.on("load", () => {
          if (cancelled || !instance) return;
          instance.addSource("aoi", { type: "geojson", data: EMPTY });
          instance.addLayer({ id: "aoi-fill", type: "fill", source: "aoi", paint: { "fill-color": "#e6b450", "fill-opacity": 0.06 } });
          instance.addLayer({
            id: "aoi-line",
            type: "line",
            source: "aoi",
            paint: { "line-color": "#e6b450", "line-width": 1.5, "line-dasharray": [3, 2] },
          });
          instance.addLayer({
            id: "aoi-point",
            type: "circle",
            source: "aoi",
            filter: ["==", ["geometry-type"], "Point"],
            paint: { "circle-radius": 5, "circle-color": "#e6b450", "circle-stroke-color": "#11141a", "circle-stroke-width": 1.5 },
          });
          instance.addSource("footprints", { type: "geojson", data: EMPTY });
          instance.addLayer({ id: "footprints", type: "line", source: "footprints", paint: { "line-color": "#5b9bff", "line-width": 1.2 } });
          instance.addLayer({
            id: "footprint-selected",
            type: "line",
            source: "footprints",
            filter: ["==", ["get", "id"], ""],
            paint: { "line-color": "#e7eaf0", "line-width": 2.4 },
          });
          // The person's own annotations: magenta, apart from the green photographs, the blue measurement sketch and
          // the amber area of interest.
          instance.addSource("annotations", { type: "geojson", data: EMPTY });
          instance.addLayer({
            id: "annotation-fill",
            type: "fill",
            source: "annotations",
            filter: ["==", ["geometry-type"], "Polygon"],
            paint: { "fill-color": "#d946ef", "fill-opacity": 0.16 },
          });
          instance.addLayer({
            id: "annotation-line",
            type: "line",
            source: "annotations",
            filter: ["!=", ["geometry-type"], "Point"],
            paint: { "line-color": "#d946ef", "line-width": 2.5 },
          });
          instance.addLayer({
            id: "annotation-point",
            type: "circle",
            source: "annotations",
            filter: ["==", ["geometry-type"], "Point"],
            paint: { "circle-radius": 6, "circle-color": "#d946ef", "circle-stroke-color": "#ffffff", "circle-stroke-width": 2 },
          });
          instance.addLayer({
            id: "annotation-selected-line",
            type: "line",
            source: "annotations",
            filter: ["all", ["!=", ["geometry-type"], "Point"], ["==", ["get", "id"], ""]],
            paint: { "line-color": "#ffffff", "line-width": 5, "line-opacity": 0.55 },
          });
          instance.addLayer({
            id: "annotation-selected-point",
            type: "circle",
            source: "annotations",
            filter: ["all", ["==", ["geometry-type"], "Point"], ["==", ["get", "id"], ""]],
            paint: { "circle-radius": 11, "circle-color": "rgba(0,0,0,0)", "circle-stroke-color": "#ffffff", "circle-stroke-width": 2.5 },
          });
          instance.addSource("sketch", { type: "geojson", data: EMPTY });
          instance.addLayer({
            id: "sketch-fill",
            type: "fill",
            source: "sketch",
            filter: ["==", ["geometry-type"], "Polygon"],
            paint: { "fill-color": "#5b9bff", "fill-opacity": 0.15 },
          });
          instance.addLayer({
            id: "sketch-line",
            type: "line",
            source: "sketch",
            filter: ["!=", ["geometry-type"], "Point"],
            paint: { "line-color": "#5b9bff", "line-width": 2 },
          });
          instance.addLayer({
            id: "sketch-points",
            type: "circle",
            source: "sketch",
            filter: ["==", ["geometry-type"], "Point"],
            paint: { "circle-radius": 4, "circle-color": "#e7eaf0", "circle-stroke-color": "#5b9bff", "circle-stroke-width": 1.5 },
          });
          // Direction lines from a photograph to its objects, under the photograph markers so a camera stays clickable.
          instance.addSource("sight", { type: "geojson", data: EMPTY });
          instance.addLayer({
            id: "sight-fan",
            type: "fill",
            source: "sight",
            filter: ["==", ["get", "part"], "fan"],
            paint: { "fill-color": "#ffffff", "fill-opacity": 0.1 },
          });
          instance.addLayer({
            id: "sight-fan-line",
            type: "line",
            source: "sight",
            filter: ["==", ["get", "part"], "fan"],
            paint: { "line-color": "#ffffff", "line-width": 1, "line-opacity": 0.55, "line-dasharray": [2, 2] },
          });
          instance.addLayer({
            id: "sight-selected",
            type: "line",
            source: "sight",
            filter: ["all", ["!=", ["get", "part"], "fan"], ["==", ["geometry-type"], "LineString"], ["==", ["get", "id"], ""]],
            paint: { "line-color": "#ffffff", "line-width": 6, "line-opacity": 0.7 },
          });
          instance.addLayer({
            id: "sight-rays",
            type: "line",
            source: "sight",
            filter: ["==", ["get", "part"], "ray"],
            layout: { "line-cap": "round" },
            paint: { "line-color": ["get", "colour"], "line-width": 2.2, "line-opacity": 0.9 },
          });
          instance.addLayer({
            id: "sight-link",
            type: "line",
            source: "sight",
            filter: ["==", ["get", "part"], "link"],
            paint: { "line-color": ["get", "colour"], "line-width": 2, "line-dasharray": [1.5, 1.5] },
          });
          instance.addLayer({
            id: "sight-controls",
            type: "circle",
            source: "sight",
            filter: ["==", ["get", "part"], "control"],
            paint: { "circle-radius": 4.5, "circle-color": "#ffffff", "circle-stroke-color": "#11141a", "circle-stroke-width": 2 },
          });
          instance.addLayer({
            id: "sight-estimated",
            type: "circle",
            source: "sight",
            filter: ["==", ["get", "part"], "estimated"],
            paint: {
              "circle-radius": ["case", ["==", ["get", "id"], ""], 8, 5],
              "circle-color": ["case", ["get", "extrapolated"], "rgba(0,0,0,0)", ["get", "colour"]],
              "circle-stroke-color": ["case", ["get", "extrapolated"], ["get", "colour"], "#11141a"],
              "circle-stroke-width": ["case", ["get", "extrapolated"], 2, 1.2],
            },
          });
          instance.addLayer({
            id: "sight-marked",
            type: "circle",
            source: "sight",
            filter: ["==", ["get", "part"], "marked"],
            paint: {
              "circle-radius": ["case", ["==", ["get", "id"], ""], 9, 6.5],
              "circle-color": ["get", "colour"],
              "circle-stroke-color": "#ffffff",
              "circle-stroke-width": 2,
            },
          });
          instance.addSource("photos", { type: "geojson", data: EMPTY });
          instance.addLayer({
            id: "photos",
            type: "circle",
            source: "photos",
            paint: { "circle-radius": 7, "circle-color": "#3ecf8e", "circle-stroke-color": "#0b1220", "circle-stroke-width": 2 },
          });
          instance.addLayer({
            id: "photos-selected",
            type: "circle",
            source: "photos",
            filter: ["==", ["get", "id"], ""],
            paint: { "circle-radius": 11, "circle-color": "rgba(0,0,0,0)", "circle-stroke-color": "#e7eaf0", "circle-stroke-width": 2.5 },
          });
          instance.addSource("preview", { type: "geojson", data: EMPTY });
          instance.addLayer({
            id: "preview",
            type: "circle",
            source: "preview",
            paint: { "circle-radius": 9, "circle-color": "rgba(230,180,80,0.18)", "circle-stroke-color": "#e6b450", "circle-stroke-width": 2 },
          });
          instance.addSource("pinned", { type: "geojson", data: EMPTY });
          instance.addLayer({
            id: "pinned",
            type: "circle",
            source: "pinned",
            paint: { "circle-radius": 5, "circle-color": "#11141a", "circle-stroke-color": "#e7eaf0", "circle-stroke-width": 2 },
          });
          setReady(true);
          report({ state: "ready", zoom: instance.getZoom() });
        });
        instance.on("error", (event) => {
          if (engine === "mapbox") {
            // The project's own raster layers report their problems through the API; only the basemap is handled here.
            const failed = event as { sourceId?: string; error?: { message?: string; status?: number } };
            const status = failed.error?.status;
            if (status === 401 || status === 403) {
              report({
                state: "error",
                message: `Mapbox rejected the access token (HTTP ${status}). Check that ${"VITE_MAPBOX_ACCESS_TOKEN"} is a valid public token and that its URL restrictions allow this address.`,
              });
            } else if (!failed.sourceId?.startsWith("raster-")) {
              report({
                state: "error",
                message: `The Mapbox basemap reported a problem: ${redactSecrets(failed.error?.message ?? "unknown error")}. Parts of the basemap may be missing. This usually means the network or Mapbox could not be reached, or the picture does not exist at this zoom.`,
              });
            }
            return;
          }
          // Tile errors are per layer and are reported by the API; they must not take down the map.
          if (!(event as { sourceId?: string }).sourceId) setFailure(event.error?.message ?? "The map reported an error.");
        });
        let frame = 0;
        instance.on("mousemove", (event) => {
          cancelAnimationFrame(frame);
          frame = requestAnimationFrame(() => callbacks.current.onPointer?.({ lon: event.lngLat.lng, lat: event.lngLat.lat }));
        });
        instance.on("mouseout", () => callbacks.current.onPointer?.(null));
        instance.on("click", (event) => {
          // A click near a photograph marker selects that photograph; anywhere else picks a point. Markers can
          // overlap, so the marker nearest the click is chosen, and a click that could mean several asks instead.
          const m = instance;
          if (m && callbacks.current.onPhotoSelect && m.getLayer("photos")) {
            const { x, y } = event.point;
            const nearby = m.queryRenderedFeatures(
              [
                [x - HIT_RADIUS_PX, y - HIT_RADIUS_PX],
                [x + HIT_RADIUS_PX, y + HIT_RADIUS_PX],
              ],
              { layers: ["photos"] },
            );
            const markers = new Map<string, ScreenMarker>();
            for (const feature of nearby) {
              const id = feature.properties?.id;
              if (typeof id !== "string" || feature.geometry.type !== "Point") continue;
              const pixel = m.project(feature.geometry.coordinates as [number, number]);
              markers.set(id, { id, x: pixel.x, y: pixel.y });
            }
            const decision = chooseMarker({ x, y }, [...markers.values()]);
            if (decision.kind === "one") {
              callbacks.current.onPhotoSelect(decision.id);
              return;
            }
            if (decision.kind === "several") {
              if (callbacks.current.onPhotoChoice) callbacks.current.onPhotoChoice(decision.ids);
              else callbacks.current.onPhotoSelect(decision.ids[0]!);
              return;
            }
          }
          if (m && callbacks.current.onSightLineSelect && m.getLayer("sight-rays")) {
            const { x, y } = event.point;
            const hit = m.queryRenderedFeatures(
              [
                [x - 6, y - 6],
                [x + 6, y + 6],
              ],
              { layers: ["sight-marked", "sight-estimated", "sight-link", "sight-rays"].filter((id) => m.getLayer(id)) },
            );
            const id = hit.map((feature) => feature.properties?.id).find((value): value is string => typeof value === "string");
            if (id) {
              callbacks.current.onSightLineSelect(id);
              return;
            }
          }
          if (m && callbacks.current.onAnnotationSelect && m.getLayer("annotation-line")) {
            const { x, y } = event.point;
            const hit = m.queryRenderedFeatures(
              [
                [x - 6, y - 6],
                [x + 6, y + 6],
              ],
              { layers: ["annotation-point", "annotation-line", "annotation-fill"] },
            );
            const id = hit.map((feature) => feature.properties?.id).find((value): value is string => typeof value === "string");
            if (id) {
              callbacks.current.onAnnotationSelect(id);
              return;
            }
          }
          callbacks.current.onPick?.({ lon: event.lngLat.lng, lat: event.lngLat.lat });
        });
        instance.on("mouseenter", "photos", () => {
          if (instance) instance.getCanvas().style.cursor = "pointer";
        });
        instance.on("mouseleave", "photos", () => {
          if (instance) instance.getCanvas().style.cursor = "";
          callbacks.current.onPhotoHover?.(null);
        });
        instance.on("mousemove", "photos", (event) => {
          const feature = event.features?.[0];
          const id = feature?.properties?.id;
          if (!instance || typeof id !== "string" || feature?.geometry.type !== "Point") return;
          const at = instance.project(feature.geometry.coordinates as [number, number]);
          callbacks.current.onPhotoHover?.({ id, x: at.x, y: at.y });
        });
        instance.on("movestart", () => callbacks.current.onPhotoHover?.(null));
        map.current = instance;
      })
      .catch(() => {
        report({ state: "error", message: "The Mapbox map library could not be loaded." });
        setFailure("The map library could not be loaded.");
      });
    return () => {
      cancelled = true;
      instance?.remove();
      map.current = null;
    };
    // `token` is fixed for the life of the page; `engine` is the one thing that replaces the map.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [engine]);

  // Label and feature groups of the Mapbox basemap, switched without rebuilding the map.
  useEffect(() => {
    const m = map.current;
    if (!ready || !m || engine !== "mapbox") return;
    try {
      for (const [name, value] of Object.entries(labels)) {
        (m as unknown as MapboxExtras).setConfigProperty("basemap", name, value);
      }
    } catch (error) {
      callbacks.current.onBasemapStatus?.({
        state: "error",
        message: `A label setting could not be applied: ${redactSecrets(error instanceof Error ? error.message : "unknown error")}.`,
      });
    }
  }, [ready, engine, labels]);

  // Go to a typed latitude and longitude.
  useEffect(() => {
    const m = map.current;
    const target = props.goTo;
    if (!ready || !m || !target || moved.current === target) return;
    moved.current = target;
    m.resize();
    const zoom = target.zoom ?? Math.max(m.getZoom(), 15);
    if (target.instant) m.jumpTo({ center: [target.lon, target.lat], zoom });
    else m.flyTo({ center: [target.lon, target.lat], zoom, essential: true });
  }, [ready, props.goTo]);

  // Raster layers: add new ones, update visibility and opacity, remove those that are gone.
  useEffect(() => {
    const m = map.current;
    if (!ready || !m) return;
    // The georeferencing version is part of the id: a new version is a new source, never a reused one.
    // Display settings are also part of it: a new setting is a new source, so no tile drawn differently is reused.
    const layerId = (layer: RasterLayer, query?: string) =>
      `raster-${layer.asset_id}-v${layer.georef_version}${query ? `-${query.replace(/[^a-z0-9.,-]/gi, "_")}` : ""}`;
    const wanted = new Set(props.rasters.filter((r) => r.layer.tile_url).map((r) => layerId(r.layer, r.query)));
    for (const { layer, visible, opacity, query } of props.rasters) {
      if (!layer.tile_url) continue;
      const id = layerId(layer, query);
      if (!m.getSource(id)) {
        m.addSource(id, {
          type: "raster",
          tiles: [`${window.location.origin}${layer.tile_url}${query ? `&${query}` : ""}`],
          tileSize: 256,
          bounds: layer.bounds_4326 as [number, number, number, number],
          minzoom: 0,
          maxzoom: Math.min(22, layer.native_zoom + 1),
        });
        m.addLayer({ id, type: "raster", source: id, paint: { "raster-resampling": "nearest" } }, "aoi-fill");
      }
      m.setLayoutProperty(id, "visibility", visible ? "visible" : "none");
      m.setPaintProperty(id, "raster-opacity", opacity);
      // The rasters arrive bottom first; moving each just below the overlays in turn leaves the last one on top.
      m.moveLayer(id, "aoi-fill");
    }
    for (const style of m.getStyle().layers ?? []) {
      if (style.id.startsWith("raster-") && !wanted.has(style.id)) {
        m.removeLayer(style.id);
        m.removeSource(style.id);
      }
    }
    (m.getSource("footprints") as GeoJSONSource | undefined)?.setData(footprints(props.rasters));
  }, [ready, props.rasters]);

  useEffect(() => {
    const m = map.current;
    if (!ready || !m) return;
    (m.getSource("aoi") as GeoJSONSource | undefined)?.setData(aoiData(props.aoi));
    for (const id of ["aoi-fill", "aoi-line", "aoi-point"]) m.setLayoutProperty(id, "visibility", props.showAoi ? "visible" : "none");
  }, [ready, props.aoi, props.showAoi]);

  useEffect(() => {
    const m = map.current;
    if (!ready || !m) return;
    for (const id of ["footprints", "footprint-selected"]) m.setLayoutProperty(id, "visibility", props.showFootprints ? "visible" : "none");
    m.setFilter("footprint-selected", ["==", ["get", "id"], props.selectedRasterId ?? ""]);
  }, [ready, props.showFootprints, props.selectedRasterId]);

  useEffect(() => {
    const m = map.current;
    if (!ready || !m || !props.fitTo) return;
    const target = props.fitTo;
    const [west, south, east, north] = target;
    // A map replaced by a basemap change keeps the view it had; the same fit is not applied twice.
    if (fitted.current === target) return;
    // The frame may have changed size since the map was created (panels loading beside it): measure it first.
    const frame = requestAnimationFrame(() => {
      fitted.current = target;
      m.resize();
      m.fitBounds([[west, south], [east, north]] as LngLatBoundsLike, { padding: 48, maxZoom: 17, duration: 0 });
    });
    return () => cancelAnimationFrame(frame);
  }, [ready, props.fitTo]);

  useEffect(() => {
    const m = map.current;
    if (!ready || !m) return;
    const features = (props.photos ?? []).map((photo) => ({
      type: "Feature",
      properties: { id: photo.id, label: photo.label },
      geometry: { type: "Point", coordinates: [photo.lon, photo.lat] },
    }));
    (m.getSource("photos") as GeoJSONSource | undefined)?.setData({ type: "FeatureCollection", features } as unknown as Data);
    const visibility = props.showPhotos === false ? "none" : "visible";
    for (const id of ["photos", "photos-selected"]) m.setLayoutProperty(id, "visibility", visibility);
    m.setFilter("photos-selected", ["==", ["get", "id"], props.selectedPhotoId ?? ""]);
  }, [ready, props.photos, props.showPhotos, props.selectedPhotoId]);

  useEffect(() => {
    const m = map.current;
    if (!ready || !m) return;
    const features = (props.annotations ?? []).map((annotation) => ({
      type: "Feature",
      properties: { id: annotation.id },
      geometry: annotation.geometry,
    }));
    (m.getSource("annotations") as GeoJSONSource | undefined)?.setData({ type: "FeatureCollection", features } as unknown as Data);
    const visibility = props.showAnnotations === false ? "none" : "visible";
    for (const id of ["annotation-fill", "annotation-line", "annotation-point", "annotation-selected-line", "annotation-selected-point"]) {
      m.setLayoutProperty(id, "visibility", visibility);
    }
    const selected = props.selectedAnnotationId ?? "";
    m.setFilter("annotation-selected-line", ["all", ["!=", ["geometry-type"], "Point"], ["==", ["get", "id"], selected]]);
    m.setFilter("annotation-selected-point", ["all", ["==", ["geometry-type"], "Point"], ["==", ["get", "id"], selected]]);
  }, [ready, props.annotations, props.showAnnotations, props.selectedAnnotationId]);

  // Direction lines from a photograph to its objects, and the positions a person marked for them.
  useEffect(() => {
    const m = map.current;
    if (!m || !ready) return;
    const sight = props.sightLines;
    const features: unknown[] = [];
    if (sight?.fan) {
      features.push({ type: "Feature", properties: { part: "fan" }, geometry: { type: "Polygon", coordinates: [sight.fan] } });
    }
    for (const ray of sight?.rays ?? []) {
      features.push({
        type: "Feature",
        properties: { part: "ray", id: ray.id, colour: ray.colour },
        geometry: { type: "LineString", coordinates: ray.coordinates },
      });
    }
    for (const mark of sight?.marked ?? []) {
      features.push({
        type: "Feature",
        properties: { part: "link", id: mark.id, colour: mark.colour },
        geometry: { type: "LineString", coordinates: [mark.from, mark.at] },
      });
      features.push({ type: "Feature", properties: { part: "marked", id: mark.id, colour: mark.colour }, geometry: { type: "Point", coordinates: mark.at } });
    }
    for (const spot of sight?.estimated ?? []) {
      features.push({
        type: "Feature",
        properties: { part: "estimated", id: spot.id, colour: spot.colour, extrapolated: spot.extrapolated },
        geometry: { type: "Point", coordinates: spot.at },
      });
    }
    for (const control of sight?.controls ?? []) {
      features.push({ type: "Feature", properties: { part: "control", id: control.id }, geometry: { type: "Point", coordinates: control.at } });
    }
    (m.getSource("sight") as GeoJSONSource | undefined)?.setData({ type: "FeatureCollection", features } as unknown as Data);
    const selected = sight?.selectedId ?? "";
    if (m.getLayer("sight-selected")) {
      m.setFilter("sight-selected", ["all", ["!=", ["get", "part"], "fan"], ["==", ["geometry-type"], "LineString"], ["==", ["get", "id"], selected]]);
      m.setPaintProperty("sight-marked", "circle-radius", ["case", ["==", ["get", "id"], selected], 9, 6.5]);
      m.setPaintProperty("sight-estimated", "circle-radius", ["case", ["==", ["get", "id"], selected], 8, 5]);
    }
  }, [ready, props.sightLines]);

  useEffect(() => {
    const m = map.current;
    if (!ready || !m) return;
    const point = props.preview;
    (m.getSource("preview") as GeoJSONSource | undefined)?.setData(
      (point
        ? { type: "FeatureCollection", features: [{ type: "Feature", properties: {}, geometry: { type: "Point", coordinates: [point.lon, point.lat] } }] }
        : EMPTY) as Data,
    );
  }, [ready, props.preview]);

  useEffect(() => {
    const m = map.current;
    if (!ready || !m) return;
    const point = props.pinned;
    (m.getSource("pinned") as GeoJSONSource | undefined)?.setData(
      (point
        ? { type: "FeatureCollection", features: [{ type: "Feature", properties: {}, geometry: { type: "Point", coordinates: [point.lon, point.lat] } }] }
        : EMPTY) as Data,
    );
  }, [ready, props.pinned]);

  useEffect(() => {
    const m = map.current;
    if (!ready || !m) return;
    const sketch = props.sketch;
    const features: unknown[] = [];
    if (sketch && sketch.points.length > 0) {
      for (const point of sketch.points) features.push({ type: "Feature", properties: {}, geometry: { type: "Point", coordinates: point } });
      if (sketch.points.length > 1) {
        const ring = sketch.kind === "area" && sketch.points.length > 2 ? [...sketch.points, sketch.points[0]!] : sketch.points;
        features.push({
          type: "Feature",
          properties: {},
          geometry: sketch.kind === "area" && sketch.points.length > 2 ? { type: "Polygon", coordinates: [ring] } : { type: "LineString", coordinates: ring },
        });
      }
    }
    (m.getSource("sketch") as GeoJSONSource | undefined)?.setData({ type: "FeatureCollection", features } as unknown as Data);
  }, [ready, props.sketch]);

  if (failure) {
    return (
      <Notice tone="error" title="The map cannot be drawn in this browser">
        {failure} The layer list and coordinates are still available. WebGL is required to draw the map.
      </Notice>
    );
  }
  return (
    <div className={`relative overflow-hidden rounded-lg border border-line bg-[#11141a] ${props.className ?? "h-[min(70vh,40rem)] min-h-80"}`}>
      {/* MapLibre makes its container position: relative, so it is sized by height and width, not by inset. */}
      <div ref={container} className="h-full w-full" role="region" aria-label="Map of project layers" />
      {!ready ? (
        <div className="absolute inset-0 flex items-center justify-center">
          <Loading label="Loading the map…" />
        </div>
      ) : null}
    </div>
  );
}
