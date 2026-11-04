import { useMutation, useQueryClient } from "@tanstack/react-query";
import { type MouseEvent, useMemo, useState } from "react";

import {
  ApiError,
  type Asset,
  type ControlPointsRequest,
  type ControlPointsValidation,
  type Crs,
  recordControlPoints,
  validateControlPoints,
} from "../api/client";
import { coordinateHeadings } from "../lib/axes";
import { ACK_REQUIRED, acknowledgementFromPlan } from "../lib/transformation";
import { AccuracyAcknowledgement } from "./AccuracyAcknowledgement";
import { CrsPicker } from "./CrsPicker";
import { TransformationPlanView } from "./TransformationPlan";
import { Badge, Button, ErrorNotice, Field, inputClass, Notice, Panel } from "./ui";

export interface Row {
  id: number;
  label: string;
  column: string;
  row: string;
  x: string;
  y: string;
}

const MIN_POINTS = 3;
let nextRow = 1;
const blank = (): Row => ({ id: nextRow++, label: "", column: "", row: "", x: "", y: "" });

const CHECK_TONES = { ok: "ok", warning: "warn", failed: "danger" } as const;

const num = (text: string): number | null => {
  const value = text.trim() === "" ? NaN : Number(text);
  return Number.isFinite(value) ? value : null;
};

/** The request a set of rows makes, or the reason it cannot be made yet. */
export function buildRequest(
  rows: Row[],
  crs: Crs | null,
  source: string,
  reason: string,
  operation: string | null,
  acknowledged = false,
): { request: ControlPointsRequest | null; problem: string | null } {
  if (!crs) return { request: null, problem: "Choose the CRS the coordinates are in." };
  const used = rows.filter((r) => [r.column, r.row, r.x, r.y].some((v) => v.trim() !== ""));
  if (used.length < MIN_POINTS) return { request: null, problem: `Enter at least ${MIN_POINTS} control points.` };
  const points = used.map((r) => ({ label: r.label.trim(), column: num(r.column), row: num(r.row), x: num(r.x), y: num(r.y) }));
  if (points.some((p) => p.column === null || p.row === null || p.x === null || p.y === null)) {
    return { request: null, problem: "Every control point needs a pixel column, pixel row, and both coordinates, as numbers." };
  }
  if (!source.trim()) return { request: null, problem: "Say where the coordinates come from." };
  if (!reason.trim()) return { request: null, problem: "Enter the reason for this georeferencing." };
  return {
    request: {
      crs_id: crs.identifier,
      points: points as ControlPointsRequest["points"],
      source: source.trim(),
      reason: reason.trim(),
      acknowledge_transformation: acknowledged,
      ...(operation ? { transformation_operation: operation } : {}),
    },
    problem: null,
  };
}

function PointPicker({
  asset,
  rows,
  active,
  onPick,
}: {
  asset: Asset;
  rows: Row[];
  active: number;
  onPick: (column: number, row: number) => void;
}) {
  const raster = asset.raster!;
  const ready = asset.preview.status === "ready";
  if (!ready) {
    return (
      <p className="text-xs text-fg-subtle">
        A preview of the picture is not ready, so points cannot be picked on it. Enter pixel positions by hand.
      </p>
    );
  }
  const click = (event: MouseEvent<HTMLDivElement>) => {
    const box = event.currentTarget.getBoundingClientRect();
    if (box.width === 0 || box.height === 0) return;
    const column = ((event.clientX - box.left) / box.width) * raster.width;
    const row = ((event.clientY - box.top) / box.height) * raster.height;
    onPick(Math.round(column * 10) / 10, Math.round(row * 10) / 10);
  };
  return (
    <div className="space-y-1">
      <p className="text-xs text-fg-muted">
        Click the picture to set the pixel position of point {active + 1}. The picture is {raster.width} × {raster.height} pixels; (0, 0) is
        its top-left corner.
      </p>
      <div
        role="img"
        aria-label="Map sheet picture: click to set the active control point"
        data-testid="point-picker"
        className="relative inline-block max-w-full cursor-crosshair overflow-hidden rounded-md border border-line"
        onClick={click}
      >
        <img src={`/api/v1/assets/${asset.id}/preview?variant=view`} alt="" className="block max-h-96 max-w-full select-none" draggable={false} />
        {rows.map((r, index) => {
          const c = num(r.column);
          const v = num(r.row);
          if (c === null || v === null) return null;
          return (
            <span
              key={r.id}
              className={`pointer-events-none absolute -translate-x-1/2 -translate-y-1/2 rounded-full px-1 text-[10px] font-bold ${
                index === active ? "bg-accent text-accent-fg" : "bg-warn text-canvas"
              }`}
              style={{ left: `${(c / raster.width) * 100}%`, top: `${(v / raster.height) * 100}%` }}
            >
              {index + 1}
            </span>
          );
        })}
      </div>
    </div>
  );
}

