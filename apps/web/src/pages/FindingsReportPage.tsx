import { useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";

import { assetPreviewUrl, type FindingsReport, getFindingsReport } from "../api/client";
import { Badge, Breadcrumbs, Button, ErrorNotice, LinkButton, Loading, type Tone } from "../components/ui";
import { formatArea, formatLength, formatPosition, KIND_LABEL, pointsOf } from "../lib/annotations";
import { formatDateTime } from "../lib/format";
import { MODALITY_NAMES } from "../lib/ingestion";
import { REVIEW_STATUS_LABELS } from "../lib/jobs";
import { projectCrumbs, useProject } from "./ProjectLayout";

type ReportObservation = FindingsReport["observations"][number];
type ReportPhoto = FindingsReport["photo_locations"][number];
type ReportAnnotation = FindingsReport["annotations"]["items"][number];
type ReportRevision = ReportObservation["revisions"][number];
type Region = NonNullable<ReportObservation["evidence"][number]["region"]>;

/** A value the API says is not stored. It is never shown as a blank, a zero or a default. */
function Unavailable({ what }: { what?: string }) {
  return <span className="italic text-fg-subtle">{what ? `${what} unavailable (not stored)` : "Unavailable (not stored)"}</span>;
}

const STATUS_TONE: Record<string, Tone> = { verified: "ok", rejected: "danger", disputed: "warn", inconclusive: "warn" };

function StatusBadge({ status }: { status: string }) {
  return <Badge tone={STATUS_TONE[status] ?? "neutral"}>{REVIEW_STATUS_LABELS[status] ?? status}</Badge>;
}

function Section({ id, title, note, children }: { id: string; title: string; note?: ReactNode; children: ReactNode }) {
  return (
    <section aria-labelledby={id} className="mt-7 first:mt-0">
      <h2 id={id} className="border-b border-line-strong pb-1 text-base font-semibold text-fg">
        {title}
      </h2>
      {note ? <p className="mt-1.5 text-xs text-fg-muted">{note}</p> : null}
      <div className="mt-3">{children}</div>
    </section>
  );
}

function Hash({ value }: { value: string }) {
  return <code className="font-mono text-[11px] break-all">{value}</code>;
}

function describeRegion(region: Region): string {
  const c = region.coordinates as unknown[];
  if (region.type === "bbox_xyxy") {
    const [x0, y0, x1, y1] = c as number[];
    return `box x ${x0}–${x1}, y ${y0}–${y1} px of a ${region.image_width}×${region.image_height} px image (top-left origin)`;
  }
  return `polygon of ${c.length} points in a ${region.image_width}×${region.image_height} px image (top-left origin)`;
}

/** The asset's thumbnail with the stored region drawn over it, in percent of the stored image size. */
function EvidenceThumb({ assetId, filename, region }: { assetId: string; filename: string; region: Region | null }) {
  const aspect = region ? `${region.image_width} / ${region.image_height}` : "4 / 3";
  const [x0 = 0, y0 = 0, x1 = 0, y1 = 0] = (region?.coordinates ?? []) as number[];
  return (
    <div className="relative w-40 shrink-0 overflow-hidden rounded border border-line bg-raised" style={{ aspectRatio: aspect }}>
      <img
        src={assetPreviewUrl(assetId, "thumb")}
        alt={`Thumbnail of ${filename}`}
        className="absolute inset-0 size-full object-fill"
        loading="lazy"
      />
      {region ? (
        <svg
          className="absolute inset-0 size-full"
          viewBox={`0 0 ${region.image_width} ${region.image_height}`}
          preserveAspectRatio="none"
          aria-hidden="true"
        >
          {region.type === "bbox_xyxy" ? (
            <rect
              x={x0}
              y={y0}
              width={x1 - x0}
              height={y1 - y0}
              fill="none"
              stroke="#e11d48"
              strokeWidth={2}
              vectorEffect="non-scaling-stroke"
            />
          ) : (
            <polygon
              points={(region.coordinates as number[][]).map(([x, y]) => `${x},${y}`).join(" ")}
              fill="none"
              stroke="#e11d48"
              strokeWidth={2}
              vectorEffect="non-scaling-stroke"
            />
          )}
        </svg>
      ) : null}
    </div>
  );
}

function Notes({ notes }: { notes: ReportRevision[] }) {
  if (notes.length === 0) return <p className="text-xs text-fg-subtle">No reviewer notes are stored for this finding.</p>;
  return (
    <ul className="space-y-1.5">
      {notes.map((n) => (
        <li key={n.revision_no} className="rounded border border-line bg-raised px-2.5 py-1.5 text-[12px]">
          <p className="whitespace-pre-wrap text-fg">{n.note}</p>
          <p className="mt-0.5 text-fg-subtle">
            {n.actor_label} ({n.actor_kind}) · {formatDateTime(n.created_at)} · revision {n.revision_no}: {n.action},{" "}
            {REVIEW_STATUS_LABELS[n.status_before] ?? n.status_before} → {REVIEW_STATUS_LABELS[n.status_after] ?? n.status_after}
          </p>
        </li>
      ))}
    </ul>
  );
}

function Finding({ item, thumbs }: { item: ReportObservation; thumbs: Set<string> }) {
  const region = item.evidence.find((e) => e.region)?.region ?? null;
  const hasThumb = thumbs.has(item.asset_id);
  return (
    <li className="report-avoid-break rounded-lg border border-line p-3" data-testid="finding">
      <div className="flex flex-wrap gap-4">
        {hasThumb ? <EvidenceThumb assetId={item.asset_id} filename={item.asset_filename} region={region} /> : null}
        <div className="min-w-0 flex-1 space-y-1.5 text-[13px]">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-semibold text-fg">{item.current_category}</span>
            <StatusBadge status={item.review_status} />
            {item.open_conflict_ids.length > 0 ? <Badge tone="warn">In an open conflict</Badge> : null}
          </div>
          {item.relabelled ? (
            <p className="text-fg-muted">
              Relabelled by a reviewer. The model output was <b>{item.model_category}</b>.
            </p>
          ) : null}
          <p className="wrap-anywhere text-fg-muted">{item.claim_description}</p>
          <dl className="grid grid-cols-[8rem_minmax(0,1fr)] gap-x-3 gap-y-0.5 text-[12px]">
            <dt className="text-fg-subtle">Source file</dt>
            <dd className="wrap-anywhere">{item.asset_filename}</dd>
            <dt className="text-fg-subtle">Score</dt>
            <dd>
              {item.score_value === null ? (
                <Unavailable what="score" />
              ) : (
                <>
                  {item.score_value}
                  {item.score_range ? ` (range ${item.score_range[0]}–${item.score_range[1]})` : ""} —{" "}
                  {item.score_calibrated
                    ? "stated as calibrated by the model record"
                    : "not calibrated: the model's own value, not a probability"}
                  <span className="block text-fg-subtle">{item.score_semantics}</span>
                </>
              )}
            </dd>
            <dt className="text-fg-subtle">Evidence</dt>
            <dd>
              {item.evidence.some((e) => e.region) ? (
                <ul>
                  {item.evidence
                    .filter((e) => e.region)
                    .map((e, i) => (
                      <li key={i}>{describeRegion(e.region as Region)}</li>
                    ))}
                </ul>
              ) : (
                <Unavailable what="image region" />
              )}
            </dd>
            <dt className="text-fg-subtle">Model</dt>
            <dd>{item.model ? `${item.model.name} ${item.model.version}` : <Unavailable what="model" />}</dd>
            <dt className="text-fg-subtle">Geographic location</dt>
            <dd>
              {item.geographically_located ? "Stored with the finding" : "None stored (the region is in image pixels only)"}
            </dd>
            {item.quality_flags.length > 0 ? (
              <>
                <dt className="text-fg-subtle">Quality flags</dt>
                <dd>{item.quality_flags.join(", ")}</dd>
              </>
            ) : null}
            <dt className="text-fg-subtle">Review revisions</dt>
            <dd>{item.revisions.length === 0 ? "none: the model output has not been changed by anyone" : item.revisions.length}</dd>
          </dl>
          <div>
            <h4 className="mb-1 text-xs font-semibold text-fg-muted">Reviewer notes</h4>
            <Notes notes={item.reviewer_notes} />
          </div>
        </div>
      </div>
    </li>
  );
}

function PhotoRow({ photo }: { photo: ReportPhoto }) {
  const c = photo.confirmed;
  return (
    <li className="report-avoid-break rounded-lg border border-line p-3 text-[13px]" data-testid="photo-location">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-semibold wrap-anywhere">{photo.filename}</span>
        {photo.state === "confirmed" ? <Badge tone="ok">Confirmed location</Badge> : null}
        {photo.state === "unconfirmed_candidate" ? <Badge tone="warn">Unconfirmed GPS candidate</Badge> : null}
        {photo.state === "none" ? <Badge>No location</Badge> : null}
      </div>
      {c ? (
        <p className="mt-1">
          <b>
            {c.latitude}, {c.longitude}
          </b>{" "}
          ({c.crs_id}, latitude then longitude) — {c.source_label}; confirmed by {c.confirmed_by} on {formatDateTime(c.confirmed_at)}
          {c.reason ? `; reason: ${c.reason}` : ""}. This is where the photograph is recorded as taken or placed, not the position of
          anything shown in it.
        </p>
      ) : null}
      {photo.state === "unconfirmed_candidate" ? (
        <p className="mt-1 text-fg-muted">
          A GPS position was read from the file but no person has confirmed it. It is not treated as a location and its
          coordinates are not shown here.
        </p>
      ) : null}
      {photo.state === "none" ? <p className="mt-1 text-fg-muted">No location is recorded for this photograph.</p> : null}
      {photo.history.length > 0 ? (
        <table className="mt-2 w-full border-collapse text-left text-[12px]">
          <caption className="sr-only">Location history of {photo.filename}</caption>
          <thead>
            <tr className="text-fg-subtle">
              <th className="py-0.5 pr-2 font-medium">Version</th>
              <th className="py-0.5 pr-2 font-medium">Step</th>
              <th className="py-0.5 pr-2 font-medium">Source</th>
              <th className="py-0.5 pr-2 font-medium">Position</th>
              <th className="py-0.5 pr-2 font-medium">By</th>
              <th className="py-0.5 font-medium">When</th>
            </tr>
          </thead>
          <tbody>
            {photo.history.map((h) => (
              <tr key={h.version_no} className="border-t border-line">
                <td className="py-0.5 pr-2">{h.version_no}</td>
                <td className="py-0.5 pr-2">{h.status === "candidate" ? "unconfirmed candidate" : h.status}</td>
                <td className="py-0.5 pr-2">{h.source}</td>
                <td className="py-0.5 pr-2">
                  {h.coordinates_withheld
                    ? "not shown (unconfirmed)"
                    : h.latitude === null || h.longitude === null
                      ? "none"
                      : `${h.latitude}, ${h.longitude}`}
                </td>
                <td className="py-0.5 pr-2">{h.actor}</td>
                <td className="py-0.5">{formatDateTime(h.at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
    </li>
  );
}

const SHOWN_VERTICES = 12;

type ReportVersion = ReportAnnotation["versions"][number];

/** What a version did, in words: created, deleted, or changed (shape and/or note) compared with the version before it. */
function versionChange(version: ReportVersion, previous: ReportVersion | undefined): string {
  if (version.action === "create") return "created";
  if (version.action === "delete") return "deleted";
  const parts = [version.geometry_changed ? "shape" : null, previous && previous.note !== version.note ? "note" : null].filter(Boolean);
  return parts.length > 0 ? `changed (${parts.join(" and ")})` : "changed";
}

/** Every vertex of a short shape; the first few of a long one, with the rest pointing to the exports that hold all of it. */
function ShapeText({ item }: { item: ReportAnnotation }) {
  const points = pointsOf(item.geometry as { type: string; coordinates: unknown });
  const shown = points.slice(0, SHOWN_VERTICES);
  return (
    <>
      {item.geometry.type as string}, {points.length} position{points.length === 1 ? "" : "s"} (latitude, longitude):
      <ul className="mt-0.5 font-mono text-[11px]">
        {shown.map((p, i) => (
          <li key={i}>{formatPosition(p)}</li>
        ))}
      </ul>
      {points.length > shown.length ? (
        <span className="block text-fg-subtle">
          … and {points.length - shown.length} more. All positions are in the JSON and GeoJSON exports.
        </span>
      ) : null}
    </>
  );
}

function AnnotationRow({ item }: { item: ReportAnnotation }) {
  const deleted = item.status === "deleted";
  const m = item.measurement;
  return (
    <li
      className="report-avoid-break rounded-lg border border-line p-3 text-[13px]"
      data-testid="annotation"
      data-status={item.status}
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-semibold">{KIND_LABEL[item.kind]}</span>
        {deleted ? <Badge tone="danger">Deleted: not an active mark</Badge> : <Badge tone="ok">Active</Badge>}
        <span className="text-xs text-fg-subtle">version {item.version_no}</span>
      </div>
      <p className="mt-1 whitespace-pre-wrap wrap-anywhere text-fg" data-testid="annotation-note">
        {item.note === "" ? <span className="italic text-fg-subtle">No note</span> : item.note}
      </p>
      <dl className="mt-1.5 grid grid-cols-[9rem_minmax(0,1fr)] gap-x-3 gap-y-0.5 text-[12px]">
        <dt className="text-fg-subtle">Annotation ID</dt>
        <dd>
          <Hash value={item.annotation_id} />
        </dd>
        <dt className="text-fg-subtle">{deleted ? "Last shape (before deletion)" : "Shape"}</dt>
        <dd>
          <ShapeText item={item} />
          <span className="block text-fg-subtle">Coordinate system {item.crs_id}</span>
        </dd>
        <dt className="text-fg-subtle">Drawn by</dt>
        <dd>
          {item.created_by} on {formatDateTime(item.created_at)}
        </dd>
        <dt className="text-fg-subtle">Last changed</dt>
        <dd>
          {item.updated_by} on {formatDateTime(item.updated_at)}
        </dd>
        {deleted ? (
          <>
            <dt className="text-fg-subtle">Deleted by</dt>
            <dd>
              {item.deleted_by ?? "unknown"} on {item.deleted_at ? formatDateTime(item.deleted_at) : "an unknown date"}
            </dd>
          </>
        ) : null}
        {m && !deleted ? (
          <>
            <dt className="text-fg-subtle">Measurement</dt>
            <dd>
              {m.length_m != null ? <span className="block">Length (approximate): {formatLength(m.length_m)}</span> : null}
              {m.area_m2 != null ? <span className="block">Area (approximate): {formatArea(m.area_m2)}</span> : null}
              {m.perimeter_m != null ? <span className="block">Outline length (approximate): {formatLength(m.perimeter_m)}</span> : null}
              <span className="block text-fg-subtle">Method: {m.method}. Computed from the drawn vertices, not a survey measurement.</span>
            </dd>
          </>
        ) : null}
      </dl>
      <table className="mt-2 w-full border-collapse text-left text-[12px]">
        <caption className="sr-only">Version history of this annotation</caption>
        <thead>
          <tr className="text-fg-subtle">
            <th className="py-0.5 pr-2 font-medium">Version</th>
            <th className="py-0.5 pr-2 font-medium">Change</th>
            <th className="py-0.5 pr-2 font-medium">Note</th>
            <th className="py-0.5 pr-2 font-medium">Reason</th>
            <th className="py-0.5 pr-2 font-medium">By</th>
            <th className="py-0.5 font-medium">When</th>
          </tr>
        </thead>
        <tbody>
          {item.versions.map((v, i) => (
            <tr key={v.version_no} className="border-t border-line align-top">
              <td className="py-0.5 pr-2">{v.version_no}</td>
              <td className="py-0.5 pr-2">{versionChange(v, item.versions[i - 1])}</td>
              <td className="py-0.5 pr-2 whitespace-pre-wrap wrap-anywhere">{v.note}</td>
              <td className="py-0.5 pr-2 whitespace-pre-wrap wrap-anywhere">{v.reason ?? ""}</td>
              <td className="py-0.5 pr-2">{v.actor_label}</td>
              <td className="py-0.5">{formatDateTime(v.created_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </li>
  );
}

const th = "border-b border-line-strong px-2 py-1 text-left text-[11px] font-semibold text-fg-muted";
const td = "border-b border-line px-2 py-1.5 align-top text-[12px]";

export function ReportView({ report }: { report: FindingsReport }) {
  const s = report.summary;
  const thumbs = new Set(report.assets.filter((a) => a.thumbnail_available).map((a) => a.asset_id));
  const reviewed = report.observations.filter((o) => o.review_group === "reviewed");
  const unreviewed = report.observations.filter((o) => o.review_group === "unreviewed");
  const annotations = report.annotations;
  const activeAnnotations = annotations.items.filter((a) => a.status === "active");
  const deletedAnnotations = annotations.items.filter((a) => a.status === "deleted");
  const counts: [string, number | null, string][] = [
    ["Findings", s.observations_total, "All stored observations of this project"],
    ["Reviewed", s.observations_reviewed, "Verified, rejected or inconclusive"],
    ["Unreviewed", s.observations_unreviewed, "Proposed, needs review or disputed"],
    ["Without a geographic location", s.observations_unlocated, "No geometry stored with the finding"],
    ["With a geographic location", s.observations_located, "Geometry stored with the finding"],
    ["With reviewer notes", s.observations_with_reviewer_notes, "At least one note in the revision history"],
    ["Photographs", s.photos_total, "Ground images in the project"],
    ["Photos: confirmed location", s.photos_confirmed_location, "A person confirmed or entered a position"],
    ["Photos: unconfirmed GPS candidate", s.photos_unconfirmed_candidate, "Read from the file, not confirmed"],
    ["Photos: no location", s.photos_without_location, "Nothing recorded, or cleared"],
    ["Open conflicts", s.conflicts_open, "Supported findings that disagree"],
    ["Resolved conflicts", s.conflicts_resolved, "Closed with a documented reason"],
    ["Annotations: active", s.annotations_active, "Marks a person drew; not findings"],
    ["Annotations: deleted", s.annotations_deleted, "Kept in history; not active marks"],
  ];

  return (
    <article className="report-sheet mx-auto max-w-5xl rounded-lg border border-line bg-surface p-6 text-fg print:border-0 print:p-0" aria-label="Findings report">
      <header className="border-b-2 border-line-strong pb-3">
        <p className="text-xs font-medium tracking-wide text-fg-subtle uppercase">Findings report</p>
        <h1 className="mt-0.5 text-xl font-semibold wrap-anywhere">{report.project.name}</h1>
        {report.project.description ? <p className="mt-1 text-[13px] text-fg-muted wrap-anywhere">{report.project.description}</p> : null}
        <dl className="mt-2 grid grid-cols-[9rem_minmax(0,1fr)] gap-x-3 gap-y-0.5 text-[12px]">
          <dt className="text-fg-subtle">Report generated</dt>
          <dd>
            {formatDateTime(report.generated_at)} by {report.generated_by}
          </dd>
          <dt className="text-fg-subtle">Project</dt>
          <dd>
            created {formatDateTime(report.project.created_at)} by {report.project.created_by}
            {report.project.archived_at ? `; archived ${formatDateTime(report.project.archived_at)}` : ""}
          </dd>
          <dt className="text-fg-subtle">Project identifier</dt>
          <dd>
            <Hash value={report.project.id} />
          </dd>
          <dt className="text-fg-subtle">Nature of this report</dt>
          <dd>Read-only summary of stored records (format version {report.schema_version}). Nothing was added, estimated or changed to produce it.</dd>
        </dl>
      </header>

      <div className="mt-5">
        <Section id="summary" title="1. Summary" note="Counts are of stored records only. How each is counted is stated in section 2.">
          <ul className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4">
            {counts.map(([label, value, hint]) => (
              <li key={label} className="report-avoid-break rounded border border-line px-2.5 py-1.5">
                <p className="text-[11px] text-fg-subtle">{label}</p>
                <p className="text-lg font-semibold" data-testid={`count-${label}`}>
                  {value === null ? <Unavailable /> : value}
                </p>
                <p className="text-[11px] text-fg-subtle">{hint}</p>
              </li>
            ))}
          </ul>
          <p className="mt-2 text-xs text-fg-muted">
            By review status:{" "}
            {Object.entries(s.observations_by_review_status)
              .map(([k, v]) => `${REVIEW_STATUS_LABELS[k] ?? k} ${v}`)
              .join(", ")}
            . Scores: {s.scores_not_calibrated} not calibrated, {s.scores_calibrated} stated as calibrated, {s.scores_unavailable} with no stored
            value.
          </p>
        </Section>

        <Section id="rules" title="2. How records are counted">
          <ul className="list-disc space-y-1 pl-5 text-[12px] text-fg-muted">
            {report.counting_rules.map((rule) => (
              <li key={rule}>{rule}</li>
            ))}
          </ul>
        </Section>

        <Section id="limitations" title="3. Limitations">
          <ul className="list-disc space-y-1 pl-5 text-[13px]">
            {report.limitations.map((text) => (
              <li key={text}>{text}</li>
            ))}
          </ul>
        </Section>

        <Section id="assets" title="4. Source files" note="Originals are stored unchanged; the SHA-256 checksum identifies each one.">
          {report.assets.length === 0 ? (
            <p className="text-[13px] text-fg-muted">No files have been added to this project.</p>
          ) : (
            <table className="w-full border-collapse">
              <thead>
                <tr>
                  <th className={th}>File</th>
                  <th className={th}>Type</th>
                  <th className={th}>Size</th>
                  <th className={th}>SHA-256</th>
                  <th className={th}>Added</th>
                  <th className={th}>Findings</th>
                </tr>
              </thead>
              <tbody>
                {report.assets.map((a) => (
                  <tr key={a.asset_id}>
                    <td className={td}>
                      <span className="wrap-anywhere font-medium">{a.filename}</span>
                      <span className="block text-fg-subtle">{MODALITY_NAMES[a.modality] ?? a.modality}</span>
                      {a.duplicate_of_asset_id ? <span className="block text-fg-subtle">Same content as another file in this project</span> : null}
                    </td>
                    <td className={td}>{a.media_type}</td>
                    <td className={td}>{a.size_bytes.toLocaleString("en-GB")} bytes</td>
                    <td className={td}>
                      <Hash value={a.sha256} />
                    </td>
                    <td className={td}>
                      {formatDateTime(a.ingested_at)}
                      <span className="block text-fg-subtle">by {a.uploaded_by || "unknown"}</span>
                    </td>
                    <td className={td}>{a.observation_count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Section>

        <Section id="methods" title="5. Methods and models" note="Taken from the stored record of each analysis run.">
          {report.runs.length === 0 ? (
            <p className="text-[13px] text-fg-muted">No analysis has been run in this project, so there is no model or run to report.</p>
          ) : (
            <table className="w-full border-collapse">
              <thead>
                <tr>
                  <th className={th}>Model</th>
                  <th className={th}>Pipeline and file</th>
                  <th className={th}>Weights SHA-256</th>
                  <th className={th}>Device</th>
                  <th className={th}>Run</th>
                  <th className={th}>Findings</th>
                </tr>
              </thead>
              <tbody>
                {report.runs.map((r) => (
                  <tr key={r.run_id}>
                    <td className={td}>
                      {r.model_name} {r.model_version}
                    </td>
                    <td className={td}>
                      {r.pipeline}
                      <span className="block wrap-anywhere text-fg-subtle">{r.asset_filename}</span>
                    </td>
                    <td className={td}>{r.weights_sha256 ? <Hash value={r.weights_sha256} /> : <Unavailable />}</td>
                    <td className={td}>{r.device ?? <Unavailable />}</td>
                    <td className={td}>
                      {formatDateTime(r.started_at)} – {formatDateTime(r.finished_at)}
                      <span className="block text-fg-subtle">
                        <Hash value={r.run_id} />
                      </span>
                      {Object.keys(r.settings).length > 0 ? (
                        <span className="block text-fg-subtle">settings: {JSON.stringify(r.settings)}</span>
                      ) : null}
                    </td>
                    <td className={td}>{r.observation_count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Section>

        <Section
          id="findings"
          title="6. Findings"
          note="Each finding is the model's output with the current review state beside it. A finding no person has reviewed is a proposal."
        >
          {report.observations.length === 0 ? (
            <p className="text-[13px] text-fg-muted">There are no findings in this project.</p>
          ) : (
            <>
              <h3 className="mb-2 text-sm font-semibold">Reviewed findings ({reviewed.length})</h3>
              {reviewed.length === 0 ? <p className="mb-4 text-[13px] text-fg-muted">None of the findings has been reviewed.</p> : null}
              <ul className="mb-5 space-y-3">
                {reviewed.map((o) => (
                  <Finding key={o.observation_id} item={o} thumbs={thumbs} />
                ))}
              </ul>
              <h3 className="mb-2 text-sm font-semibold">Unreviewed findings ({unreviewed.length})</h3>
              {unreviewed.length === 0 ? <p className="text-[13px] text-fg-muted">Every finding has been reviewed.</p> : null}
              <ul className="space-y-3">
                {unreviewed.map((o) => (
                  <Finding key={o.observation_id} item={o} thumbs={thumbs} />
                ))}
              </ul>
            </>
          )}
        </Section>

        <Section
          id="locations"
          title="7. Photograph locations"
          note="Only positions a person confirmed, typed or chose on a map are shown. A GPS position read from a file is a candidate until someone confirms it."
        >
          {report.photo_locations.length === 0 ? (
            <p className="text-[13px] text-fg-muted">This project has no photographs.</p>
          ) : (
            <ul className="space-y-3">
              {report.photo_locations.map((p) => (
                <PhotoRow key={p.asset_id} photo={p} />
              ))}
            </ul>
          )}
        </Section>

        <Section id="conflicts" title="8. Conflicts" note="Supported findings that disagree. Both are kept; nothing is averaged or merged.">
          {report.conflicts.length === 0 ? (
            <p className="text-[13px] text-fg-muted">No conflicts are stored for this project.</p>
          ) : (
            <ul className="space-y-3">
              {report.conflicts.map((c) => (
                <li key={c.id} className="report-avoid-break rounded-lg border border-line p-3 text-[13px]" data-testid="conflict">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-semibold">Disagreement on {c.attribute}</span>
                    <Badge tone={c.status === "open" ? "warn" : "ok"}>{c.status === "open" ? "Open" : "Resolved (documented)"}</Badge>
                  </div>
                  <p className="text-xs text-fg-subtle">
                    Detected {formatDateTime(c.created_at)} by rule {c.rule}
                    {c.resolved_at ? `; resolved ${formatDateTime(c.resolved_at)}` : ""}
                  </p>
                  {c.resolution_note ? <p className="mt-1 whitespace-pre-wrap">Resolution: {c.resolution_note}</p> : null}
                  <ul className="mt-1 text-[12px]">
                    {c.members.map((m) => (
                      <li key={m.observation_id}>
                        {m.asset_filename}: {String((m.value as Record<string, unknown>).category ?? "value")} — currently{" "}
                        {REVIEW_STATUS_LABELS[m.current_status] ?? m.current_status}
                      </li>
                    ))}
                  </ul>
                </li>
              ))}
            </ul>
          )}
        </Section>

        <Section
          id="annotations"
          title="9. Map annotations"
          note="Marks a person drew on the map, with a note. They are not findings and are not counted among them."
        >
          <p className="mb-3 text-[12px] text-fg-muted">{annotations.note}</p>
          {!annotations.available ? (
            <p className="text-[13px] font-medium" role="note">
              Map annotations cannot be listed from this database.
            </p>
          ) : annotations.items.length === 0 ? (
            <p className="text-[13px] text-fg-muted">No map annotations are stored for this project.</p>
          ) : (
            <>
              <h3 className="mb-2 text-sm font-semibold">Active annotations ({activeAnnotations.length})</h3>
              {activeAnnotations.length === 0 ? <p className="mb-4 text-[13px] text-fg-muted">There are no active annotations.</p> : null}
              <ul className="mb-5 space-y-3">
                {activeAnnotations.map((a) => (
                  <AnnotationRow key={a.annotation_id} item={a} />
                ))}
              </ul>
              <h3 className="mb-2 text-sm font-semibold">Deleted annotations ({deletedAnnotations.length})</h3>
              <p className="mb-2 text-[12px] text-fg-muted">
                A deleted annotation is no longer an active mark. It is listed here so the record is complete, and its history is kept.
              </p>
              {deletedAnnotations.length === 0 ? <p className="text-[13px] text-fg-muted">No annotation has been deleted.</p> : null}
              <ul className="space-y-3">
                {deletedAnnotations.map((a) => (
                  <AnnotationRow key={a.annotation_id} item={a} />
                ))}
              </ul>
            </>
          )}
        </Section>
      </div>

      <footer className="mt-8 border-t border-line pt-2 text-[11px] text-fg-subtle">
        Generated {formatDateTime(report.generated_at)} from the local database. Printing this page, for example to a PDF printer, produces a copy; no PDF is
        created by the application.
      </footer>
    </article>
  );
}

export function FindingsReportPage() {
  const project = useProject();
  const report = useQuery({
    queryKey: ["findings-report", project.id],
    queryFn: () => getFindingsReport(project.id),
    // Each generation is recorded in the audit log, so it happens when the page opens or when asked, not on focus.
    staleTime: Infinity,
    gcTime: 0,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    retry: false,
  });

  return (
    <div className="mx-auto max-w-5xl">
      <div className="print:hidden">
        <Breadcrumbs items={projectCrumbs(project, { label: "Reports", to: `/projects/${project.id}/reports` }, { label: "Findings report" })} />
        <div className="my-3 flex flex-wrap items-center justify-between gap-2">
          <p className="text-[13px] text-fg-muted">
            Built from stored records only. Opening or regenerating it is recorded in the audit log.
          </p>
          <div className="flex gap-2">
            <LinkButton variant="secondary" size="sm" to={`/projects/${project.id}/reports`}>
              Back to Reports
            </LinkButton>
            <Button variant="secondary" size="sm" disabled={report.isFetching} onClick={() => void report.refetch()}>
              Regenerate
            </Button>
            <Button size="sm" disabled={!report.data} onClick={() => window.print()}>
              Print report
            </Button>
          </div>
        </div>
      </div>
      {report.isLoading ? <Loading label="Generating the report…" /> : null}
      {report.isError ? <ErrorNotice error={report.error} title="The report could not be generated" /> : null}
      {report.data ? <ReportView report={report.data} /> : null}
    </div>
  );
}
