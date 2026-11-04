/**
 * Display-only adjustments for a raster's picture: grey, brightness and contrast. They are applied to the picture in the
 * browser (a CSS filter) and never reach the stored raster, its statistics, its pixel values or any measurement.
 */
export type ColourMode = "colour" | "grey";

export interface DisplayAdjust {
  mode: ColourMode;
  /** Percent; 100 is unchanged. */
  brightness: number;
  contrast: number;
}

export const DEFAULT_ADJUST: DisplayAdjust = { mode: "colour", brightness: 100, contrast: 100 };
export const ADJUST_RANGE = { min: 40, max: 200 } as const;

export function isDefaultAdjust(adjust: DisplayAdjust): boolean {
  return adjust.mode === DEFAULT_ADJUST.mode && adjust.brightness === 100 && adjust.contrast === 100;
}

/** The CSS filter for these settings, or undefined when nothing is changed. */
export function adjustFilter(adjust: DisplayAdjust): string | undefined {
  const parts: string[] = [];
  if (adjust.mode === "grey") parts.push("grayscale(1)");
  if (adjust.brightness !== 100) parts.push(`brightness(${(adjust.brightness / 100).toFixed(2)})`);
  if (adjust.contrast !== 100) parts.push(`contrast(${(adjust.contrast / 100).toFixed(2)})`);
  return parts.length > 0 ? parts.join(" ") : undefined;
}

/** A band the file itself declares as red, green or blue (by its colour interpretation or description). */
function declaredColour(band: { description?: unknown; color_interpretation?: unknown }): string | null {
  const text = `${String(band.color_interpretation ?? "")} ${String(band.description ?? "")}`.toLowerCase();
  for (const name of ["red", "green", "blue"]) if (new RegExp(`\\b${name}\\b`).test(text)) return name;
  return null;
}

/**
 * Whether the first three bands are declared red, green and blue, in that order. When they are not declared, the picture
 * still shows bands 1, 2 and 3 as red, green and blue (the application's existing default), and says that it is not known
 * what those bands hold.
 */
export function bandsDeclaredRgb(bands: { description?: unknown; color_interpretation?: unknown }[]): boolean {
  return bands.length >= 3 && ["red", "green", "blue"].every((name, i) => declaredColour(bands[i]!) === name);
}
