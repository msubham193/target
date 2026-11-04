import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useEffect, useState } from "react";
import { Link, useLocation } from "react-router-dom";

import {
  ApiError,
  type Crs,
  type Georeferencing,
  type GeoreferencingRequest,
  type GeoreferenceValidation,
  getGeoreferencing,
  recordGeoreferencing,
  validateGeoreferencing,
} from "../api/client";
import { formatDateTime } from "../lib/format";
import { CrsPicker } from "./CrsPicker";
import { IconAlert, IconCheck, IconInfo } from "./icons";
import { acknowledgementFromPlan, ACK_REQUIRED } from "../lib/transformation";
import { AccuracyAcknowledgement } from "./AccuracyAcknowledgement";
import { TransformationPlanView } from "./TransformationPlan";
import { Badge, Button, DescriptionList, ErrorNotice, inputClass, Loading, muted, Notice, Panel } from "./ui";

type Operation = GeoreferencingRequest["operation"];
type Step = "edit" | "review" | "done";

const OPERATION_LABELS: Record<Operation, string> = {
  assign_crs: "Assign a CRS to the existing coordinates",
  set_geotransform: "Enter a geotransform",
  revert_to_file: "Use the file's own metadata again",
};
const METHOD_LABELS: Record<string, string> = {
  registry_crs_selected: "CRS chosen from the local registry",
  geotransform_entered: "Geotransform entered by hand",
  world_file_entered: "Geotransform read from a world file",
  file_metadata_restored: "File metadata restored",
  sidecar_geotransform: "Geotransform from a sidecar file, reviewed and confirmed",
  sidecar_crs: "CRS from a sidecar file, reviewed and confirmed",
};
// GDAL order, with the meaning of each number.
const GT_FIELDS = [
  "x of the top-left corner",
  "pixel width (x per column)",
  "row rotation (x per row)",
  "y of the top-left corner",
  "column rotation (y per column)",
  "pixel height (y per row, usually negative)",
];

export function formatGeotransform(values: number[] | null | undefined): string {
  return values ? values.map((v) => (Number.isInteger(v) ? String(v) : v.toPrecision(10).replace(/\.?0+$/, ""))).join(", ") : "none";
}

export function CheckList({ checks }: { checks: GeoreferenceValidation["checks"] }) {
  return (
    <ul className="space-y-1.5" aria-label="Validation checks">
      {checks.map((check, index) => {
        const Icon = check.status === "ok" ? IconCheck : check.status === "warning" ? IconInfo : IconAlert;
        const tone = check.status === "ok" ? "text-ok" : check.status === "warning" ? "text-warn" : "text-danger";
        return (
          <li key={`${check.code}-${index}`} className="flex gap-2 text-xs">
            <Icon size={14} className={`mt-0.5 shrink-0 ${tone}`} />
            <span>
              <span className={`font-medium ${tone}`}>{check.status === "ok" ? "Passed" : check.status === "warning" ? "Warning" : "Failed"}</span>
              <span className="text-fg-muted"> · {check.message}</span>
            </span>
          </li>
        );
      })}
    </ul>
  );
}

