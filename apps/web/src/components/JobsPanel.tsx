import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";

import { type Asset, cancelJob, createJob, type Job, listJobs, retryJob } from "../api/client";
import { formatDateTime } from "../lib/format";
import { isActive, JOB_KIND_LABELS, JOB_STATUS_LABELS, jobTone } from "../lib/jobs";
import { useModelHealth } from "../lib/queries";
import { isVideoJob, NOT_RETRYABLE_VIDEO } from "../lib/video";
import { Badge, Button, ErrorNotice, Loading, Notice, Panel, selectClass } from "./ui";

const POLL_MS = 1500;
// Failures that depend only on the file or the request: running the same job again ends the same way.
const NOT_RETRYABLE = ["PREVIEW_UNSUPPORTED", "PIPELINE_INCOMPATIBLE", "MODEL_UNKNOWN", "ASSET_NOT_FOUND", ...NOT_RETRYABLE_VIDEO];

function count(n: number, one: string, many: string): string {
  return `${n === 0 ? "No" : n} ${n === 1 ? one : many}`;
}

export function resultSummary(job: Job): string | null {
  const result = job.result as {
    observations?: number;
    below_min_score?: number;
    rejected_outputs?: number;
    frames_analysed?: number;
    frames_failed?: number;
  } | null;
  if (job.kind !== "analyze" || !result || result.observations === undefined) return null;
  const parts = [`${count(result.observations, "finding", "findings")} stored`];
  if (result.frames_analysed !== undefined) parts.push(`${count(result.frames_analysed, "frame", "frames")} analysed`);
  if (result.frames_failed) parts.push(`${count(result.frames_failed, "frame", "frames")} could not be taken`);
  if (result.below_min_score) parts.push(`${result.below_min_score} below the model's cutoff, not stored`);
  if (result.rejected_outputs) {
    parts.push(`${count(result.rejected_outputs, "model output", "model outputs")} failed validation`);
  }
  return parts.join(" · ");
}

export function JobStatusBadge({ job }: { job: Pick<Job, "status"> }) {
  return <Badge tone={jobTone(job.status)}>{JOB_STATUS_LABELS[job.status] ?? job.status}</Badge>;
}

export function JobRow({ job, projectId, onChanged }: { job: Job; projectId: string; onChanged: () => void }) {
  const cancel = useMutation({ mutationFn: () => cancelJob(job.id), onSuccess: onChanged });
  const retry = useMutation({ mutationFn: () => retryJob(job.id), onSuccess: onChanged });
  const active = isActive(job);
  const pointless = job.status === "failed" && NOT_RETRYABLE.includes(job.error?.code ?? "");
  const model = (job.payload as { model?: { name?: string; version?: string } }).model;
  const summary = resultSummary(job);
  const runId = job.kind === "analyze" ? ((job.result as { run_id?: string } | null)?.run_id ?? null) : null;
  const video = isVideoJob(job);
  const label = video ? "Video-frame analysis" : (JOB_KIND_LABELS[job.kind] ?? job.kind);

  return (
    <li className="rounded-md border border-line bg-raised p-3 text-[13px]" aria-label={`${label} job`}>
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span className="font-medium text-fg">{label}</span>
        {model ? (
          <span className="wrap-anywhere text-fg-subtle">
            {model.name} {model.version}
          </span>
        ) : null}
        <span className="ml-auto">
          <JobStatusBadge job={job} />
        </span>
      </div>
      <p className="mt-1 text-xs text-fg-subtle">
        Created {formatDateTime(job.created_at)}
        {job.attempt > 1 ? ` · attempt ${job.attempt} of ${job.max_attempts}` : ""}
        {job.retry_of_job_id ? " · retry of an earlier job" : ""}
      </p>

      {active ? (
        <div className="mt-2 space-y-1" role="status">
          <p className="text-fg-muted">
            {job.cancel_requested ? "Stopping…" : job.stage ? `Stage: ${job.stage}` : "Waiting for the worker"}
          </p>
          {job.progress != null ? (
            <progress className="h-1.5 w-full" max={1} value={job.progress} aria-label="Job progress" />
          ) : (
            <p className="text-xs text-fg-subtle">Progress cannot be measured for this step.</p>
          )}
        </div>
      ) : null}

      {summary ? <p className="mt-1.5 text-fg-muted">{summary}</p> : null}
      {job.status === "cancelled" ? (
        <p className="mt-1.5 text-fg-muted">
          {job.has_partial_outputs ? "Some outputs from before the cancellation exist." : "No outputs exist from this job."}
        </p>
      ) : null}
      {job.error ? (
        <div role="alert" className="mt-1.5">
          <p className="text-danger">{job.error.message ?? "The job failed."}</p>
          <p className="font-mono text-[11px] text-fg-subtle">Code: {job.error.code}</p>
        </div>
      ) : null}
      {pointless ? (
        <p className="mt-1 text-xs text-fg-subtle">Running this again would end the same way, so no retry is offered.</p>
      ) : null}
      {cancel.isError ? <ErrorNotice error={cancel.error} /> : null}
      {retry.isError ? <ErrorNotice error={retry.error} /> : null}

      {(active && !job.cancel_requested) || (job.status === "failed" && !pointless) || job.status === "cancelled" || runId ? (
        <div className="mt-2 flex flex-wrap items-center gap-2">
          {active && !job.cancel_requested ? (
            <Button variant="secondary" size="sm" disabled={cancel.isPending} onClick={() => cancel.mutate()}>
              Cancel
            </Button>
          ) : null}
          {(job.status === "failed" && !pointless) || job.status === "cancelled" ? (
            <Button variant="secondary" size="sm" disabled={retry.isPending} onClick={() => retry.mutate()}>
              Retry
            </Button>
          ) : null}
          {runId && job.asset_id ? (
            // Each job links to its own run, not simply to the latest one.
            <Link className="text-xs text-accent hover:underline" to={`/projects/${projectId}/assets/${job.asset_id}/${video ? "video" : "ground"}?run=${runId}`}>
              View results
            </Link>
          ) : null}
        </div>
      ) : null}
    </li>
  );
}

