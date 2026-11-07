import { useQuery } from "@tanstack/react-query";
import { type ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { getLayers, listPhotoLocations, type Project } from "../api/client";
import { formatLatLon } from "../lib/coordinates";
import { type Bounds4326, isPoint, startView } from "../lib/location3d";
import { type BasemapStatus, readMapboxToken } from "../lib/mapbox";
import { IconSatellite } from "./icons";
import { MapCanvas, type PhotoMarker, type RasterDisplay } from "./MapCanvas";
import { PhotoHoverCard, PhotoOnMapPanel, usePhotoOnMap } from "./PhotoOnMap";
import { PlaceSearch } from "./PlaceSearch";
import { Badge, Button, Notice } from "./ui";

/** How close the view goes to a searched position: streets and buildings are visible. */
const PLACE_ZOOM = 16;
/** How close the view goes to a photo: a few hundred metres around the camera, where most things in a photo are. */
const PHOTO_ZOOM = 17;
/** The photo panel's width (26 rem) and its right margin (3 rem), in screen pixels. */
const PANEL_PX = 416;
const PANEL_MARGIN_PX = 48;
/** While spots are matched the panel takes this share of the map's width (at most `MATCH_PANEL_MAX_PX`), so the photo is large. */
const MATCH_PANEL_SHARE = 0.55;
const MATCH_PANEL_MAX_PX = 880;

/** The hover popup: 24 rem wide, about 440 px tall with a 4:3 photo and its summary. */
const POPUP_HALF_WIDTH_PX = 192;
const POPUP_HEIGHT_PX = 440;

/**
 * Where the popup goes (its top-left corner, in pixels from the map's top left): above the marker when it fits, else below,
 * else beside it (right, or left), never covering the marker and always inside the map.
 */
export function popupPosition(marker: { x: number; y: number }, mapWidth: number, mapHeight: number): { left: number; top: number } {
  const gap = 18;
  const margin = 8;
  const width = POPUP_HALF_WIDTH_PX * 2;
  const clamp = (value: number, low: number, high: number) => Math.max(low, Math.min(value, high));
  const centredLeft = clamp(marker.x - width / 2, margin, mapWidth - width - margin);
  if (marker.y - gap - POPUP_HEIGHT_PX >= margin) return { left: centredLeft, top: marker.y - gap - POPUP_HEIGHT_PX };
  if (marker.y + gap + POPUP_HEIGHT_PX <= mapHeight - margin) return { left: centredLeft, top: marker.y + gap };
  const top = clamp(marker.y - POPUP_HEIGHT_PX / 2, margin, mapHeight - POPUP_HEIGHT_PX - margin);
  const right = marker.x + gap;
  if (right + width <= mapWidth - margin) return { left: right, top };
  return { left: Math.max(margin, marker.x - gap - width), top };
}

/** How far east of the camera to centre the map so the camera sits in the middle of the part the panel leaves uncovered. */
function shiftForPanel(panelPx: number, zoom: number): number {
  return (((panelPx + PANEL_MARGIN_PX) / 2) * 360) / (512 * 2 ** zoom);
}

interface Found {
  lat: number;
  lon: number;
  label: string;
}

/** The chosen position, kept in the address (?lat=&lon=&label=) so the view can be reloaded or shared. Never saved. */
function useFound(): [Found | null, (found: Found | null) => void] {
  const [params, setParams] = useSearchParams();
  const lat = Number(params.get("lat"));
  const lon = Number(params.get("lon"));
  const found =
    params.has("lat") && params.has("lon") && Number.isFinite(lat) && Number.isFinite(lon) && Math.abs(lat) <= 90 && Math.abs(lon) <= 180
      ? { lat, lon, label: params.get("label") || formatLatLon(lat, lon) }
      : null;
  const set = (next: Found | null) =>
    setParams(
      (current) => {
        const updated = new URLSearchParams(current);
        updated.set("view", "location");
        if (next) {
          updated.set("lat", next.lat.toFixed(6));
          updated.set("lon", next.lon.toFixed(6));
          updated.set("label", next.label);
        } else {
          updated.delete("lat");
          updated.delete("lon");
          updated.delete("label");
        }
        return updated;
      },
      { replace: true },
    );
  return [found, set];
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <Button
      size="sm"
      variant="ghost"
      onClick={() => {
        void navigator.clipboard?.writeText(text).then(
          () => {
            setCopied(true);
            window.setTimeout(() => setCopied(false), 1500);
          },
          () => undefined,
        );
      }}
    >
      {copied ? "Copied" : "Copy"}
    </Button>
  );
}

