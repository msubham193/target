import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, Navigate, useParams, useSearchParams } from "react-router-dom";

import { type Asset, getRun, listJobs, listObservations, listPhotoLocations, type ObservationView } from "../api/client";
import { AssetPreview, AssetThumb } from "../components/AssetPreview";
import { boxes, classColour, type DrawTool, DetectionOverlay as Overlay } from "../components/DetectionOverlay";
import { IconAlert, IconCheck, IconClock, IconImage, IconUpload } from "../components/icons";
import { JobRow, RunAnalysis, useAssetJobs } from "../components/JobsPanel";
import { PhotoLocationPanel } from "../components/PhotoLocationPanel";
import { MarkToolbar, PhotoMarksPanel } from "../components/PhotoMarkTools";
import { ReviewPanel } from "../components/ReviewPanel";
import {
  Badge,
  Breadcrumbs,
  DescriptionList,
  EmptyState,
  ErrorNotice,
  LinkButton,
  Loading,
  Notice,
  PageHeader,
  Panel,
  selectClass,
  tableClass,
  Tabs,
  tdClass,
  thClass,
} from "../components/ui";
import { formatDateTime } from "../lib/format";
import { flagLabel } from "../lib/ingestion";
import { categoryLabel, isActive, REVIEW_STATUS_LABELS, reviewTone } from "../lib/jobs";
import { usePhotoMarks } from "../lib/photoMarks";
import { useAsset, useAssets } from "../lib/queries";
import { projectCrumbs, useProject } from "./ProjectLayout";

/** The project's photographs as a row of thumbnails above the screen, so the photograph itself can use the full width. */
function PhotoStrip({ projectId, selectedId }: { projectId: string; selectedId: string }) {
  const photos = useAssets(projectId, "ground_image");
  const locations = useQuery({ queryKey: ["photo-locations", projectId], queryFn: () => listPhotoLocations(projectId) });
  const stateOf = new Map((locations.data?.items ?? []).map((item) => [item.asset_id, item.state]));
  const items = (photos.data?.items ?? []) as Asset[];
  if (items.length < 2) return null;
  return (
    <nav aria-label="Photographs" className="flex gap-2 overflow-x-auto pb-1">
      {items.map((asset) => {
        const selected = asset.id === selectedId;
        const located = stateOf.get(asset.id) === "confirmed";
        return (
          <Link
            key={asset.id}
            to={`/projects/${projectId}/assets/${asset.id}/ground`}
            aria-current={selected ? "page" : undefined}
            title={asset.original_filename}
            className={`flex w-44 shrink-0 items-center gap-2 rounded-lg border px-2 py-1.5 text-xs transition-colors ${
              selected ? "border-accent bg-accent-soft text-fg" : "border-line bg-surface text-fg-muted hover:bg-overlay hover:text-fg"
            }`}
          >
            <AssetThumb asset={asset} className="h-9 w-12" />
            <span className="min-w-0">
              <span className="block truncate">{asset.original_filename}</span>
              <span className={`block text-[11px] ${located ? "text-ok" : "text-fg-subtle"}`}>{located ? "Located" : "No confirmed location"}</span>
            </span>
          </Link>
        );
      })}
    </nav>
  );
}

