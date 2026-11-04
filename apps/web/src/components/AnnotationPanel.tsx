import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import {
  type Annotation,
  ApiError,
  createAnnotation,
  deleteAnnotation,
  getAnnotationHistory,
  listAnnotations,
  updateAnnotation,
} from "../api/client";
import {
  type AnnotationKind,
  boundsOf,
  type Drawing,
  draftGeometry,
  draftHint,
  formatArea,
  formatLength,
  formatPosition,
  KIND_LABEL,
  LEGEND,
  MAX_NOTE,
  MIN_POINTS,
  type Position,
  pointsOf,
  samePoints,
} from "../lib/annotations";
import { formatDateTime } from "../lib/format";
import { Badge, Button, ErrorNotice, Field, inputClass, Loading, Notice, Panel } from "./ui";

export interface AnnotationPanelProps {
  projectId: string;
  drawing: Drawing | null;
  onDrawing: (next: Drawing | null) => void;
  selectedId: string | null;
  onSelect: (id: string | null) => void;
  showOnMap: boolean;
  onShowOnMap: (show: boolean) => void;
  onZoom: (bounds: [number, number, number, number]) => void;
}

const TOOLS: AnnotationKind[] = ["point", "line", "polygon"];

/**
 * The person's own marks on the map. Saving, editing and deleting always go through the API as new versions; nothing
 * on this panel reads or changes a finding or a photograph location.
 */
