import { useQuery } from "@tanstack/react-query";
import { type ReactNode, useState } from "react";
import { Link } from "react-router-dom";

import { listConflicts, listObservations, type ObservationView } from "../api/client";
import { IconList, IconSearch } from "../components/icons";
import { ReviewPanel } from "../components/ReviewPanel";
import {
  Badge,
  Breadcrumbs,
  DescriptionList,
  EmptyState,
  ErrorNotice,
  inputClass,
  LinkButton,
  Loading,
  Notice,
  PageHeader,
  Panel,
  selectClass,
  TableFrame,
  tableClass,
  tdClass,
  thClass,
} from "../components/ui";
import { formatDateTime } from "../lib/format";
import { flagLabel, MODALITY_NAMES } from "../lib/ingestion";
import { categoryLabel, REVIEW_STATUS_LABELS, REVIEW_STATUSES, reviewTone } from "../lib/jobs";
import { formatTimestamp } from "../lib/video";
import { projectCrumbs, useProject } from "./ProjectLayout";

const MEANINGS: Record<string, string> = {
  proposed: "Passes technical validation; not human verification.",
  needs_review: "Someone has to look; nothing is concluded.",
  verified: "Confirmed by a reviewer.",
  inconclusive: "The input cannot support the claim.",
  disputed: "Conflicts with another supported observation.",
  rejected: "Established to be invalid; kept for the record.",
};
const LIMIT = 500;

function Detail({ view, onOpen }: { view: ObservationView; onOpen: (id: string) => void }) {
  const project = useProject();
  const { observation, current } = view;
  const region = observation.evidence[0]?.region_px;
  return (
    <Panel
      title={categoryLabel(current.category)}
      description={current.relabelled ? `Model class: ${categoryLabel(observation.claim.category)}` : undefined}
      actions={<Badge tone={reviewTone(current.status)}>{REVIEW_STATUS_LABELS[current.status]}</Badge>}
    >
      <div className="space-y-4">
        <p className="text-[13px] text-fg-muted">{observation.claim.description}</p>
        <section aria-label="Claim">
          <DescriptionList
            rows={[
              ["Model class", <span className="font-mono text-xs">{observation.claim.category}</span>],
              ["Claim type", observation.claim.claim_type],
              ["Raw score", observation.score.raw_value == null ? "—" : observation.score.raw_value.toFixed(3)],
              ["Calibration", observation.score.calibrated ? "Calibrated" : "Uncalibrated: not a probability"],
              ["Flags", (observation.quality_flags ?? []).map(flagLabel).join(", ") || "none"],
            ]}
          />
        </section>
        <section aria-label="Evidence" className="border-t border-line pt-3">
          <p className="mb-1.5 text-xs font-medium text-fg-muted">Evidence</p>
          <DescriptionList
            rows={[
              [
                "Source file",
                <Link className="text-accent hover:underline" to={`/projects/${project.id}/assets/${observation.asset_id}`}>
                  {view.asset_filename || observation.asset_id}
                </Link>,
              ],
              ...(observation.evidence[0]?.frame_index != null && observation.evidence[0]?.timestamp_ms != null
                ? [
                    [
                      "Video frame",
                      <span key="frame">
                        Frame {observation.evidence[0].frame_index} at {formatTimestamp(observation.evidence[0].timestamp_ms)}
                        <span className="block text-[11px] text-fg-subtle">The region below is in that frame, not in the whole video.</span>
                      </span>,
                    ] as [string, ReactNode],
                  ]
                : []),
              [
                "Image region",
                region ? (
                  <span className="font-mono text-xs">
                    [{(region.coordinates as number[]).map((value) => value.toFixed(1)).join(", ")}] px of {region.image_width} ×{" "}
                    {region.image_height}
                  </span>
                ) : (
                  "none"
                ),
              ],
              ["Location", observation.geospatial.geometry ? "Georeferenced" : "None established"],
            ]}
          />
        </section>
        <section aria-label="Provenance" className="border-t border-line pt-3">
          <p className="mb-1.5 text-xs font-medium text-fg-muted">Provenance</p>
          <DescriptionList
            rows={[
              ["Model", observation.model ? `${observation.model.name} ${observation.model.version}` : "human entered"],
              ["Run", observation.model ? <span className="font-mono text-xs">{observation.model.run_id}</span> : "—"],
              ["Created", formatDateTime(observation.provenance.created_at)],
              ["Source checksum", <span className="font-mono text-xs">{observation.provenance.source_checksum}</span>],
            ]}
          />
        </section>
        {observation.pipeline === "ground_image" && observation.model ? (
          <LinkButton
            variant="secondary"
            size="sm"
            to={`/projects/${project.id}/assets/${observation.asset_id}/ground?run=${observation.model.run_id}`}
          >
            Open in Photo Analysis
          </LinkButton>
        ) : null}
        {observation.pipeline === "video" && observation.model ? (
          <LinkButton
            variant="secondary"
            size="sm"
            to={`/projects/${project.id}/assets/${observation.asset_id}/video?run=${observation.model.run_id}`}
          >
            Open in Videos
          </LinkButton>
        ) : null}
        <div className="border-t border-line pt-3">
          <ReviewPanel observationId={observation.observation_id} projectId={project.id} onOpenObservation={onOpen} />
        </div>
      </div>
    </Panel>
  );
}

