import { type ReactNode, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";

import "mapbox-gl/dist/mapbox-gl.css";

import { assetPreviewUrl } from "../api/client";
import { type Bounds4326, isPoint, type StartView } from "../lib/location3d";
import { DEFAULT_LABELS, MAPBOX_STYLE, readMapboxToken, redactSecrets } from "../lib/mapbox";

export interface Photo3D {
  id: string;
  filename: string;
  lon: number;
  lat: number;
}

/** The parts of the Mapbox GL JS map this view uses. */
interface Map3D {
  on(event: string, handler: (event?: unknown) => void): void;
  addControl(control: unknown, position?: string): void;
  getSource(id: string): unknown;
  addSource(id: string, source: Record<string, unknown>): void;
  getLayer(id: string): unknown;
  addLayer(layer: Record<string, unknown>): void;
  setLayoutProperty(layer: string, name: string, value: unknown): void;
  setTerrain(terrain: Record<string, unknown> | null): void;
  getTerrain(): unknown;
  setConfigProperty(importId: string, name: string, value: unknown): void;
  setFog?(fog: Record<string, unknown> | null): void;
  getCenter(): { lng: number; lat: number };
  queryTerrainElevation(lngLat: [number, number], options?: { exaggerated?: boolean }): number | null | undefined;
  easeTo(options: Record<string, unknown>): void;
  fitBounds(bounds: [[number, number], [number, number]], options?: Record<string, unknown>): void;
  jumpTo(options: Record<string, unknown>): void;
  getBearing(): number;
  getPitch(): number;
  resize(): void;
  remove(): void;
}

interface MapboxLibrary {
  accessToken: string;
  Map: new (options: Record<string, unknown>) => Map3D;
  NavigationControl: new (options?: Record<string, unknown>) => unknown;
  ScaleControl: new (options?: Record<string, unknown>) => unknown;
  Marker: new (options?: Record<string, unknown>) => {
    setLngLat(lngLat: [number, number]): { addTo(map: Map3D): { remove(): void } };
  };
}

const TILT = 65;
const BUILDINGS = "online-3d-buildings";
const SHADE = "online-3d-shade";
const CONTOURS = "online-3d-contours";
const DEM = "mapbox-dem";
// Wide enough to see the lie of the land around a photograph, not just the nearest street.
const CLOSE_ZOOM = 15;
// A place chosen by name is a town or a landmark: start further back, so hills around it are seen from above, not from inside.
const PLACE_ZOOM = 12.5;
const MAX_EXAGGERATION = 5;
/** Half the side of the square around the centre whose ground is sampled for the relief readout, in metres. */
const RELIEF_RADIUS_M = 2000;

/** Lowest and highest ground within about 2 km of the centre, from a grid of Mapbox terrain samples; null until loaded. */
function reliefNearCentre(m: Map3D): { low: number; high: number } | null {
  const c = m.getCenter();
  const dLat = RELIEF_RADIUS_M / 111_320;
  const dLon = dLat / Math.max(0.1, Math.cos((c.lat * Math.PI) / 180));
  const values: number[] = [];
  for (let i = 0; i <= 8; i++) {
    for (let j = 0; j <= 8; j++) {
      const lon = c.lng - dLon + (2 * dLon * i) / 8;
      const lat = c.lat - dLat + (2 * dLat * j) / 8;
      const v = m.queryTerrainElevation([lon, lat], { exaggerated: false });
      if (typeof v === "number" && Number.isFinite(v)) values.push(v);
    }
  }
  return values.length < 10 ? null : { low: Math.min(...values), high: Math.max(...values) };
}

/**
 * An online 3D view: Mapbox satellite imagery draped on Mapbox's terrain model, buildings raised where Mapbox records a
 * height, and the project's located photographs marked. Imagery and heights are Mapbox's: they are shown, not measured.
 */
export function Mapbox3DView({
  projectId,
  photos,
  start,
  goTo,
  search,
  className = "w-full",
}: {
  projectId: string;
  photos: Photo3D[];
  start: StartView | null;
  /** A place chosen by the person; it only moves the camera. */
  goTo?: { lon: number; lat: number; label: string } | null;
  /** A place search shown over the map, under the controls. */
  search?: ReactNode;
  className?: string;
}) {
  const token = useMemo(() => readMapboxToken(), []);
  const shell = useRef<HTMLDivElement>(null);
  const container = useRef<HTMLDivElement>(null);
  const map = useRef<Map3D | null>(null);
  const [ready, setReady] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [tilted, setTilted] = useState(true);
  const [rotating, setRotating] = useState(false);
  const [exaggeration, setExaggeration] = useState(1.5);
  const [labels, setLabels] = useState(true);
  const [buildings, setBuildings] = useState(true);
  const [shading, setShading] = useState(true);
  const [contours, setContours] = useState(false);
  const [relief, setRelief] = useState<{ low: number; high: number } | null>(null);
  const [height, setHeight] = useState<number | null>(null);
  const [chosen, setChosen] = useState<string | null>(null);
  const [full, setFull] = useState(false);
  // The view fills the window from where it starts down to the bottom edge, whatever is above it, and follows resizing.
  const [viewHeight, setViewHeight] = useState(560);
  useLayoutEffect(() => {
    const measure = () => {
      const el = shell.current;
      if (!el) return;
      const top = el.getBoundingClientRect().top + window.scrollY;
      setViewHeight(Math.max(420, Math.round(window.innerHeight - top - 16)));
    };
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, []);
  const exaggerationNow = useRef(exaggeration);
  exaggerationNow.current = exaggeration;

  const frame = (target: Map3D, bounds: Bounds4326 | null, animate: boolean, zoom = CLOSE_ZOOM) => {
    const camera = { pitch: tilted ? TILT : 0, bearing: tilted ? -20 : 0 };
    if (!bounds) return;
    if (isPoint(bounds)) {
      const options = { center: [bounds[0], bounds[1]], zoom, ...camera };
      if (animate) target.easeTo({ ...options, duration: 1500 });
      else target.jumpTo(options);
    } else {
      target.fitBounds(
        [
          [bounds[0], bounds[1]],
          [bounds[2], bounds[3]],
        ],
        { padding: 80, maxZoom: CLOSE_ZOOM, duration: animate ? 1500 : 0, ...camera },
      );
    }
  };

  // Create the map once.
  useEffect(() => {
    if (!token.ok || !container.current) return;
    let cancelled = false;
    let instance: Map3D | null = null;
    void import("mapbox-gl").then((module) => {
      if (cancelled || !container.current) return;
      const mapboxgl = module.default as unknown as MapboxLibrary;
      mapboxgl.accessToken = token.token;
      const first = start?.bounds ?? null;
      const centre = first ? [(first[0] + first[2]) / 2, (first[1] + first[3]) / 2] : [0, 20];
      try {
        instance = new mapboxgl.Map({
          container: container.current,
          style: MAPBOX_STYLE,
          config: { basemap: { ...DEFAULT_LABELS } },
          center: centre,
          zoom: first ? CLOSE_ZOOM : 1.5,
          pitch: first ? TILT : 0,
          bearing: first ? -20 : 0,
          maxPitch: 85,
          antialias: true,
          attributionControl: true,
        });
      } catch (error) {
        setProblem(redactSecrets(error instanceof Error ? error.message : "The 3D view could not be started."));
        return;
      }
      const m = instance;
      map.current = m;
      m.addControl(new mapboxgl.NavigationControl({ visualizePitch: true }), "top-right");
      m.addControl(new mapboxgl.ScaleControl({ unit: "metric" }), "bottom-left");
      m.on("style.load", () => {
        if (cancelled) return;
        if (!m.getSource(DEM)) {
          m.addSource(DEM, { type: "raster-dem", url: "mapbox://mapbox.mapbox-terrain-dem-v1", tileSize: 512, maxzoom: 14 });
        }
        // The style flattens its terrain at street zoom; this view keeps the relief at every zoom.
        m.setTerrain({ source: DEM, exaggeration: exaggerationNow.current });
        // Sky and haze towards the horizon, so the tilted view reads as a landscape rather than a tilted photograph.
        try {
          m.setFog?.({
            range: [1, 12],
            color: "#c9d6e6",
            "high-color": "#3d6ea8",
            "horizon-blend": 0.12,
            "space-color": "#0b1a2e",
            "star-intensity": 0,
          });
        } catch {
          // a style that manages its own atmosphere keeps it
        }
        // Hill shading from the same terrain model, laid over the imagery: slopes facing away from the light darken, so
        // valleys, ridges and embankments stand out even where the ground is gentle.
        if (!m.getSource("online-3d-shade-dem")) {
          m.addSource("online-3d-shade-dem", { type: "raster-dem", url: "mapbox://mapbox.mapbox-terrain-dem-v1", tileSize: 512, maxzoom: 14 });
        }
        if (!m.getLayer(SHADE)) {
          m.addLayer({
            id: SHADE,
            type: "hillshade",
            source: "online-3d-shade-dem",
            slot: "bottom",
            paint: {
              "hillshade-exaggeration": 0.6,
              "hillshade-shadow-color": "#0b1020",
              "hillshade-highlight-color": "#fff6e0",
              "hillshade-accent-color": "#1d2433",
              "hillshade-illumination-direction": 315,
            },
          });
        }
        // Height lines from Mapbox's terrain tiles (off at first): each line joins ground of the same height.
        if (!m.getSource("online-3d-terrain-lines")) m.addSource("online-3d-terrain-lines", { type: "vector", url: "mapbox://mapbox.mapbox-terrain-v2" });
        if (!m.getLayer(CONTOURS)) {
          m.addLayer({
            id: CONTOURS,
            type: "line",
            source: "online-3d-terrain-lines",
            "source-layer": "contour",
            slot: "bottom",
            layout: { visibility: "none" },
            paint: {
              "line-color": "#fde68a",
              "line-opacity": ["case", ["==", ["%", ["get", "index"], 5], 0], 0.85, 0.45],
              "line-width": ["case", ["==", ["%", ["get", "index"], 5], 0], 1.4, 0.7],
            },
          });
        }
        if (!m.getSource("online-3d-streets")) m.addSource("online-3d-streets", { type: "vector", url: "mapbox://mapbox.mapbox-streets-v8" });
        if (!m.getLayer(BUILDINGS)) {
          m.addLayer({
            id: BUILDINGS,
            type: "fill-extrusion",
            source: "online-3d-streets",
            "source-layer": "building",
            minzoom: 14,
            // Only buildings whose height Mapbox records are raised: no height is assumed for the others.
            filter: ["all", ["==", ["get", "extrude"], "true"], ["has", "height"]],
            paint: {
              "fill-extrusion-color": "#c9ccd3",
              "fill-extrusion-height": ["get", "height"],
              "fill-extrusion-base": ["coalesce", ["get", "min_height"], 0],
              "fill-extrusion-opacity": 0.82,
            },
          });
        }
        setReady(true);
      });
      m.on("mousemove", (event) => {
        const lngLat = (event as { lngLat?: { lng: number; lat: number } }).lngLat;
        if (!lngLat) return;
        const value = m.queryTerrainElevation([lngLat.lng, lngLat.lat], { exaggerated: false });
        setHeight(typeof value === "number" && Number.isFinite(value) ? value : null);
      });
      m.on("mouseout", () => setHeight(null));
      m.on("idle", () => setRelief(reliefNearCentre(m)));
      m.on("error", (event) => {
        const failed = event as { error?: { message?: string; status?: number } };
        const status = failed.error?.status;
        setProblem(
          status === 401 || status === 403
            ? `Mapbox rejected the access token (HTTP ${status}). Check that VITE_MAPBOX_ACCESS_TOKEN is a valid public token.`
            : `Mapbox reported a problem: ${redactSecrets(failed.error?.message ?? "unknown error")}. Parts of the 3D view may be missing.`,
        );
      });
      // Markers for the located photographs.
      for (const photo of photos) {
        const el = document.createElement("button");
        el.type = "button";
        el.className = "online-3d-marker";
        el.setAttribute("aria-label", `Photograph ${photo.filename}`);
        el.style.cssText =
          "width:18px;height:18px;border-radius:9999px;background:#22c55e;border:3px solid #fff;box-shadow:0 0 0 2px rgb(0 0 0/.35),0 4px 10px rgb(0 0 0/.5);cursor:pointer";
        el.addEventListener("click", (e) => {
          e.stopPropagation();
          setChosen(photo.id);
        });
        new mapboxgl.Marker({ element: el, anchor: "center" }).setLngLat([photo.lon, photo.lat]).addTo(m);
      }
    });
    return () => {
      cancelled = true;
      instance?.remove();
      map.current = null;
      setReady(false);
    };
    // Created once; later changes go through the effects below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token.ok]);

  // Exaggeration, labels and buildings change in place.
  useEffect(() => {
    const m = map.current;
    if (ready && m) m.setTerrain({ source: DEM, exaggeration });
  }, [ready, exaggeration]);
  useEffect(() => {
    const m = map.current;
    if (!ready || !m) return;
    try {
      for (const name of ["showPlaceLabels", "showRoadLabels", "showPointOfInterestLabels", "showTransitLabels"]) m.setConfigProperty("basemap", name, labels);
    } catch {
      // a style without these settings keeps its labels
    }
  }, [ready, labels]);
  useEffect(() => {
    const m = map.current;
    if (ready && m && m.getLayer(BUILDINGS)) m.setLayoutProperty(BUILDINGS, "visibility", buildings ? "visible" : "none");
  }, [ready, buildings]);
  useEffect(() => {
    const m = map.current;
    if (ready && m && m.getLayer(SHADE)) m.setLayoutProperty(SHADE, "visibility", shading ? "visible" : "none");
  }, [ready, shading]);
  useEffect(() => {
    const m = map.current;
    if (ready && m && m.getLayer(CONTOURS)) m.setLayoutProperty(CONTOURS, "visibility", contours ? "visible" : "none");
  }, [ready, contours]);

  // Tilt or look straight down.
  useEffect(() => {
    const m = map.current;
    if (ready && m) m.easeTo({ pitch: tilted ? TILT : 0, bearing: tilted ? m.getBearing() || -20 : 0, duration: 900 });
  }, [ready, tilted]);

  // A slow turn around the centre.
  useEffect(() => {
    const m = map.current;
    if (!ready || !m || !rotating) return;
    let stop = false;
    const step = () => {
      if (stop) return;
      m.easeTo({ bearing: m.getBearing() + 24, duration: 4000, easing: (t: number) => t });
      timer = window.setTimeout(step, 4000);
    };
    let timer = window.setTimeout(step, 0);
    return () => {
      stop = true;
      window.clearTimeout(timer);
    };
  }, [ready, rotating]);

  // A place the person chose.
  useEffect(() => {
    const m = map.current;
    if (ready && m && goTo) frame(m, [goTo.lon, goTo.lat, goTo.lon, goTo.lat], true, PLACE_ZOOM);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, goTo]);

  // Full screen for the whole view, controls included.
  useEffect(() => {
    const onChange = () => {
      setFull(document.fullscreenElement !== null && document.fullscreenElement === shell.current);
      window.setTimeout(() => map.current?.resize(), 50);
    };
    document.addEventListener("fullscreenchange", onChange);
    return () => document.removeEventListener("fullscreenchange", onChange);
  }, []);

  if (!token.ok) {
    return (
      <div className="rounded-xl border border-line bg-surface p-5 text-[13px] text-fg-muted" data-testid="online-3d-no-token">
        <p className="font-medium text-fg">The online 3D view needs a Mapbox public token</p>
        <p className="mt-1">{token.message}</p>
      </div>
    );
  }

  const chosenPhoto = photos.find((p) => p.id === chosen) ?? null;
  const glass = "pointer-events-auto rounded-xl border border-line/70 bg-surface/80 shadow-panel backdrop-blur-md";
  const toggle = (on: boolean) =>
    `h-7 rounded-md border px-2.5 text-xs transition-colors ${on ? "border-accent bg-accent-soft font-medium text-fg" : "border-line text-fg-muted hover:bg-overlay hover:text-fg"}`;

  return (
    <div
      ref={shell}
      className={`relative overflow-hidden rounded-xl border border-line bg-[#11141a] ${full ? "" : className}`}
      style={full ? { width: "100vw", height: "100vh" } : { height: viewHeight }}
      data-testid="online-3d-shell"
    >
      {/* Sized by height and width, not by absolute insets: Mapbox's own stylesheet makes the map element position: relative. */}
      <div ref={container} className="h-full w-full" role="region" aria-label="Online 3D view" data-testid="online-3d-map" />

      {/* controls, top left */}
      <div className="pointer-events-none absolute top-3 left-3 flex max-w-[calc(100%-6rem)] flex-col items-start gap-2">
        <div className={`${glass} flex flex-wrap items-center gap-1.5 p-2`} role="group" aria-label="3D view controls">
          <button type="button" className={toggle(tilted)} aria-pressed={tilted} onClick={() => setTilted((v) => !v)}>
            {tilted ? "3D tilt" : "Top-down"}
          </button>
          <button type="button" className={toggle(rotating)} aria-pressed={rotating} onClick={() => setRotating((v) => !v)}>
            Rotate
          </button>
          <button type="button" className={toggle(shading)} aria-pressed={shading} onClick={() => setShading((v) => !v)}>
            Hill shading
          </button>
          <button type="button" className={toggle(contours)} aria-pressed={contours} onClick={() => setContours((v) => !v)}>
            Height lines
          </button>
          <button type="button" className={toggle(buildings)} aria-pressed={buildings} onClick={() => setBuildings((v) => !v)}>
            Buildings
          </button>
          <button type="button" className={toggle(labels)} aria-pressed={labels} onClick={() => setLabels((v) => !v)}>
            Labels
          </button>
          <label className="flex items-center gap-1.5 px-1 text-xs text-fg-muted">
            Height ×{exaggeration.toFixed(1)}
            <input
              type="range"
              aria-label="Height exaggeration"
              className="w-20 accent-accent"
              min={1}
              max={MAX_EXAGGERATION}
              step={0.5}
              value={exaggeration}
              onChange={(e) => setExaggeration(Number(e.target.value))}
            />
          </label>
          <button
            type="button"
            className={toggle(false)}
            disabled={!start && !goTo}
            onClick={() => {
              setRotating(false);
              setTilted(true);
              if (!map.current) return;
              if (goTo) frame(map.current, [goTo.lon, goTo.lat, goTo.lon, goTo.lat], true, PLACE_ZOOM);
              else frame(map.current, start?.bounds ?? null, true);
            }}
          >
            Reset view
          </button>
          {typeof document !== "undefined" && document.fullscreenEnabled ? (
            <button
              type="button"
              className={toggle(full)}
              aria-pressed={full}
              onClick={() => (document.fullscreenElement ? void document.exitFullscreen() : void shell.current?.requestFullscreen?.())}
            >
              {full ? "Exit full screen" : "Full screen"}
            </button>
          ) : null}
        </div>
        {search ? <div className={`${glass} w-120 max-w-full p-2`}>{search}</div> : null}
      </div>

      {/* what is shown, and the height under the pointer: bottom right, above the attribution */}
      <div className={`${glass} absolute right-3 bottom-10 max-w-xs p-2.5 text-[11px] text-fg-muted`}>
        <p data-testid="online-3d-centre">
          {goTo ? `Centred on ${goTo.label}.` : start ? `Centred on ${start.label}.` : "No location in this project yet: choose a place above."}
        </p>
        <p className="mt-0.5" data-testid="online-3d-height">
          {height === null
            ? "Point at the ground to read its height."
            : `Ground height here: about ${Math.round(height)} m (Mapbox terrain model, approximate; not measured by this app).`}
        </p>
        {relief ? (
          <p className="mt-0.5" data-testid="online-3d-relief">
            Ground within about 2 km: {Math.round(relief.low)}–{Math.round(relief.high)} m.
            {relief.high - relief.low < 30
              ? " This area is nearly flat, so there is little relief to see; raise Height × or look at a hilly place."
              : ""}
          </p>
        ) : null}
      </div>

      {chosenPhoto ? (
        <div className={`${glass} absolute bottom-10 left-3 flex w-72 max-w-[calc(100%-1.5rem)] items-center gap-3 p-2.5`} data-testid="online-3d-photo">
          <img src={assetPreviewUrl(chosenPhoto.id, "thumb")} alt="" className="h-12 w-16 shrink-0 rounded border border-line object-cover" />
          <div className="min-w-0 flex-1 text-xs">
            <p className="truncate font-medium text-fg">{chosenPhoto.filename}</p>
            <p className="text-fg-subtle">Where the camera was, as confirmed by a person.</p>
            <Link className="text-accent hover:underline" to={`/projects/${projectId}/assets/${chosenPhoto.id}/ground`}>
              Open in Photo Analysis
            </Link>
          </div>
          <button type="button" aria-label="Close" className="self-start text-fg-subtle hover:text-fg" onClick={() => setChosen(null)}>
            ×
          </button>
        </div>
      ) : null}

      {problem ? (
        <div className="absolute inset-x-3 top-16 rounded-lg border border-danger/40 bg-danger-soft px-3 py-2 text-xs text-fg" role="alert">
          {problem}
        </div>
      ) : null}
      {!ready && !problem ? (
        <p className="pointer-events-none absolute inset-0 flex items-center justify-center text-[13px] text-fg-muted">Loading the 3D view…</p>
      ) : null}
    </div>
  );
}
