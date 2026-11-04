const dateTime = new Intl.DateTimeFormat("en-GB", {
  day: "2-digit",
  month: "short",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

/** "12 Oct 2026, 17:04" in the computer's time zone. Unambiguous, unlike 12/10/2026. */
export function formatDateTime(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? "unknown" : dateTime.format(date);
}

type Position = [number, number];

function positions(geometry: { type?: unknown; coordinates?: unknown }): Position[] {
  const found: Position[] = [];
  const walk = (value: unknown) => {
    if (!Array.isArray(value)) return;
    if (value.length >= 2 && typeof value[0] === "number" && typeof value[1] === "number") {
      found.push([value[0], value[1]]);
    } else {
      value.forEach(walk);
    }
  };
  walk(geometry.coordinates);
  return found;
}

/**
 * A readable statement of a point or an extent. `digits` rounds for display only; the stored values are
 * unchanged. Returns null for a geometry it cannot describe.
 */
export function describeGeometry(
  geometry: { type?: unknown; coordinates?: unknown },
  labels: { x: string; y: string },
  digits?: number,
): string | null {
  const points = positions(geometry);
  if (points.length === 0) return null;
  const show = (value: number) => (digits === undefined ? String(value) : value.toFixed(digits));
  if (geometry.type === "Point") {
    const [x, y] = points[0]!;
    return `${labels.x} ${show(x)}, ${labels.y} ${show(y)}`;
  }
  const xs = points.map(([x]) => x);
  const ys = points.map(([, y]) => y);
  return (
    `${labels.x} ${show(Math.min(...xs))} to ${show(Math.max(...xs))}, ` +
    `${labels.y} ${show(Math.min(...ys))} to ${show(Math.max(...ys))}`
  );
}
