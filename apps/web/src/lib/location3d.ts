/**
 * Where the online 3D view starts. Taken, in this order, from: the project's area of interest, the photographs whose
 * position a person confirmed, then the extent of the project's placed rasters. Nothing is guessed: with none of these the
 * person chooses a place, and that choice only moves the camera.
 */
export type Bounds4326 = [number, number, number, number]; // west, south, east, north

export interface StartView {
  /** What the view is centred on, said on screen. */
  source: "area of interest" | "photographs" | "rasters";
  label: string;
  bounds: Bounds4326;
}

export interface PhotoPoint {
  filename: string;
  state: string;
  latitude: number | null;
  longitude: number | null;
}

type Geometry = { type?: unknown; coordinates?: unknown } | null | undefined;

function pointsOf(geometry: Geometry): [number, number][] {
  if (!geometry || typeof geometry !== "object") return [];
  const out: [number, number][] = [];
  const walk = (value: unknown) => {
    if (Array.isArray(value) && value.length >= 2 && typeof value[0] === "number" && typeof value[1] === "number") {
      out.push([value[0], value[1]]);
    } else if (Array.isArray(value)) {
      for (const item of value) walk(item);
    }
  };
  walk(geometry.coordinates);
  return out.filter(([lon, lat]) => Number.isFinite(lon) && Number.isFinite(lat) && Math.abs(lat) <= 90 && Math.abs(lon) <= 180);
}

function boundsOf(points: [number, number][]): Bounds4326 | null {
  if (points.length === 0) return null;
  const lons = points.map((p) => p[0]);
  const lats = points.map((p) => p[1]);
  return [Math.min(...lons), Math.min(...lats), Math.max(...lons), Math.max(...lats)];
}

export function startView(input: {
  aoi?: Geometry;
  photos?: PhotoPoint[];
  rasterBounds?: (number[] | null | undefined)[];
}): StartView | null {
  const aoi = boundsOf(pointsOf(input.aoi));
  if (aoi) return { source: "area of interest", label: "the project's area of interest", bounds: aoi };

  const located = (input.photos ?? []).filter(
    (p) => p.state === "confirmed" && p.latitude !== null && p.longitude !== null,
  ) as (PhotoPoint & { latitude: number; longitude: number })[];
  const photos = boundsOf(located.map((p) => [p.longitude, p.latitude]));
  if (photos) {
    const label = located.length === 1 ? `the photograph ${located[0]!.filename}` : `${located.length} located photographs`;
    return { source: "photographs", label, bounds: photos };
  }

  const corners: [number, number][] = [];
  for (const b of input.rasterBounds ?? []) {
    if (b && b.length === 4 && b.every((v) => Number.isFinite(v))) {
      corners.push([b[0]!, b[1]!], [b[2]!, b[3]!]);
    }
  }
  const rasters = boundsOf(corners);
  if (rasters) return { source: "rasters", label: "the project's placed rasters", bounds: rasters };
  return null;
}

/** True when the bounds are a single point (or nearly), so the camera should centre on it rather than fit a box. */
export function isPoint(bounds: Bounds4326): boolean {
  return Math.abs(bounds[2] - bounds[0]) < 1e-6 && Math.abs(bounds[3] - bounds[1]) < 1e-6;
}
