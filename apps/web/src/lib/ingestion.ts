import type { IngestionRule } from "../api/client";

export function formatBytes(bytes: number): string {
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(bytes % 1024 ** 3 === 0 ? 0 : 2)} GB`;
  if (bytes >= 1024 ** 2) return `${(bytes / 1024 ** 2).toFixed(bytes % 1024 ** 2 === 0 ? 0 : 1)} MB`;
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${bytes} bytes`;
}

export function extensionOf(name: string): string {
  const dot = name.lastIndexOf(".");
  return dot > 0 ? name.slice(dot).toLowerCase() : "";
}

export interface LocalRejection {
  code: string;
  title: string;
  detail: string;
}

/**
 * A quick check before uploading, so an obviously unacceptable file gets an immediate answer.
 * The server repeats every check and also inspects the content; it remains the authority.
 */
export function checkFile(file: { name: string; size: number }, rule: IngestionRule): LocalRejection | null {
  const extension = extensionOf(file.name);
  if (!rule.extensions.includes(extension)) {
    return {
      code: "UNSUPPORTED_FILE_TYPE",
      title: "This file type is not supported here",
      detail: `${rule.label} accepts ${rule.extensions.join(", ")}; this file ends in ${extension || "(no extension)"}.`,
    };
  }
  if (file.size === 0) {
    return { code: "EMPTY_FILE", title: "The file is empty", detail: "Nothing was uploaded." };
  }
  if (file.size > rule.max_bytes) {
    return {
      code: "FILE_TOO_LARGE",
      title: "The file is too large",
      detail: `This file is ${formatBytes(file.size)}; the limit for ${rule.label.toLowerCase()} is ${formatBytes(rule.max_bytes)}.`,
    };
  }
  return null;
}

/** Whether a file is a sidecar metadata file (world file, .prj, .aux.xml) accepted with this kind of input. */
export function isSidecar(name: string, rule: IngestionRule): boolean {
  const lower = name.toLowerCase();
  return (rule.sidecar_extensions ?? []).some((extension) => lower.endsWith(extension) && lower.length > extension.length);
}

/**
 * Whether a sidecar's name refers to this raster, by the server's rules: the same name without the extension
 * (scan.tfw, scan.prj, scan.aux.xml for scan.tif), or the full raster name for scan.tif.aux.xml. Case-insensitive.
 * The server repeats the check; this only lets a batch pair sidecars with the rasters uploaded in it.
 */
export function sidecarPairsWith(sidecarName: string, rasterName: string): boolean {
  const sidecar = sidecarName.toLowerCase();
  const raster = rasterName.toLowerCase();
  const rasterExtension = [".tif", ".tiff"].find((extension) => raster.endsWith(extension));
  if (!rasterExtension) return false;
  if (sidecar.endsWith(".aux.xml")) {
    const base = sidecar.slice(0, -".aux.xml".length);
    if (base.endsWith(".tif") || base.endsWith(".tiff")) return base === raster;
    return base === raster.slice(0, -rasterExtension.length);
  }
  const extension = [".tfw", ".tifw", ".tiffw", ".wld", ".prj"].find((candidate) => sidecar.endsWith(candidate));
  return extension !== undefined && sidecar.slice(0, -extension.length) === raster.slice(0, -rasterExtension.length);
}

export function checkSidecar(file: { name: string; size: number }, rule: IngestionRule): LocalRejection | null {
  if (file.size === 0) return { code: "EMPTY_FILE", title: "The file is empty", detail: "Nothing was uploaded." };
  if (rule.sidecar_max_bytes && file.size > rule.sidecar_max_bytes) {
    return {
      code: "FILE_TOO_LARGE",
      title: "The sidecar file is too large",
      detail: `This file is ${formatBytes(file.size)}; sidecar files are at most ${formatBytes(rule.sidecar_max_bytes)}.`,
    };
  }
  return null;
}

const FLAG_LABELS: Record<string, string> = {
  NOT_GEOREFERENCED: "Not georeferenced",
  MISSING_CRS: "No CRS in file",
  INVALID_CRS: "CRS not usable",
  INVALID_GEOREFERENCING: "Georeferencing not trusted",
  CRS_NO_AUTHORITY_CODE: "CRS has no registry code",
  TRANSFORMATION_BLOCKED: "Datum transformation blocked",
  TRANSFORMATION_BALLPARK_BLOCKED: "Only a ballpark transformation exists",
  TRANSFORMATION_GRID_MISSING: "Datum-shift grid not installed",
  TRANSFORMATION_UNAVAILABLE: "No datum transformation available",
  TRANSFORMATION_SELECTION_REQUIRED: "Choose a datum transformation",
  TRANSFORMATION_APPROXIMATE: "Approximate datum shift",
  MAP_SHEET: "Map sheet",
  MAP_SHEET_SCANNED: "Scanned map sheet: needs georeferencing",
  MAP_SHEET_PDF: "PDF map sheet",
  PDF_NOT_RENDERED: "PDF cannot be drawn here",
  PDF_GEO_METADATA_UNVERIFIED: "PDF geospatial metadata (unverified)",
  PDF_ENCRYPTED: "Encrypted PDF",
  TIMESTAMP_UNKNOWN: "Capture time unknown",
  METADATA_INCOMPLETE: "Metadata incomplete",
  DUPLICATE_ASSET: "Duplicate content",
  UNCALIBRATED_SCORE: "Score not calibrated",
  LOW_CONFIDENCE: "Low confidence",
  UNCLASSIFIED: "Outside the known classes",
  EVIDENCE_INCOMPLETE: "Evidence incomplete",
  SOURCE_CONFLICT: "Sources disagree",
  BLUR: "Blurred",
  LOW_RESOLUTION: "Low resolution",
  METADATA_MISSING: "Metadata missing",
  OCCLUDED: "Partly hidden",
  PARTIAL_OUTPUT: "Run was interrupted",
};

/** A readable label for a quality-flag code. An unknown code is shown in plain words, never as RAW_CODE. */
export function flagLabel(code: string): string {
  const known = FLAG_LABELS[code];
  if (known) return known;
  const words = code.toLowerCase().replaceAll("_", " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

const FORMAT_LABELS: Record<string, string> = { GTiff: "TIFF / GeoTIFF" };

export function formatLabel(detected: string): string {
  return FORMAT_LABELS[detected] ?? detected;
}

const numbers = new Intl.NumberFormat("en-GB");

/** 791,555 — fixed grouping, whatever the computer's regional settings are. */
export function formatCount(value: number): string {
  return numbers.format(value);
}

export const MODALITY_NAMES: Record<string, string> = {
  ground_image: "Ground photograph",
  overhead_raster: "Satellite / aerial raster",
  video: "Video",
};

export const STATUS_NAMES: Record<string, string> = {
  validated: "Validated",
  validated_with_warnings: "Validated with warnings",
};
