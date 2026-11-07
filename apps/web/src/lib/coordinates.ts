/**
 * Reads a position typed into one search box, the way people paste it from a phone, a GPS or a report:
 *
 *   20.2961, 85.8245              latitude first, decimal degrees (a minus sign for south or west)
 *   20.2961 85.8245               the same, separated by a space
 *   20.2961 N, 85.8245 E          hemisphere letters after or before the number (N 20.2961 E 85.8245)
 *   85.8245 E 20.2961 N           any order when every part carries its letter
 *   20°17'46.0"N 85°49'28.2"E     degrees, minutes and seconds (or degrees and decimal minutes: 20°17.766'N)
 *
 * Plain numbers are always latitude first; nothing is swapped or guessed. Text that is not a position (a place name) is
 * reported as such, so the caller can search for it by name instead. Nothing here contacts anything.
 */

export type CoordinateQuery =
  | { kind: "coordinates"; lat: number; lon: number }
  | { kind: "invalid"; message: string }
  | { kind: "text" };

/** The limit of the web map (Web Mercator). */
const MAX_LAT = 85.0511;
const LOOKS_LIKE_POSITION = /^[\s\d.,;°'′"″+\-NSEWnsew]+$/;
const PART =
  /^\s*([NSEWnsew])?\s*([+-]?\d+(?:\.\d+)?)\s*°?\s*(?:(\d+(?:\.\d+)?)\s*['′]\s*)?(?:(\d+(?:\.\d+)?)\s*(?:"|″|'')\s*)?([NSEWnsew])?\s*$/;

interface Part {
  value: number;
  axis: "lat" | "lon" | null; // from its hemisphere letter, when it has one
}

function readPart(text: string): Part | string {
  const match = PART.exec(text);
  if (!match) return `“${text.trim()}” is not a coordinate.`;
  const [, before, degreesText, minutesText, secondsText, after] = match;
  if (before && after) return `“${text.trim()}” has two hemisphere letters.`;
  const letter = (before ?? after)?.toUpperCase();
  const signed = degreesText!.startsWith("-") || degreesText!.startsWith("+");
  if (letter && signed) return "Use either a minus sign or a hemisphere letter (N, S, E, W), not both.";
  if ((minutesText || secondsText) && degreesText!.includes(".")) {
    return "With minutes or seconds, the degrees must be a whole number.";
  }
  const minutes = minutesText ? Number(minutesText) : 0;
  const seconds = secondsText ? Number(secondsText) : 0;
  if (minutes >= 60 || seconds >= 60) return "Minutes and seconds must be less than 60.";
  const magnitude = Math.abs(Number(degreesText)) + minutes / 60 + seconds / 3600;
  const negative = degreesText!.startsWith("-") || letter === "S" || letter === "W";
  return {
    value: negative ? -magnitude : magnitude,
    axis: letter === "N" || letter === "S" ? "lat" : letter === "E" || letter === "W" ? "lon" : null,
  };
}

/** Splits "a, b" / "a; b" / "20N 85E" / "N20 E85" / "20 85" into its two halves, or null. */
function halves(text: string): [string, string] | null {
  const separated = text.split(/[,;]/);
  if (separated.length === 2) return [separated[0]!, separated[1]!];
  if (separated.length > 2) return null;
  // Letter first ("N 20.29 E 85.82") is tried before letter last, which would split it after the "E".
  const leading = /^([NSEWnsew][^NSEWnsew]*?)\s*([NSEWnsew][^NSEWnsew]*)$/.exec(text.trim());
  if (leading) return [leading[1]!, leading[2]!];
  const trailing = /^(.*?\d.*?['′"″°]?\s*[NSEWnsew])\s+(.*\d.*)$/.exec(text.trim());
  if (trailing) return [trailing[1]!, trailing[2]!];
  const words = text.trim().split(/\s+/);
  return words.length === 2 ? [words[0]!, words[1]!] : null;
}

export function parseCoordinateText(input: string): CoordinateQuery {
  const text = input.trim();
  if (!text || !/\d/.test(text) || !LOOKS_LIKE_POSITION.test(text)) return { kind: "text" };
  const pair = halves(text);
  if (!pair) {
    return { kind: "invalid", message: "Type a latitude and a longitude, for example 20.2961, 85.8245." };
  }
  const first = readPart(pair[0]);
  if (typeof first === "string") return { kind: "invalid", message: first };
  const second = readPart(pair[1]);
  if (typeof second === "string") return { kind: "invalid", message: second };

  let lat: number;
  let lon: number;
  if (first.axis && second.axis) {
    if (first.axis === second.axis) {
      return { kind: "invalid", message: "One value must be a latitude (N or S) and the other a longitude (E or W)." };
    }
    [lat, lon] = first.axis === "lat" ? [first.value, second.value] : [second.value, first.value];
  } else if (first.axis === "lon" || second.axis === "lat") {
    [lat, lon] = [second.value, first.value];
  } else {
    [lat, lon] = [first.value, second.value];
  }

  if (Math.abs(lat) > 90) {
    const hint =
      !first.axis && !second.axis && Math.abs(lon) <= 90
        ? ` The latitude comes first: did you mean ${pair[1].trim()}, ${pair[0].trim()}?`
        : "";
    return { kind: "invalid", message: `Latitude must be between -90 and 90.${hint}` };
  }
  if (Math.abs(lat) > MAX_LAT) {
    return { kind: "invalid", message: "Latitude must be between -85.0511 and 85.0511, the limit of the web map." };
  }
  if (Math.abs(lon) > 180) return { kind: "invalid", message: "Longitude must be between -180 and 180." };
  return { kind: "coordinates", lat, lon };
}

/** "20.29610° N, 85.82450° E": how a position is shown back to the person. */
export function formatLatLon(lat: number, lon: number, decimals = 5): string {
  const ns = lat < 0 ? "S" : "N";
  const ew = lon < 0 ? "W" : "E";
  return `${Math.abs(lat).toFixed(decimals)}° ${ns}, ${Math.abs(lon).toFixed(decimals)}° ${ew}`;
}
