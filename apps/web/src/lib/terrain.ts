/**
 * The geometry behind the 3D terrain view: scene layout, the mesh, the orbit camera and picking.
 *
 * Pure functions, so they can be tested without a browser. Scene units are metres: x east, y north, z up, drawn from
 * the cell centres of the height grid. Heights are the source's own values (converted to metres only by the unit the
 * file declares, or the person stated); the lowest valid cell is z = 0 and nothing is added, filled in or smoothed.
 * No-data cells (NaN) have no surface: no triangle touches them.
 */
import type { TerrainMeta } from "../api/client";

export type Vec3 = [number, number, number];
export type Mat4 = Float32Array;

// --- matrices -----------------------------------------------------------------------------------------------------------

export function perspective(fovY: number, aspect: number, near: number, far: number): Mat4 {
  const f = 1 / Math.tan(fovY / 2);
  const out = new Float32Array(16);
  out[0] = f / aspect;
  out[5] = f;
  out[10] = (far + near) / (near - far);
  out[11] = -1;
  out[14] = (2 * far * near) / (near - far);
  return out;
}

export function lookAt(eye: Vec3, target: Vec3, up: Vec3 = [0, 0, 1]): Mat4 {
  const z = normalise(sub(eye, target));
  const x = normalise(cross(up, z));
  const y = cross(z, x);
  const out = new Float32Array(16);
  out.set([x[0], y[0], z[0], 0, x[1], y[1], z[1], 0, x[2], y[2], z[2], 0, -dot(x, eye), -dot(y, eye), -dot(z, eye), 1]);
  return out;
}

export function multiply(a: Mat4, b: Mat4): Mat4 {
  const out = new Float32Array(16);
  for (let col = 0; col < 4; col++) {
    for (let row = 0; row < 4; row++) {
      let sum = 0;
      for (let k = 0; k < 4; k++) sum += a[k * 4 + row]! * b[col * 4 + k]!;
      out[col * 4 + row] = sum;
    }
  }
  return out;
}