export function ObservationsPage() {
  const project = useProject();
  const observations = useQuery({
    queryKey: ["observations", project.id, "all"],
    queryFn: () => listObservations(project.id, { limit: LIMIT }),
  });
  const conflicts = useQuery({ queryKey: ["conflicts", project.id, "open"], queryFn: () => listConflicts(project.id, "open") });
  const [search, setSearch] = useState("");
  const [modality, setModality] = useState("");
  const [status, setStatus] = useState("");
  const [conflictFilter, setConflictFilter] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const all = observations.data ?? [];
  const needle = search.trim().toLowerCase();
  const items = all.filter(
    ({ observation, current, asset_filename, open_conflict_ids }) =>
      (!modality || observation.pipeline === modality) &&
      (!status || current.status === status) &&
      (!conflictFilter || (conflictFilter === "open" ? open_conflict_ids.length > 0 : open_conflict_ids.length === 0)) &&
      (!needle ||
        current.category.toLowerCase().includes(needle) ||
        observation.claim.category.toLowerCase().includes(needle) ||
        asset_filename.toLowerCase().includes(needle)),
  );
  const selected = all.find((view) => view.observation.observation_id === selectedId) ?? null;
  const counts = REVIEW_STATUSES.map((value) => [value, all.filter((o) => o.current.status === value).length] as const);
  const openConflicts = conflicts.data?.length ?? 0;

  const open = (id: string) => {
    setSearch("");
    setStatus("");
    setModality("");
    setConflictFilter("");
    setSelectedId(id);
  };

  return (
    <div className="mx-auto max-w-7xl">
      <Breadcrumbs items={projectCrumbs(project, { label: "Findings" })} />
      <div className="mt-2">
        <PageHeader
          title="Findings"
          description="Every stored finding in this project, with its evidence, how it was produced, and its review."
          meta={
            <>
              {counts
                .filter(([, n]) => n > 0)
                .map(([value, n]) => (
                  <Badge key={value} tone={reviewTone(value)}>
                    {REVIEW_STATUS_LABELS[value]} {n}
                  </Badge>
                ))}
              {openConflicts > 0 ? <Badge tone="danger">Open conflicts {openConflicts}</Badge> : null}
            </>
          }
        />
      </div>

      <Notice className="mb-4" title="Model findings are predictions until a person reviews them">
        A review changes only the finding's current state and category; the model's original output is kept unchanged,
        and every change is recorded with who made it and when. Scores are not probabilities.
      </Notice>

      <div className="mb-3 flex flex-wrap items-end gap-2" role="search">
        <div className="relative w-64">
          <label htmlFor="obs-search" className="mb-1 block text-xs text-fg-muted">
            Search class or file
          </label>
          <IconSearch size={15} className="pointer-events-none absolute bottom-2.5 left-2.5 text-fg-subtle" />
          <input id="obs-search" className={`${inputClass} pl-8`} value={search} onChange={(e) => setSearch(e.target.value)} />
        </div>
        <div className="w-48">
          <label htmlFor="obs-modality" className="mb-1 block text-xs text-fg-muted">
            Kind of input
          </label>
          <select id="obs-modality" className={selectClass} value={modality} onChange={(e) => setModality(e.target.value)}>
            <option value="">All</option>
            {Object.entries(MODALITY_NAMES).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </div>
        <div className="w-44">
          <label htmlFor="obs-status" className="mb-1 block text-xs text-fg-muted">
            Review state
          </label>
          <select id="obs-status" className={selectClass} value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">All</option>
            {REVIEW_STATUSES.map((value) => (
              <option key={value} value={value}>
                {REVIEW_STATUS_LABELS[value]}
              </option>
            ))}
          </select>
        </div>
        <div className="w-44">
          <label htmlFor="obs-conflict" className="mb-1 block text-xs text-fg-muted">
            Conflicts
          </label>
          <select id="obs-conflict" className={selectClass} value={conflictFilter} onChange={(e) => setConflictFilter(e.target.value)}>
            <option value="">All</option>
            <option value="open">In an open conflict</option>
            <option value="none">Not in a conflict</option>
          </select>
        </div>
      </div>

      {observations.isLoading ? <Loading label="Loading observations…" /> : null}
      {observations.isError ? <ErrorNotice error={observations.error} /> : null}
      {observations.isSuccess && all.length === LIMIT ? (
        <Notice tone="warning" className="mb-3" title={`Only the first ${LIMIT} observations are shown`}>
          Use the exports on the Reports screen for the complete set.
        </Notice>
      ) : null}
      {observations.isSuccess && all.length === 0 ? (
        <EmptyState icon={<IconList size={28} />} title="No observations yet">
          Findings are created when a photograph is analysed.
        </EmptyState>
      ) : null}
      {observations.isSuccess && all.length > 0 && items.length === 0 ? <EmptyState title="No observations match these filters" /> : null}

      {all.length > 0 ? (
        <div className="grid grid-cols-1 items-start gap-4 xl:grid-cols-[minmax(0,1fr)_26rem]">
          <div className="min-w-0">
            {items.length > 0 ? (
              <TableFrame>
                <table className={tableClass}>
                  <caption className="sr-only">Findings in this project</caption>
                  <thead>
                    <tr>
                      <th scope="col" className={thClass}>Category</th>
                      <th scope="col" className={thClass}>Raw score</th>
                      <th scope="col" className={thClass}>Calibration</th>
                      <th scope="col" className={thClass}>Review</th>
                      <th scope="col" className={thClass}>Source file</th>
                      <th scope="col" className={thClass}>Kind</th>
                      <th scope="col" className={thClass}>Created</th>
                    </tr>
                  </thead>
                  <tbody>
                    {items.map(({ observation, current, asset_filename, open_conflict_ids, possible_duplicate_group_ids }) => {
                      const id = observation.observation_id;
                      const active = id === selectedId;
                      return (
                        <tr
                          key={id}
                          tabIndex={0}
                          aria-selected={active}
                          onClick={() => setSelectedId(active ? null : id)}
                          onKeyDown={(event) => {
                            if (event.key === "Enter" || event.key === " ") {
                              event.preventDefault();
                              setSelectedId(active ? null : id);
                            }
                          }}
                          className={`cursor-pointer ${active ? "bg-accent-soft" : "hover:bg-raised"}`}
                        >
                          <td className={tdClass}>
                            {categoryLabel(current.category)}
                            {current.relabelled ? (
                              <span className="block text-[11px] text-fg-subtle">corrected; model: {categoryLabel(observation.claim.category)}</span>
                            ) : null}
                          </td>
                          <td className={`${tdClass} font-mono`}>{observation.score.raw_value == null ? "—" : observation.score.raw_value.toFixed(3)}</td>
                          <td className={`${tdClass} text-xs text-fg-muted`}>{observation.score.calibrated ? "calibrated" : "uncalibrated"}</td>
                          <td className={tdClass}>
                            <span className="flex flex-wrap items-center gap-1" title={MEANINGS[current.status]}>
                              <Badge tone={reviewTone(current.status)}>{REVIEW_STATUS_LABELS[current.status]}</Badge>
                              {open_conflict_ids.length > 0 ? <Badge tone="danger">conflict</Badge> : null}
                              {possible_duplicate_group_ids.length > 0 ? <Badge tone="neutral">possible duplicate</Badge> : null}
                            </span>
                          </td>
                          <td className={`${tdClass} max-w-56 truncate text-fg-muted`}>{asset_filename || "—"}</td>
                          <td className={`${tdClass} whitespace-nowrap text-xs text-fg-muted`}>{MODALITY_NAMES[observation.pipeline]}</td>
                          <td className={`${tdClass} whitespace-nowrap text-xs text-fg-subtle`}>{formatDateTime(observation.provenance.created_at)}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </TableFrame>
            ) : null}
          </div>
          <div className="xl:sticky xl:top-16">
            {selected ? (
              <Detail key={selected.observation.observation_id} view={selected} onOpen={open} />
            ) : (
              <Panel title="Details">
                <p className="text-[13px] text-fg-subtle">Select an observation to see its evidence and provenance and to review it.</p>
                <ul className="mt-3 space-y-1.5 text-xs">
                  {REVIEW_STATUSES.map((value) => (
                    <li key={value} className="flex gap-2">
                      <span className="w-24 shrink-0">
                        <Badge tone={reviewTone(value)}>{REVIEW_STATUS_LABELS[value]}</Badge>
                      </span>
                      <span className="text-fg-muted">{MEANINGS[value]}</span>
                    </li>
                  ))}
                </ul>
              </Panel>
            )}
          </div>
        </div>
      ) : null}
    </div>
  );
}
