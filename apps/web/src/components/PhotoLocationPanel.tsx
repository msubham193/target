import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { lazy, Suspense, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import {
  ApiError,
  type Asset,
  changePhotoLocation,
  getLayers,
  getPhotoLocation,
  type PhotoLocation,
  type PhotoLocationApply,
  readPhotoExif,
} from "../api/client";
import { formatDateTime } from "../lib/format";
import { formatDecimalDegrees } from "../lib/geo";
import { checkPosition, toInputValue } from "../lib/photoLocation";
import { readMapboxToken } from "../lib/mapbox";
import type { RasterDisplay } from "./MapCanvas";
import { Badge, Button, ErrorNotice, Field, inputClass, Loading, Notice, Panel, selectClass } from "./ui";

// The map library is large and needs WebGL, so it is loaded only when a photograph's panel is shown.
const MapCanvas = lazy(() => import("./MapCanvas").then((module) => ({ default: module.MapCanvas })));

const SOURCE_LABELS: Record<string, string> = {
  exif: "GPS position from the photo file, confirmed",
  manual_entry: "Coordinates typed in",
  map_selection: "Point chosen on the map",
  none: "No location",
};

type Bounds = [number, number, number, number];

function around(lon: number, lat: number): Bounds {
  const pad = 0.01; // about a kilometre: close enough to see a place, far enough to see its surroundings
  return [lon - pad, lat - pad, lon + pad, lat + pad];
}

function Position({ lat, lon }: { lat: number; lon: number }) {
  return (
    <span className="font-mono text-xs">
      {formatDecimalDegrees(lat, "lat")}, {formatDecimalDegrees(lon, "lon")}
    </span>
  );
}

/**
 * Where a photograph is: the unverified position read from its file (a candidate), and the position a person has
 * confirmed. A candidate is never presented as, or drawn as, a location. Every change goes through the API, which
 * validates it again and keeps the history.
 */
export function PhotoLocationPanel({ asset }: { asset: Asset }) {
  const queryClient = useQueryClient();
  const projectId = asset.project_id;
  const state = useQuery({ queryKey: ["photo-location", asset.id], queryFn: () => getPhotoLocation(asset.id) });
  const layers = useQuery({
    queryKey: ["layers", projectId],
    queryFn: () => getLayers(projectId),
    // A raster uploaded a moment ago is placed before its picture is ready (the worker is still preparing it).
    refetchInterval: (query) => (query.state.dataUpdateCount < 60 && query.state.data?.rasters.some((layer) => !layer.tile_url) ? 3000 : false),
  });

  const [latText, setLatText] = useState("");
  const [lonText, setLonText] = useState("");
  const [method, setMethod] = useState<"manual_entry" | "map_selection">("manual_entry");
  const [reason, setReason] = useState("");
  const [reviewed, setReviewed] = useState(false);
  const [imageryId, setImageryId] = useState<string | null | undefined>(undefined); // undefined: the first available
  const [notice, setNotice] = useState<string | null>(null);

  const store = (updated: PhotoLocation) => {
    queryClient.setQueryData(["photo-location", asset.id], updated);
    void queryClient.invalidateQueries({ queryKey: ["photo-locations", projectId] });
    void queryClient.invalidateQueries({ queryKey: ["asset", asset.id] });
  };
  const resetForm = () => {
    setLatText("");
    setLonText("");
    setReason("");
    setReviewed(false);
    setMethod("manual_entry");
  };
  const onConflict = (error: unknown) => {
    if (error instanceof ApiError && error.code === "LOCATION_VERSION_CONFLICT") void state.refetch();
  };

  const change = useMutation({
    mutationFn: (body: PhotoLocationApply) => changePhotoLocation(asset.id, body),
    onSuccess: (updated, body) => {
      store(updated);
      resetForm();
      setNotice(
        body.action === "clear"
          ? "The location was removed. The history keeps every earlier step."
          : "The location was saved. It is attributed to the active profile in the history.",
      );
    },
    onError: onConflict,
  });
  const reread = useMutation({
    mutationFn: () => readPhotoExif(asset.id),
    onSuccess: (result) => {
      store(result.location);
      setNotice(
        result.outcome === "candidate_recorded"
          ? "A GPS position was read from the file. It is unverified until you confirm it."
          : result.outcome === "no_gps"
            ? "The file carries no GPS position."
            : `The file has GPS tags, but they cannot be used: ${result.problems.map((p) => p.message).join(" ")}`,
      );
    },
  });

  const typed = checkPosition(latText, lonText);
  const touched = latText !== "" || lonText !== "";

  const rasters: RasterDisplay[] = useMemo(() => {
    const placed = (layers.data?.rasters ?? []).filter((layer) => layer.tile_url);
    const chosen = imageryId === null ? undefined : placed.find((l) => l.asset_id === (imageryId ?? placed[0]?.asset_id));
    return chosen ? [{ layer: chosen, visible: true, opacity: 1 }] : [];
  }, [layers.data, imageryId]);
  const placedCount = (layers.data?.rasters ?? []).filter((layer) => layer.tile_url).length;
  const preparing = (layers.data?.rasters ?? []).filter((layer) => !layer.tile_url);

  const data = state.data;
  const confirmed = data?.current.state === "confirmed" ? data.current : null;
  const candidate = data?.exif.state === "candidate" ? data.exif : null;
  const candidatePoint =
    candidate && candidate.latitude != null && candidate.longitude != null ? { lat: candidate.latitude, lon: candidate.longitude } : null;
  const confirmedPoint = confirmed && confirmed.latitude != null && confirmed.longitude != null ? { lat: confirmed.latitude, lon: confirmed.longitude } : null;
  // What the map shows beside the saved position: what is being typed or picked, otherwise the unverified candidate.
  const previewPoint = typed.ok ? typed.position : !confirmedPoint ? candidatePoint : null;

  const fitKey = `${confirmedPoint?.lat}|${confirmedPoint?.lon}|${candidatePoint?.lat}|${candidatePoint?.lon}|${rasters[0]?.layer.asset_id ?? ""}`;
  const fitTo: Bounds | null = useMemo(() => {
    const point = confirmedPoint ?? candidatePoint;
    if (point) return around(point.lon, point.lat);
    const layer = rasters[0]?.layer;
    return layer ? (layer.bounds_4326 as Bounds) : null;
    // eslint-disable-next-line react-hooks/exhaustive-deps -- keyed on the values that decide the view, not on object identity
  }, [fitKey]);

  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(null), 12_000);
    return () => window.clearTimeout(timer);
  }, [notice]);

  if (state.isLoading) return <Loading label="Loading the photo location…" />;
  if (state.isError) return <ErrorNotice error={state.error} title="The photo location could not be loaded" />;
  const loaded = state.data!;
  const version = loaded.current.version_no;
  const busy = change.isPending || reread.isPending;

  const submitPosition = () => {
    if (!typed.ok) return;
    setNotice(null);
    change.mutate({
      action: "set_position",
      method,
      latitude: typed.position.lat,
      longitude: typed.position.lon,
      reason: reason.trim() || null,
      expected_version: version,
      confirm: reviewed,
    });
  };

  return (
    <Panel
      title="Photo location"
      description="Where this photograph was taken or placed, as confirmed by a person. It does not locate the findings in the picture."
      actions={
        confirmed ? <Badge tone="ok">Confirmed</Badge> : candidate ? <Badge tone="warn">Unverified GPS</Badge> : <Badge tone="neutral">No location</Badge>
      }
    >
      <div className="space-y-4">
        {notice ? <Notice tone="success">{notice}</Notice> : null}
        {change.isError ? <ErrorNotice error={change.error} title="The location was not saved" /> : null}
        {reread.isError ? <ErrorNotice error={reread.error} title="The file could not be read again" /> : null}

        {confirmed && confirmedPoint ? (
          <section aria-label="Confirmed location" className="space-y-1.5 rounded-md border border-ok/35 bg-ok-soft px-3 py-2.5">
            <p className="text-[13px] font-medium text-ok">Confirmed location</p>
            <p>
              <Position {...confirmedPoint} />
            </p>
            <p className="text-xs text-fg-muted">
              {SOURCE_LABELS[confirmed.source ?? "none"]} · by {confirmed.actor_label ?? "unknown"}
              {confirmed.changed_at ? ` · ${formatDateTime(confirmed.changed_at)}` : ""}
            </p>
            {confirmed.reason ? <p className="text-xs text-fg-muted">Note: “{confirmed.reason}”</p> : null}
            <p className="text-[11px] text-fg-subtle">WGS 84 (EPSG:4326). A person stands behind this position; the software cannot check it.</p>
            <Link
              to={`/projects/${projectId}/satellite?view=location&photo=${asset.id}`}
              className="inline-block text-xs text-accent hover:underline"
            >
              Show on the satellite map, with the objects in this photo
            </Link>
          </section>
        ) : (
          <p className="text-[13px] text-fg-muted">
            No confirmed location. This photograph is not drawn on the map and is not written to the GeoJSON export until one is saved.
          </p>
        )}

        <section aria-label="GPS in the photo file" className="space-y-2">
          <h3 className="text-[13px] font-medium text-fg">GPS position in the photo file</h3>
          {loaded.exif.state === "candidate" && candidatePoint ? (
            <div className="space-y-2 rounded-md border border-warn/35 bg-warn-soft px-3 py-2.5">
              <p className="text-[13px] font-medium text-warn">Unverified candidate — not a location yet</p>
              <p>
                <Position {...candidatePoint} />
              </p>
              <p className="text-xs text-fg-muted">
                Read from the file's metadata{loaded.exif.datum ? ` (datum: ${loaded.exif.datum})` : ""}. Metadata can be wrong, edited or
                copied from another photo. It is shown on the map as a hollow ring. Check it, then confirm it or enter another position.
              </p>
              {confirmedPoint && confirmedPoint.lat === candidatePoint.lat && confirmedPoint.lon === candidatePoint.lon ? (
                <p className="text-xs text-ok">This is the position you confirmed.</p>
              ) : (
                <Button
                  size="sm"
                  disabled={busy}
                  onClick={() => {
                    setNotice(null);
                    change.mutate({ action: "confirm_exif", reason: reason.trim() || null, expected_version: version, confirm: true });
                  }}
                >
                  Confirm this position
                </Button>
              )}
            </div>
          ) : loaded.exif.state === "unusable" ? (
            <Notice tone="warning" title="The file has GPS tags that cannot be used">
              <ul className="list-disc pl-4 text-xs">
                {loaded.exif.problems.map((problem) => (
                  <li key={problem.code}>{problem.message}</li>
                ))}
              </ul>
              <p className="text-xs">No position was taken from the file. You can enter one below.</p>
            </Notice>
          ) : loaded.exif.state === "not_read" ? (
            <Notice title="This photograph has GPS tags that have not been read">
              It was uploaded before GPS positions were read. Reading the stored original does not change it.
            </Notice>
          ) : (
            <p className="text-xs text-fg-subtle">The file carries no GPS position.</p>
          )}
          {!confirmed ? (
            <Button size="sm" variant="secondary" disabled={busy} onClick={() => { setNotice(null); reread.mutate(); }}>
              {reread.isPending ? "Reading…" : loaded.exif.state === "not_read" ? "Read the GPS position from the file" : "Read the file's GPS again"}
            </Button>
          ) : null}
        </section>

        <section aria-label="Enter a position" className="space-y-3 border-t border-line pt-3">
          <h3 className="text-[13px] font-medium text-fg">{confirmed ? "Change the position" : "Enter a position"}</h3>
          <p className="text-xs text-fg-subtle">Type decimal degrees or click the map. The ring shows where it would be placed.</p>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Latitude" htmlFor="photo-lat" error={touched && !typed.ok ? typed.latError : null} hint="-90 to 90, north positive">
              <input
                id="photo-lat"
                className={inputClass}
                inputMode="decimal"
                autoComplete="off"
                value={latText}
                aria-invalid={touched && !typed.ok && Boolean(typed.latError)}
                onChange={(e) => {
                  setLatText(e.target.value);
                  setMethod("manual_entry");
                  setReviewed(false);
                }}
              />
            </Field>
            <Field label="Longitude" htmlFor="photo-lon" error={touched && !typed.ok ? typed.lonError : null} hint="-180 to 180, east positive">
              <input
                id="photo-lon"
                className={inputClass}
                inputMode="decimal"
                autoComplete="off"
                value={lonText}
                aria-invalid={touched && !typed.ok && Boolean(typed.lonError)}
                onChange={(e) => {
                  setLonText(e.target.value);
                  setMethod("manual_entry");
                  setReviewed(false);
                }}
              />
            </Field>
          </div>

          <div className="space-y-1.5">
            <label htmlFor="photo-imagery" className="block text-[13px] font-medium text-fg">
              Context imagery
            </label>
            {placedCount > 0 ? (
              <select
                id="photo-imagery"
                className={selectClass}
                value={imageryId === undefined ? (rasters[0]?.layer.asset_id ?? "") : (imageryId ?? "")}
                onChange={(e) => setImageryId(e.target.value === "" ? null : e.target.value)}
              >
                <option value="">None (coordinate grid only)</option>
                {(layers.data?.rasters ?? [])
                  .filter((layer) => layer.tile_url)
                  .map((layer) => (
                    <option key={layer.asset_id} value={layer.asset_id}>
                      {layer.title}
                    </option>
                  ))}
              </select>
            ) : layers.isLoading ? (
              <Loading label="Looking for imagery…" />
            ) : preparing.length > 0 ? (
              <p className="text-xs text-fg-subtle" data-testid="imagery-preparing">
                {preparing.map((layer) => layer.title).join(", ")} {preparing.length === 1 ? "is" : "are"} placed but not ready to display yet
                {preparing[0]?.display_unavailable_reason ? ` (${preparing[0].display_unavailable_reason})` : ""}. It will appear here when it
                is ready.
              </p>
            ) : (
              <p className="text-xs text-fg-subtle" data-testid="no-imagery">
                No georeferenced GeoTIFF imagery is available in this project, so the map shows only a coordinate grid. Upload a GeoTIFF with a
                valid CRS to use it as context. Nothing is downloaded from an online source.
              </p>
            )}
            {layers.isError ? <ErrorNotice error={layers.error} title="The project's imagery could not be loaded" /> : null}
          </div>

          <Suspense fallback={<Loading label="Loading the map…" />}>
            <MapCanvas
              basemap={readMapboxToken().ok ? "mapbox" : "offline"}
              className="h-64"
              rasters={rasters}
              aoi={layers.data?.aoi?.geometry_4326 ?? null}
              showAoi
              showFootprints={false}
              selectedRasterId={null}
              fitTo={fitTo}
              pinned={null}
              preview={previewPoint ? { lon: previewPoint.lon, lat: previewPoint.lat } : null}
              photos={confirmedPoint ? [{ id: asset.id, lon: confirmedPoint.lon, lat: confirmedPoint.lat, label: asset.original_filename }] : []}
              selectedPhotoId={asset.id}
              onPick={(point) => {
                setLatText(toInputValue(point.lat));
                setLonText(toInputValue(point.lon));
                setMethod("map_selection");
                setReviewed(false);
              }}
            />
          </Suspense>

          <Field label="Note (optional)" htmlFor="photo-reason" hint="Why this position, for the history.">
            <input id="photo-reason" className={inputClass} value={reason} maxLength={1000} onChange={(e) => setReason(e.target.value)} />
          </Field>

          <label className="flex items-start gap-2 text-[13px] text-fg-muted">
            <input type="checkbox" className="mt-0.5 accent-accent" checked={reviewed} disabled={!typed.ok} onChange={(e) => setReviewed(e.target.checked)} />
            <span>I have checked this position on the map and I confirm it.</span>
          </label>
          <div className="flex flex-wrap gap-2">
            <Button size="sm" disabled={!typed.ok || !reviewed || busy} onClick={submitPosition}>
              {change.isPending ? "Saving…" : "Save location"}
            </Button>
            {confirmed ? (
              <Button
                size="sm"
                variant="secondary"
                disabled={busy}
                onClick={() => {
                  setNotice(null);
                  change.mutate({ action: "clear", reason: reason.trim() || null, expected_version: version, confirm: true });
                }}
              >
                Remove the location
              </Button>
            ) : null}
          </div>
        </section>

        {loaded.history.length > 0 ? (
          <details className="border-t border-line pt-3 text-xs">
            <summary className="cursor-pointer text-fg-muted">History ({loaded.history.length})</summary>
            <ol className="mt-2 space-y-1.5" aria-label="Location history">
              {[...loaded.history].reverse().map((row) => (
                <li key={row.version_no} className="text-fg-muted">
                  <span className="font-medium text-fg">
                    v{row.version_no} · {row.status === "candidate" ? "unverified EXIF candidate" : row.status === "cleared" ? "location removed" : "confirmed"}
                  </span>
                  {row.latitude != null && row.longitude != null ? (
                    <>
                      {" "}
                      · <Position lat={row.latitude} lon={row.longitude} />
                    </>
                  ) : null}
                  <span className="block text-fg-subtle">
                    {SOURCE_LABELS[row.source]} · {row.actor_label} · {formatDateTime(row.created_at)}
                    {row.reason ? ` · “${row.reason}”` : ""}
                  </span>
                </li>
              ))}
            </ol>
          </details>
        ) : null}
        <p className="text-[11px] text-fg-subtle">{loaded.note}</p>
      </div>
    </Panel>
  );
}