export function invert(m: Mat4): Mat4 | null {
  const a = Array.from(m);
  const inv = new Array<number>(16);
  inv[0] = a[5]! * a[10]! * a[15]! - a[5]! * a[11]! * a[14]! - a[9]! * a[6]! * a[15]! + a[9]! * a[7]! * a[14]! + a[13]! * a[6]! * a[11]! - a[13]! * a[7]! * a[10]!;
  inv[4] = -a[4]! * a[10]! * a[15]! + a[4]! * a[11]! * a[14]! + a[8]! * a[6]! * a[15]! - a[8]! * a[7]! * a[14]! - a[12]! * a[6]! * a[11]! + a[12]! * a[7]! * a[10]!;
  inv[8] = a[4]! * a[9]! * a[15]! - a[4]! * a[11]! * a[13]! - a[8]! * a[5]! * a[15]! + a[8]! * a[7]! * a[13]! + a[12]! * a[5]! * a[11]! - a[12]! * a[7]! * a[9]!;
  inv[12] = -a[4]! * a[9]! * a[14]! + a[4]! * a[10]! * a[13]! + a[8]! * a[5]! * a[14]! - a[8]! * a[6]! * a[13]! - a[12]! * a[5]! * a[10]! + a[12]! * a[6]! * a[9]!;
  inv[1] = -a[1]! * a[10]! * a[15]! + a[1]! * a[11]! * a[14]! + a[9]! * a[2]! * a[15]! - a[9]! * a[3]! * a[14]! - a[13]! * a[2]! * a[11]! + a[13]! * a[3]! * a[10]!;
  inv[5] = a[0]! * a[10]! * a[15]! - a[0]! * a[11]! * a[14]! - a[8]! * a[2]! * a[15]! + a[8]! * a[3]! * a[14]! + a[12]! * a[2]! * a[11]! - a[12]! * a[3]! * a[10]!;
  inv[9] = -a[0]! * a[9]! * a[15]! + a[0]! * a[11]! * a[13]! + a[8]! * a[1]! * a[15]! - a[8]! * a[3]! * a[13]! - a[12]! * a[1]! * a[11]! + a[12]! * a[3]! * a[9]!;
  inv[13] = a[0]! * a[9]! * a[14]! - a[0]! * a[10]! * a[13]! - a[8]! * a[1]! * a[14]! + a[8]! * a[2]! * a[13]! + a[12]! * a[1]! * a[10]! - a[12]! * a[2]! * a[9]!;
  inv[2] = a[1]! * a[6]! * a[15]! - a[1]! * a[7]! * a[14]! - a[5]! * a[2]! * a[15]! + a[5]! * a[3]! * a[14]! + a[13]! * a[2]! * a[7]! - a[13]! * a[3]! * a[6]!;
  inv[6] = -a[0]! * a[6]! * a[15]! + a[0]! * a[7]! * a[14]! + a[4]! * a[2]! * a[15]! - a[4]! * a[3]! * a[14]! - a[12]! * a[2]! * a[7]! + a[12]! * a[3]! * a[6]!;
  inv[10] = a[0]! * a[5]! * a[15]! - a[0]! * a[7]! * a[13]! - a[4]! * a[1]! * a[15]! + a[4]! * a[3]! * a[13]! + a[12]! * a[1]! * a[7]! - a[12]! * a[3]! * a[5]!;
  inv[14] = -a[0]! * a[5]! * a[14]! + a[0]! * a[6]! * a[13]! + a[4]! * a[1]! * a[14]! - a[4]! * a[2]! * a[13]! - a[12]! * a[1]! * a[6]! + a[12]! * a[2]! * a[5]!;
  inv[3] = -a[1]! * a[6]! * a[11]! + a[1]! * a[7]! * a[10]! + a[5]! * a[2]! * a[11]! - a[5]! * a[3]! * a[10]! - a[9]! * a[2]! * a[7]! + a[9]! * a[3]! * a[6]!;
  inv[7] = a[0]! * a[6]! * a[11]! - a[0]! * a[7]! * a[10]! - a[4]! * a[2]! * a[11]! + a[4]! * a[3]! * a[10]! + a[8]! * a[2]! * a[7]! - a[8]! * a[3]! * a[6]!;
  inv[11] = -a[0]! * a[5]! * a[11]! + a[0]! * a[7]! * a[9]! + a[4]! * a[1]! * a[11]! - a[4]! * a[3]! * a[9]! - a[8]! * a[1]! * a[7]! + a[8]! * a[3]! * a[5]!;
  inv[15] = a[0]! * a[5]! * a[10]! - a[0]! * a[6]! * a[9]! - a[4]! * a[1]! * a[10]! + a[4]! * a[2]! * a[9]! + a[8]! * a[1]! * a[6]! - a[8]! * a[2]! * a[5]!;
  const det = a[0]! * inv[0]! + a[1]! * inv[4]! + a[2]! * inv[8]! + a[3]! * inv[12]!;
  if (!Number.isFinite(det) || Math.abs(det) < 1e-300) return null;
  return Float32Array.from(inv.map((v) => v / det));
}