const metres = (value: number | null | undefined, digits = 2) => (value == null ? "not available" : `${value.toFixed(digits)} m`);

function Fit({ result }: { result: ControlPointsValidation }) {
  const fit = result.fit;
  if (!fit) return null;
  const tolerance = fit.tolerance as
    | {
        ground_resolution_m?: number;
        warn_above_m?: number;
        fail_above_m?: number;
        reference_uncertainty_stated?: boolean;
        source_uncertainty_stated?: boolean;
      }
    | null
    | undefined;
  const loo = fit.leave_one_out as { status?: string; reason?: string | null; essential?: string[]; worst_label?: string | null } | null | undefined;
  return (
    <div className="space-y-2" aria-label="Fit of the control points">
      <dl className="grid grid-cols-2 gap-x-6 gap-y-1 text-[13px] sm:grid-cols-4">
        <div>
          <dt className="text-xs text-fg-subtle">Residual (RMS, allowing for the fit)</dt>
          <dd data-testid="rms">{fit.exact_fit ? "not available (exact fit)" : metres(fit.rms_m_allowing_for_fit ?? fit.rms_m)}</dd>
        </div>
        <div>
          <dt className="text-xs text-fg-subtle">Worst residual</dt>
          <dd>{metres(fit.max_m)}</dd>
        </div>
        <div>
          <dt className="text-xs text-fg-subtle">Ground pixel</dt>
          <dd>
            {fit.ground_pixel_m && fit.ground_pixel_m.length === 2
              ? fit.ground_pixel_m.map((v) => Number(v.toPrecision(4))).join(" × ") + " m"
              : `${fit.pixel_size.map((v) => Number(v.toPrecision(6))).join(" × ")} ${fit.map_units ?? ""}`}
          </dd>
        </div>
        <div>
          <dt className="text-xs text-fg-subtle">Rotation</dt>
          <dd>{fit.rotation_degrees.toFixed(2)}°</dd>
        </div>
        <div>
          <dt className="text-xs text-fg-subtle">Shear</dt>
          <dd>{fit.shear_degrees == null ? "not available" : `${fit.shear_degrees.toFixed(3)}°`}</dd>
        </div>
        <div>
          <dt className="text-xs text-fg-subtle">Uncertainty at the sheet's corners</dt>
          <dd data-testid="corner-uncertainty">{metres(fit.corner_uncertainty_m, 1)}</dd>
        </div>
      </dl>
      {tolerance && tolerance.warn_above_m != null ? (
        <p className="text-xs text-fg-muted" data-testid="tolerance">
          Judged against what a {Number((tolerance.ground_resolution_m ?? 0).toPrecision(3))} m pixel allows: a warning above{" "}
          {tolerance.warn_above_m.toFixed(1)} m and a failure above {tolerance.fail_above_m?.toFixed(1)} m.
          {tolerance.reference_uncertainty_stated ? "" : " Uncertainty of the reference coordinates: not stated."}
          {tolerance.source_uncertainty_stated ? "" : " Uncertainty of the printed source: not stated."}
        </p>
      ) : null}
      {fit.exact_fit ? (
        <p className="text-xs text-warn" data-testid="exact-fit-warning">
          Three points fit exactly, so the fit cannot reveal a wrong point and its accuracy cannot be checked. A residual of zero means
          nothing here. Add at least one more point.
        </p>
      ) : loo?.status === "partial" ? (
        <p className="text-xs text-warn">
          {loo.essential?.join(", ")} cannot be checked against the other points, because without it they cannot fix the sheet.
        </p>
      ) : null}
      <div className="overflow-x-auto">
        <table className="w-full text-left text-xs">
          <caption className="sr-only">Residual of each control point</caption>
          <thead>
            <tr className="text-fg-subtle">
              <th scope="col" className="py-1 pr-3 font-medium">Point</th>
              <th scope="col" className="py-1 pr-3 font-medium">Residual (m)</th>
              <th scope="col" className="py-1 pr-3 font-medium">Left out (m)</th>
              <th scope="col" className="py-1 font-medium">Residual (px)</th>
            </tr>
          </thead>
          <tbody>
            {fit.residuals.map((r) => (
              <tr key={r.label} className="border-t border-line">
                <th scope="row" className="py-1 pr-3 font-medium text-fg">{r.label}</th>
                <td className="py-1 pr-3">{r.residual_m == null ? "—" : r.residual_m.toFixed(2)}</td>
                <td className="py-1 pr-3">{r.leave_one_out_m == null ? "—" : r.leave_one_out_m.toFixed(2)}</td>
                <td className="py-1">{r.residual_pixels.toFixed(2)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-xs text-fg-subtle">{fit.note}</p>
    </div>
  );
}

/**
 * Georeference a scanned map sheet from control points a person enters: pairs of a pixel position and a coordinate in a CRS
 * they choose. The application fits the transform and shows every residual; it does not find the points, assume the sheet's
 * projection or place anything until the person has reviewed the fit and confirmed.
 */
export function ControlPointsPanel({ asset, projectId }: { asset: Asset; projectId: string }) {
  const queryClient = useQueryClient();
  const [rows, setRows] = useState<Row[]>(() => [blank(), blank(), blank(), blank()]);
  const [active, setActive] = useState(0);
  const [crs, setCrs] = useState<Crs | null>(null);
  const [source, setSource] = useState("");
  const [reason, setReason] = useState("");
  const [operation, setOperation] = useState<string | null>(null);
  // Acknowledgement of a datum shift worse than 10 m (or with no stated accuracy), recorded with the georeferencing.
  const [acknowledged, setAcknowledged] = useState(false);
  const [reviewed, setReviewed] = useState<{ key: string; result: ControlPointsValidation } | null>(null);
  const [confirmed, setConfirmed] = useState(false);

  const built = useMemo(
    () => buildRequest(rows, crs, source, reason, operation, acknowledged),
    [rows, crs, source, reason, operation, acknowledged],
  );
  const headings = coordinateHeadings(crs?.axis_order);
  const key = built.request ? JSON.stringify(built.request) : null;
  const stale = reviewed !== null && reviewed.key !== key;
  const result = reviewed && !stale ? reviewed.result : null;

  const check = useMutation({
    mutationFn: () => validateControlPoints(asset.id, built.request!),
    onSuccess: (data) => {
      setReviewed({ key: key!, result: data });
      setConfirmed(false);
    },
  });
  const record = useMutation({
    mutationFn: () =>
      recordControlPoints(asset.id, { ...built.request!, expected_version: asset.georeferencing?.version ?? 0, confirm: true }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["layers", projectId] });
      void queryClient.invalidateQueries({ queryKey: ["asset", asset.id] });
      void queryClient.invalidateQueries({ queryKey: ["assets", projectId] });
      void queryClient.invalidateQueries({ queryKey: ["georeferencing", asset.id] });
      void queryClient.invalidateQueries({ queryKey: ["map-sheet", asset.id] });
      void queryClient.invalidateQueries({ queryKey: ["crs-assessment", asset.id] });
    },
  });

  const edit = (id: number, patch: Partial<Row>) => setRows((current) => current.map((r) => (r.id === id ? { ...r, ...patch } : r)));
  const plan = result?.validation?.transformation_plan ?? null;
  const done = record.isSuccess;

  // A sheet that is placed needs no control points; once recorded here, the confirmation stays on screen.
  if (asset.georeferencing?.placed && !done) return null;

  if (done) {
    return (
      <Panel title="Georeference with control points">
        <div id="control-points">
          <Notice tone="success" title="Georeferencing recorded">
            <p className="text-xs">
              The sheet is placed from your control points. The fit, your points and their source are kept in the georeferencing history.
              The original file is unchanged.
            </p>
          </Notice>
        </div>
      </Panel>
    );
  }

  return (
    <Panel
      title="Georeference with control points"
      description="For a scanned sheet with no georeferencing. You supply pairs of a pixel position and a coordinate; the application fits the transform and shows how well the points agree."
    >
      <div className="space-y-4" id="control-points">
        <Notice tone="info" title="What this does and does not do">
          <p className="text-xs">
            The points, their CRS and their source are yours: the application does not find them on the sheet. One affine fit is made
            in the CRS you choose. If the sheet is printed in another projection, residuals will not be zero. Nothing is placed until
            you review the fit and confirm.
          </p>
        </Notice>
        <PointPicker
          asset={asset}
          rows={rows}
          active={active}
          onPick={(column, row) => edit(rows[active]!.id, { column: String(column), row: String(row) })}
        />
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <caption className="sr-only">Control points</caption>
            <thead>
              <tr className="text-fg-subtle">
                <th scope="col" className="py-1 pr-2 font-medium">#</th>
                <th scope="col" className="py-1 pr-2 font-medium">Label</th>
                <th scope="col" className="py-1 pr-2 font-medium">Pixel column</th>
                <th scope="col" className="py-1 pr-2 font-medium">Pixel row</th>
                <th scope="col" className="py-1 pr-2 font-medium">{headings.x}</th>
                <th scope="col" className="py-1 pr-2 font-medium">{headings.y}</th>
                <th scope="col" className="py-1 font-medium"><span className="sr-only">Actions</span></th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, index) => (
                <tr key={r.id} className={index === active ? "bg-accent-soft" : ""}>
                  <td className="py-1 pr-2">
                    <button type="button" className="text-accent hover:underline" aria-pressed={index === active} onClick={() => setActive(index)}>
                      {index + 1}
                    </button>
                  </td>
                  {(["label", "column", "row", "x", "y"] as const).map((field) => (
                    <td key={field} className="py-1 pr-2">
                      <input
                        className={`${inputClass} h-7 min-w-16 px-1.5 text-xs`}
                        aria-label={`Point ${index + 1} ${field === "label" ? "label" : field === "column" ? "pixel column" : field === "row" ? "pixel row" : field === "x" ? "x coordinate" : "y coordinate"}`}
                        value={r[field]}
                        inputMode={field === "label" ? "text" : "decimal"}
                        onFocus={() => setActive(index)}
                        onChange={(event) => edit(r.id, { [field]: event.target.value })}
                      />
                    </td>
                  ))}
                  <td className="py-1">
                    <button
                      type="button"
                      className="text-fg-subtle hover:text-danger disabled:opacity-40"
                      disabled={rows.length <= MIN_POINTS}
                      aria-label={`Remove point ${index + 1}`}
                      onClick={() => {
                        setRows((current) => current.filter((x) => x.id !== r.id));
                        setActive(0);
                      }}
                    >
                      Remove
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <Button
          size="sm"
          variant="secondary"
          onClick={() => {
            setRows((current) => [...current, blank()]);
            setActive(rows.length);
          }}
        >
          Add a point
        </Button>

        <Field label="CRS of the coordinates" htmlFor="crs-search" hint="Chosen from the local registry. It becomes the sheet's CRS if you record this.">
          <CrsPicker
            selected={crs}
            onSelect={(next) => {
              setCrs(next);
              setOperation(null);
              setAcknowledged(false);
            }}
          />
        </Field>
        <Field label="Where the coordinates come from" htmlFor="cp-source" hint="Required, for example: printed 1 km grid ticks read from the sheet.">
          <input id="cp-source" className={inputClass} value={source} maxLength={1000} onChange={(e) => setSource(e.target.value)} />
        </Field>
        <Field label="Reason" htmlFor="cp-reason" hint="Kept in the history with the points.">
          <input id="cp-reason" className={inputClass} value={reason} maxLength={1000} onChange={(e) => setReason(e.target.value)} />
        </Field>

        {built.problem ? <p className="text-xs text-fg-subtle">{built.problem}</p> : null}
        <div className="flex flex-wrap items-center gap-2">
          <Button disabled={!built.request || check.isPending} onClick={() => check.mutate()}>
            {check.isPending ? "Checking…" : result ? "Check again" : "Check the fit"}
          </Button>
          {stale ? <span className="text-xs text-warn">The points changed since the last check; check again before recording.</span> : null}
        </div>
        {check.isError ? <ErrorNotice error={check.error} title="The control points could not be checked" /> : null}

        {result ? (
          <div className="space-y-3 rounded-md border border-line bg-raised p-4" aria-label="Review the fit" data-testid="control-point-review">
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone={result.valid ? "ok" : "danger"}>{result.valid ? "Passes every check" : "Does not pass"}</Badge>
            </div>
            <Fit result={result} />
            <ul className="space-y-1" aria-label="Checks">
              {result.checks.map((c, index) => (
                <li key={`${c.code}-${index}`} className="flex gap-2 text-xs" data-testid="check">
                  <Badge tone={CHECK_TONES[c.status]}>{c.status}</Badge>
                  <span className="text-fg-muted">{c.message}</span>
                </li>
              ))}
            </ul>
            {plan ? (
              <div className="space-y-2 rounded-md border border-line p-3" aria-label="Datum transformation">
                <p className="text-[13px] font-medium text-fg">Datum transformation</p>
                <TransformationPlanView
                  plan={plan}
                  idPrefix="cp"
                  value={operation}
                  onChange={
                    plan.status === "selection_required"
                      ? (id) => {
                          setOperation(id);
                          setAcknowledged(false); // an acknowledgement belongs to one operation
                        }
                      : undefined
                  }
                />
                {plan.status === "selection_required" ? (
                  <p className="text-xs text-fg-subtle">Choose one, then check again. Nothing is chosen for you.</p>
                ) : null}
                {[...result.checks, ...(result.validation?.checks ?? [])].some((c) => c.code === ACK_REQUIRED && c.status === "failed") ? (
                  (() => {
                    const need = acknowledgementFromPlan(plan, operation);
                    return need ? (
                      <AccuracyAcknowledgement
                        need={need}
                        checked={acknowledged}
                        onChange={setAcknowledged}
                        idPrefix="cp"
                        where="It is recorded with this georeferencing version, with your name and the time. Tick it, then check the fit again."
                      />
                    ) : null;
                  })()
                ) : null}
              </div>
            ) : null}
            <label className="flex items-start gap-2 text-[13px] text-fg">
              <input
                type="checkbox"
                className="mt-0.5 accent-accent"
                checked={confirmed}
                disabled={!result.valid}
                onChange={(event) => setConfirmed(event.target.checked)}
              />
              <span>I have checked the points and the fit, and I want this recorded as the sheet's georeferencing.</span>
            </label>
            <Button disabled={!result.valid || !confirmed || record.isPending} onClick={() => record.mutate()}>
              {record.isPending ? "Recording…" : "Record georeferencing"}
            </Button>
            {record.isError ? (
              <ErrorNotice
                error={record.error}
                title={
                  record.error instanceof ApiError && record.error.code === "GEOREFERENCE_VERSION_CONFLICT"
                    ? "Someone else changed this georeferencing. Reload the page and check again."
                    : "The georeferencing was not recorded"
                }
              />
            ) : null}
          </div>
        ) : null}
      </div>
    </Panel>
  );
}
