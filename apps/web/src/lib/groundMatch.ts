/**
 * From a photo to the ground: a person matches spots on flat ground in the photo with the same spots on the satellite map,
 * and the photo-to-ground mapping (a plane-to-plane homography) is fitted to those pairs. Any point on that ground in the
 * photo (where an object touches the road) can then be placed on the map.
 *
 * Assumptions, said wherever the result is shown: the ground is flat between the matched spots; an object's position is
 * where the bottom of its box touches the ground; the result is only as good as the person's matches and the satellite
 * picture's own alignment. Nothing here is stored; positions are computed when shown.
 */
import type { LonLat } from "./sightLines";

const EARTH_RADIUS_M = 6_371_008.8;
const rad = (degrees: number) => (degrees * Math.PI) / 180;

/** One matched spot: a pixel in the photo (in the detection boxes' grid) and the same spot on the map. */
export interface GroundPair {
  u: number;
  v: number;
  lon: number;
  lat: number;
}

export interface Local {
  e: number;
  n: number;
}

/** East/north metres from `origin` (a flat approximation, accurate to well under a metre over a few kilometres). */
export function toLocal(origin: LonLat, point: LonLat): Local {
  return {
    e: rad(point.lon - origin.lon) * Math.cos(rad(origin.lat)) * EARTH_RADIUS_M,
    n: rad(point.lat - origin.lat) * EARTH_RADIUS_M,
  };
}

export function fromLocal(origin: LonLat, local: Local): LonLat {
  return {
    lon: origin.lon + (local.e / (EARTH_RADIUS_M * Math.cos(rad(origin.lat)))) * (180 / Math.PI),
    lat: origin.lat + (local.n / EARTH_RADIUS_M) * (180 / Math.PI),
  };
}

type Matrix3 = [number, number, number, number, number, number, number, number, number];
type Point = [number, number];

/** Moves a point set to its centroid and scales it to an average distance of √2 (Hartley), for a well-conditioned fit. */
function normaliser(points: Point[]): Matrix3 {
  const cx = points.reduce((sum, p) => sum + p[0], 0) / points.length;
  const cy = points.reduce((sum, p) => sum + p[1], 0) / points.length;
  const spread = points.reduce((sum, p) => sum + Math.hypot(p[0] - cx, p[1] - cy), 0) / points.length;
  const s = spread > 0 ? Math.SQRT2 / spread : 1;
  return [s, 0, -s * cx, 0, s, -s * cy, 0, 0, 1];
}

function multiply(a: Matrix3, b: Matrix3): Matrix3 {
  const out = new Array<number>(9) as Matrix3;
  for (let r = 0; r < 3; r += 1) {
    for (let c = 0; c < 3; c += 1) out[r * 3 + c] = a[r * 3]! * b[c]! + a[r * 3 + 1]! * b[3 + c]! + a[r * 3 + 2]! * b[6 + c]!;
  }
  return out;
}

function invertNormaliser(t: Matrix3): Matrix3 {
  const s = t[0];
  return [1 / s, 0, -t[2] / s, 0, 1 / s, -t[5] / s, 0, 0, 1];
}

function apply(h: Matrix3, x: number, y: number): [number, number, number] {
  return [h[0] * x + h[1] * y + h[2], h[3] * x + h[4] * y + h[5], h[6] * x + h[7] * y + h[8]];
}

/** Solves the square system `a·x = b` by Gaussian elimination with partial pivoting; null when it is (nearly) singular. */
function solve(a: number[][], b: number[]): number[] | null {
  const n = b.length;
  const m = a.map((row, i) => [...row, b[i]!]);
  const scale = Math.max(...m.map((row) => Math.max(...row.slice(0, n).map(Math.abs))), 1e-300);
  for (let col = 0; col < n; col += 1) {
    let pivot = col;
    for (let row = col + 1; row < n; row += 1) if (Math.abs(m[row]![col]!) > Math.abs(m[pivot]![col]!)) pivot = row;
    if (Math.abs(m[pivot]![col]!) < scale * 1e-12) return null;
    [m[col], m[pivot]] = [m[pivot]!, m[col]!];
    for (let row = col + 1; row < n; row += 1) {
      const factor = m[row]![col]! / m[col]![col]!;
      for (let k = col; k <= n; k += 1) m[row]![k]! -= factor * m[col]![k]!;
    }
  }
  const x = new Array<number>(n).fill(0);
  for (let row = n - 1; row >= 0; row -= 1) {
    let sum = m[row]![n]!;
    for (let k = row + 1; k < n; k += 1) sum -= m[row]![k]! * x[k]!;
    x[row] = sum / m[row]![row]!;
  }
  return x;
}