function sub(a: Vec3, b: Vec3): Vec3 {
  return [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
}
function dot(a: Vec3, b: Vec3): number {
  return a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
}
function cross(a: Vec3, b: Vec3): Vec3 {
  return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
}
function normalise(v: Vec3): Vec3 {
  const length = Math.hypot(v[0], v[1], v[2]) || 1;
  return [v[0] / length, v[1] / length, v[2] / length];
}

// --- the scene ----------------------------------------------------------------------------------------------------------

export interface Scene {
  cols: number;
  rows: number;
  /** Metres per cell, east-west and north-south. */
  dx: number;
  dy: number;
  /** Metres per unit of the source's elevation values. */
  toMetre: number;
  /** The lowest valid value, in source units: z = 0 in the scene. */
  base: number;
  min: number;
  max: number;
  values: Float32Array;
  /** Scene width and depth in metres (between the first and last cell centres). */
  width: number;
  depth: number;
  /** Tallest point above z = 0, in metres at exaggeration 1. */
  relief: number;
}

export function sceneOf(values: Float32Array, meta: Pick<TerrainMeta, "grid" | "cell_size_m" | "elevation">): Scene {
  const { width: cols, height: rows } = meta.grid;
  if (values.length !== cols * rows) throw new RangeError(`The grid has ${values.length} values; ${cols} × ${rows} were expected.`);
  const toMetre = meta.elevation.to_metre;
  const [dx, dy] = meta.cell_size_m as [number, number];
  const base = meta.elevation.min;
  return {
    cols,
    rows,
    dx,
    dy,
    toMetre,
    base,
    min: meta.elevation.min,
    max: meta.elevation.max,
    values,
    width: Math.max(0, cols - 1) * dx,
    depth: Math.max(0, rows - 1) * dy,
    relief: (meta.elevation.max - meta.elevation.min) * toMetre,
  };
}

/** The value of one cell, or null for a no-data cell or a cell outside the grid. */
export function valueAt(scene: Scene, col: number, row: number): number | null {
  if (col < 0 || row < 0 || col >= scene.cols || row >= scene.rows) return null;
  const v = scene.values[row * scene.cols + col]!;
  return Number.isNaN(v) ? null : v;
}

/** Scene position (metres) of a cell centre: x east from the first column, y north from the last row. */
export function cellPosition(scene: Scene, col: number, row: number): { x: number; y: number } {
  return { x: col * scene.dx, y: (scene.rows - 1 - row) * scene.dy };
}

/** Which cell a scene position falls in (nearest cell centre), or null outside the grid. */
export function cellAt(scene: Scene, x: number, y: number): { col: number; row: number } | null {
  const col = Math.round(x / scene.dx);
  const row = scene.rows - 1 - Math.round(y / scene.dy);
  return col >= 0 && row >= 0 && col < scene.cols && row < scene.rows ? { col, row } : null;
}

export interface Mesh {
  /** x, y, z (metres at exaggeration 1) per vertex. */
  positions: Float32Array;
  /** dz/dx, dz/dy per vertex (unitless); the shader scales them by the exaggeration. */
  slopes: Float32Array;
  /** The source value per vertex, for colouring. */
  heights: Float32Array;
  indices: Uint32Array;
  triangles: number;
  skippedCells: number;
}

/** The surface as triangles. A quad is drawn only when all four of its corner cells have data. */
export function buildMesh(scene: Scene): Mesh {
  const { cols, rows, dx, dy, toMetre, base, values } = scene;
  const count = cols * rows;
  const positions = new Float32Array(count * 3);
  const slopes = new Float32Array(count * 2);
  const heights = new Float32Array(count);
  const z = (c: number, r: number) => {
    const v = values[r * cols + c]!;
    return Number.isNaN(v) ? Number.NaN : (v - base) * toMetre;
  };
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      const i = r * cols + c;
      const here = z(c, r);
      const { x, y } = cellPosition(scene, c, r);
      positions.set([x, y, Number.isNaN(here) ? 0 : here], i * 3);
      heights[i] = values[i]!;
      // Central differences over valid neighbours, one-sided at an edge or next to a hole; 0 when none is usable.
      const slope = (a: number, b: number, span: number) => {
        if (Number.isNaN(a) && Number.isNaN(b)) return 0;
        if (Number.isNaN(a)) return (b - here) / span;
        if (Number.isNaN(b)) return (here - a) / span;
        return (b - a) / (2 * span);
      };
      const left = c > 0 ? z(c - 1, r) : Number.NaN;
      const right = c < cols - 1 ? z(c + 1, r) : Number.NaN;
      const north = r > 0 ? z(c, r - 1) : Number.NaN;
      const south = r < rows - 1 ? z(c, r + 1) : Number.NaN;
      // east is +x; north is +y (the row above)
      slopes[i * 2] = Number.isNaN(here) ? 0 : slope(left, right, dx);
      slopes[i * 2 + 1] = Number.isNaN(here) ? 0 : slope(south, north, dy);
    }
  }
  const indices: number[] = [];
  let skipped = 0;
  for (let r = 0; r < rows - 1; r++) {
    for (let c = 0; c < cols - 1; c++) {
      const nw = r * cols + c;
      const ne = nw + 1;
      const sw = nw + cols;
      const se = sw + 1;
      if ([nw, ne, sw, se].some((i) => Number.isNaN(values[i]!))) {
        skipped++;
        continue;
      }
      indices.push(nw, sw, se, nw, se, ne);
    }
  }
  return { positions, slopes, heights, indices: Uint32Array.from(indices), triangles: indices.length / 3, skippedCells: skipped };
}

// --- the orbit camera ---------------------------------------------------------------------------------------------------

export interface Camera {
  /** Compass bearing the camera looks from, in radians: 0 = from the south looking north. */
  yaw: number;
  /** Angle of the camera above the horizon, radians. */
  pitch: number;
  distance: number;
  target: Vec3;
}

export const FOV = (45 * Math.PI) / 180;
export const MIN_PITCH = (5 * Math.PI) / 180;
export const MAX_PITCH = (89 * Math.PI) / 180;

export type Preset = "oblique" | "top" | "north";

function span(scene: Scene): number {
  return Math.max(scene.width, scene.depth, scene.relief, 1);
}

