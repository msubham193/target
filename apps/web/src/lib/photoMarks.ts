import { useCallback, useEffect, useState } from "react";

/**
 * Marks a person draws on a photograph. They are the person's own: not model findings, not confirmed findings, and
 * with no geographic location. They are kept in this browser only (the server has no place for them), so they are not
 * shared, not in reports and not backed up.
 */
export type MarkShape =
  | { kind: "box"; x: number; y: number; width: number; height: number }
  | { kind: "point"; x: number; y: number }
  | { kind: "outline"; points: [number, number][] };

export interface Mark {
  id: string;
  label: string;
  note: string;
  shape: MarkShape;
  created_at: string;
}

export const MARK_KIND_LABELS: Record<MarkShape["kind"], string> = { box: "Box", point: "Point", outline: "Outline" };

const key = (assetId: string) => `geopoc.photo-marks.v1.${assetId}`;

const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);

function validShape(shape: unknown): shape is MarkShape {
  if (!shape || typeof shape !== "object") return false;
  const s = shape as Record<string, unknown>;
  if (s.kind === "box") return [s.x, s.y, s.width, s.height].every(finite);
  if (s.kind === "point") return finite(s.x) && finite(s.y);
  if (s.kind === "outline") {
    return Array.isArray(s.points) && s.points.length >= 3 && s.points.every((p) => Array.isArray(p) && p.length === 2 && p.every(finite));
  }
  return false;
}

/** Marks saved for a photograph. Anything unreadable or malformed is dropped, never guessed at. */
export function loadMarks(assetId: string): Mark[] {
  try {
    const raw = localStorage.getItem(key(assetId));
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(
      (item): item is Mark =>
        Boolean(item) &&
        typeof item === "object" &&
        typeof (item as Mark).id === "string" &&
        typeof (item as Mark).label === "string" &&
        validShape((item as Mark).shape),
    ).map((item) => ({ ...item, note: typeof item.note === "string" ? item.note : "", created_at: String(item.created_at ?? "") }));
  } catch {
    return [];
  }
}

function saveMarks(assetId: string, marks: Mark[]): void {
  try {
    if (marks.length === 0) localStorage.removeItem(key(assetId));
    else localStorage.setItem(key(assetId), JSON.stringify(marks));
  } catch {
    // the marks then last only until the page is closed
  }
}

export const clamp = (value: number, low: number, high: number): number => Math.min(high, Math.max(low, value));

/** A box from two opposite corners, kept inside the picture. Returns null when it is too small to mean anything. */
export function boxFromCorners(
  a: [number, number],
  b: [number, number],
  size: { width: number; height: number },
  minimum: number,
): Extract<MarkShape, { kind: "box" }> | null {
  const x0 = clamp(Math.min(a[0], b[0]), 0, size.width);
  const x1 = clamp(Math.max(a[0], b[0]), 0, size.width);
  const y0 = clamp(Math.min(a[1], b[1]), 0, size.height);
  const y1 = clamp(Math.max(a[1], b[1]), 0, size.height);
  if (x1 - x0 < minimum || y1 - y0 < minimum) return null;
  return { kind: "box", x: x0, y: y0, width: x1 - x0, height: y1 - y0 };
}

/** Where a mark's label is anchored: the top-left of its box, its point, or the top-left of its outline's extent. */
export function anchorOf(shape: MarkShape): [number, number] {
  if (shape.kind === "box" || shape.kind === "point") return [shape.x, shape.y];
  return [Math.min(...shape.points.map((p) => p[0])), Math.min(...shape.points.map((p) => p[1]))];
}

const newId = (): string =>
  typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : `m${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`;

export function usePhotoMarks(assetId: string) {
  const [marks, setMarks] = useState<Mark[]>(() => loadMarks(assetId));
  useEffect(() => setMarks(loadMarks(assetId)), [assetId]);

  const commit = useCallback(
    (update: (current: Mark[]) => Mark[]) => {
      setMarks((current) => {
        const next = update(current);
        saveMarks(assetId, next);
        return next;
      });
    },
    [assetId],
  );

  const add = useCallback(
    (label: string, shape: MarkShape): Mark => {
      const mark: Mark = { id: newId(), label: label.trim() || "target", note: "", shape, created_at: new Date().toISOString() };
      commit((current) => [...current, mark]);
      return mark;
    },
    [commit],
  );
  const update = useCallback(
    (id: string, patch: Partial<Pick<Mark, "label" | "note">>) =>
      commit((current) => current.map((mark) => (mark.id === id ? { ...mark, ...patch } : mark))),
    [commit],
  );
  const remove = useCallback((id: string) => commit((current) => current.filter((mark) => mark.id !== id)), [commit]);
  const clear = useCallback(() => commit(() => []), [commit]);

  return { marks, add, update, remove, clear };
}