export function AnnotationPanel(props: AnnotationPanelProps) {
  const { projectId, drawing, onDrawing, selectedId, onSelect } = props;
  const queryClient = useQueryClient();
  const [includeDeleted, setIncludeDeleted] = useState(false);
  const [note, setNote] = useState("");
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  const [historyOf, setHistoryOf] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [lat, setLat] = useState("");
  const [lon, setLon] = useState("");
  const [coordinateError, setCoordinateError] = useState<string | null>(null);

  const list = useQuery({
    queryKey: ["annotations", projectId, includeDeleted],
    queryFn: () => listAnnotations(projectId, includeDeleted),
    retry: false,
  });
  const items = list.data?.items ?? [];
  const notMigrated = list.error instanceof ApiError && list.error.code === "ANNOTATIONS_NOT_MIGRATED";
  const refresh = () => void queryClient.invalidateQueries({ queryKey: ["annotations", projectId] });

  const save = useMutation({
    mutationFn: async () => {
      if (!drawing) throw new Error("Nothing is being drawn.");
      const geometry = draftGeometry(drawing.kind, drawing.points);
      if (!geometry) throw new Error("The shape is not complete.");
      if (!drawing.editingId) {
        return createAnnotation(projectId, { kind: drawing.kind, geometry: geometry as never, crs_id: "EPSG:4326", note });
      }
      const original = items.find((item) => item.annotation_id === drawing.editingId);
      const shapeChanged = !original || !samePoints(drawing.points, pointsOf(original.geometry as never));
      return updateAnnotation(projectId, drawing.editingId, {
        expected_version: drawing.baseVersion ?? 1,
        crs_id: "EPSG:4326",
        note,
        ...(shapeChanged ? { geometry: geometry as never } : {}),
      });
    },
    onSuccess: (saved, _vars, _ctx) => {
      setMessage(drawing?.editingId ? "The annotation was saved as a new version." : "The annotation was saved.");
      onDrawing(null);
      onSelect(saved.annotation_id);
      setNote("");
      refresh();
    },
  });

  const remove = useMutation({
    mutationFn: (item: Annotation) => deleteAnnotation(projectId, item.annotation_id, item.version_no),
    onSuccess: (gone) => {
      setMessage("The annotation was deleted. It is hidden from the map; its history is kept.");
      setConfirmDelete(null);
      if (selectedId === gone.annotation_id) onSelect(null);
      refresh();
    },
  });

  const history = useQuery({
    queryKey: ["annotation-history", projectId, historyOf],
    queryFn: () => getAnnotationHistory(projectId, historyOf!),
    enabled: historyOf !== null,
  });

  const begin = (kind: AnnotationKind) => {
    save.reset();
    setMessage(null);
    setNote("");
    setConfirmDelete(null);
    onDrawing({ kind, points: [], editingId: null, baseVersion: null, active: true });
  };
  const edit = (item: Annotation) => {
    save.reset();
    setMessage(null);
    setNote(item.note);
    setConfirmDelete(null);
    onSelect(item.annotation_id);
    onDrawing({
      kind: item.kind,
      points: pointsOf(item.geometry as never),
      editingId: item.annotation_id,
      baseVersion: item.version_no,
      active: false,
    });
  };
  const cancel = () => {
    save.reset();
    onDrawing(null);
    setNote("");
  };
  const placeByCoordinates = () => {
    const latitude = Number(lat);
    const longitude = Number(lon);
    if (lat.trim() === "" || lon.trim() === "" || !Number.isFinite(latitude) || !Number.isFinite(longitude)) {
      setCoordinateError("Enter a latitude and a longitude as numbers.");
    } else if (Math.abs(latitude) > 90 || Math.abs(longitude) > 180) {
      setCoordinateError("Latitude must be within ±90° and longitude within ±180°.");
    } else {
      setCoordinateError(null);
      if (drawing) onDrawing({ ...drawing, points: [[longitude, latitude]] });
    }
  };

  const complete = drawing ? drawing.points.length >= MIN_POINTS[drawing.kind] : false;

  return (
    <Panel
      title="Annotations"
      description={`${LEGEND}. Marks you draw, with a note. Not detections, not photograph locations.`}
    >
      <div className="space-y-3">
        {list.isLoading ? <Loading label="Loading annotations…" /> : null}
        {notMigrated ? (
          <Notice tone="warning" title="Annotations are not available yet">
            <p className="text-xs">
              The database has not been updated for map annotations, so none can be read or saved. Nothing else on this page
              is affected.
            </p>
          </Notice>
        ) : list.isError ? (
          <ErrorNotice error={list.error} title="Annotations could not be loaded" />
        ) : null}

        {message ? (
          <Notice tone="success" title={message}>
            <p className="sr-only">Saved</p>
          </Notice>
        ) : null}

        {!notMigrated ? (
          <div role="group" aria-label="Draw an annotation" className="flex flex-wrap gap-1.5">
            {TOOLS.map((kind) => (
              <Button
                key={kind}
                size="sm"
                variant="secondary"
                aria-pressed={drawing?.kind === kind && drawing.editingId === null}
                disabled={drawing !== null}
                onClick={() => begin(kind)}
              >
                Add {kind}
              </Button>
            ))}
          </div>
        ) : null}

        {drawing ? (
          <form
            aria-label={drawing.editingId ? "Edit annotation" : "New annotation"}
            className="space-y-2.5 rounded-md border border-line-strong bg-raised p-3"
            onSubmit={(event) => {
              event.preventDefault();
              if (complete && !save.isPending) save.mutate();
            }}
          >
            <p className="text-[13px] font-medium text-fg">
              {drawing.editingId ? `Editing a ${drawing.kind}` : `New ${drawing.kind}`}
            </p>
            <p className="text-xs text-fg-muted" aria-live="polite">
              {drawing.active ? draftHint(drawing.kind, drawing.points.length) : "The shape is unchanged. Redraw it to move it."}
            </p>
            <div className="flex flex-wrap gap-1.5">
              {drawing.editingId && !drawing.active ? (
                <Button size="sm" variant="secondary" onClick={() => onDrawing({ ...drawing, points: [], active: true })}>
                  Redraw shape
                </Button>
              ) : null}
              {drawing.active ? (
                <>
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={drawing.points.length === 0}
                    onClick={() => onDrawing({ ...drawing, points: drawing.points.slice(0, -1) })}
                  >
                    Undo last point
                  </Button>
                  <Button size="sm" variant="ghost" disabled={drawing.points.length === 0} onClick={() => onDrawing({ ...drawing, points: [] })}>
                    Clear points
                  </Button>
                </>
              ) : null}
            </div>

            {drawing.kind === "point" && drawing.active ? (
              <fieldset className="space-y-1.5">
                <legend className="text-xs text-fg-muted">Or type the position (decimal degrees, WGS 84)</legend>
                <div className="flex flex-wrap items-end gap-2">
                  <label className="text-xs">
                    Latitude
                    <input className={`${inputClass} mt-1 w-28`} inputMode="decimal" value={lat} onChange={(e) => setLat(e.target.value)} />
                  </label>
                  <label className="text-xs">
                    Longitude
                    <input className={`${inputClass} mt-1 w-28`} inputMode="decimal" value={lon} onChange={(e) => setLon(e.target.value)} />
                  </label>
                  <Button size="sm" variant="secondary" onClick={placeByCoordinates}>
                    Place point
                  </Button>
                </div>
                {coordinateError ? (
                  <p role="alert" className="text-xs text-danger">
                    {coordinateError}
                  </p>
                ) : null}
              </fieldset>
            ) : null}

            {drawing.points.length > 0 && drawing.kind === "point" ? (
              <p className="font-mono text-xs text-fg-muted">{formatPosition(drawing.points[0] as Position)}</p>
            ) : null}

            <Field label="Note" htmlFor="annotation-note" hint={`${note.length} / ${MAX_NOTE} characters. Shown as plain text.`}>
              <textarea
                id="annotation-note"
                className={`${inputClass} min-h-20`}
                maxLength={MAX_NOTE}
                value={note}
                onChange={(event) => setNote(event.target.value)}
              />
            </Field>
            <div className="flex flex-wrap gap-2">
              <Button type="submit" size="sm" disabled={!complete || save.isPending}>
                {save.isPending ? "Saving…" : drawing.editingId ? "Save changes" : "Save annotation"}
              </Button>
              <Button size="sm" variant="ghost" onClick={cancel}>
                Cancel
              </Button>
            </div>
            {save.isError ? <ErrorNotice error={save.error} title="The annotation was not saved" /> : null}
          </form>
        ) : null}

        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs">
          <label className="flex items-center gap-1.5">
            <input type="checkbox" checked={props.showOnMap} onChange={(e) => props.onShowOnMap(e.target.checked)} />
            Show on the map
          </label>
          <label className="flex items-center gap-1.5">
            <input type="checkbox" checked={includeDeleted} onChange={(e) => setIncludeDeleted(e.target.checked)} />
            List deleted annotations
          </label>
        </div>

        {list.data ? (
          <p className="text-xs text-fg-subtle">
            {list.data.active} active · {list.data.deleted} deleted
          </p>
        ) : null}

        {list.data && items.length === 0 ? (
          <p className="text-[13px] text-fg-muted">
            {includeDeleted ? "No annotations in this project." : "No annotations yet. Choose Add point, Add line or Add polygon."}
          </p>
        ) : null}

        {items.length > 0 ? (
          <ul aria-label="Annotations in this project" className="space-y-2">
            {items.map((item) => (
              <AnnotationRow
                key={item.annotation_id}
                item={item}
                selected={item.annotation_id === selectedId}
                busy={drawing !== null}
                confirming={confirmDelete === item.annotation_id}
                deleting={remove.isPending}
                onSelect={() => {
                  onSelect(item.annotation_id);
                  props.onZoom(boundsOf(item.geometry as never));
                }}
                onEdit={() => edit(item)}
                onAskDelete={() => setConfirmDelete(item.annotation_id)}
                onCancelDelete={() => setConfirmDelete(null)}
                onDelete={() => remove.mutate(item)}
                onHistory={() => setHistoryOf(historyOf === item.annotation_id ? null : item.annotation_id)}
                historyOpen={historyOf === item.annotation_id}
              />
            ))}
          </ul>
        ) : null}

        {remove.isError ? <ErrorNotice error={remove.error} title="The annotation was not deleted" /> : null}

        {historyOf ? (
          <section aria-label="Annotation history" className="space-y-1.5 rounded-md border border-line bg-raised p-3">
            <h3 className="text-[13px] font-medium text-fg">History</h3>
            {history.isLoading ? <Loading label="Loading history…" /> : null}
            {history.isError ? <ErrorNotice error={history.error} /> : null}
            {history.data ? (
              <ol className="space-y-1.5 text-xs text-fg-muted">
                {history.data.versions.map((version) => (
                  <li key={version.version_no}>
                    <span className="font-medium text-fg">
                      Version {version.version_no} · {version.action === "create" ? "drawn" : version.action === "update" ? "changed" : "deleted"}
                    </span>{" "}
                    by {version.actor_label}, {formatDateTime(version.created_at)}
                    {version.reason ? <span> · reason: {version.reason}</span> : null}
                    {version.note ? <p className="whitespace-pre-wrap break-words text-fg-subtle">{version.note}</p> : null}
                  </li>
                ))}
              </ol>
            ) : null}
          </section>
        ) : null}

        <p className="text-xs text-fg-subtle">
          The author shown is the local profile that made the change. Profiles are not authenticated, so this is attribution,
          not proof of who drew it.
        </p>
      </div>
    </Panel>
  );
}