/** Where the raster's corners and centre would be with a proposed change: pixel, native and lon/lat. */
export function CornersTable({ corners }: { corners: GeoreferenceValidation["corners"] }) {
  if (corners.length === 0) return null;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-xs">
        <caption className="mb-1 text-left text-fg-muted">Where the raster's pixels would be</caption>
        <thead>
          <tr className="text-fg-subtle">
            <th scope="col" className="py-1 pr-3 font-medium">Point</th>
            <th scope="col" className="py-1 pr-3 font-medium">Pixel (column, row)</th>
            <th scope="col" className="py-1 pr-3 font-medium">Coordinates in the CRS</th>
            <th scope="col" className="py-1 font-medium">Longitude, latitude</th>
          </tr>
        </thead>
        <tbody className="font-mono">
          {corners.map((corner) => {
            const [col, row] = corner.pixel as number[];
            const [x, y] = corner.native as number[];
            const [lon, lat] = corner.lon_lat as number[];
            return (
              <tr key={String(corner.point)} className="border-t border-line">
                <td className="py-1 pr-3 font-sans text-fg-muted">{String(corner.point)}</td>
                <td className="py-1 pr-3">{col}, {row}</td>
                <td className="py-1 pr-3">{x!.toFixed(3)}, {y!.toFixed(3)}</td>
                <td className="py-1">{lon!.toFixed(6)}, {lat!.toFixed(6)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function StatusBadge({ data }: { data: Georeferencing }) {
  if (!data.current.georeferenced) return <Badge tone="warn">Not placed</Badge>;
  return data.current.source === "recorded" ? (
    <Badge tone="accent">Placed from recorded georeferencing</Badge>
  ) : (
    <Badge tone="ok">Placed from the file's metadata</Badge>
  );
}

function History({ history }: { history: Georeferencing["history"] }) {
  if (history.length === 0) return <p className="text-xs text-fg-subtle">No georeferencing has been recorded for this raster.</p>;
  return (
    <ol className="space-y-3" aria-label="Georeferencing history">
      {[...history].reverse().map((version) => {
        const warnings = ((version.validation.checks as { status: string; message: string }[] | undefined) ?? []).filter((c) => c.status === "warning");
        return (
          <li key={version.version_no} className="border-l-2 border-line pl-3 text-xs">
            <p className="text-[13px] text-fg">
              <span className="text-fg-subtle">Version {version.version_no}</span> · {OPERATION_LABELS[version.operation]}
            </p>
            <p className="text-fg-muted">{METHOD_LABELS[version.method] ?? version.method}</p>
            <p className="text-fg-muted">
              CRS: {String(version.previous.crs_id ?? "none")} → {String(version.result.crs_id ?? "none")}
            </p>
            <p className="font-mono text-[11px] text-fg-muted">
              Geotransform: {formatGeotransform(version.previous.geotransform as number[] | null)} →{" "}
              {formatGeotransform(version.result.geotransform as number[] | null)}
            </p>
            <p className="text-fg-muted">Placed: {version.previous.georeferenced ? "yes" : "no"} → {version.georeferenced ? "yes" : "no"}</p>
            {version.geotransform_source ? <p className="text-fg-muted">Source of the numbers: {version.geotransform_source}</p> : null}
            {Array.isArray(version.validation.sidecars) ? (
              <p className="text-fg-muted">
                Sidecar files used:{" "}
                {(version.validation.sidecars as { filename: string; sha256: string }[])
                  .map((s) => `${s.filename} (SHA-256 ${s.sha256.slice(0, 12)}…)`)
                  .join(", ")}
              </p>
            ) : null}
            <p className="whitespace-pre-wrap text-fg-muted">Reason: “{version.reason}”</p>
            {warnings.length > 0 ? (
              <ul className="mt-1 space-y-0.5 text-warn">
                {warnings.map((w) => (
                  <li key={w.message}>Warning at the time: {w.message}</li>
                ))}
              </ul>
            ) : null}
            <p className="text-fg-subtle">
              {version.actor_label} · {formatDateTime(version.created_at)} · original SHA-256 {version.source_checksum.slice(0, 12)}…
            </p>
          </li>
        );
      })}
    </ol>
  );
}

/**
 * Recording a CRS or a geotransform for a raster whose own metadata is missing or invalid. The two are separate:
 * a CRS says what existing coordinates mean; a geotransform gives the pixels coordinates. Every proposal is checked
 * by the API and reviewed before it is recorded; the file itself is never changed.
 */
export function GeoreferencingPanel({ assetId, projectId }: { assetId: string; projectId: string }) {
  const queryClient = useQueryClient();
  const state = useQuery({ queryKey: ["georeferencing", assetId], queryFn: () => getGeoreferencing(assetId) });
  const [operation, setOperation] = useState<Operation | null>(null);
  const [step, setStep] = useState<Step>("edit");
  const [crs, setCrs] = useState<Crs | null>(null);
  const [mode, setMode] = useState<"numbers" | "world">("numbers");
  const [numbers, setNumbers] = useState<string[]>(["", "", "", "", "", ""]);
  const [worldFile, setWorldFile] = useState("");
  const [source, setSource] = useState("");
  const [reason, setReason] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  // Which registered datum transformation to use, when several apply. Chosen by a person; never defaulted.
  const [transformationOperation, setTransformationOperation] = useState<string | null>(null);
  // The person's acknowledgement of a datum shift worse than 10 m (or with no stated accuracy). It is made for what the
  // API refused, recorded with the version it is saved in, and cleared whenever the values or the choice change.
  const [acknowledged, setAcknowledged] = useState(false);
  const [review, setReview] = useState<GeoreferenceValidation | null>(null);
  const location = useLocation();
  const loaded = state.isSuccess;
  // Links from the map and the satellite screen point here (#georeferencing).
  useEffect(() => {
    if (loaded && location.hash === "#georeferencing") document.getElementById("georeferencing")?.scrollIntoView?.({ block: "start" });
  }, [loaded, location.hash]);

  const request = (): GeoreferencingRequest => {
    const base: GeoreferencingRequest = {
      operation: operation!,
      reason,
      acknowledge_transformation: acknowledged,
      ...(transformationOperation ? { transformation_operation: transformationOperation } : {}),
    };
    if (operation === "assign_crs") return { ...base, crs_id: crs?.identifier ?? null };
    if (operation === "set_geotransform") {
      return {
        ...base,
        crs_id: crs?.identifier ?? null,
        geotransform: mode === "numbers" ? numbers.map((n) => Number(n)) : null,
        world_file: mode === "world" ? worldFile : null,
        geotransform_source: source,
      };
    }
    return base;
  };

  const check = useMutation({
    mutationFn: () => validateGeoreferencing(assetId, request()),
    onSuccess: (result) => {
      setReview(result);
      setConfirmed(false);
      setStep("review");
    },
  });
  const save = useMutation({
    mutationFn: (current: Georeferencing) =>
      recordGeoreferencing(assetId, { ...request(), expected_version: current.current.version, confirm: confirmed }),
    onSuccess: (updated) => {
      queryClient.setQueryData(["georeferencing", assetId], updated);
      void queryClient.invalidateQueries({ queryKey: ["layers", projectId] });
      void queryClient.invalidateQueries({ queryKey: ["asset", assetId] });
      void queryClient.invalidateQueries({ queryKey: ["assets", projectId] });
      void queryClient.invalidateQueries({ queryKey: ["sidecars", assetId] });
      setStep("done");
    },
    onError: (error) => {
      if (error instanceof ApiError && error.code === "GEOREFERENCE_VERSION_CONFLICT") void state.refetch();
    },
  });

  const start = (next: Operation) => {
    setOperation(next);
    setStep("edit");
    setReview(null);
    setCrs(null);
    setReason("");
    setSource("");
    setConfirmed(false);
    setTransformationOperation(null);
    setAcknowledged(false);
    check.reset();
    save.reset();
  };

  if (state.isLoading) return <Loading label="Loading georeferencing…" />;
  if (state.isError) return <ErrorNotice error={state.error} />;
  const data = state.data!;
  const { file, current, options } = data;
  const editable = options.assign_crs || options.set_geotransform || options.revert_to_file;
  const numbersComplete = numbers.every((n) => n.trim() !== "" && Number.isFinite(Number(n)));
  const canCheck =
    reason.trim() !== "" &&
    (operation === "revert_to_file" ||
      (operation === "assign_crs" && crs !== null) ||
      (operation === "set_geotransform" &&
        source.trim() !== "" &&
        (mode === "numbers" ? numbersComplete : worldFile.trim() !== "") &&
        (!options.crs_required_with_geotransform || crs !== null)));

  const submitCheck = (event: FormEvent) => {
    event.preventDefault();
    check.mutate();
  };

  return (
    <Panel
      title="Spatial placement"
      description="How this raster's pixels relate to places on the Earth, and whether it can be put on the map."
      actions={<StatusBadge data={data} />}
    >
      <div className="space-y-5" id="georeferencing">
        {data.unplaced_reason ? (
          <Notice tone="warning" title="Why this raster is not on the map">
            {data.unplaced_reason}
          </Notice>
        ) : current.source === "recorded" ? (
          <Notice title="Placed from georeferencing recorded by a person">
            The CRS and geotransform in use were recorded in version {current.version}, not read from the file. A valid CRS
            and geotransform make placement possible; they do not show that the location is correct.{" "}
            <Link className="text-accent hover:underline" to={`/projects/${projectId}/map`}>
              Open the map
            </Link>
          </Notice>
        ) : null}

        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <section aria-label="What the file declares">
            <p className="mb-1.5 text-xs font-medium text-fg-muted">What the file declares (never changed)</p>
            <DescriptionList
              labelWidth="md"
              rows={[
                ["CRS", file.crs_status === "missing" ? "missing" : `${file.crs_identifier ?? file.crs_name ?? "unnamed"} (${file.crs_status})`],
                [
                  "Geotransform",
                  file.geotransform ? (
                    <span className="font-mono text-xs">{formatGeotransform(file.geotransform)}</span>
                  ) : (
                    `missing${file.geotransform_problem ? ` — ${file.geotransform_problem}` : ""}`
                  ),
                ],
                ["Ground control points", file.ground_control_points ? `${file.ground_control_points}, not used` : "none"],
                ["Size", `${data.width} × ${data.height} pixels`],
              ]}
            />
          </section>
          <section aria-label="In use now">
            <p className="mb-1.5 text-xs font-medium text-fg-muted">In use now (version {current.version})</p>
            <DescriptionList
              labelWidth="md"
              rows={[
                ["From", current.source === "recorded" ? "recorded georeferencing" : "the file"],
                ["CRS", current.crs_usable ? `${current.crs_id ?? ""} ${current.crs_name ?? ""}`.trim() : muted],
                [
                  "Geotransform",
                  current.geotransform_usable ? <span className="font-mono text-xs">{formatGeotransform(current.geotransform)}</span> : muted,
                ],
                ["Placed", current.georeferenced ? "yes" : "no"],
              ]}
            />
          </section>
        </div>

        {editable ? (
          <div className="space-y-2 border-t border-line pt-4">
            <p className="text-xs font-medium text-fg-muted">Record georeferencing</p>
            <div className="flex flex-wrap gap-2" role="group" aria-label="Georeferencing operations">
              {(["assign_crs", "set_geotransform", "revert_to_file"] as const).map((name) => {
                const available = name === "assign_crs" ? options.assign_crs : name === "set_geotransform" ? options.set_geotransform : options.revert_to_file;
                if (name === "revert_to_file" && !available) return null;
                return (
                  <Button key={name} size="sm" variant={operation === name ? "primary" : "secondary"} aria-pressed={operation === name} disabled={!available} onClick={() => start(name)}>
                    {OPERATION_LABELS[name]}
                  </Button>
                );
              })}
            </div>
            {!options.assign_crs ? <p className="text-xs text-fg-subtle">{options.assign_crs_reason}</p> : null}
          </div>
        ) : (
          <p className="border-t border-line pt-4 text-xs text-fg-subtle">{options.assign_crs_reason}</p>
        )}

        {operation && step === "edit" ? (
          <form onSubmit={submitCheck} className="space-y-4 rounded-md border border-line bg-raised p-4" aria-label={OPERATION_LABELS[operation]}>
            {operation === "assign_crs" ? (
              <Notice title="What assigning a CRS does">
                It states which coordinate reference system the raster's existing coordinates are in. The coordinates
                ({formatGeotransform(current.geotransform)}) are kept exactly; no pixel is moved, reprojected or
                resampled, and the file is not changed. It does not show that the chosen CRS is the right one.
              </Notice>
            ) : null}
            {operation === "set_geotransform" ? (
              <Notice title="What entering a geotransform does">
                It gives every pixel coordinates in a CRS, using six numbers from a documented source such as the
                supplier's world file. The numbers are used as entered: nothing is estimated, fitted or adjusted, and the
                file is not changed. A CRS on its own never places a raster.
              </Notice>
            ) : null}
            {operation === "revert_to_file" ? (
              <Notice title="What this does">
                The raster goes back to the metadata in its file. If that is missing or invalid, it is no longer placed.
                Earlier versions remain in the history.
              </Notice>
            ) : null}

            {operation === "set_geotransform" ? (
              <fieldset className="space-y-3">
                <legend className="text-[13px] font-medium text-fg">Geotransform</legend>
                <div className="flex gap-3 text-xs">
                  <label className="flex items-center gap-1.5">
                    <input type="radio" name="gt-mode" className="accent-accent" checked={mode === "numbers"} onChange={() => setMode("numbers")} />
                    Six numbers (GDAL order)
                  </label>
                  <label className="flex items-center gap-1.5">
                    <input type="radio" name="gt-mode" className="accent-accent" checked={mode === "world"} onChange={() => setMode("world")} />
                    World file (.tfw, .wld)
                  </label>
                </div>
                {mode === "numbers" ? (
                  <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                    {GT_FIELDS.map((label, index) => (
                      <div key={label}>
                        <label htmlFor={`gt-${index}`} className="mb-1 block text-xs text-fg-muted">
                          {index + 1}. {label}
                        </label>
                        <input
                          id={`gt-${index}`}
                          inputMode="decimal"
                          className={`${inputClass} font-mono`}
                          value={numbers[index]}
                          onChange={(event) => setNumbers((current) => current.map((v, i) => (i === index ? event.target.value : v)))}
                        />
                      </div>
                    ))}
                    <p className="text-[11px] text-fg-subtle sm:col-span-2">
                      The origin is the top-left corner of the top-left pixel. Rotation terms are used in full; enter 0 for
                      a north-up image.
                    </p>
                  </div>
                ) : (
                  <div>
                    <label htmlFor="world-file" className="mb-1 block text-xs text-fg-muted">
                      World file contents (six lines: A, D, B, E, C, F)
                    </label>
                    <textarea id="world-file" className={`${inputClass} min-h-28 py-1.5 font-mono`} value={worldFile} onChange={(e) => setWorldFile(e.target.value)} />
                    <p className="text-[11px] text-fg-subtle">A world file gives the centre of the top-left pixel; it is converted to the corner.</p>
                  </div>
                )}
                <div>
                  <label htmlFor="gt-source" className="mb-1 block text-xs font-medium text-fg">
                    Where these numbers come from (required)
                  </label>
                  <input id="gt-source" className={inputClass} value={source} maxLength={500} onChange={(e) => setSource(e.target.value)} />
                </div>
              </fieldset>
            ) : null}

            {operation === "assign_crs" || operation === "set_geotransform" ? (
              <div className="space-y-1.5">
                <label htmlFor="crs-search" className="block text-[13px] font-medium text-fg">
                  {operation === "assign_crs"
                    ? "CRS of the existing coordinates"
                    : options.crs_required_with_geotransform
                      ? "CRS of these coordinates (required)"
                      : `CRS of these coordinates (leave empty to keep ${current.crs_id ?? current.crs_name})`}
                </label>
                <CrsPicker selected={crs} onSelect={setCrs} />
              </div>
            ) : null}

            <div>
              <label htmlFor="georef-reason" className="mb-1 block text-[13px] font-medium text-fg">
                Reason (required)
              </label>
              <textarea id="georef-reason" className={`${inputClass} min-h-16 py-1.5`} value={reason} maxLength={2000} onChange={(e) => setReason(e.target.value)} />
              <p className="mt-1 text-[11px] text-fg-subtle">Say how you know. It is kept in the history with your profile name.</p>
            </div>
            {check.isError ? <ErrorNotice error={check.error} /> : null}
            <div className="flex gap-2">
              <Button type="submit" size="sm" disabled={!canCheck || check.isPending}>
                {check.isPending ? "Checking…" : "Check this change"}
              </Button>
              <Button type="button" size="sm" variant="ghost" onClick={() => setOperation(null)}>
                Cancel
              </Button>
            </div>
          </form>
        ) : null}

        {operation && step === "review" && review ? (
          <section aria-label="Review the proposed change" className="space-y-4 rounded-md border border-line bg-raised p-4">
            <div className="flex flex-wrap items-center gap-2">
              <p className="text-[13px] font-medium text-fg">{OPERATION_LABELS[operation]}</p>
              {review.valid ? <Badge tone="ok">Passed validation</Badge> : <Badge tone="danger">Did not pass validation</Badge>}
            </div>
            <p className="text-xs text-fg-muted">{review.explanation}</p>
            <CheckList checks={review.checks} />
            {review.transformation_plan ? (
              <div className="space-y-2 rounded-md border border-line p-3" aria-label="Datum transformation" data-testid="georeference-transformation">
                <p className="text-[13px] font-medium text-fg">Datum transformation</p>
                <TransformationPlanView
                  plan={review.transformation_plan}
                  idPrefix="georef"
                  value={transformationOperation}
                  onChange={
                    review.transformation_plan.status === "selection_required"
                      ? (id) => {
                          setTransformationOperation(id);
                          setAcknowledged(false); // an acknowledgement belongs to one operation
                        }
                      : undefined
                  }
                />
                {review.transformation_plan.status === "selection_required" ? (
                  <Button size="sm" disabled={!transformationOperation || check.isPending} onClick={() => check.mutate()}>
                    {check.isPending ? "Checking…" : "Check again with this choice"}
                  </Button>
                ) : null}
                {review.checks.some((c) => c.code === ACK_REQUIRED && c.status === "failed") &&
                review.transformation_plan.status !== "selection_required" ? (
                  <div className="space-y-2">
                    {(() => {
                      const need = acknowledgementFromPlan(review.transformation_plan, transformationOperation);
                      return need ? (
                        <AccuracyAcknowledgement
                          need={need}
                          checked={acknowledged}
                          onChange={setAcknowledged}
                          idPrefix="georef"
                          where="It is recorded with this georeferencing version, with your name and the time, and cannot be changed afterwards."
                        />
                      ) : null;
                    })()}
                    <Button size="sm" disabled={!acknowledged || check.isPending} onClick={() => check.mutate()}>
                      {check.isPending ? "Checking…" : "Check again with this acknowledgement"}
                    </Button>
                  </div>
                ) : null}
              </div>
            ) : null}
            {review.proposed ? (
              <DescriptionList
                labelWidth="md"
                rows={[
                  ["CRS", `${review.proposed.crs_id ?? ""} ${review.proposed.crs_name ?? ""}`.trim() || muted],
                  ["Geotransform", <span className="font-mono text-xs">{formatGeotransform(review.proposed.geotransform)}</span>],
                  ["Pixel size", review.proposed.pixel_size ? `${review.proposed.pixel_size.map((v) => v.toPrecision(4)).join(" × ")} ${review.proposed.pixel_units ?? ""}` : muted],
                  ["Placed after the change", review.proposed.georeferenced ? "yes" : "no"],
                ]}
              />
            ) : null}
            <CornersTable corners={review.corners} />
            {review.valid ? (
              <label className="flex items-start gap-2 text-xs text-fg">
                <input type="checkbox" className="mt-0.5 accent-accent" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} />
                I have checked these values and their source. I understand that recording them does not show that the
                raster's location is correct.
              </label>
            ) : (
              <p className="text-xs text-danger">Nothing can be recorded until every failed check is resolved. The raster keeps its current state.</p>
            )}
            {save.isError ? (
              save.error instanceof ApiError && save.error.code === "GEOREFERENCE_VERSION_CONFLICT" ? (
                <Notice tone="error" title="Someone else changed this raster's georeferencing">
                  {save.error.detail} The latest version is now shown above; check it before trying again.
                </Notice>
              ) : (
                <ErrorNotice error={save.error} />
              )
            ) : null}
            <div className="flex gap-2">
              {review.valid ? (
                <Button size="sm" disabled={!confirmed || save.isPending} onClick={() => save.mutate(data)}>
                  {save.isPending ? "Recording…" : `Record as version ${current.version + 1}`}
                </Button>
              ) : null}
              <Button
                size="sm"
                variant="secondary"
                onClick={() => {
                  setAcknowledged(false); // changed values need their own check, and their own acknowledgement
                  setStep("edit");
                }}
              >
                Back to the values
              </Button>
            </div>
          </section>
        ) : null}

        {step === "done" ? (
          <Notice tone="success" title={`Recorded as version ${current.version}`}>
            {current.georeferenced ? (
              <>
                The raster is placed using this version.{" "}
                <Link className="text-accent hover:underline" to={`/projects/${projectId}/map`}>
                  Open the map
                </Link>
              </>
            ) : (
              "The raster is not placed with this version."
            )}
          </Notice>
        ) : null}

        <div className="border-t border-line pt-4">
          <p className="mb-2 text-xs font-medium text-fg-muted">History</p>
          <History history={data.history} />
        </div>
        <p className="text-[11px] text-fg-subtle">{data.note} Changes are attributed to your local profile, which is not a login.</p>
      </div>
    </Panel>
  );
}