export function cameraFor(scene: Scene, preset: Preset = "oblique"): Camera {
  const target: Vec3 = [scene.width / 2, scene.depth / 2, scene.relief / 2];
  const distance = span(scene) * 1.6;
  if (preset === "top") return { yaw: 0, pitch: MAX_PITCH, distance: distance * 1.1, target };
  if (preset === "north") return { yaw: Math.PI, pitch: (30 * Math.PI) / 180, distance, target };
  return { yaw: (-20 * Math.PI) / 180, pitch: (40 * Math.PI) / 180, distance, target };
}

export function distanceLimits(scene: Scene): [number, number] {
  return [span(scene) * 0.05, span(scene) * 8];
}

export function eyeOf(camera: Camera): Vec3 {
  const { yaw, pitch, distance, target } = camera;
  return [
    target[0] + distance * Math.sin(yaw) * Math.cos(pitch),
    target[1] - distance * Math.cos(yaw) * Math.cos(pitch),
    target[2] + distance * Math.sin(pitch),
  ];
}

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));

/** Drag by (dx, dy) pixels: dragging right turns the scene to the right, dragging down raises the camera. */
export function orbit(camera: Camera, dx: number, dy: number): Camera {
  return { ...camera, yaw: camera.yaw - dx * 0.006, pitch: clamp(camera.pitch + dy * 0.006, MIN_PITCH, MAX_PITCH) };
}

export function zoom(camera: Camera, amount: number, scene: Scene): Camera {
  const [lo, hi] = distanceLimits(scene);
  return { ...camera, distance: clamp(camera.distance * Math.exp(amount), lo, hi) };
}

/** Drag the scene by (dx, dy) pixels along the ground. The target never leaves the extent (with a margin). */
export function pan(camera: Camera, dx: number, dy: number, viewportHeight: number, scene: Scene): Camera {
  const perPixel = (2 * camera.distance * Math.tan(FOV / 2)) / Math.max(1, viewportHeight);
  const right: [number, number] = [Math.cos(camera.yaw), Math.sin(camera.yaw)];
  const forward: [number, number] = [-Math.sin(camera.yaw), Math.cos(camera.yaw)];
  const lift = 1 / Math.max(0.2, Math.sin(camera.pitch));
  const x = camera.target[0] - right[0] * dx * perPixel - forward[0] * dy * perPixel * lift;
  const y = camera.target[1] - right[1] * dx * perPixel - forward[1] * dy * perPixel * lift;
  const margin = span(scene) * 0.25;
  return {
    ...camera,
    target: [clamp(x, -margin, scene.width + margin), clamp(y, -margin, scene.depth + margin), camera.target[2]],
  };
}

export function viewProjection(camera: Camera, aspect: number, scene: Scene): { view: Mat4; projection: Mat4; viewProjection: Mat4 } {
  const view = lookAt(eyeOf(camera), camera.target);
  const far = camera.distance + span(scene) * 4;
  const projection = perspective(FOV, aspect, Math.max(0.1, camera.distance * 0.01), far);
  return { view, projection, viewProjection: multiply(projection, view) };
}

// --- picking ------------------------------------------------------------------------------------------------------------

export interface Hit {
  col: number;
  row: number;
  value: number;
  x: number;
  y: number;
  z: number;
}

/** The ray through a pixel of a viewport, in scene space. */
export function rayThrough(camera: Camera, scene: Scene, px: number, py: number, width: number, height: number): { origin: Vec3; direction: Vec3 } | null {
  const { viewProjection: vp } = viewProjection(camera, width / height, scene);
  const inverse = invert(vp);
  if (!inverse) return null;
  const ndcX = (px / width) * 2 - 1;
  const ndcY = 1 - (py / height) * 2;
  const unproject = (z: number): Vec3 => {
    const v = [ndcX, ndcY, z, 1];
    const out = [0, 0, 0, 0];
    for (let row = 0; row < 4; row++) for (let k = 0; k < 4; k++) out[row]! += inverse[k * 4 + row]! * v[k]!;
    const w = out[3]! || 1;
    return [out[0]! / w, out[1]! / w, out[2]! / w];
  };
  const near = unproject(-1);
  const far = unproject(1);
  const direction = normalise(sub(far, near));
  return { origin: eyeOf(camera), direction };
}

