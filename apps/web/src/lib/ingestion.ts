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

const FLAG_LABELS: Record<string, string> = {
  NOT_GEOREFERENCED: "Not georeferenced",
  MISSING_CRS: "No CRS in file",
  INVALID_CRS: "CRS not usable",
  INVALID_GEOREFERENCING: "Georeferencing not trusted",
  CRS_NO_AUTHORITY_CODE: "CRS has no registry code",
  TIMESTAMP_UNKNOWN: "Capture time unknown",
  METADATA_INCOMPLETE: "Metadata incomplete",
  DUPLICATE_ASSET: "Duplicate content",
};

export function flagLabel(code: string): string {
  return FLAG_LABELS[code] ?? code;
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
