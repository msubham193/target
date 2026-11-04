/**
 * The optional online satellite basemap: Mapbox Standard Satellite, drawn by Mapbox GL JS.
 *
 * It is the only external basemap. It needs a public access token in `VITE_MAPBOX_ACCESS_TOKEN`; without one the app keeps
 * its offline map and says why. A secret token (sk.…) is refused: anything in `VITE_*` is shipped to the browser.
 */

export const MAPBOX_STYLE = "mapbox://styles/mapbox/standard-satellite";
export const TOKEN_VARIABLE = "VITE_MAPBOX_ACCESS_TOKEN";

export type Basemap = "offline" | "mapbox";

export type TokenState =
  | { ok: true; token: string }
  | { ok: false; reason: "missing" | "secret" | "malformed"; message: string };

/** The token from the build environment, checked for what it is, never logged and never echoed back. */
export function readMapboxToken(raw: unknown = import.meta.env.VITE_MAPBOX_ACCESS_TOKEN): TokenState {
  const value = typeof raw === "string" ? raw.trim() : "";
  if (!value) {
    return {
      ok: false,
      reason: "missing",
      message: `No Mapbox access token is configured. Set ${TOKEN_VARIABLE} to a Mapbox public token (pk.…) in apps/web/.env.local and restart the web app. The offline map is shown instead.`,
    };
  }
  if (value.startsWith("sk.")) {
    return {
      ok: false,
      reason: "secret",
      message: `${TOKEN_VARIABLE} holds a secret Mapbox token (sk.…). It was not used: a secret token must never be placed where the browser can read it. Create a public token (pk.…) and use that instead.`,
    };
  }
  if (!/^(pk|tk)\.[A-Za-z0-9_.-]{20,}$/.test(value)) {
    return {
      ok: false,
      reason: "malformed",
      message: `${TOKEN_VARIABLE} is not a Mapbox public token (it should start with pk.). The offline map is shown instead.`,
    };
  }
  return { ok: true, token: value };
}

