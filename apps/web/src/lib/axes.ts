/** Which way a CRS's x and y coordinates run. Never assumes east and north: EPSG:2046 has x pointing west and y south. */

interface Axis {
  abbreviation: string;
  direction: string;
  unit?: string;
}

/**
 * The axes in the order coordinates are entered here (x first). The registry lists a latitude-first pair (north/south
 * then east/west) the other way round from how it is entered, so those two are swapped; every other order is kept.
 */
export function xyAxes(axisOrder: readonly Axis[]): [Axis, Axis] | null {
  if (axisOrder.length < 2) return null;
  const [first, second] = axisOrder as [Axis, Axis];
  const northSouth = (d: string) => d === "north" || d === "south";
  const eastWest = (d: string) => d === "east" || d === "west";
  return northSouth(first.direction) && eastWest(second.direction) ? [second, first] : [first, second];
}

const PLAIN = new Set(["east", "west", "north", "south"]);

/** A column heading for the x or y coordinate of a CRS: its own abbreviation and the way it increases. */
export function axisHeading(axis: Axis | undefined, fallback: string): string {
  if (!axis) return fallback;
  const direction = PLAIN.has(axis.direction) ? ` (${axis.abbreviation}, towards the ${axis.direction})` : ` (${axis.abbreviation})`;
  return `${fallback}${direction}`;
}

/** Headings for the two coordinate columns once a CRS is chosen; neutral ones before. */
export function coordinateHeadings(axisOrder: readonly Axis[] | null | undefined): { x: string; y: string } {
  const pair = axisOrder ? xyAxes(axisOrder) : null;
  if (!pair) return { x: "X (first coordinate)", y: "Y (second coordinate)" };
  return { x: axisHeading(pair[0], "X"), y: axisHeading(pair[1], "Y") };
}