/**
 * Find any place on the online satellite map by its name or by latitude and longitude, and look at it. It is for looking
 * only: nothing is added to the project. The imagery is Mapbox's (not live; when it was taken differs by place).
 */
export function SatelliteLocationView({
  project,
  imageIds,
  actions,
  notices,
}: {
  project: Project;
  /** The project's satellite and aerial images. Elevation files (height grids) are left out: they are not pictures. */
  imageIds: string[];
  actions?: ReactNode;
  notices?: ReactNode;
}) {
  const token = useMemo(() => readMapboxToken(), []);
  const [found, setFound] = useFound();
  const [pinned, setPinned] = useState<{ lon: number; lat: number } | null>(null);
  const [status, setStatus] = useState<BasemapStatus | null>(null);

  // The project's georeferenced images, drawn in place over the satellite map.
  const [params, setParams] = useSearchParams();
  const imageId = params.get("image");
  const [showImages, setShowImages] = useState(true);
  const layers = useQuery({ queryKey: ["layers", project.id, "satellite-location"], queryFn: () => getLayers(project.id) });
  const imageKey = imageIds.join(",");
  const placedImages = useMemo(
    () => (layers.data?.rasters ?? []).filter((layer) => layer.tile_url && imageKey.split(",").includes(layer.asset_id)),
    [layers.data, imageKey],
  );
  const rasters: RasterDisplay[] = useMemo(
    () => (showImages ? placedImages.map((layer) => ({ layer, visible: true, opacity: 1 })) : []),
    [placedImages, showImages],
  );
  const image = imageId ? (layers.data?.rasters ?? []).find((layer) => layer.asset_id === imageId) : undefined;
  const imageBounds = image?.bounds_4326 && image.bounds_4326.length === 4 ? (image.bounds_4326 as Bounds4326) : null;

  // Before anything is searched, open on the project's own area (its area of interest, else its located photographs).
  const photos = useQuery({ queryKey: ["photo-locations", project.id], queryFn: () => listPhotoLocations(project.id) });
  const start = useMemo(
    () =>
      startView({
        aoi: project.aoi?.geometry_4326 as { type?: unknown; coordinates?: unknown } | undefined,
        photos: photos.data?.items,
      }),
    [project.aoi, photos.data],
  );
  // The project's photos with a confirmed location, drawn as green markers where they were taken.
  const located = useMemo(
    () => (photos.data?.items ?? []).filter((item) => item.state === "confirmed" && item.latitude != null && item.longitude != null),
    [photos.data],
  );
  const photoMarkers: PhotoMarker[] = useMemo(
    () => located.map((item) => ({ id: item.asset_id, lon: item.longitude!, lat: item.latitude!, label: item.filename })),
    [located],
  );
  const photoId = params.get("photo");
  const selectedPhoto = located.find((item) => item.asset_id === photoId) ?? null;
  // The photo under the pointer, shown in a small popup at its marker.
  const [hover, setHover] = useState<{ id: string; x: number; y: number } | null>(null);
  const hovered = hover ? located.find((item) => item.asset_id === hover.id) : undefined;
  const choosePhoto = (id: string | null) =>
    setParams(
      (current) => {
        const updated = new URLSearchParams(current);
        updated.set("view", "location");
        if (id) updated.set("photo", id);
        else updated.delete("photo");
        return updated;
      },
      { replace: true },
    );
  const photo = usePhotoOnMap(project.id, selectedPhoto);
  // Going to a photo happens once each time one is chosen; closing it leaves the view where it is.
  const [photoGoTo, setPhotoGoTo] = useState<{ lon: number; lat: number; zoom: number } | null>(null);
  const flownFor = useRef<string | null>(null);
  useEffect(() => {
    if (!selectedPhoto) {
      flownFor.current = null;
      return;
    }
    if (flownFor.current === selectedPhoto.asset_id) return;
    flownFor.current = selectedPhoto.asset_id;
    // The photo panel covers the right of the map, so the camera is put in the middle of the part left uncovered.
    setPhotoGoTo({ lon: selectedPhoto.longitude! + shiftForPanel(PANEL_PX, PHOTO_ZOOM), lat: selectedPhoto.latitude!, zoom: PHOTO_ZOOM });
  }, [selectedPhoto]);
  const aiming = photo.aim !== null;
  // Matching spots: the panel widens so the photo is large, and the camera is centred in the map that is left.
  const matching = photo.aim?.kind === "match";
  const mapArea = useRef<HTMLDivElement>(null);
  const [matchPanelPx, setMatchPanelPx] = useState(MATCH_PANEL_MAX_PX);
  useEffect(() => {
    if (!matching || !selectedPhoto) return;
    const width = mapArea.current?.clientWidth || 1360;
    const panel = Math.round(Math.min(MATCH_PANEL_MAX_PX, width * MATCH_PANEL_SHARE));
    setMatchPanelPx(panel);
    setPhotoGoTo({ lon: selectedPhoto.longitude! + shiftForPanel(panel, PHOTO_ZOOM), lat: selectedPhoto.latitude!, zoom: PHOTO_ZOOM });
    // Only when matching starts: later clicks must not move the map.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [matching]);

  const startPoint = start && isPoint(start.bounds) ? { lon: start.bounds[0], lat: start.bounds[1], zoom: 14 } : null;
  // Keyed on the numbers, not the object: the position is re-read from the address on every render, and a new object
  // would make the map fly again.
  const foundLat = found?.lat;
  const foundLon = found?.lon;
  const goTo = useMemo(
    () => (foundLat !== undefined && foundLon !== undefined ? { lon: foundLon, lat: foundLat, zoom: PLACE_ZOOM } : null),
    [foundLat, foundLon],
  );
  const marker = useMemo(
    () => pinned ?? (foundLat !== undefined && foundLon !== undefined ? { lon: foundLon, lat: foundLat } : null),
    [pinned, foundLat, foundLon],
  );
  // Name searches prefer places near what is being looked at: the found place, else the project's own area.
  const near = useMemo(() => {
    if (foundLat !== undefined && foundLon !== undefined) return { lat: foundLat, lon: foundLon };
    const bounds = imageBounds ?? start?.bounds;
    return bounds ? { lon: (bounds[0] + bounds[2]) / 2, lat: (bounds[1] + bounds[3]) / 2 } : null;
  }, [foundLat, foundLon, imageBounds, start]);

  return (
    <div className="flex h-full min-h-0 flex-col bg-canvas">
      <div className="flex h-11 shrink-0 items-center gap-3 border-b border-line/70 bg-surface/80 px-3 backdrop-blur-md">
        <IconSatellite size={16} className="shrink-0 text-fg-subtle" />
        <span className="text-[13px] font-medium text-fg">Find a location</span>
        <span
          title={
            token.ok
              ? "Online: place names you search and the map area you view are sent to Mapbox. The imagery is Mapbox's, not live, and when it was taken differs by place. Coordinates you type are read on this computer. Nothing is saved to the project unless you save a map note."
              : "Coordinates you type are read on this computer. Nothing is saved to the project unless you save a map note."
          }
        >
          <Badge tone={token.ok ? "neutral" : "warn"}>{token.ok ? "Online satellite map (Mapbox)" : "Plain offline map"}</Badge>
        </span>
        <div className="ml-auto flex items-center gap-2">{actions}</div>
      </div>
      {notices}
      <div ref={mapArea} className="relative min-h-0 flex-1">
        <MapCanvas
          className="h-full rounded-none border-0"
          basemap="mapbox"
          rasters={rasters}
          aoi={(project.aoi?.geometry_4326 as Record<string, unknown> | undefined) ?? null}
          showAoi
          showFootprints={showImages}
          selectedRasterId={image?.asset_id ?? null}
          fitTo={found ? null : (imageBounds ?? (start && !startPoint ? start.bounds : null))}
          start={found ? { lon: found.lon, lat: found.lat, zoom: PLACE_ZOOM } : (startPoint ?? { lon: 0, lat: 20, zoom: 1.5 })}
          goTo={photoGoTo ?? goTo}
          pinned={marker}
          onPhotoHover={setHover}
          photos={photoMarkers}
          showPhotos
          selectedPhotoId={selectedPhoto?.asset_id ?? null}
          onPhotoSelect={aiming ? undefined : (id) => choosePhoto(id)}
          sightLines={photo.sightLines}
          onSightLineSelect={aiming ? undefined : (id) => photo.setSelected(id)}
          onPick={(position) => {
            if (!photo.pick(position)) setPinned(position);
          }}
          onBasemapStatus={setStatus}
        />

        <div
          className={`pointer-events-none absolute top-3 left-3 z-10 w-120 max-w-[calc(100%-1.5rem)] flex-col gap-2 ${matching ? "hidden" : "flex"}`}
        >
          {/* Above the cards below it, so its list of suggestions is not hidden behind them. */}
          <div className="pointer-events-auto relative z-20 rounded-xl border border-line bg-surface/95 p-3 shadow-[0_12px_32px_rgb(0_0_0/0.45)] backdrop-blur-md">
            <PlaceSearch
              compact
              coordinates
              landmarks
              near={near}
              onPick={(match) => {
                setPinned(null);
                setPhotoGoTo(null);
                setFound({ lat: match.lat, lon: match.lon, label: match.label });
              }}
            />
          </div>

          {found ? (
            <div
              className="pointer-events-auto rounded-xl border border-line bg-surface/95 p-3 text-[13px] backdrop-blur-md"
              aria-label="Found location"
              role="region"
            >
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <p className="wrap-anywhere font-medium text-fg">{found.label}</p>
                  <p className="font-mono text-xs text-fg-muted">{formatLatLon(found.lat, found.lon, 6)}</p>
                </div>
                <div className="flex shrink-0 gap-1">
                  <CopyButton text={`${found.lat.toFixed(6)}, ${found.lon.toFixed(6)}`} />
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => {
                      setPinned(null);
                      setFound(null);
                    }}
                  >
                    Clear
                  </Button>
                </div>
              </div>
            </div>
          ) : null}

          {image ? (
            <div className="pointer-events-auto rounded-xl border border-line bg-surface/95 p-3 text-[13px] backdrop-blur-md" role="region" aria-label="Your image">
              <p className="text-xs text-fg-subtle">Your image, drawn in place from its georeferencing</p>
              <p className="wrap-anywhere font-medium text-fg">{image.title}</p>
              {imageBounds ? (
                <p className="font-mono text-xs text-fg-muted">
                  Centre {formatLatLon((imageBounds[1] + imageBounds[3]) / 2, (imageBounds[0] + imageBounds[2]) / 2)}
                </p>
              ) : null}
              <Link to={`/projects/${project.id}/assets/${image.asset_id}/overhead`} className="text-xs text-accent hover:underline">
                Back to the image
              </Link>
            </div>
          ) : null}

          {located.length > 0 ? (
            <div className="pointer-events-auto rounded-xl border border-line bg-surface/95 p-3 text-[13px] backdrop-blur-md" role="region" aria-label="Your photos">
              <ul className="max-h-36 space-y-0.5 overflow-y-auto">
                {located.map((item) => (
                  <li key={item.asset_id}>
                    <button
                      type="button"
                      aria-pressed={item.asset_id === selectedPhoto?.asset_id}
                      onClick={() => choosePhoto(item.asset_id === selectedPhoto?.asset_id ? null : item.asset_id)}
                      className={`flex w-full items-center gap-2 rounded-md px-2 py-1 text-left ${item.asset_id === selectedPhoto?.asset_id ? "bg-accent-soft text-fg" : "text-fg-muted hover:bg-overlay hover:text-fg"}`}
                    >
                      <span className="size-2.5 shrink-0 rounded-full bg-[#3ecf8e]" />
                      <span className="min-w-0 flex-1 truncate">{item.filename}</span>
                      <span className="shrink-0 text-[11px] text-fg-subtle">{item.observations} finding(s)</span>
                    </button>
                  </li>
                ))}
              </ul>
              {(photos.data?.candidates ?? 0) > 0 ? (
                <p className="mt-1 text-[11px] text-fg-subtle">
                  {photos.data!.candidates} more photo(s) have a GPS position that is not confirmed yet. Confirm it in Photo Analysis to show them here.
                </p>
              ) : null}
            </div>
          ) : (photos.data?.candidates ?? 0) > 0 ? (
            <p className="pointer-events-auto w-fit rounded-lg bg-surface/95 px-3 py-1.5 text-xs text-fg-muted backdrop-blur-md">
              {photos.data!.candidates} photo(s) have a GPS position that is not confirmed yet. Confirm it in Photo Analysis to show them here.
            </p>
          ) : null}

          {placedImages.length > 0 ? (
            <label className="pointer-events-auto flex w-fit items-center gap-2 rounded-lg bg-surface/95 px-3 py-1.5 text-xs text-fg-muted backdrop-blur-md">
              <input type="checkbox" checked={showImages} onChange={(event) => setShowImages(event.target.checked)} />
              Show this project's georeferenced images ({placedImages.length}) on the map
            </label>
          ) : null}

          {pinned ? (
            <div className="pointer-events-auto rounded-xl border border-line bg-surface/95 p-3 text-[13px] backdrop-blur-md" aria-label="Clicked point" role="region">
              <div className="flex items-center justify-between gap-2">
                <div>
                  <p className="text-xs text-fg-subtle">Clicked point</p>
                  <p className="font-mono text-xs text-fg">{formatLatLon(pinned.lat, pinned.lon, 6)}</p>
                </div>
                <CopyButton text={`${pinned.lat.toFixed(6)}, ${pinned.lon.toFixed(6)}`} />
              </div>
            </div>
          ) : null}

          {!token.ok ? (
            <div className="pointer-events-auto">
              <Notice tone="warning" title="Satellite imagery needs a Mapbox token">
                <p>
                  The plain offline map is shown. Coordinates work without a token; searching by place name and the satellite
                  picture need one (see the installation guide, section 8.1).
                </p>
              </Notice>
            </div>
          ) : null}
          {status?.state === "error" && status.message ? (
            <div className="pointer-events-auto">
              <Notice tone="error" title="The satellite map could not be loaded">
                <p>{status.message}</p>
              </Notice>
            </div>
          ) : null}
        </div>

        {hover && hovered ? (
          <div
            role="tooltip"
            aria-label="Photo taken here"
            className="pointer-events-none absolute z-30 w-96 overflow-hidden rounded-xl border border-line bg-surface/95 text-[13px] shadow-[0_12px_32px_rgb(0_0_0/0.5)] backdrop-blur-md"
            style={popupPosition(hover, mapArea.current?.clientWidth || 1600, mapArea.current?.clientHeight || 900)}
          >
            <PhotoHoverCard projectId={project.id} item={hovered} open={hovered.asset_id === selectedPhoto?.asset_id} />
          </div>
        ) : null}

        {selectedPhoto ? (
          <div
            className={`pointer-events-none absolute top-3 right-12 bottom-8 z-10 flex max-w-[calc(100%-4rem)] flex-col ${matching ? "" : "w-104"}`}
            style={matching ? { width: matchPanelPx } : undefined}
          >
            <PhotoOnMapPanel projectId={project.id} state={photo} onClose={() => choosePhoto(null)} />
          </div>
        ) : null}
      </div>
    </div>
  );
}
