/**
 * Direction lines from where a photograph was taken towards the objects found in it, for drawing on the map.
 *
 * A single photograph gives the direction to each object (from where the object sits across the picture, once it is
 * known which way the camera faced and how wide its view was), never its distance. So each object is drawn as a line:
 * it is somewhere along that line. Where it actually is on the ground is only known when a person marks it. Lens
 * distortion, camera tilt and roll are ignored, so a direction is approximate (a few degrees with a level phone).
 */
import { badgeFor } from "./avatar";

/** A typical phone main camera (about 26 mm equivalent) in a landscape 4:3 picture is roughly this wide. */
export const TYPICAL_VIEW_WIDTH_DEG = 65;
export const DEFAULT_LINE_LENGTH_M = 250;

const EARTH_RADIUS_M = 6_371_008.8;
/** Half the diagonal of the 36 × 24 mm frame that "35 mm equivalent" focal lengths refer to. */
const HALF_DIAGONAL_35MM = Math.hypot(36, 24) / 2;

const rad = (degrees: number) => (degrees * Math.PI) / 180;
const deg = (radians: number) => (radians * 180) / Math.PI;

export function normaliseDegrees(value: number): number {
  const turned = value % 360;
  return turned < 0 ? turned + 360 : turned + 0;
}

/**
 * How wide the picture's view is from side to side, in degrees, from the 35 mm equivalent focal length. The equivalent is
 * defined on the frame diagonal, so the picture's own shape decides how much of it is width.
 */
export function viewWidthFromFocal(focal35mm: number, imageWidth: number, imageHeight: number): number | null {
  if (!(focal35mm > 0) || !(imageWidth > 0) || !(imageHeight > 0)) return null;
  const halfWidth = HALF_DIAGONAL_35MM * (imageWidth / Math.hypot(imageWidth, imageHeight));
  return deg(2 * Math.atan(halfWidth / focal35mm));
}

/**
 * The compass direction to something at column `x` of a picture `imageWidth` pixels wide, taken facing `heading` with a
 * view `viewWidth` degrees wide. The centre column looks along the heading; the edges are half the view width either side.
 */
export function bearingInPicture(heading: number, viewWidth: number, x: number, imageWidth: number): number {
  const offset = (2 * x) / imageWidth - 1;
  return normaliseDegrees(heading + deg(Math.atan(offset * Math.tan(rad(viewWidth) / 2))));
}

export interface LonLat {
  lon: number;
  lat: number;
}

/** The point `metres` away from `from` along compass direction `bearing` (on a sphere; fine over a few kilometres). */
export function destination(from: LonLat, bearing: number, metres: number): LonLat {
  const angular = metres / EARTH_RADIUS_M;
  const theta = rad(bearing);
  const lat1 = rad(from.lat);
  const lon1 = rad(from.lon);
  const lat2 = Math.asin(Math.sin(lat1) * Math.cos(angular) + Math.cos(lat1) * Math.sin(angular) * Math.cos(theta));
  const lon2 = lon1 + Math.atan2(Math.sin(theta) * Math.sin(angular) * Math.cos(lat1), Math.cos(angular) - Math.sin(lat1) * Math.sin(lat2));
  return { lon: ((deg(lon2) + 540) % 360) - 180, lat: deg(lat2) };
}

/** The compass direction from `from` to `to`, 0–360 degrees clockwise from north. */
export function bearingTo(from: LonLat, to: LonLat): number {
  const lat1 = rad(from.lat);
  const lat2 = rad(to.lat);
  const dLon = rad(to.lon - from.lon);
  const y = Math.sin(dLon) * Math.cos(lat2);
  const x = Math.cos(lat1) * Math.sin(lat2) - Math.sin(lat1) * Math.cos(lat2) * Math.cos(dLon);
  return normaliseDegrees(deg(Math.atan2(y, x)));
}

const POINTS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"] as const;

/** "212° SW": the direction in degrees and as one of eight compass points. */
export function formatBearing(bearing: number): string {
  const value = normaliseDegrees(bearing);
  return `${Math.round(value) % 360}° ${POINTS[Math.round(value / 45) % 8]}`;
}

/** The camera's view as a closed outline: the camera position, an arc at `metres`, and back. */
export function viewOutline(camera: LonLat, heading: number, viewWidth: number, metres: number, steps = 24): [number, number][] {
  const ring: [number, number][] = [[camera.lon, camera.lat]];
  for (let step = 0; step <= steps; step += 1) {
    const point = destination(camera, heading - viewWidth / 2 + (viewWidth * step) / steps, metres);
    ring.push([point.lon, point.lat]);
  }
  ring.push([camera.lon, camera.lat]);
  return ring;
}

/** The class colour as #rrggbb (the map's style language does not read every CSS colour form). */
export function classColourHex(label: string): string {
  const hue = badgeFor(label.toLowerCase()).hue;
  const s = 0.78;
  const l = 0.62;
  const k = (n: number) => (n + hue / 30) % 12;
  const a = s * Math.min(l, 1 - l);
  const channel = (n: number) => Math.round(255 * (l - a * Math.max(-1, Math.min(k(n) - 3, Math.min(9 - k(n), 1)))));
  return `#${[channel(0), channel(8), channel(4)].map((v) => v.toString(16).padStart(2, "0")).join("")}`;
}
