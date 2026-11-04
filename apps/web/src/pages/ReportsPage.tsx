import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { downloadExport, type ExportFormat, getProjectSummary } from "../api/client";
import { IconDownload, IconReport } from "../components/icons";
import {
  Badge,
  Breadcrumbs,
  Button,
  DescriptionList,
  ErrorNotice,
  Loading,
  LinkButton,
  PageHeader,
  Panel,
  PlannedBadge,
} from "../components/ui";
import { formatDateTime } from "../lib/format";
import { MODALITY_NAMES } from "../lib/ingestion";
import { REVIEW_STATUS_LABELS } from "../lib/jobs";
import { projectCrumbs, useProject } from "./ProjectLayout";

const EXPORTS: { format: ExportFormat; title: string; description: string }[] = [
  {
    format: "csv",
    title: "Observations table (CSV)",
    description:
      "One row per finding: model class and current category, raw score, review state, and the image region in columns labelled as source-image pixels.",
  },
  {
    format: "geojson",
    title: "Located observations (GeoJSON)",
    description:
      "Only findings with an established geographic location, in WGS 84. Findings that are positions in an image are counted and left out, never placed.",
  },
  {
    format: "json",
    title: "Full record (JSON)",
    description: "The model's original output, current review state, revision history, conflicts and run provenance.",
  },
];

// The contents a report must disclose (specification R-023, REP-001), and whether the findings report has them.
// "In the findings report" means the page shows it from stored data; it does not mean the report is complete for every
// future need (see Report history below).
const SECTIONS = [
  "Project and source assets",
  "Methods, models and versions",
  "Settings and timestamps",
  "Findings with evidence references",
  "Quality warnings and conflicts",
  "Confidence semantics and review state",
  "Known limitations",
];

export function ReportsPage() {
  const project = useProject();
  const summary = useQuery({ queryKey: ["summary", project.id], queryFn: () => getProjectSummary(project.id) });
  const [exported, setExported] = useState<string | null>(null);
  const exportFile = useMutation({
    mutationFn: (format: ExportFormat) => downloadExport(project.id, format),
    onSuccess: ({ filename, bytes }) => setExported(`Saved ${filename} (${(bytes / 1024).toFixed(1)} KB).`),
  });
  const total = summary.data?.observations_total ?? 0;

  return (
    <div className="mx-auto max-w-6xl">
      <Breadcrumbs items={projectCrumbs(project, { label: "Reports" })} />
      <div className="mt-2">
        <PageHeader
          title="Reports"
          description="The findings report for this project, and the data exports."
          meta={<Badge tone="ok">Findings report available</Badge>}
        />
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1fr)_20rem]">
        <div className="min-w-0 space-y-4">
          <Panel title="Project context" description="Counted from what is stored.">
            {summary.isLoading ? <Loading /> : null}
            {summary.isError ? <ErrorNotice error={summary.error} /> : null}
            {summary.data ? (
              <DescriptionList
                labelWidth="md"
                rows={[
                  ["Project", project.name],
                  [
                    "Files",
                    `${summary.data.assets_total} (${Object.entries(summary.data.assets_by_modality)
                      .map(([modality, n]) => `${MODALITY_NAMES[modality] ?? modality} ${n}`)
                      .join(", ")})`,
                  ],
                  ["Findings", String(summary.data.observations_total)],
                  [
                    "Review states",
                    Object.entries(summary.data.observations_by_review_status)
                      .map(([status, n]) => `${REVIEW_STATUS_LABELS[status] ?? status} ${n}`)
                      .join(", ") || "none",
                  ],
                  ["Last activity", formatDateTime(summary.data.last_activity_at)],
                ]}
              />
            ) : null}
          </Panel>

          <Panel
            title="Findings report"
            description="A read-only report built from this project's stored files, runs, findings, reviewer notes, photograph locations and conflicts."
          >
            <div className="flex flex-wrap items-center justify-between gap-3 text-[13px] text-fg-muted">
              <p className="max-w-prose">
                Opens as a page you can print. Values that are not stored are shown as unavailable, never filled in. Each time it is
                generated, the event is recorded in the audit log.
              </p>
              <LinkButton to={`/projects/${project.id}/reports/findings`}>
                <IconReport size={14} />
                Open findings report
              </LinkButton>
            </div>
          </Panel>

          <Panel title="Report contents" description="What the findings report includes, from stored data only." padded={false}>
            <ul className="divide-y divide-line">
              {SECTIONS.map((section) => (
                <li key={section} className="flex items-center justify-between gap-3 px-4 py-2.5 text-[13px] text-fg-muted">
                  {section}
                  <Badge tone="ok">In the findings report</Badge>
                </li>
              ))}
            </ul>
          </Panel>

          <Panel title="Report history">
            <div className="flex items-start gap-3 text-[13px] text-fg-muted">
              <IconReport size={18} className="mt-0.5 shrink-0 text-fg-subtle" />
              <p>
                Reports are generated on demand and are not saved as versions. Saved, versioned reports are planned for Phase 5.
              </p>
            </div>
          </Panel>
        </div>

        <aside className="space-y-4" aria-label="Exports">
          <Panel title="Exports" description="Produced by the API from what is stored. Each export is recorded in the audit log.">
            <ul className="space-y-3 text-[13px]">
              {EXPORTS.map((item) => (
                <li key={item.format} className="space-y-1.5 border-t border-line pt-3 first:border-t-0 first:pt-0">
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-medium text-fg">{item.title}</span>
                    <Badge tone="ok">Available</Badge>
                  </div>
                  <p className="text-xs text-fg-muted">{item.description}</p>
                  <Button
                    variant="secondary"
                    size="sm"
                    disabled={exportFile.isPending}
                    onClick={() => {
                      setExported(null);
                      exportFile.mutate(item.format);
                    }}
                  >
                    <IconDownload size={14} />
                    Download {item.format.toUpperCase()}
                  </Button>
                </li>
              ))}
              {summary.data && total === 0 ? (
                <li className="text-xs text-fg-subtle">There are no observations yet; an export contains only headers.</li>
              ) : null}
              {exportFile.isError ? (
                <li>
                  <ErrorNotice error={exportFile.error} />
                </li>
              ) : null}
              {exported ? (
                <li role="status" className="text-xs text-ok">
                  {exported}
                </li>
              ) : null}
              <li className="space-y-1 border-t border-line pt-3">
                <div className="flex items-center justify-between gap-2">
                  <span className="font-medium text-fg">Original files</span>
                  <Badge tone="ok">Available</Badge>
                </div>
                <p className="text-xs text-fg-muted">Each file can be downloaded unchanged from its page under Add Files.</p>
              </li>
              <li className="space-y-1 border-t border-line pt-3">
                <div className="flex items-center justify-between gap-2">
                  <span className="font-medium text-fg">Findings report (HTML, printable)</span>
                  <Badge tone="ok">Available</Badge>
                </div>
                <p className="text-xs text-fg-muted">Print it from the browser. The application does not generate a PDF file.</p>
              </li>
              {["PDF report", "GeoPackage"].map((name) => (
                <li key={name} className="flex items-center justify-between gap-2 border-t border-line pt-3">
                  <span className="text-fg-muted">{name}</span>
                  <PlannedBadge />
                </li>
              ))}
            </ul>
          </Panel>
        </aside>
      </div>
    </div>
  );
}
