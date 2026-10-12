export type AoiMode = "point" | "bbox";

export interface AoiFields {
  x: string;
  y: string;
  minX: string;
  minY: string;
  maxX: string;
  maxY: string;
}

export const emptyAoiFields: AoiFields = { x: "", y: "", minX: "", minY: "", maxX: "", maxY: "" };

type GeoJsonGeometry = { type: string; coordinates: unknown };
export type AoiResult = { ok: true; geometry: GeoJsonGeometry } | { ok: false; error: string };

function parse(value: string): number | null {
  if (value.trim() === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

/** Builds GeoJSON in x, y order (easting or longitude first), exactly as typed. No coordinate is adjusted. */
export function buildAoiGeometry(mode: AoiMode, fields: AoiFields): AoiResult {
  if (mode === "point") {
    const x = parse(fields.x);
    const y = parse(fields.y);
    if (x === null || y === null) return { ok: false, error: "Enter numeric X and Y coordinates." };
    return { ok: true, geometry: { type: "Point", coordinates: [x, y] } };
  }
  const minX = parse(fields.minX);
  const minY = parse(fields.minY);
  const maxX = parse(fields.maxX);
  const maxY = parse(fields.maxY);
  if (minX === null || minY === null || maxX === null || maxY === null) {
    return { ok: false, error: "Enter all four numeric bounding-box values." };
  }
  if (minX >= maxX || minY >= maxY) {
    return { ok: false, error: "Minimum values must be smaller than maximum values." };
  }
  return {
    ok: true,
    geometry: {
      type: "Polygon",
      coordinates: [
        [
          [minX, minY],
          [maxX, minY],
          [maxX, maxY],
          [minX, maxY],
          [minX, minY],
        ],
      ],
    },
  };
}

export function parseTags(text: string): string[] {
  return [...new Set(text.split(",").map((tag) => tag.trim()).filter(Boolean))];
}
