import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link, useLocation } from "react-router-dom";

import {
  ApiError,
  applySidecars,
  getSidecars,
  type SidecarCheck,
  type SidecarItem,
  type SidecarReview,
  uploadSidecar,
  validateSidecars,
} from "../api/client";
import { formatDateTime } from "../lib/format";
import { formatBytes } from "../lib/ingestion";
import { CheckList, CornersTable, formatGeotransform } from "./GeoreferencingPanel";
import { Badge, Button, DescriptionList, ErrorNotice, inputClass, Loading, muted, Notice, Panel, type Tone } from "./ui";

const RELATION: Record<string, { label: string; tone: Tone }> = {
  same: { label: "same", tone: "ok" },
  differs: { label: "differs", tone: "warn" },
  none: { label: "none to compare", tone: "neutral" },
  unresolved: { label: "not resolved", tone: "danger" },
};
const STATUS: Record<SidecarItem["status"], { label: string; tone: Tone }> = {
  parsed: { label: "Read", tone: "ok" },
  malformed: { label: "Could not be read", tone: "danger" },
  unsupported: { label: "Nothing usable", tone: "warn" },
};
const STATE_TONE: Record<SidecarReview["state"], "info" | "warning" | "success"> = {
  none: "info",
  file_valid: "info",
  proposal: "info",
  conflict: "warning",
  insufficient: "warning",
  in_use: "success",
};
const OPERATION: Record<string, string> = {
  assign_crs: "Assign a CRS to the raster's existing coordinates",
  set_geotransform: "Give the raster's pixels coordinates (a geotransform)",
};

function show(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  return String(value);
}

