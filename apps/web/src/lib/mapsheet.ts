import type { MapSeries, MapSheetAssessment, MapSheetInfo } from "../api/client";
import type { Tone } from "../components/ui";

export const SERIES_LABELS: Record<MapSeries, string> = {
  osm: "Open Series Map (OSM)",
  esm: "Everest Series Map (ESM)",
  unspecified: "Series not stated",
};

export const SERIES_SHORT: Record<MapSeries, string> = { osm: "OSM", esm: "ESM", unspecified: "Series not stated" };

export const INTAKE_LABELS: Record<MapSheetInfo["intake"], string> = {
  georeferenced_raster: "Georeferenced GeoTIFF",
  scanned_raster: "Scanned TIFF (no georeferencing)",
  scanned_image: "Scanned picture (PNG / JPEG)",
  pdf_document: "PDF document",
};

export const FACT_STATE_LABELS: Record<MapSheetAssessment["facts"][number]["state"], string> = {
  stated_by_file: "Stated by the file",
  recorded_by_person: "Recorded by a person",
  from_registry: "From the PROJ registry",
  unknown: "Unknown",
};

export const FACT_STATE_TONES: Record<MapSheetAssessment["facts"][number]["state"], Tone> = {
  stated_by_file: "ok",
  recorded_by_person: "accent",
  from_registry: "neutral",
  unknown: "warn",
};

export const UNRESOLVED_LABELS: Record<MapSheetAssessment["unresolved"][number]["status"], string> = {
  missing: "Missing",
  needs_authority: "Needs an authoritative source",
  ambiguous: "Ambiguous",
  declared_unverified: "Declared, not verified",
};

export const UNRESOLVED_TONES: Record<MapSheetAssessment["unresolved"][number]["status"], Tone> = {
  missing: "danger",
  needs_authority: "warn",
  ambiguous: "warn",
  declared_unverified: "neutral",
};

export const NOTE_FIELDS: { value: MapSheetAssessment["notes"][number]["field"]; label: string }[] = [
  { value: "datum", label: "Datum" },
  { value: "ellipsoid", label: "Ellipsoid" },
  { value: "prime_meridian", label: "Prime meridian" },
  { value: "projection", label: "Projection" },
  { value: "projection_parameters", label: "Projection parameters" },
  { value: "units", label: "Units" },
  { value: "coordinate_grid", label: "Coordinate grid" },
  { value: "extent", label: "Geographic extent" },
  { value: "transformation_to_wgs84", label: "Transformation to WGS 84" },
  { value: "other", label: "Other" },
];

const number = (value: unknown, digits = 4): string => (typeof value === "number" ? String(Number(value.toPrecision(digits + 4))) : String(value));

/** A short, honest rendering of one assessed fact's value. Nothing is invented: an unknown fact renders as nothing. */
export function factValue(field: string, value: unknown): string | null {
  if (value === null || value === undefined) return null;
  const record = value as Record<string, unknown>;
  switch (field) {
    case "datum":
      return String(record.name ?? "unnamed");
    case "ellipsoid":
      return `${record.name} · a = ${number(record.semi_major_m, 10)} m` + (record.inverse_flattening ? ` · 1/f = ${number(record.inverse_flattening, 10)}` : "");
    case "prime_meridian":
      return `${record.name} (${record.longitude_degrees}°)`;
    case "projection":
      return record.method ? String(record.method) : "None (geographic CRS)";
    case "projection_parameters": {
      const parameters = value as { name: string; value: number; unit: string }[];
      return parameters.length === 0 ? "None" : parameters.map((p) => `${p.name} = ${number(p.value, 8)} ${p.unit}`).join("; ");
    }
    case "units":
      return (value as string[]).join(", ");
    case "coordinate_grid":
      return `${record.kind} coordinates in ${(record.units as string[]).join(", ")}`;
    case "extent": {
      const bounds = record.bounds_4326 as number[] | undefined;
      if (bounds) return `${bounds.map((b) => b.toFixed(5)).join(", ")} (W, S, E, N) · ${number(record.area_km2, 4)} km²`;
      const pixels = record.pixels as number[] | undefined;
      return pixels ? `${pixels[0]} × ${pixels[1]} pixels, not placed` : null;
    }
    case "transformation_to_wgs84": {
      const accuracy = record.accuracy_m as number | null;
      return `${record.status}${record.classification ? ` · ${record.classification}` : ""}${accuracy != null ? ` · about ${accuracy} m` : ""}`;
    }
    default:
      return null;
  }
}