/** How close a point set is to a single line: smallest over largest spread (0 = all on one line). */
function flatness(points: Point[]): number {
  const cx = points.reduce((sum, p) => sum + p[0], 0) / points.length;
  const cy = points.reduce((sum, p) => sum + p[1], 0) / points.length;
  let xx = 0;
  let yy = 0;
  let xy = 0;
  for (const [x, y] of points) {
    xx += (x - cx) ** 2;
    yy += (y - cy) ** 2;
    xy += (x - cx) * (y - cy);
  }
  const mean = (xx + yy) / 2;
  const root = Math.sqrt(((xx - yy) / 2) ** 2 + xy ** 2);
  const largest = mean + root;
  return largest > 0 ? Math.max(0, mean - root) / largest : 0;
}

function threeOnALine(points: Point[]): boolean {
  for (let i = 0; i < points.length; i += 1) {
    for (let j = i + 1; j < points.length; j += 1) {
      for (let k = j + 1; k < points.length; k += 1) {
        const [a, b, c] = [points[i]!, points[j]!, points[k]!];
        const area = Math.abs((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]));
        const longest = Math.max(Math.hypot(b[0] - a[0], b[1] - a[1]), Math.hypot(c[0] - a[0], c[1] - a[1]), Math.hypot(c[0] - b[0], c[1] - b[1]));
        if (longest === 0 || area / longest ** 2 < 0.01) return true;
      }
    }
  }
  return false;
}

/** The photo-to-ground mapping from pixels to local metres, or why it cannot be fitted. */
function fitHomography(photo: Point[], ground: Point[]): { ok: true; h: Matrix3; sign: number } | { ok: false; reason: string } {
  if (photo.length < 4) return { ok: false, reason: `Match at least 4 spots (${photo.length} so far).` };
  if (photo.length === 4 && (threeOnALine(photo) || threeOnALine(ground))) {
    return { ok: false, reason: "Three of the four spots are on one line. Choose spots spread out across the ground, not along one road edge." };
  }
  if (flatness(photo) < 1e-3 || flatness(ground) < 1e-3) {
    return { ok: false, reason: "All the spots are on one line. Add spots to the left and right, and near and far." };
  }
  const tp = normaliser(photo);
  const tg = normaliser(ground);
  const ata = Array.from({ length: 8 }, () => new Array<number>(8).fill(0));
  const atb = new Array<number>(8).fill(0);
  const addRow = (row: number[], value: number) => {
    for (let i = 0; i < 8; i += 1) {
      atb[i]! += row[i]! * value;
      for (let j = 0; j < 8; j += 1) ata[i]![j]! += row[i]! * row[j]!;
    }
  };
  photo.forEach((p, i) => {
    const [x, y] = apply(tp, p[0], p[1]);
    const [gx, gy] = apply(tg, ground[i]![0], ground[i]![1]);
    addRow([x, y, 1, 0, 0, 0, -x * gx, -y * gx], gx);
    addRow([0, 0, 0, x, y, 1, -x * gy, -y * gy], gy);
  });
  const solution = solve(ata, atb);
  if (!solution || solution.some((value) => !Number.isFinite(value))) {
    return { ok: false, reason: "These spots do not define a mapping. Choose spots spread out across the ground." };
  }
  const hn = [...solution, 1] as Matrix3;
  const h = multiply(invertNormaliser(tg), multiply(hn, tp));
  // Every matched spot must be on the same side of the photo's horizon, or some pairs do not belong together.
  const signs = photo.map((p) => Math.sign(apply(h, p[0], p[1])[2]));
  if (signs.some((s) => s === 0 || s !== signs[0])) {
    return { ok: false, reason: "Some pairs do not fit together: check that each photo spot was matched with the same spot on the map." };
  }
  // The mapping must not fold the picture over on itself between the matched spots.
  const jacobianSign = (p: Point) => {
    const d = 1e-3;
    const at = (x: number, y: number) => {
      const [a, b, w] = apply(h, x, y);
      return [a / w, b / w] as Point;
    };
    const o = at(p[0], p[1]);
    const dx = at(p[0] + d, p[1]);
    const dy = at(p[0], p[1] + d);
    return Math.sign((dx[0] - o[0]) * (dy[1] - o[1]) - (dx[1] - o[1]) * (dy[0] - o[0]));
  };
  const folds = photo.map(jacobianSign);
  if (folds.some((s) => s === 0 || s !== folds[0])) {
    return { ok: false, reason: "Some pairs do not fit together (the photo would be folded over). Check the matches, or remove the last one." };
  }
  return { ok: true, h, sign: signs[0]! };
}