function AnnotationRow({
  item,
  selected,
  busy,
  confirming,
  deleting,
  historyOpen,
  onSelect,
  onEdit,
  onAskDelete,
  onCancelDelete,
  onDelete,
  onHistory,
}: {
  item: Annotation;
  selected: boolean;
  busy: boolean;
  confirming: boolean;
  deleting: boolean;
  historyOpen: boolean;
  onSelect: () => void;
  onEdit: () => void;
  onAskDelete: () => void;
  onCancelDelete: () => void;
  onDelete: () => void;
  onHistory: () => void;
}) {
  const deleted = item.status === "deleted";
  const measurement = item.measurement;
  return (
    <li
      aria-current={selected ? "true" : undefined}
      className={`space-y-1.5 rounded-md border px-3 py-2 ${selected ? "border-accent bg-accent/5" : "border-line"} ${deleted ? "opacity-70" : ""}`}
    >
      <div className="flex flex-wrap items-center gap-1.5">
        <Badge tone="neutral">{KIND_LABEL[item.kind]}</Badge>
        {deleted ? <Badge tone="danger">Deleted</Badge> : null}
        <span className="text-xs text-fg-subtle">version {item.version_no}</span>
      </div>
      {/* The note is the author's text: React escapes it, so markup in a note is shown, never run. */}
      <p className="whitespace-pre-wrap break-words text-[13px] text-fg">{item.note || <em className="text-fg-subtle">No note</em>}</p>

      {measurement ? (
        <div className="text-xs text-fg-muted">
          {measurement.length_m != null ? <p>Length (approximate): {formatLength(measurement.length_m)}</p> : null}
          {measurement.area_m2 != null ? <p>Area (approximate): {formatArea(measurement.area_m2)}</p> : null}
          {measurement.perimeter_m != null ? <p>Outline length (approximate): {formatLength(measurement.perimeter_m)}</p> : null}
          <details>
            <summary className="cursor-pointer text-fg-subtle">How this is measured</summary>
            <p className="mt-1">Method: {measurement.method}.</p>
            <ul className="mt-1 list-disc pl-4">
              {measurement.limitations.map((limit) => (
                <li key={limit}>{limit}</li>
              ))}
            </ul>
          </details>
        </div>
      ) : null}

      <p className="text-xs text-fg-subtle">
        {deleted
          ? `Deleted by ${item.deleted_by ?? "unknown"}, ${formatDateTime(item.deleted_at ?? item.updated_at)}`
          : `Drawn by ${item.created_by}, ${formatDateTime(item.created_at)}${item.version_no > 1 ? ` · changed by ${item.updated_by}, ${formatDateTime(item.updated_at)}` : ""}`}
      </p>

      <div className="flex flex-wrap gap-1.5">
        <Button size="sm" variant="secondary" aria-label={`Show ${item.kind} on the map`} onClick={onSelect} disabled={deleted}>
          Show
        </Button>
        {!deleted ? (
          <>
            <Button size="sm" variant="secondary" aria-label={`Edit ${item.kind}`} disabled={busy} onClick={onEdit}>
              Edit
            </Button>
            <Button size="sm" variant="ghost" aria-label={`Delete ${item.kind}`} disabled={busy || confirming} onClick={onAskDelete}>
              Delete
            </Button>
          </>
        ) : null}
        <Button size="sm" variant="ghost" aria-label={`${historyOpen ? "Hide" : "Show"} history of ${item.kind}`} onClick={onHistory}>
          {historyOpen ? "Hide history" : "History"}
        </Button>
      </div>

      {confirming ? (
        <div role="alertdialog" aria-label="Confirm delete" className="space-y-1.5 rounded-md border border-danger/40 bg-danger-soft p-2.5">
          <p className="text-xs text-fg">
            Delete this {item.kind}? It will be hidden from the map. Its history stays and can still be listed.
          </p>
          <div className="flex gap-1.5">
            <Button size="sm" variant="danger" disabled={deleting} onClick={onDelete}>
              {deleting ? "Deleting…" : "Delete annotation"}
            </Button>
            <Button size="sm" variant="ghost" onClick={onCancelDelete}>
              Keep it
            </Button>
          </div>
        </div>
      ) : null}
    </li>
  );
}
