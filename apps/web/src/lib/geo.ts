/**
 * Display conversions for geographic coordinates (GEO-002). The stored value is always the number; these
 * functions only format it, deterministically, and can parse their own output back for a round-trip check.
 */

export interface Dms {
  degrees: number;
  minutes: number;
  seconds: number;
  hemisphere: "N" | "S" | "E" | "W";
}

/** Degrees, minutes and seconds of an angle. Seconds are rounded to `secondDecimals` and carried correctly. */
export function toDms(value: number, axis: "lat" | "lon", secondDecimals = 2): Dms {
  if (!Number.isFinite(value)) throw new RangeError("coordinate must be a finite number");
  const limit = axis === "lat" ? 90 : 180;
  if (Math.abs(value) > limit) throw new RangeError(`${axis === "lat" ? "latitude" : "longitude"} out of range`);
  const hemisphere = axis === "lat" ? (value < 0 ? "S" : "N") : value < 0 ? "W" : "E";
  const scale = 10 ** secondDecimals;
  // Work in whole units of the last shown decimal of a second so rounding carries into minutes and degrees.
  let units = Math.round(Math.abs(value) * 3600 * scale);
  const degrees = Math.floor(units / (3600 * scale));
  units -= degrees * 3600 * scale;
  const minutes = Math.floor(units / (60 * scale));
  units -= minutes * 60 * scale;
  return { degrees, minutes, seconds: units / scale, hemisphere };
}

export function formatDms(value: number, axis: "lat" | "lon", secondDecimals = 2): string {
  const { degrees, minutes, seconds, hemisphere } = toDms(value, axis, secondDecimals);
  const s = seconds.toFixed(secondDecimals).padStart(secondDecimals > 0 ? 3 + secondDecimals : 2, "0");
  return `${degrees}°${String(minutes).padStart(2, "0")}′${s}″${hemisphere}`;
}

/** Parses the output of formatDms. Not a general-purpose coordinate parser. */
export function parseDms(text: string): number {
  const match = /^(\d+)°(\d{2})′(\d{2}(?:\.\d+)?)″([NSEW])$/.exec(text.trim());
  if (!match) throw new SyntaxError(`not a DMS value: ${text}`);
  const [, d, m, s, h] = match;
  const value = Number(d) + Number(m) / 60 + Number(s) / 3600;
  return h === "S" || h === "W" ? -value : value;
}

/** Decimal degrees with a fixed number of places and the hemisphere spelled out, never a bare signed number. */
export function formatDecimalDegrees(value: number, axis: "lat" | "lon", decimals = 6): string {
  const hemisphere = axis === "lat" ? (value < 0 ? "S" : "N") : value < 0 ? "W" : "E";
  return `${Math.abs(value).toFixed(decimals)}° ${hemisphere}`;
}

export interface LineFeature {
  type: "Feature";
  properties: { kind: "meridian" | "parallel"; value: number };
  geometry: { type: "LineString"; coordinates: [number, number][] };
}

/** Lines of longitude and latitude covering the world, for drawing a graticule without a basemap. */
export function graticule(stepDegrees: number): { type: "FeatureCollection"; features: LineFeature[] } {
  const features: LineFeature[] = [];
  for (let lon = -180; lon <= 180; lon += stepDegrees) {
    const coordinates: [number, number][] = [];
    for (let lat = -85; lat <= 85; lat += 1) coordinates.push([lon, lat]);
    features.push({ type: "Feature", properties: { kind: "meridian", value: lon }, geometry: { type: "LineString", coordinates } });
  }
  for (let lat = -80; lat <= 80; lat += stepDegrees) {
    const coordinates: [number, number][] = [];
    for (let lon = -180; lon <= 180; lon += 2) coordinates.push([lon, lat]);
    features.push({ type: "Feature", properties: { kind: "parallel", value: lat }, geometry: { type: "LineString", coordinates } });
  }
  return { type: "FeatureCollection", features };
}

/** A length in metres, in m below 10 km and in km above, with the exact metres kept in the title text. */
export function formatLength(metres: number): string {
  if (metres < 10_000) return `${metres.toFixed(metres < 10 ? 2 : 1)} m`;
  return `${(metres / 1000).toFixed(3)} km`;
}

/** An area in square metres: m² below 1 ha, hectares below 100 km², km² above. */
export function formatArea(squareMetres: number): string {
  if (squareMetres < 10_000) return `${squareMetres.toFixed(1)} m²`;
  if (squareMetres < 100_000_000) return `${(squareMetres / 10_000).toFixed(2)} ha`;
  return `${(squareMetres / 1_000_000).toFixed(2)} km²`;
}