function Detections({
  observations,
  selected,
  onSelect,
  category,
}: {
  observations: ObservationView[];
  selected: number | null;
  onSelect: (index: number | null) => void;
  /** Show only findings of this model class; every finding keeps its own number. */
  category: string | null;
}) {
  return (
    <div className="max-h-80 overflow-auto">
      <table className={tableClass}>
        <caption className="sr-only">Model findings in this photograph</caption>
        <thead className="sticky top-0 z-10">
          <tr>
            <th scope="col" className={compactTh}>#</th>
            <th scope="col" className={compactTh}>Found</th>
            <th scope="col" className={compactTh}>Score</th>
            <th scope="col" className={compactTh}>Score type</th>
            <th scope="col" className={compactTh}>Review</th>
          </tr>
        </thead>
        <tbody>
          {observations.map(({ observation, current, open_conflict_ids }, position) => {
            if (category !== null && categoryLabel(current.category) !== category) return null;
            const index = position + 1;
            const active = selected === index;
            return (
              <tr
                key={observation.observation_id}
                aria-selected={active}
                tabIndex={0}
                onClick={() => onSelect(active ? null : index)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    onSelect(active ? null : index);
                  }
                }}
                className={`cursor-pointer transition-colors ${active ? "bg-warn-soft" : "hover:bg-raised"}`}
              >
                <td className={compactTd}>{index}</td>
                <td className={compactTd}>
                  <span
                    aria-hidden="true"
                    className="mr-1.5 inline-block size-2.5 rounded-full align-middle"
                    style={{ background: classColour(categoryLabel(current.category)) }}
                  />
                  {categoryLabel(current.category)}
                  {current.relabelled ? <span className="block text-[11px] text-fg-subtle">corrected; model: {categoryLabel(observation.claim.category)}</span> : null}
                </td>
                <td className={`${compactTd} font-mono`}>
                  {observation.score.raw_value == null ? "—" : observation.score.raw_value.toFixed(3)}
                </td>
                <td className={compactTd}>
                  <span className="text-xs text-fg-muted">{observation.score.calibrated ? "calibrated" : "uncalibrated"}</span>
                </td>
                <td className={compactTd}>
                  <Badge tone={reviewTone(current.status)}>{REVIEW_STATUS_LABELS[current.status] ?? current.status}</Badge>
                  {open_conflict_ids.length > 0 ? <span className="sr-only"> (in an open conflict)</span> : null}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// Narrower cells so all five columns fit the side panel without scrolling.
const compactTh = thClass.replace("px-3", "px-2");
const compactTd = tdClass.replace("px-3", "px-2");

function SelectedDetection({ view, index, projectId }: { view: ObservationView; index: number; projectId: string }) {
  const { observation, current } = view;
  const region = observation.evidence[0]?.region_px;
  return (
    <Panel title={`Finding ${index}: ${categoryLabel(current.category)}`}>
      <div className="space-y-3">
        <p className="text-[13px] text-fg-muted">{observation.claim.description}</p>
        <DescriptionList
          rows={[
            ["Model class", categoryLabel(observation.claim.category)],
            ["Claim type", observation.claim.claim_type === "inferred" ? "Model-inferred" : observation.claim.claim_type],
            ["Raw score", observation.score.raw_value == null ? "—" : observation.score.raw_value.toFixed(3)],
            ["Calibration", observation.score.calibrated ? "Calibrated" : "Uncalibrated: not a probability"],
            [
              "Image region",
              region ? (
                <span className="font-mono text-xs">
                  [{(region.coordinates as number[]).map((value) => value.toFixed(1)).join(", ")}] px
                </span>
              ) : (
                "none"
              ),
            ],
            ["Flags", (observation.quality_flags ?? []).map(flagLabel).join(", ") || "none"],
            ["Location", "None for the finding. Boxes are positions in the image; the photograph's own location is under Photo location."],
          ]}
        />
        <div className="border-t border-line pt-3">
          <ReviewPanel observationId={observation.observation_id} projectId={projectId} />
        </div>
      </div>
    </Panel>
  );
}

type View = "results" | "location" | "details";

/** One small fact in the strip under the page title: an icon, a short sentence, and a colour for how it stands. */
function Fact({ icon: Icon, tone, children }: { icon: typeof IconCheck; tone: "ok" | "warn" | "neutral"; children: React.ReactNode }) {
  const colour = tone === "ok" ? "text-ok" : tone === "warn" ? "text-warn" : "text-fg-subtle";
  return (
    <li className="flex items-center gap-1.5 rounded-full border border-line/70 bg-surface px-3 py-1 text-xs text-fg-muted">
      <Icon size={13} className={colour} />
      {children}
    </li>
  );
}

function AnalysisResults({ asset }: { asset: Asset }) {
  const project = useProject();
  const [searchParams] = useSearchParams();
  const [chosenRun, setChosenRun] = useState<string | null>(null);
  const [selected, setSelected] = useState<number | null>(null);
  const [view, setView] = useState<View>("results");
  const [category, setCategory] = useState<string | null>(null);
  const [tool, setTool] = useState<DrawTool>("select");
  const [markLabel, setMarkLabel] = useState("target");
  const [allLabels, setAllLabels] = useState(false);
  const [selectedMark, setSelectedMark] = useState<string | null>(null);
  const photoMarks = usePhotoMarks(asset.id);
  const meta = asset.metadata as { width?: number; height?: number };
  const imageWidth = asset.raster?.width ?? meta.width ?? 1280;
  const imageHeight = asset.raster?.height ?? meta.height ?? 960;
  const { jobs: assetJobs, refresh } = useAssetJobs(asset);
  const locations = useQuery({ queryKey: ["photo-locations", project.id], queryFn: () => listPhotoLocations(project.id) });
  const locationState = (locations.data?.items ?? []).find((item) => item.asset_id === asset.id)?.state ?? "none";

  const jobs = useQuery({
    queryKey: ["jobs", asset.id, "analyze"],
    queryFn: () => listJobs(project.id, { asset_id: asset.id, kind: "analyze", limit: 50 }),
  });
  const finished = (jobs.data?.items ?? []).filter((job) => (job.result as { run_id?: string } | null)?.run_id);
  const runIds = finished.map((job) => (job.result as { run_id: string }).run_id);
  // A link from a job names its run (?run=...). Otherwise the latest run is shown.
  const requested = searchParams.get("run");
  const runId = chosenRun ?? (requested && runIds.includes(requested) ? requested : null) ?? runIds[0] ?? null;

  const run = useQuery({ queryKey: ["run", runId], queryFn: () => getRun(runId!), enabled: runId !== null });
  const observations = useQuery({
    queryKey: ["observations", project.id, runId],
    queryFn: () => listObservations(project.id, { run_id: runId! }),
    enabled: runId !== null,
  });
  const found = observations.data ?? [];
  const drawn = boxes(found);
  const latestAnalysis = (assetJobs.data?.items ?? []).find((job) => job.kind === "analyze");
  const analysing = (assetJobs.data?.items ?? []).some((job) => job.kind === "analyze" && isActive(job));
  const settings = (run.data?.settings ?? {}) as Record<string, unknown>;
  const summary = (run.data?.summary ?? {}) as Record<string, unknown>;

  // What was found, grouped by the model's class, biggest group first.
  const groups = new Map<string, number>();
  for (const { current } of found) groups.set(categoryLabel(current.category), (groups.get(categoryLabel(current.category)) ?? 0) + 1);
  const groupList = [...groups.entries()].sort((a, b) => b[1] - a[1]);
  const needReview = found.filter(({ current }) => current.status === "needs_review").length;

  const runPanel = (
    <div className="space-y-3">
      <RunAnalysis asset={asset} onQueued={refresh} />
      {latestAnalysis ? (
        <div>
          <p className="mb-1.5 text-xs text-fg-subtle">Latest analysis job</p>
          <ul aria-label="Latest analysis job">
            <JobRow job={latestAnalysis} projectId={project.id} onChanged={refresh} />
          </ul>
        </div>
      ) : null}
    </div>
  );

  const results = (
    <div className="space-y-4">
      {runIds.length === 0 ? (
        <Panel title="Analyse this photograph">{runPanel}</Panel>
      ) : (
        <details className="group rounded-xl border border-line/80 bg-surface" open={analysing}>
          <summary className="flex cursor-pointer items-center justify-between gap-2 px-4 py-3 text-[13px] font-medium text-fg">
            Run another analysis
            <span className="text-xs font-normal text-fg-subtle group-open:hidden">choose a model and start</span>
          </summary>
          <div className="border-t border-line/70 p-4">{runPanel}</div>
        </details>
      )}

      <div className="grid grid-cols-1 items-start gap-4 xl:grid-cols-[minmax(0,1fr)_25rem]">
        <div className="min-w-0">
          <AssetPreview
            asset={asset}
            large
            toolbar={
              <MarkToolbar
                tool={tool}
                onTool={setTool}
                label={markLabel}
                onLabel={setMarkLabel}
                classes={[...new Set(found.map(({ current }) => categoryLabel(current.category)))]}
                allLabels={allLabels}
                onAllLabels={setAllLabels}
              />
            }
            overlay={(scale) => (
              <Overlay
                items={drawn}
                selected={selected}
                onSelect={setSelected}
                scale={scale}
                imageWidth={imageWidth}
                imageHeight={imageHeight}
                marks={photoMarks.marks}
                selectedMark={selectedMark}
                onSelectMark={setSelectedMark}
                tool={tool}
                showAllLabels={allLabels}
                onDraw={(shape) => setSelectedMark(photoMarks.add(markLabel, shape).id)}
              />
            )}
          />
        </div>

        <div className="min-w-0 space-y-4 xl:sticky xl:top-14 xl:max-h-[calc(100vh-4.5rem)] xl:overflow-y-auto xl:pr-1" aria-label="Targets">
         <Notice tone="warning" title="Model predictions, not confirmed findings">
          Scores are the model's own values, not probabilities. No threshold has been validated, so every finding needs review by a person. Boxes are positions in the image only: no geographic location is established.
         </Notice>
         <div className="min-w-0 space-y-4">
          {runIds.length === 0 ? (
            jobs.isLoading ? (
              <Loading />
            ) : (
              <Panel title="Findings">
                <EmptyState title="This photograph has not been analysed yet">
                  Choose a model and press Run analysis. Results appear here when the job finishes.
                </EmptyState>
              </Panel>
            )
          ) : (
            <Panel
              title="Findings"
              description={run.data ? `${found.length} stored · run of ${formatDateTime(run.data.started_at)}` : undefined}
              padded={false}
            >
              {runIds.length > 1 ? (
                <div className="border-b border-line/70 px-4 py-3">
                  <label htmlFor="run-select" className="mb-1 block text-xs text-fg-muted">
                    Analysis run
                  </label>
                  <select
                    id="run-select"
                    className={selectClass}
                    value={runId ?? ""}
                    onChange={(event) => {
                      setChosenRun(event.target.value);
                      setSelected(null);
                      setCategory(null);
                    }}
                  >
                    {finished.map((job, position) => (
                      <option key={job.id} value={runIds[position]}>
                        {formatDateTime(job.created_at)} — {(job.payload as { model?: { name?: string } }).model?.name}
                        {position === 0 ? " (latest)" : ""}
                      </option>
                    ))}
                  </select>
                </div>
              ) : null}
              {observations.isLoading ? <Loading className="p-4" label="Loading findings…" /> : null}
              {observations.isError ? (
                <div className="p-4">
                  <ErrorNotice error={observations.error} />
                </div>
              ) : null}
              {observations.isSuccess && found.length === 0 ? (
                <EmptyState title="No findings">
                  The model reported nothing above its cutoff in this photograph. That is a valid result, not an error.
                </EmptyState>
              ) : null}
              {found.length > 0 ? (
                <>
                  <div className="space-y-2 border-b border-line/70 px-4 py-3">
                    <p className="text-[13px] text-fg">
                      <span className="font-semibold">{found.length}</span> objects marked,{" "}
                      <span className="font-semibold">{needReview}</span> waiting for a person to review.
                    </p>
                    <div role="group" aria-label="Show by kind" className="flex flex-wrap gap-1.5">
                      <button
                        type="button"
                        aria-pressed={category === null}
                        onClick={() => setCategory(null)}
                        className={`rounded-full border px-2.5 py-0.5 text-xs transition-colors ${
                          category === null ? "border-accent bg-accent-soft text-fg" : "border-line text-fg-muted hover:bg-overlay"
                        }`}
                      >
                        All {found.length}
                      </button>
                      {groupList.map(([key, count]) => (
                        <button
                          key={key}
                          type="button"
                          aria-pressed={category === key}
                          onClick={() => setCategory(category === key ? null : key)}
                          className={`rounded-full border px-2.5 py-0.5 text-xs transition-colors ${
                            category === key ? "border-accent bg-accent-soft text-fg" : "border-line text-fg-muted hover:bg-overlay"
                          }`}
                        >
                          <span
                            aria-hidden="true"
                            className="mr-1.5 inline-block size-2 rounded-full align-middle"
                            style={{ background: classColour(key) }}
                          />
                          {key} {count}
                        </button>
                      ))}
                    </div>
                  </div>
                  <Detections observations={found} selected={selected} onSelect={setSelected} category={category} />
                  <p className="border-t border-line/70 px-4 py-2 text-xs text-fg-subtle">
                    Select a row or a box on the photo to see its details and review it.
                  </p>
                </>
              ) : null}
            </Panel>
          )}

         </div>
         <div className="min-w-0 space-y-4">
          {selected !== null && found[selected - 1] ? (
            <SelectedDetection key={found[selected - 1]!.observation.observation_id} view={found[selected - 1]!} index={selected} projectId={project.id} />
          ) : null}

          <PhotoMarksPanel
            marks={photoMarks.marks}
            selected={selectedMark}
            onSelect={setSelectedMark}
            onUpdate={photoMarks.update}
            onRemove={(id) => {
              photoMarks.remove(id);
              if (selectedMark === id) setSelectedMark(null);
            }}
            onClear={() => {
              photoMarks.clear();
              setSelectedMark(null);
            }}
          />
         </div>
        </div>
      </div>
    </div>
  );

  const details = run.data ? (
    <Panel title="How this was produced" description="For anyone who needs to check or repeat the analysis.">
      <div className="space-y-3">
        <DescriptionList
          rows={[
            ["Model", `${run.data.model_name} ${run.data.model_version}`],
            ["Score meaning", found[0]?.observation.score.semantics ?? "—"],
            [
              "Cutoff used",
              `${String(settings.min_score ?? "none")} — an operating setting, not a validated threshold. ${String(summary.below_min_score ?? 0)} raw detection(s) fell below it and were not stored.`,
            ],
            ["Device", run.data.device ?? "unknown"],
            ["Run", <span className="font-mono text-xs">{run.data.id}</span>],
            ["Started", formatDateTime(run.data.started_at)],
            ["Source file", asset.original_filename],
          ]}
        />
        <details className="text-xs">
          <summary className="cursor-pointer text-fg-muted">Advanced technical details</summary>
          <div className="mt-2 space-y-2">
            <DescriptionList
              rows={[
                ["Weights SHA-256", <span className="font-mono">{run.data.weights_sha256 ?? "not applicable"}</span>],
                ["Source checksum", <span className="font-mono">{asset.sha256}</span>],
                ["Inference size", Array.isArray(settings.inference_size) ? `${settings.inference_size.join(" × ")} px` : "—"],
                ["Source size", Array.isArray(settings.source_size) ? `${settings.source_size.join(" × ")} px` : "—"],
                ["Model load", summary.load_ms != null ? `${String(summary.load_ms)} ms` : "—"],
                ["Inference", summary.inference_ms != null ? `${String(summary.inference_ms)} ms` : "—"],
                ["GPU memory peak", summary.gpu_peak_memory_mb != null ? `${String(summary.gpu_peak_memory_mb)} MB` : "—"],
                ["Raw detections", summary.raw_detections != null ? String(summary.raw_detections) : "—"],
                [
                  "Libraries",
                  Object.entries(run.data.library_versions)
                    .map(([name, version]) => `${name} ${String(version)}`)
                    .join(", ") || "—",
                ],
              ]}
            />
            <p className="text-fg-subtle">
              Box coordinates are in the original photograph's pixel grid (top-left origin, EXIF orientation not
              applied), scaled back from the reduced image the model saw.
            </p>
          </div>
        </details>
      </div>
    </Panel>
  ) : (
    <Panel title="How this was produced">
      <p className="text-[13px] text-fg-muted">Nothing to show yet: this photograph has not been analysed.</p>
    </Panel>
  );

  const locationLabel = locationState === "confirmed" ? "Confirmed" : locationState === "candidate" ? "GPS not confirmed" : "Not set";

  const main = (
    <div className="space-y-4">
      <ul aria-label="At a glance" className="flex flex-wrap gap-2">
        {runIds.length === 0 ? (
          <Fact icon={analysing ? IconClock : IconAlert} tone={analysing ? "neutral" : "warn"}>
            {analysing ? "Analysis in progress" : "Not analysed yet"}
          </Fact>
        ) : (
          <Fact icon={IconCheck} tone="ok">
            Analysed{run.data ? ` ${formatDateTime(run.data.started_at)}` : ""}
          </Fact>
        )}
        {found.length > 0 ? (
          <Fact icon={needReview > 0 ? IconAlert : IconCheck} tone={needReview > 0 ? "warn" : "ok"}>
            {found.length} marked · {needReview} to review
          </Fact>
        ) : null}
        <Fact icon={locationState === "confirmed" ? IconCheck : IconAlert} tone={locationState === "confirmed" ? "ok" : "warn"}>
          Location: {locationLabel}
        </Fact>
      </ul>

      <Tabs
        label="Photo Analysis sections"
        value={view}
        onChange={setView}
        tabs={[
          { value: "results", label: "Results" },
          { value: "location", label: "Photo location" },
          { value: "details", label: "How it was produced" },
        ]}
      />

      <div role="tabpanel" hidden={view !== "results"}>
        {results}
      </div>
      {/* The location panel holds a map, which needs a visible box to size itself: it is built only while its tab is open. */}
      {view === "location" ? (
        <div role="tabpanel" className="max-w-3xl">
          <PhotoLocationPanel asset={asset} />
        </div>
      ) : null}
      <div role="tabpanel" hidden={view !== "details"} className="max-w-3xl">
        {details}
      </div>
    </div>
  );

  return (
    <div className="space-y-4">
      <PhotoStrip projectId={project.id} selectedId={asset.id} />
      {main}
    </div>
  );
}

export function GroundAnalysisPage() {
  const project = useProject();
  const { assetId } = useParams();
  const asset = useAsset(assetId);
  const photos = useAssets(project.id, "ground_image");

  const header = (subtitle?: string) => (
    <>
      <Breadcrumbs
        items={projectCrumbs(
          project,
          { label: "Photo Analysis", to: `/projects/${project.id}/ground` },
          ...(subtitle ? [{ label: subtitle }] : []),
        )}
      />
      <div className="mt-2">
        <PageHeader
          title="Photo Analysis"
          description={subtitle ? undefined : "General-purpose object detection in ground photographs, run on this computer."}
          meta={subtitle ? <span className="wrap-anywhere text-fg-muted">{subtitle}</span> : undefined}
          actions={
            assetId ? (
              <LinkButton variant="secondary" to={`/projects/${project.id}/assets/${assetId}`}>
                File details
              </LinkButton>
            ) : undefined
          }
        />
      </div>
    </>
  );

  if (!assetId) {
    if (photos.isLoading) return <Loading label="Loading photographs…" />;
    if (photos.isError) return <ErrorNotice error={photos.error} />;
    const first = photos.data!.items[0];
    if (first) return <Navigate to={`/projects/${project.id}/assets/${first.id}/ground`} replace />;
    return (
      <div>
        {header()}
        <EmptyState
          icon={<IconImage size={28} />}
          title="No ground photographs in this project"
          action={
            <LinkButton to={`/projects/${project.id}/assets/new`}>
              <IconUpload size={15} />
              Upload photographs
            </LinkButton>
          }
        >
          Upload JPEG, PNG or WebP photographs, then analyse them here.
        </EmptyState>
      </div>
    );
  }

  if (asset.isLoading) return <Loading label="Loading photograph…" />;
  if (asset.isError) return <ErrorNotice error={asset.error} />;
  const file = asset.data!;
  if (file.project_id !== project.id) return <Notice tone="error" title="This asset belongs to a different project" />;
  if (file.modality !== "ground_image") {
    return (
      <div>
        {header(file.original_filename)}
        <Notice tone="error" title="This screen shows ground-photograph analysis; this file is a different kind of input" />
      </div>
    );
  }

  return (
    <div>
      {header(file.original_filename)}
      <AnalysisResults key={file.id} asset={file} />
    </div>
  );
}