function convexHull(points: Point[]): Point[] {
  const sorted = [...points].sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  if (sorted.length < 3) return sorted;
  const cross = (o: Point, a: Point, b: Point) => (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
  const lower: Point[] = [];
  for (const p of sorted) {
    while (lower.length >= 2 && cross(lower[lower.length - 2]!, lower[lower.length - 1]!, p) <= 0) lower.pop();
    lower.push(p);
  }
  const upper: Point[] = [];
  for (const p of [...sorted].reverse()) {
    while (upper.length >= 2 && cross(upper[upper.length - 2]!, upper[upper.length - 1]!, p) <= 0) upper.pop();
    upper.push(p);
  }
  return [...lower.slice(0, -1), ...upper.slice(0, -1)];
}

function insideHull(hull: Point[], p: Point): boolean {
  if (hull.length < 3) return false;
  for (let i = 0; i < hull.length; i += 1) {
    const a = hull[i]!;
    const b = hull[(i + 1) % hull.length]!;
    if ((b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0]) < 0) return false;
  }
  return true;
}

export type Placement =
  | { ok: true; lon: number; lat: number; /** Outside the matched area: less reliable. */ extrapolated: boolean; distanceM: number }
  | { ok: false; reason: string };

export type GroundFit =
  | {
      ok: true;
      pairs: number;
      /** Distance in metres between each matched map spot and where the fit puts its photo spot. */
      residuals: number[];
      rmsM: number;
      /** Leave-one-out error in metres (each spot predicted from the others); null below 5 spots. */
      checkM: number | null;
      project: (u: number, v: number) => Placement;
    }
  | { ok: false; pairs: number; reason: string };

/** Beyond this many times the farthest matched spot, a placement is called extrapolated. */
const FAR_FACTOR = 1.5;

/** Fits the photo-to-ground mapping for one photo taken at `camera`, and gives a way to place any photo pixel. */
export function fitGround(camera: LonLat, pairs: GroundPair[]): GroundFit {
  const photo: Point[] = pairs.map((p) => [p.u, p.v]);
  const ground: Point[] = pairs.map((p) => {
    const local = toLocal(camera, p);
    return [local.e, local.n];
  });
  const fitted = fitHomography(photo, ground);
  if (!fitted.ok) return { ok: false, pairs: pairs.length, reason: fitted.reason };
  const { h, sign } = fitted;
  const hull = convexHull(photo);
  const farthest = Math.max(...ground.map(([e, n]) => Math.hypot(e, n)));

  const toGround = (hm: Matrix3, s: number, u: number, v: number): Point | null => {
    const [x, y, w] = apply(hm, u, v);
    if (Math.sign(w) !== s || Math.abs(w) < 1e-12) return null;
    return [x / w, y / w];
  };
  const residuals = photo.map((p, i) => {
    const at = toGround(h, sign, p[0], p[1]);
    return at ? Math.hypot(at[0] - ground[i]![0], at[1] - ground[i]![1]) : Number.POSITIVE_INFINITY;
  });
  const rmsM = Math.sqrt(residuals.reduce((sum, r) => sum + r * r, 0) / residuals.length);

  let checkM: number | null = null;
  if (pairs.length >= 5) {
    const errors: number[] = [];
    for (let i = 0; i < pairs.length; i += 1) {
      const without = fitHomography(photo.filter((_, j) => j !== i), ground.filter((_, j) => j !== i));
      if (!without.ok) continue;
      const at = toGround(without.h, without.sign, photo[i]![0], photo[i]![1]);
      if (at) errors.push(Math.hypot(at[0] - ground[i]![0], at[1] - ground[i]![1]));
    }
    if (errors.length > 0) checkM = Math.sqrt(errors.reduce((sum, e) => sum + e * e, 0) / errors.length);
  }

  return {
    ok: true,
    pairs: pairs.length,
    residuals,
    rmsM,
    checkM,
    project: (u, v) => {
      const at = toGround(h, sign, u, v);
      if (!at) return { ok: false, reason: "At or above the horizon in the photo: not on the matched ground." };
      const distanceM = Math.hypot(at[0], at[1]);
      const where = fromLocal(camera, { e: at[0], n: at[1] });
      return { ok: true, ...where, distanceM, extrapolated: distanceM > FAR_FACTOR * farthest || !insideHull(hull, [u, v]) };
    },
  };
}

/** Where a detected object touches the ground in the photo: the middle of the bottom edge of its box. */
export function footPoint(region: { type: string; coordinates: unknown }): { u: number; v: number } | null {
  if (region.type === "bbox_xyxy") {
    const [x0, y0, x1, y1] = region.coordinates as number[];
    if (x0 === undefined || y0 === undefined || x1 === undefined || y1 === undefined) return null;
    return { u: (x0 + x1) / 2, v: Math.max(y0, y1) };
  }
  const points = region.coordinates as number[][];
  if (!Array.isArray(points) || points.length === 0) return null;
  const xs = points.map((p) => p[0]).filter((x): x is number => typeof x === "number");
  const ys = points.map((p) => p[1]).filter((y): y is number => typeof y === "number");
  if (xs.length === 0 || ys.length === 0) return null;
  return { u: (Math.min(...xs) + Math.max(...xs)) / 2, v: Math.max(...ys) };
}