/** Choose a ground-image model and queue an analysis. Only offered where a pipeline exists. */
export function RunAnalysis({ asset, onQueued }: { asset: Asset; onQueued: () => void }) {
  const models = useModelHealth();
  const [chosen, setChosen] = useState("");
  const start = useMutation({
    mutationFn: (model: { name: string; version: string }) =>
      createJob(asset.project_id, { kind: "analyze", asset_id: asset.id, pipeline: "ground_image", model, submit: true }),
    onSuccess: onQueued,
  });

  if (asset.modality === "video") {
    return (
      <Notice title="Video is analysed from the Videos screen">
        There you choose how often a frame is taken, see how many frames that means, and start the analysis yourself.
      </Notice>
    );
  }
  if (asset.modality !== "ground_image") {
    return (
      <Notice title="Analysis is not available for this kind of input yet">
        Only ground photographs and video frames can be analysed so far. Raster analysis is not implemented.
      </Notice>
    );
  }
  if (models.isLoading) return <Loading label="Loading models…" />;
  if (models.isError) return <ErrorNotice error={models.error} />;
  const options = models.data!.models.filter((model) => model.pipeline === "ground_image");
  if (options.length === 0) {
    return <Notice title="No ground-image model is installed">Analysis is unavailable until a model is installed.</Notice>;
  }
  const key = (model: { name: string; version: string }) => `${model.name}@${model.version}`;
  const selected = options.find((model) => key(model) === chosen) ?? options[0]!;

  return (
    <div className="space-y-2.5">
      <div className="flex flex-wrap items-end gap-2">
        <div className="min-w-48 flex-1">
          <label htmlFor="analysis-model" className="mb-1 block text-[13px] font-medium">
            Model
          </label>
          <select
            id="analysis-model"
            className={selectClass}
            value={key(selected)}
            onChange={(event) => setChosen(event.target.value)}
          >
            {options.map((model) => (
              <option key={key(model)} value={key(model)}>
                {model.name} {model.version}
                {model.available ? "" : " — unavailable"}
              </option>
            ))}
          </select>
        </div>
        <Button disabled={!selected.available || start.isPending} onClick={() => start.mutate({ name: selected.name, version: selected.version })}>
          Run analysis
        </Button>
      </div>
      {!selected.available ? (
        <Notice tone="warning" title="This model cannot be run">
          {selected.reason_unavailable}
        </Notice>
      ) : null}
      {selected.available && !models.data!.worker.running ? (
        <Notice tone="warning" title="No worker is running">
          The job will be queued and will start when a worker is started.
        </Notice>
      ) : null}
      <p className="text-xs text-fg-subtle">
        Score: {selected.score_semantics}{" "}
        {selected.score_calibrated ? "" : "No validated threshold exists, so every finding will need review."}
      </p>
      {start.isError ? <ErrorNotice error={start.error} /> : null}
    </div>
  );
}

/** Jobs for one asset, refreshed while any is active. When one finishes, what it produced is reloaded. */
export function useAssetJobs(asset: Asset) {
  const queryClient = useQueryClient();
  const jobs = useQuery({
    queryKey: ["jobs", asset.id],
    queryFn: () => listJobs(asset.project_id, { asset_id: asset.id, limit: 20 }),
    refetchInterval: (query) => (query.state.data?.items.some(isActive) ? POLL_MS : false),
  });
  const activeCount = (jobs.data?.items ?? []).filter(isActive).length;
  const previous = useRef(activeCount);
  useEffect(() => {
    if (activeCount < previous.current) {
      void queryClient.invalidateQueries({ queryKey: ["asset", asset.id] });
      void queryClient.invalidateQueries({ queryKey: ["assets", asset.project_id] });
      void queryClient.invalidateQueries({ queryKey: ["jobs", asset.id, "analyze"] });
      void queryClient.invalidateQueries({ queryKey: ["observations", asset.project_id] });
      void queryClient.invalidateQueries({ queryKey: ["video-frames", asset.project_id] });
      void queryClient.invalidateQueries({ queryKey: ["summary", asset.project_id] });
    }
    previous.current = activeCount;
  }, [activeCount, asset.id, asset.project_id, queryClient]);
  const refresh = () => void queryClient.invalidateQueries({ queryKey: ["jobs", asset.id] });
  return { jobs, refresh };
}

/** Background jobs for one asset: what is queued or running, how it ended, and the controls to act on it. */
export function JobsPanel({ asset, showRun = true }: { asset: Asset; showRun?: boolean }) {
  const { jobs, refresh } = useAssetJobs(asset);
  const items = jobs.data?.items ?? [];
  return (
    <Panel title="Analysis and background jobs" description="Previews and analyses run in the background worker.">
      <div className="space-y-3">
        {showRun ? <RunAnalysis asset={asset} onQueued={refresh} /> : null}
        {jobs.isLoading ? <Loading label="Loading jobs…" /> : null}
        {jobs.isError ? <ErrorNotice error={jobs.error} /> : null}
        {jobs.isSuccess && items.length === 0 ? <p className="text-[13px] text-fg-muted">No jobs for this file yet.</p> : null}
        {items.length > 0 ? (
          <ul className="space-y-2" aria-label="Jobs for this file">
            {items.map((job) => (
              <JobRow key={job.id} job={job} projectId={asset.project_id} onChanged={refresh} />
            ))}
          </ul>
        ) : null}
      </div>
    </Panel>
  );
}