function Comparisons({ item }: { item: SidecarItem }) {
  if (item.comparisons.length === 0) return null;
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[36rem] text-left text-xs" aria-label={`How ${item.filename} compares with the raster`}>
        <thead>
          <tr className="text-fg-subtle">
            <th scope="col" className="py-1 pr-3 font-medium">Field</th>
            <th scope="col" className="py-1 pr-3 font-medium">This file says</th>
            <th scope="col" className="py-1 pr-3 font-medium">The raster file says</th>
            <th scope="col" className="py-1 font-medium">In use now</th>
          </tr>
        </thead>
        <tbody>
          {item.comparisons.map((c) => (
            <tr key={c.field} className="border-t border-line align-top">
              <td className="py-1 pr-3 text-fg-muted">
                {c.field}
                {c.applied === false ? <span className="block text-[11px] text-fg-subtle">shown only, not applied</span> : null}
              </td>
              <td className="py-1 pr-3 font-mono wrap-anywhere">{show(c.sidecar)}</td>
              <td className="py-1 pr-3">
                <span className="font-mono wrap-anywhere">{show(c.file)}</span>{" "}
                <Badge tone={RELATION[c.relation_file]!.tone}>{RELATION[c.relation_file]!.label}</Badge>
              </td>
              <td className="py-1">
                <span className="font-mono wrap-anywhere">{show(c.in_use)}</span>{" "}
                <Badge tone={RELATION[c.relation_in_use]!.tone}>{RELATION[c.relation_in_use]!.label}</Badge>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function SidecarCard({ item }: { item: SidecarItem }) {
  const status = STATUS[item.status];
  const crs = (item.values.crs ?? null) as { name?: string; identifier?: string; registry_matches?: { identifier: string; confidence: number }[] } | null;
  return (
    <li className="space-y-2 rounded-md border border-line bg-raised p-3 text-xs" aria-label={item.filename}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="wrap-anywhere text-[13px] font-medium text-fg">{item.filename}</span>
        <span className="text-fg-subtle">{item.kind_label}</span>
        <Badge tone={status.tone}>{status.label}</Badge>
        {item.current ? null : <Badge tone="neutral">Superseded</Badge>}
        {item.pairing === "explicit" ? <Badge tone="warn">Attached by hand; names differ</Badge> : null}
      </div>
      <p className="text-fg-muted">{item.summary}</p>
      <p className="text-fg">{item.role}</p>
      {crs?.name ? (
        <p className="text-fg-muted">
          CRS definition: {crs.name}
          {crs.identifier ? ` → ${crs.identifier}` : ""}
          {!crs.identifier && crs.registry_matches?.length ? ` (nearest registry entries: ${crs.registry_matches.map((m) => `${m.identifier} ${m.confidence}%`).join(", ")})` : ""}
        </p>
      ) : null}
      {item.problems.length > 0 ? (
        <ul className="space-y-0.5 text-danger" aria-label={`Problems in ${item.filename}`}>
          {item.problems.map((p) => (
            <li key={p}>{p}</li>
          ))}
        </ul>
      ) : null}
      {item.ignored.length > 0 ? (
        <details>
          <summary className="cursor-pointer text-fg-muted">Present in the file, not used ({item.ignored.length})</summary>
          <ul className="mt-1 list-disc space-y-0.5 pl-4 text-fg-subtle">
            {item.ignored.map((p) => (
              <li key={p}>{p}</li>
            ))}
          </ul>
        </details>
      ) : null}
      <Comparisons item={item} />
      <p className="text-[11px] text-fg-subtle">
        {formatBytes(item.size_bytes)} · SHA-256 <span className="font-mono">{item.sha256.slice(0, 16)}…</span> · added by {item.uploaded_by},{" "}
        {formatDateTime(item.uploaded_at)} · kept unmodified
      </p>
    </li>
  );
}

/**
 * Sidecar metadata files (.tfw/.wld world files, .prj, .aux.xml) for one raster. They are kept as received and
 * never applied on their own: this shows what each says and how it compares with the raster; a change is recorded
 * only after a check, a reason and a confirmation, as a georeferencing version like any other.
 */
export function SidecarPanel({ assetId, projectId }: { assetId: string; projectId: string }) {
  const queryClient = useQueryClient();
  const sidecars = useQuery({ queryKey: ["sidecars", assetId], queryFn: () => getSidecars(assetId) });
  const [reason, setReason] = useState("");
  const [gtFrom, setGtFrom] = useState<string | null>(null);
  const [crsFrom, setCrsFrom] = useState<string | null>(null);
  const [checked, setChecked] = useState<SidecarCheck | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [recorded, setRecorded] = useState<number | null>(null);
  const location = useLocation();
  const loaded = sidecars.isSuccess;
  useEffect(() => {
    if (loaded && location.hash === "#sidecars") document.getElementById("sidecars")?.scrollIntoView?.({ block: "start" });
  }, [loaded, location.hash]);

  const resetReview = () => {
    setChecked(null);
    setConfirmed(false);
  };
  const choice = () => ({ geotransform_from: gtFrom, crs_from: crsFrom });

  const attach = useMutation({
    mutationFn: (file: File) => uploadSidecar(projectId, file, assetId),
    onSuccess: () => {
      resetReview();
      setRecorded(null);
      void queryClient.invalidateQueries({ queryKey: ["sidecars", assetId] });
    },
  });
  const check = useMutation({
    mutationFn: () => validateSidecars(assetId, { reason, ...choice() }),
    onSuccess: (result) => {
      setChecked(result);
      setConfirmed(false);
    },
  });
  const save = useMutation({
    mutationFn: (review: SidecarReview) =>
      applySidecars(assetId, {
        reason,
        ...choice(),
        expected_version: review.expected_version,
        sidecar_ids: review.sidecar_ids,
        confirm: confirmed,
      }),
    onSuccess: (updated) => {
      queryClient.setQueryData(["georeferencing", assetId], updated);
      setRecorded(updated.current.version);
      resetReview();
      setReason("");
      for (const key of [["sidecars", assetId], ["layers", projectId], ["asset", assetId], ["assets", projectId], ["georeferencing", assetId]])
        void queryClient.invalidateQueries({ queryKey: key });
    },
    onError: (error) => {
      if (error instanceof ApiError && ["GEOREFERENCE_VERSION_CONFLICT", "SIDECARS_CHANGED", "SIDECAR_CHANGED"].includes(error.code)) {
        resetReview();
        void sidecars.refetch();
        void queryClient.invalidateQueries({ queryKey: ["georeferencing", assetId] });
      }
    },
  });

  if (sidecars.isLoading) return <Loading label="Loading sidecar files…" />;
  if (sidecars.isError) return <ErrorNotice error={sidecars.error} />;
  const data = sidecars.data!;
  // A review is based on the state when it was checked; show the checked one while it is current.
  const review = checked?.review ?? data;
  const proposal = review.proposal;
  const sources = (proposal?.sources ?? {}) as Record<string, { from?: string; filename?: string | null } | undefined>;
  const choices = Object.entries(review.choices);
  const needsChoice = choices.length > 0;

  return (
    <Panel
      title="Sidecar files"
      description="World files, .prj and .aux.xml files that came with this raster. Kept as received; never applied on their own."
      actions={data.sidecars.length > 0 ? <Badge tone="neutral">{data.sidecars.length} file{data.sidecars.length === 1 ? "" : "s"}</Badge> : undefined}
    >
      <div className="space-y-4" id="sidecars">
        {data.state === "none" ? (
          <p className="text-xs text-fg-subtle">{data.explanation.join(" ")}</p>
        ) : (
          <Notice tone={STATE_TONE[review.state]} title={review.title}>
            {review.explanation.map((line) => (
              <p key={line}>{line}</p>
            ))}
          </Notice>
        )}
        {review.conflicts.length > 0 ? (
          <Notice tone="warning" title="Disagreements with the raster file">
            <ul className="list-disc space-y-0.5 pl-4">
              {review.conflicts.map((c) => (
                <li key={c}>{c} The raster file's value is kept.</li>
              ))}
            </ul>
          </Notice>
        ) : null}

        {data.sidecars.length > 0 ? (
          <ul className="space-y-2" aria-label="Sidecar files">
            {data.sidecars.map((item) => (
              <SidecarCard key={item.id} item={item} />
            ))}
          </ul>
        ) : null}

        <div>
          <label htmlFor="sidecar-file" className="mb-1 block text-xs font-medium text-fg">
            Add a sidecar file for this raster
          </label>
          <input
            id="sidecar-file"
            type="file"
            className="block text-xs text-fg-muted file:mr-3 file:rounded-md file:border file:border-line file:bg-raised file:px-3 file:py-1.5 file:text-fg"
            accept={[...data.accepted_extensions, ".xml"].join(",")}
            disabled={attach.isPending}
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) attach.mutate(file);
              event.target.value = "";
            }}
          />
          <p className="mt-1 text-[11px] text-fg-subtle">
            {data.accepted_extensions.join(", ")}. Files added here belong to this raster even if their names differ. Usually they are added
            together with the raster on the{" "}
            <Link className="text-accent hover:underline" to={`/projects/${projectId}/assets/new`}>
              upload page
            </Link>
            .
          </p>
          {attach.isSuccess ? <p role="status" className="mt-1 text-xs text-fg-muted">{attach.data.message}</p> : null}
          {attach.isError ? <div className="mt-2"><ErrorNotice error={attach.error} /></div> : null}
        </div>

        {needsChoice ? (
          <fieldset className="space-y-3 rounded-md border border-line bg-raised p-4" aria-label="Choose between disagreeing sidecar files">
            <legend className="px-1 text-[13px] font-medium text-fg">Choose which file to review</legend>
            {choices.map(([kind, options]) => (
              <div key={kind} className="space-y-1 text-xs" role="radiogroup" aria-label={`${kind === "crs" ? "CRS" : "Geotransform"} from`}>
                <p className="text-fg-muted">{kind === "crs" ? "CRS" : "Geotransform"} from:</p>
                {options.map((option) => (
                  <label key={option.sidecar_id} className="flex items-start gap-2">
                    <input
                      type="radio"
                      name={`choice-${kind}`}
                      className="mt-0.5 accent-accent"
                      checked={(kind === "crs" ? crsFrom : gtFrom) === option.sidecar_id}
                      onChange={() => {
                        (kind === "crs" ? setCrsFrom : setGtFrom)(option.sidecar_id);
                        resetReview();
                      }}
                    />
                    <span>
                      {option.filename} <span className="font-mono text-fg-muted">{option.value}</span>
                    </span>
                  </label>
                ))}
              </div>
            ))}
            <p className="text-[11px] text-fg-subtle">Choosing does not change the files; the other file stays recorded and unused.</p>
          </fieldset>
        ) : null}

        {proposal || needsChoice ? (
          <section aria-label="Review the change from sidecar files" className="space-y-4 rounded-md border border-line bg-raised p-4">
            {proposal ? (
              <>
                <p className="text-[13px] font-medium text-fg">{OPERATION[proposal.operation]}</p>
                <DescriptionList
                  labelWidth="md"
                  rows={[
                    ["CRS", proposal.crs_id ?? (sources.crs?.from === "in_use" ? "the CRS in use is kept" : sources.crs?.from === "file" ? "the raster file's CRS is kept" : muted)],
                    [
                      "Geotransform",
                      proposal.operation === "assign_crs" ? (
                        "the raster file's coordinates are kept exactly"
                      ) : (
                        <span className="font-mono text-xs">{formatGeotransform(proposal.geotransform)}</span>
                      ),
                    ],
                    ["From", [sources.geotransform?.filename, sources.crs?.filename].filter(Boolean).filter((v, i, a) => a.indexOf(v) === i).join(" and ") || "—"],
                  ]}
                />
              </>
            ) : null}
            <div>
              <label htmlFor="sidecar-reason" className="mb-1 block text-[13px] font-medium text-fg">
                Reason (required)
              </label>
              <textarea
                id="sidecar-reason"
                className={`${inputClass} min-h-16 py-1.5`}
                value={reason}
                maxLength={2000}
                onChange={(e) => {
                  setReason(e.target.value);
                  resetReview();
                }}
              />
              <p className="mt-1 text-[11px] text-fg-subtle">Say where the sidecar files come from and why they belong to this raster. Kept in the history.</p>
            </div>
            {check.isError ? <ErrorNotice error={check.error} /> : null}
            <Button size="sm" disabled={reason.trim() === "" || check.isPending || (needsChoice && (choices.some(([k]) => (k === "crs" ? !crsFrom : !gtFrom))))} onClick={() => check.mutate()}>
              {check.isPending ? "Checking…" : "Check this change"}
            </Button>

            {checked?.validation ? (
              <div className="space-y-3 border-t border-line pt-3">
                <div className="flex flex-wrap items-center gap-2">
                  {checked.validation.valid ? <Badge tone="ok">Passed validation</Badge> : <Badge tone="danger">Did not pass validation</Badge>}
                </div>
                <p className="text-xs text-fg-muted">{checked.validation.explanation}</p>
                <CheckList checks={checked.validation.checks} />
                <CornersTable corners={checked.validation.corners} />
                {checked.validation.valid ? (
                  <label className="flex items-start gap-2 text-xs text-fg">
                    <input type="checkbox" className="mt-0.5 accent-accent" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} />
                    I have checked what these sidecar files say and where they come from. I understand that recording it does not show that the
                    raster's location is correct.
                  </label>
                ) : (
                  <p className="text-xs text-danger">Nothing can be recorded until every failed check is resolved. The raster keeps its current state.</p>
                )}
                {save.isError ? <ErrorNotice error={save.error} /> : null}
                {checked.validation.valid ? (
                  <Button size="sm" disabled={!confirmed || save.isPending} onClick={() => save.mutate(checked.review)}>
                    {save.isPending ? "Recording…" : `Record as version ${checked.review.expected_version + 1}`}
                  </Button>
                ) : null}
              </div>
            ) : checked && !checked.validation ? (
              <p className="text-xs text-fg-muted">{checked.review.title}: {checked.review.explanation.join(" ")}</p>
            ) : null}
          </section>
        ) : null}

        {recorded !== null ? (
          <Notice tone="success" title={`Recorded as version ${recorded}`}>
            The sidecar files' values are now in use, labelled as recorded georeferencing.{" "}
            <Link className="text-accent hover:underline" to={`/projects/${projectId}/map`}>
              Open the map
            </Link>
          </Notice>
        ) : null}
        <p className="text-[11px] text-fg-subtle">{data.note}</p>
      </div>
    </Panel>
  );
}
