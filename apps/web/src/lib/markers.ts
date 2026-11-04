/**
 * Deciding which photograph marker a map click means. Markers can overlap on screen when photographs were taken
 * close together, so the first marker under the pointer is not necessarily the one meant. All distances are in
 * screen pixels, measured from the click to each marker's centre.
 */

export interface ScreenMarker {
  id: string;
  x: number;
  y: number;
}

export type MarkerClick = { kind: "none" } | { kind: "one"; id: string } | { kind: "several"; ids: string[] };

/** How far from a marker's centre a click still counts as aimed at it. */
export const HIT_RADIUS_PX = 10;
/**
 * The nearest marker wins outright only when the next one is at least this much farther away. Small on purpose:
 * markers 5 to 6 px apart (photographs about 30 m apart at the fitted zoom) must still be selectable by clicking
 * the centre of one, while a click between two of them still asks.
 */
export const CLEAR_WINNER_MARGIN_PX = 4;

/**
 * - none: no marker near the click, so it is an ordinary map click.
 * - one: exactly one marker is near, or one is clearly nearer than the rest.
 * - several: the click could mean more than one marker; the caller should ask the person, nearest first.
 */
export function chooseMarker(
  click: { x: number; y: number },
  markers: ScreenMarker[],
  radius = HIT_RADIUS_PX,
  margin = CLEAR_WINNER_MARGIN_PX,
): MarkerClick {
  const near = markers
    .map((marker) => ({ id: marker.id, distance: Math.hypot(marker.x - click.x, marker.y - click.y) }))
    .filter((marker) => marker.distance <= radius)
    .sort((a, b) => a.distance - b.distance || a.id.localeCompare(b.id));
  const first = near[0];
  if (!first) return { kind: "none" };
  const second = near[1];
  if (!second || second.distance - first.distance >= margin) return { kind: "one", id: first.id };
  return { kind: "several", ids: near.map((marker) => marker.id) };
}