/** The first cell with data that the ray reaches, drawn at the given vertical exaggeration; null if it misses the surface. */
export function pick(scene: Scene, origin: Vec3, direction: Vec3, exaggeration: number): Hit | null {
  const height = (x: number, y: number): number => {
    const cell = cellAt(scene, x, y);
    if (!cell) return Number.NaN;
    const v = valueAt(scene, cell.col, cell.row);
    return v === null ? Number.NaN : (v - scene.base) * scene.toMetre * exaggeration;
  };
  const halfX = scene.dx / 2;
  const halfY = scene.dy / 2;
  // Clip the ray to the extent's box so the march is short and bounded.
  // The box reaches a little above and below the surface, so a flat surface (no relief) is still found.
  const margin = Math.max(1, span(scene) * 0.01);
  const lo: Vec3 = [-halfX, -halfY, -margin];
  const hi: Vec3 = [scene.width + halfX, scene.depth + halfY, scene.relief * exaggeration + margin];
  let tMin = 0;
  let tMax = Number.POSITIVE_INFINITY;
  for (let axis = 0; axis < 3; axis++) {
    const o = origin[axis]!;
    const d = direction[axis]!;
    if (Math.abs(d) < 1e-12) {
      if (o < lo[axis]! || o > hi[axis]!) return null;
      continue;
    }
    const t1 = (lo[axis]! - o) / d;
    const t2 = (hi[axis]! - o) / d;
    tMin = Math.max(tMin, Math.min(t1, t2));
    tMax = Math.min(tMax, Math.max(t1, t2));
  }
  if (!(tMax > tMin)) return null;
  const step = Math.min(scene.dx, scene.dy) / 2;
  const limit = Math.min(tMax, tMin + (Math.hypot(scene.width, scene.depth, scene.relief * exaggeration) + step) * 2);
  let previous = tMin;
  let above = false;
  for (let t = tMin; t <= limit + step; t += step) {
    const tt = Math.min(t, limit);
    const p: Vec3 = [origin[0] + direction[0] * tt, origin[1] + direction[1] * tt, origin[2] + direction[2] * tt];
    const surface = height(p[0], p[1]);
    if (Number.isNaN(surface)) {
      above = false; // a hole or the outside: nothing to hit here, and the next surface must be entered from above
      previous = tt;
      if (tt >= limit) break;
      continue;
    }
    if (p[2] >= surface) {
      above = true;
      previous = tt;
    } else if (above) {
      // The ray went from above the surface to below it between `previous` and `tt`: refine.
      let a = previous;
      let b = tt;
      for (let i = 0; i < 10; i++) {
        const m = (a + b) / 2;
        const q: Vec3 = [origin[0] + direction[0] * m, origin[1] + direction[1] * m, origin[2] + direction[2] * m];
        const s = height(q[0], q[1]);
        if (!Number.isNaN(s) && q[2] < s) b = m;
        else a = m;
      }
      const hit: Vec3 = [origin[0] + direction[0] * b, origin[1] + direction[1] * b, origin[2] + direction[2] * b];
      const cell = cellAt(scene, hit[0], hit[1]);
      const value = cell ? valueAt(scene, cell.col, cell.row) : null;
      if (cell && value !== null) return { ...cell, value, x: hit[0], y: hit[1], z: hit[2] };
    }
    if (tt >= limit) break;
  }
  return null;
}

// --- colour and words ---------------------------------------------------------------------------------------------------

/** A sequential ramp (dark blue to yellow, readable in greyscale and for common colour-vision differences). */
export const RAMP: [number, number, number][] = [
  [0.267, 0.005, 0.329],
  [0.231, 0.322, 0.545],
  [0.128, 0.567, 0.551],
  [0.369, 0.789, 0.383],
  [0.993, 0.906, 0.144],
];

export function rampColour(t: number): [number, number, number] {
  const x = clamp(t, 0, 1) * (RAMP.length - 1);
  const i = Math.min(RAMP.length - 2, Math.floor(x));
  const f = x - i;
  const a = RAMP[i]!;
  const b = RAMP[i + 1]!;
  return [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f];
}

export function formatElevation(value: number, unitLabel: string): string {
  return `${value.toLocaleString("en", { maximumFractionDigits: Math.abs(value) < 100 ? 2 : 1 })} ${unitLabel}`;
}

export function formatMetres(metres: number): string {
  return metres >= 1000 ? `${(metres / 1000).toLocaleString("en", { maximumFractionDigits: 2 })} km` : `${metres.toLocaleString("en", { maximumFractionDigits: 1 })} m`;
}

/** Native coordinates of a cell centre, for display. */
export function nativePosition(meta: Pick<TerrainMeta, "extent_native" | "cell_size_native">, col: number, row: number): { x: number; y: number } {
  const [dx, dy] = meta.cell_size_native as [number, number];
  return { x: meta.extent_native.x_min + (col + 0.5) * dx, y: meta.extent_native.y_max - (row + 0.5) * dy };
}
