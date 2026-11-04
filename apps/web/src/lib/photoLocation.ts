/**
 * Checks for a photograph position typed or picked by a person. They mirror the server's rules, which remain the
 * authority: the server validates again and refuses anything outside them.
 */

export interface Position {
  lat: number;
  lon: number;
}

export type PositionCheck = { ok: true; position: Position } | { ok: false; latError: string | null; lonError: string | null };

/** A decimal-degree number, or null for anything else (empty, text, NaN, Infinity, "10,5"). */
export function parseDegrees(text: string): number | null {
  const trimmed = text.trim();
  if (!/^[+-]?(\d+(\.\d*)?|\.\d+)$/.test(trimmed)) return null;
  const value = Number(trimmed);
  return Number.isFinite(value) ? value : null;
}

/** Latitude and longitude fields, as typed. Nothing is accepted that the server would refuse. */
export function checkPosition(latText: string, lonText: string): PositionCheck {
  const lat = parseDegrees(latText);
  const lon = parseDegrees(lonText);
  const latError =
    latText.trim() === ""
      ? "Enter a latitude."
      : lat === null
        ? "Latitude must be a number in decimal degrees, for example 28.6139."
        : lat < -90 || lat > 90
          ? "Latitude must be between -90 and 90."
          : null;
  const lonError =
    lonText.trim() === ""
      ? "Enter a longitude."
      : lon === null
        ? "Longitude must be a number in decimal degrees, for example 77.2090."
        : lon < -180 || lon > 180
          ? "Longitude must be between -180 and 180."
          : null;
  if (latError || lonError || lat === null || lon === null) return { ok: false, latError, lonError };
  if (lat === 0 && lon === 0) {
    return { ok: false, latError: "0, 0 is almost always a missing value and is not accepted.", lonError: null };
  }
  return { ok: true, position: { lat, lon } };
}

/** A coordinate for an input box: fixed places, no hemisphere letters, so it can be typed back. */
export function toInputValue(value: number): string {
  return value.toFixed(6);
}
