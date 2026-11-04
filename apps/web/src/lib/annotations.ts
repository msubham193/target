/**
 * Map annotations: marks a person draws (points, lines, polygons) with a note.
 *
 * They are the person's own marks. They are not findings, detections or photograph locations, and nothing here refers
 * to a photograph or an observation. Lengths and areas come from the API (geodesic, WGS 84); these helpers only
 * format them and shape the drawing in progress.
 */

export type AnnotationKind = "point" | "line" | "polygon";
export type Position = [number, number]; // [longitude, latitude]

export const KIND_LABEL: Record<AnnotationKind, string> = { point: "Point", line: "Line", polygon: "Polygon" };
export const MIN_POINTS: Record<AnnotationKind, number> = { point: 1, line: 2, polygon: 3 };
export const MAX_NOTE = 2000;

/** The legend text. The wording keeps these marks apart from model output and from photograph markers. */
export const LEGEND = "Your annotations (not findings)";

export interface GeoJsonGeometry {
  type: string;
  coordinates: unknown;
}

/** A shape being drawn: vertices in the order clicked. Nothing is saved until the person saves it. */
export interface Drawing {
  kind: AnnotationKind;
  points: Position[];
  /** The annotation being edited, or null for a new one. */
  editingId: string | null;
  /** The version the edit started from; a change saved on top of a newer version is refused. */
  baseVersion: number | null;
  /** Whether map clicks add vertices. An edit keeps its shape until the person chooses to redraw it. */
  active: boolean;
}

/** The GeoJSON geometry for the vertices, or null when there are too few. A polygon's outline is closed here. */
export function draftGeometry(kind: AnnotationKind, points: Position[]): GeoJsonGeometry | null {
  if (points.length < MIN_POINTS[kind]) return null;
  if (kind === "point") return { type: "Point", coordinates: [...points[0]!] };
  if (kind === "line") return { type: "LineString", coordinates: points.map((p) => [...p]) };
  return { type: "Polygon", coordinates: [[...points.map((p) => [...p]), [...points[0]!]]] };
}

/** What the person still has to do, in words. */
export function draftHint(kind: AnnotationKind, count: number): string {
  const need = MIN_POINTS[kind];
  if (kind === "point") return count === 0 ? "Click the map to place the point." : "Click again to move the point.";
  if (count === 0) return `Click the map to add the first of at least ${need} positions.`;
  if (count < need) return `${count} position${count === 1 ? "" : "s"} placed; add ${need - count} more.`;
  return `${count} positions placed. Add more, or save.`;
}

/** The vertices of a stored geometry in drawing order (a polygon's closing position is not repeated). */
export function pointsOf(geometry: GeoJsonGeometry): Position[] {
  const raw = geometry.coordinates as unknown;
  if (geometry.type === "Point") return [raw as Position];
  if (geometry.type === "LineString") return raw as Position[];
  if (geometry.type === "Polygon") return (raw as Position[][])[0]!.slice(0, -1);
  return [];
}

/** [west, south, east, north] of a geometry, padded for a lone point so the map has an area to fit. */
export function boundsOf(geometry: GeoJsonGeometry): [number, number, number, number] {
  const points = geometry.type === "Polygon" ? ((geometry.coordinates as Position[][])[0] ?? []) : pointsOf(geometry);
  const lons = points.map((p) => p[0]);
  const lats = points.map((p) => p[1]);
  const [west, east, south, north] = [Math.min(...lons), Math.max(...lons), Math.min(...lats), Math.max(...lats)];
  if (west === east && south === north) return [west - 0.002, south - 0.002, east + 0.002, north + 0.002];
  return [west, south, east, north];
}

export function samePoints(a: Position[], b: Position[]): boolean {
  return a.length === b.length && a.every((p, i) => p[0] === b[i]![0] && p[1] === b[i]![1]);
}

const number = (value: number, digits: number) =>
  value.toLocaleString("en", { minimumFractionDigits: digits, maximumFractionDigits: digits });

/** "992 m" or "1.24 km". Always with its unit. */
export function formatLength(metres: number): string {
  if (metres < 10) return `${number(metres, 1)} m`;
  if (metres < 1000) return `${number(Math.round(metres), 0)} m`;
  return `${number(metres / 1000, 2)} km`;
}

/** "10,980 m²", "1.10 ha (10,980 m²)" or "2.34 km²". Always with its unit. */
export function formatArea(squareMetres: number): string {
  if (squareMetres < 10_000) return `${number(Math.round(squareMetres), 0)} m²`;
  if (squareMetres < 1_000_000) return `${number(squareMetres / 10_000, 2)} ha (${number(Math.round(squareMetres), 0)} m²)`;
  return `${number(squareMetres / 1_000_000, 2)} km²`;
}

/** "27.00000°, 75.00000°" for display. */
export function formatPosition([lon, lat]: Position): string {
  return `${lat.toFixed(6)}°, ${lon.toFixed(6)}°`;
}
