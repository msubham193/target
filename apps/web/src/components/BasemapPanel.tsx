import { type FormEvent, useMemo, useState } from "react";

import {
  type Basemap,
  type BasemapStatus,
  LABEL_TOGGLES,
  type LabelKey,
  type LabelState,
  parseLatLon,
  readMapboxToken,
  zoomNote,
} from "../lib/mapbox";
import { Badge, Button, Field, inputClass, Notice, Panel } from "./ui";

/**
 * Which basemap is drawn, and going to a typed latitude and longitude.
 *
 * The offline map is the default. Mapbox Standard Satellite is an online basemap: it needs a public token, sends the map's
 * view to Mapbox, and shows Mapbox's picture as it is. Going to a position works with either basemap.
 */
export function BasemapPanel({
  basemap,
  onBasemap,
  labels,
  onLabels,
  status,
  onGo,
}: {
  basemap: Basemap;
  onBasemap: (basemap: Basemap) => void;
  labels: LabelState;
  onLabels: (labels: LabelState) => void;
  status: BasemapStatus | null;
  onGo: (position: { lat: number; lon: number }) => void;
}) {
  const token = useMemo(() => readMapboxToken(), []);
  const [lat, setLat] = useState("");
  const [lon, setLon] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const satellite = basemap === "mapbox" && token.ok;

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const parsed = parseLatLon(lat, lon);
    if (!parsed.ok) {
      setProblem(parsed.message);
      return;
    }
    setProblem(null);
    onGo({ lat: parsed.lat, lon: parsed.lon });
  };
  const enlarged = satellite ? zoomNote(status?.zoom ?? null) : null;

  return (
    <Panel
      title="Basemap and location"
      actions={satellite ? <Badge tone="accent">Online</Badge> : <Badge tone="neutral">Offline</Badge>}
      description="What is drawn under your layers, and where the map looks."
    >
      <div className="space-y-4 text-[13px]">
        <fieldset className="space-y-1.5">
          <legend className="mb-1 text-xs font-medium text-fg-muted">Basemap</legend>
          <label className="flex items-center gap-2">
            <input type="radio" name="basemap" className="accent-accent" checked={!satellite} onChange={() => onBasemap("offline")} />
            Offline map (graticule only)
          </label>
          <label className="flex items-start gap-2">
            <input
              type="radio"
              name="basemap"
              className="mt-0.5 accent-accent"
              checked={satellite}
              disabled={!token.ok}
              onChange={() => onBasemap("mapbox")}
            />
            <span>
              Mapbox Standard Satellite <span className="text-fg-subtle">(online)</span>
              <span className="block text-xs text-fg-subtle">
                Satellite and aerial imagery with roads, labels and places. Needs the network and a Mapbox token.
              </span>
            </span>
          </label>
        </fieldset>

        {!token.ok ? (
          <Notice tone="warning" title="Mapbox satellite is not configured">
            <p className="text-xs" data-testid="mapbox-config-error">
              {token.message}
            </p>
          </Notice>
        ) : null}

        {satellite && status?.state === "loading" ? <p className="text-xs text-fg-muted">Loading the Mapbox basemap…</p> : null}
        {satellite && status?.state === "error" ? (
          <Notice tone="error" title="The Mapbox basemap has a problem">
            <p className="text-xs" data-testid="mapbox-status-error">
              {status.message} Your layers, annotations and measurements are still drawn and usable.
            </p>
          </Notice>
        ) : null}
        {enlarged ? (
          <Notice tone="info" title="Imagery detail">
            <p className="text-xs" data-testid="mapbox-zoom-note">
              {enlarged}
            </p>
          </Notice>
        ) : null}

        <form onSubmit={submit} className="space-y-2" aria-label="Go to a latitude and longitude" noValidate>
          <div className="grid grid-cols-2 gap-2">
            <Field label="Latitude" htmlFor="go-lat" hint="Decimal degrees, e.g. 20.2961 or 20.2961 N">
              <input id="go-lat" className={inputClass} inputMode="decimal" value={lat} onChange={(e) => setLat(e.target.value)} />
            </Field>
            <Field label="Longitude" htmlFor="go-lon" hint="e.g. 85.8245 or 85.8245 E">
              <input id="go-lon" className={inputClass} inputMode="decimal" value={lon} onChange={(e) => setLon(e.target.value)} />
            </Field>
          </div>
          {problem ? (
            <p className="text-xs text-danger" role="alert">
              {problem}
            </p>
          ) : null}
          <Button type="submit" size="sm" disabled={lat.trim() === "" || lon.trim() === ""}>
            Go to this location
          </Button>
        </form>

        {satellite ? (
          <fieldset className="space-y-1">
            <legend className="mb-1 text-xs font-medium text-fg-muted">Show on the satellite map</legend>
            {LABEL_TOGGLES.map(({ key, label }) => (
              <label key={key} className="flex items-center gap-2">
                <input
                  type="checkbox"
                  className="accent-accent"
                  checked={labels[key as LabelKey]}
                  onChange={(event) => onLabels({ ...labels, [key]: event.target.checked })}
                />
                {label}
              </label>
            ))}
            <p className="pt-1 text-[11px] text-fg-subtle">
              Whether a given road, transit or place label appears also depends on the zoom and on what Mapbox has for that place.
            </p>
          </fieldset>
        ) : null}

        {satellite ? (
          <p className="text-[11px] text-fg-subtle" data-testid="mapbox-attribution-note">
            Imagery and map data © Mapbox and its data providers (including OpenStreetMap contributors); the full attribution is shown on
            the map. This is a base picture for orientation: it is not live, its capture date is not shown here, and it varies by place.
            Your layers are drawn on top of it and are not positioned by it.
          </p>
        ) : null}
      </div>
    </Panel>
  );
}
