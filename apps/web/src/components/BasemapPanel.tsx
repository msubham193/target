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
      title="Map background"
      actions={satellite ? <Badge tone="accent">Online</Badge> : <Badge tone="neutral">Offline</Badge>}
      description="Choose what is drawn under your images, and jump to a place."
    >
      <div className="space-y-4 text-[13px]">
        <fieldset className="space-y-1.5">
          <legend className="mb-1 text-xs font-medium text-fg-muted">Background</legend>
          <label className="flex items-center gap-2">
            <input type="radio" name="basemap" className="accent-accent" checked={!satellite} onChange={() => onBasemap("offline")} />
            Plain map (works without internet)
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
              Satellite map <span className="text-fg-subtle">(online)</span>
              <span className="block text-xs text-fg-subtle">
                Real satellite pictures with roads, labels and places. Needs an internet connection.
              </span>
            </span>
          </label>
        </fieldset>

        {!token.ok ? (
          <Notice tone="warning" title="The satellite map is not set up">
            <p className="text-xs" data-testid="mapbox-config-error">
              {token.message}
            </p>
          </Notice>
        ) : null}

        {satellite && status?.state === "loading" ? <p className="text-xs text-fg-muted">Loading the satellite map…</p> : null}
        {satellite && status?.state === "error" ? (
          <Notice tone="error" title="The satellite map has a problem">
            <p className="text-xs" data-testid="mapbox-status-error">
              {status.message} Your layers, annotations and measurements are still drawn and usable.
            </p>
          </Notice>
        ) : null}
        {enlarged ? (
          <Notice tone="info" title="Picture detail">
            <p className="text-xs" data-testid="mapbox-zoom-note">
              {enlarged}
            </p>
          </Notice>
        ) : null}

        <form onSubmit={submit} className="space-y-2" aria-label="Go to a place by its coordinates" noValidate>
          <div className="grid grid-cols-2 gap-2">
            <Field label="Latitude" htmlFor="go-lat" hint="e.g. 20.2961">
              <input id="go-lat" className={inputClass} inputMode="decimal" value={lat} onChange={(e) => setLat(e.target.value)} />
            </Field>
            <Field label="Longitude" htmlFor="go-lon" hint="e.g. 85.8245">
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
              Which labels appear also depends on how far you zoom in and on what is known about the place.
            </p>
          </fieldset>
        ) : null}

        {satellite ? (
          <p className="text-[11px] text-fg-subtle" data-testid="mapbox-attribution-note">
            Imagery and map data © Mapbox and its data providers (including OpenStreetMap contributors); the full credit is shown on
            the map. It is a background to help you find your way: it is not live and may be out of date. Your images are drawn on top
            of it.
          </p>
        ) : null}
      </div>
    </Panel>
  );
}