/** Text with anything that looks like a Mapbox token, or an access_token query value, removed. Applied to every message shown. */
export function redactSecrets(text: string): string {
  return text.replace(/\b(?:pk|sk|tk)\.[A-Za-z0-9_-]{10,}(?:\.[A-Za-z0-9_-]+)*/g, "[token removed]").replace(/(access_token=)[^&\s"')]*/gi, "$1[token removed]");
}

/** The label and feature groups of the Standard style that can be switched, by their `basemap` configuration names. */
export const LABEL_TOGGLES = [
  { key: "showRoadLabels", label: "Road labels" },
  { key: "showPlaceLabels", label: "Place names" },
  { key: "showPointOfInterestLabels", label: "Points of interest" },
  { key: "showTransitLabels", label: "Transit labels" },
  { key: "showPedestrianRoads", label: "Pedestrian roads and paths" },
] as const;

export type LabelKey = (typeof LABEL_TOGGLES)[number]["key"];
export type LabelState = Record<LabelKey, boolean>;

export const DEFAULT_LABELS: LabelState = {
  showRoadLabels: true,
  showPlaceLabels: true,
  showPointOfInterestLabels: true,
  showTransitLabels: true,
  showPedestrianRoads: true,
};

/** Above this zoom the picture may be enlarged beyond the resolution it was captured at, depending on where it is. */
export const ENLARGED_ABOVE_ZOOM = 17;

export function zoomNote(zoom: number | null): string | null {
  if (zoom == null || zoom <= ENLARGED_ABOVE_ZOOM) return null;
  return `At zoom ${zoom.toFixed(1)} the satellite picture may be enlarged beyond the resolution it was captured at. How much detail exists depends on the place; the map does not sharpen or add detail, and it is not live imagery.`;
}

export interface BasemapStatus {
  state: "loading" | "ready" | "error";
  message?: string;
  zoom?: number;
}

export type Parsed = { ok: true; lat: number; lon: number } | { ok: false; message: string };

/** One coordinate in decimal degrees, optionally with a hemisphere letter after it ("20.2961 N", "-85.8245", "85.8245 W"). */
function degrees(text: string, positive: string, negative: string, name: string): number | string {
  const match = /^\s*([+-]?\d+(?:\.\d+)?)\s*°?\s*([A-Za-z])?\s*$/.exec(text);
  if (!match) return `${name} must be a number in decimal degrees, for example 20.2961.`;
  let value = Number(match[1]);
  const letter = match[2]?.toUpperCase();
  if (letter) {
    if (letter !== positive && letter !== negative) return `${name} may end in ${positive} or ${negative} only.`;
    if (match[1]!.startsWith("-") || match[1]!.startsWith("+")) return `${name}: use either a sign or a hemisphere letter, not both.`;
    if (letter === negative) value = -value;
  }
  return value;
}

/** Latitude and longitude typed by a person, checked against their ranges. Nothing is guessed or swapped. */
export function parseLatLon(latText: string, lonText: string): Parsed {
  const lat = degrees(latText, "N", "S", "Latitude");
  if (typeof lat === "string") return { ok: false, message: lat };
  const lon = degrees(lonText, "E", "W", "Longitude");
  if (typeof lon === "string") return { ok: false, message: lon };
  if (lat < -85.0511 || lat > 85.0511) {
    return { ok: false, message: "Latitude must be between -85.0511 and 85.0511, the limit of the web map." };
  }
  if (lon < -180 || lon > 180) return { ok: false, message: "Longitude must be between -180 and 180." };
  return { ok: true, lat, lon };
}

/**
 * One still satellite picture of the Odisha coast (Chilika lake) for the login screen, from Mapbox's Static Images API.
 * It is a live request, never saved: Mapbox's imagery and attribution stay Mapbox's.
 */
export function satelliteBackdropUrl(token: string): string {
  return `https://api.mapbox.com/styles/v1/mapbox/satellite-v9/static/85.3,19.7,8.6,0/1280x720@2x?logo=true&attribution=true&access_token=${encodeURIComponent(token)}`;
}

/** A wide still satellite picture for a page banner (Bhubaneswar and the Mahanadi delta), from the same Static Images API. */
export function satelliteBannerUrl(token: string): string {
  return `https://api.mapbox.com/styles/v1/mapbox/satellite-v9/static/85.9,20.1,9.2,0/1280x400@2x?logo=true&attribution=true&access_token=${encodeURIComponent(token)}`;
}

export interface PlaceMatch {
  id: string;
  /** What to show: the place name and where it is. */
  label: string;
  lon: number;
  lat: number;
}

/**
 * Looks a place name up with Mapbox's geocoding service (online). The typed text is sent to Mapbox. Returns at most five
 * matches, each with the position Mapbox gives for it; nothing is chosen for the person.
 */
export async function searchPlaces(query: string, token: string, signal?: AbortSignal): Promise<PlaceMatch[]> {
  const url = `https://api.mapbox.com/search/geocode/v6/forward?q=${encodeURIComponent(query)}&limit=5&access_token=${encodeURIComponent(token)}`;
  const response = await fetch(url, { signal });
  if (response.status === 401 || response.status === 403) {
    throw new Error("Mapbox did not accept the access token, so places cannot be searched.");
  }
  if (!response.ok) throw new Error(`The place search failed (HTTP ${response.status}).`);
  const body = (await response.json()) as {
    features?: {
      id?: string;
      geometry?: { coordinates?: [number, number] };
      properties?: { name?: string; place_formatted?: string; full_address?: string };
    }[];
  };
  const matches: PlaceMatch[] = [];
  for (const [index, feature] of (body.features ?? []).entries()) {
    const [lon, lat] = feature.geometry?.coordinates ?? [];
    if (typeof lon !== "number" || typeof lat !== "number") continue;
    const name = feature.properties?.name ?? "";
    const where = feature.properties?.place_formatted ?? "";
    const label = feature.properties?.full_address || [name, where].filter(Boolean).join(", ");
    if (!label) continue;
    matches.push({ id: feature.id ?? `${index}`, label, lon, lat });
  }
  return matches;
}
