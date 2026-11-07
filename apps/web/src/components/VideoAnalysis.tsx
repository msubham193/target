import { useMutation, useQuery } from "@tanstack/react-query";
import { type RefObject, useState } from "react";
import { useSearchParams } from "react-router-dom";

import {
  type Asset,
  createJob,
  getRun,
  getVideoFrames,
  getVideoSamplingPlan,
  listJobs,
  listObservations,
  type ObservationView,
  type VideoFrame,
  type VideoFrames,
  videoFrameUrl,
} from "../api/client";
import { formatDateTime } from "../lib/format";
import { categoryLabel, isActive, REVIEW_STATUS_LABELS, reviewTone } from "../lib/jobs";
import { useModelHealth } from "../lib/queries";
import {
  CONFIDENCE_WARNING,
  DEFAULT_SAMPLING,
  describeSampling,
  formatTimestamp,
  frameOf,
  isVideoJob,
  NO_LOCATION_WARNING,
  NO_TRACKING_WARNING,
  percentAlong,
  SAMPLING_WARNING,
  timelineSpan,
} from "../lib/video";
import { boxes, DetectionOverlay } from "./DetectionOverlay";
import { ReviewPanel } from "./ReviewPanel";
import { Badge, Button, DescriptionList, EmptyState, ErrorNotice, inputClass, Loading, Notice, Panel, selectClass } from "./ui";

/** All the state of one video's analysis results, read from what is stored. Opening a result never starts a job. */
export function useVideoAnalysis(asset: Asset) {
  const [searchParams] = useSearchParams();
  const [chosenRun, setChosenRun] = useState<string | null>(null);
  const [chosenFrame, setChosenFrame] = useState<number | null>(null);
  const [findingId, setFindingId] = useState<string | null>(null);

  const jobs = useQuery({
    queryKey: ["jobs", asset.id, "analyze"],
    queryFn: () => listJobs(asset.project_id, { asset_id: asset.id, kind: "analyze", limit: 50 }),
  });
  const finished = (jobs.data?.items ?? []).filter((job) => isVideoJob(job) && (job.result as { run_id?: string } | null)?.run_id);
  const runIds = finished.map((job) => (job.result as { run_id: string }).run_id);
  const requested = searchParams.get("run");
  const runId = chosenRun ?? (requested && runIds.includes(requested) ? requested : null) ?? runIds[0] ?? null;

  const frames = useQuery({
    queryKey: ["video-frames", asset.project_id, asset.id, runId],
    queryFn: () => getVideoFrames(asset.project_id, asset.id, runId!),
    enabled: runId !== null,
  });
  const run = useQuery({ queryKey: ["run", runId], queryFn: () => getRun(runId!), enabled: runId !== null });
  const all = useQuery({
    queryKey: ["observations", asset.project_id, "video-run", runId],
    queryFn: () => listObservations(asset.project_id, { run_id: runId!, limit: 500 }),
    enabled: runId !== null,
  });

  const list = frames.data?.frames ?? [];
  // Something is always shown for a stored run: the first frame that has findings, otherwise the first frame.
  const frameIndex = chosenFrame ?? list.find((f) => f.finding_count > 0)?.frame_index ?? list[0]?.frame_index ?? null;
  const frame = list.find((f) => f.frame_index === frameIndex) ?? null;
  const inFrame = useQuery({
    queryKey: ["observations", asset.project_id, "video-frame", runId, frameIndex],
    queryFn: () => listObservations(asset.project_id, { run_id: runId!, frame_index: frameIndex!, limit: 500 }),
    enabled: runId !== null && frameIndex !== null,
  });

  return {
    asset,
    jobs,
    finished,
    runIds,
    runId,
    chooseRun: (id: string) => {
      setChosenRun(id);
      setChosenFrame(null);
      setFindingId(null);
    },
    frames,
    run,
    all,
    inFrame,
    frame,
    selectFrame: (index: number) => {
      setChosenFrame(index);
      setFindingId(null);
    },
    findingId,
    selectFinding: setFindingId,
  };
}
export type VideoAnalysisState = ReturnType<typeof useVideoAnalysis>;

// --- starting an analysis ----------------------------------------------------------------------------------------------

const toNumber = (text: string): number | undefined => (text.trim() === "" || !Number.isFinite(Number(text)) ? undefined : Number(text));

/** The one place an analysis is started: a person chooses the model and the sampling, sees what it would take, and presses Start. */
export function RunVideoAnalysis({ asset, onQueued }: { asset: Asset; onQueued: () => void }) {
  const models = useModelHealth();
  const [chosen, setChosen] = useState("");
  const [interval, setIntervalText] = useState(String(DEFAULT_SAMPLING.interval_s));
  const [maxFrames, setMaxFrames] = useState(String(DEFAULT_SAMPLING.max_frames));
  const [startText, setStartText] = useState("");
  const [endText, setEndText] = useState("");

  const sampling = {
    interval_s: toNumber(interval),
    max_frames: toNumber(maxFrames),
    start_s: toNumber(startText),
    end_s: toNumber(endText),
  };
  const valid = sampling.interval_s !== undefined && sampling.interval_s > 0 && sampling.max_frames !== undefined && Number.isInteger(sampling.max_frames) && sampling.max_frames >= 1;
  const plan = useQuery({
    queryKey: ["video-plan", asset.project_id, asset.id, sampling],
    queryFn: () =>
      getVideoSamplingPlan(asset.project_id, asset.id, {
        interval_s: sampling.interval_s!,
        max_frames: sampling.max_frames!,
        ...(sampling.start_s !== undefined ? { start_s: sampling.start_s } : {}),
        ...(sampling.end_s !== undefined ? { end_s: sampling.end_s } : {}),
      }),
    enabled: valid,
    retry: false,
  });
  const start = useMutation({
    mutationFn: (model: { name: string; version: string }) =>
      createJob(asset.project_id, {
        kind: "analyze",
        asset_id: asset.id,
        pipeline: "video",
        model,
        sampling: {
          interval_s: sampling.interval_s!,
          max_frames: sampling.max_frames!,
          start_s: sampling.start_s ?? 0,
          ...(sampling.end_s !== undefined ? { end_s: sampling.end_s } : {}),
        },
        submit: true,
      }),
    onSuccess: onQueued,
  });

  if (models.isLoading) return <Loading label="Loading models…" />;
  if (models.isError) return <ErrorNotice error={models.error} />;
  // A frame is an image, so the installed image detectors are the models that can analyse frames.
  const options = models.data!.models.filter((model) => model.pipeline === "ground_image");
  if (options.length === 0) return <Notice title="No model is installed">Frame analysis is unavailable until a model is installed.</Notice>;
  const key = (model: { name: string; version: string }) => `${model.name}@${model.version}`;
  const selected = options.find((model) => key(model) === chosen) ?? options[0]!;
  const blocked = !valid || plan.isError || plan.isFetching || !selected.available || start.isPending;

  return (
    <form
      className="space-y-3"
      aria-label="Analyse frames"
      onSubmit={(event) => {
        event.preventDefault();
        if (!blocked) start.mutate({ name: selected.name, version: selected.version });
      }}
    >
      <div className="grid gap-2 sm:grid-cols-2">
        <div className="sm:col-span-2">
          <label htmlFor="video-model" className="mb-1 block text-[13px] font-medium">
            Model
          </label>
          <select id="video-model" className={selectClass} value={key(selected)} onChange={(event) => setChosen(event.target.value)}>
            {options.map((model) => (
              <option key={key(model)} value={key(model)}>
                {model.name} {model.version}
                {model.custom ? " (uploaded)" : ""}
                {model.available ? "" : " — unavailable"}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label htmlFor="video-interval" className="mb-1 block text-[13px] font-medium">
            Take a frame every (seconds)
          </label>
          <input id="video-interval" className={inputClass} inputMode="decimal" value={interval} onChange={(event) => setIntervalText(event.target.value)} />
        </div>
        <div>
          <label htmlFor="video-max-frames" className="mb-1 block text-[13px] font-medium">
            At most this many frames
          </label>
          <input id="video-max-frames" className={inputClass} inputMode="numeric" value={maxFrames} onChange={(event) => setMaxFrames(event.target.value)} />
        </div>
        <div>
          <label htmlFor="video-start" className="mb-1 block text-[13px] font-medium">
            From (seconds, optional)
          </label>
          <input id="video-start" className={inputClass} inputMode="decimal" value={startText} onChange={(event) => setStartText(event.target.value)} />
        </div>
        <div>
          <label htmlFor="video-end" className="mb-1 block text-[13px] font-medium">
            To (seconds, optional)
          </label>
          <input id="video-end" className={inputClass} inputMode="decimal" value={endText} onChange={(event) => setEndText(event.target.value)} />
        </div>
      </div>

      <div aria-live="polite" className="text-[13px]">
        {!valid ? <p className="text-fg-muted">Enter an interval and a maximum number of frames.</p> : null}
        {plan.isFetching ? <p className="text-fg-subtle">Working out how many frames this takes…</p> : null}
        {plan.isError ? <ErrorNotice error={plan.error} title="This sampling cannot be used" /> : null}
        {plan.data && !plan.isFetching ? (
          <div className="space-y-1">
            <p className="text-fg">
              {plan.data.planned_frames === null
                ? "The number of frames is not known yet."
                : `${plan.data.planned_frames} frame${plan.data.planned_frames === 1 ? "" : "s"} would be analysed: ${describeSampling(plan.data.sampling)}.`}
            </p>
            <p className="text-xs text-fg-subtle">{plan.data.basis}</p>
            {plan.data.warnings.map((warning) => (
              <p key={warning} className="text-xs text-warn">
                {warning}
              </p>
            ))}
          </div>
        ) : null}
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
      <div className="flex flex-wrap items-center gap-3">
        <Button type="submit" disabled={blocked}>
          Analyse these frames
        </Button>
        <p className="text-xs text-fg-subtle">Runs on this computer only. Nothing starts until you press the button. Frames are never sent anywhere.</p>
      </div>
      {start.isError ? <ErrorNotice error={start.error} /> : null}
    </form>
  );
}

// --- timeline and frames ------------------------------------------------------------------------------------------------

export function VideoTimeline({ v, durationMs, timeMs }: { v: VideoAnalysisState; durationMs: number | null; timeMs: number }) {
  const frames = v.frames.data?.frames ?? [];
  if (frames.length === 0) return null;
  const span = timelineSpan(durationMs, frames);
  return (
    <div role="group" aria-label="Sampled frames on the video timeline" className="rounded-lg border border-line bg-surface px-3 py-2.5">
      <div className="relative mx-1.5 h-7">
        <div className="absolute inset-x-0 top-1/2 h-px bg-line-strong" />
        <div
          data-testid="playhead"
          className="absolute top-0 h-full w-px bg-fg-muted"
          style={{ left: `${percentAlong(timeMs, span)}%` }}
          aria-hidden="true"
        />
        {frames.map((frame) => {
          const active = v.frame?.frame_index === frame.frame_index;
          return (
            <button
              key={frame.frame_index}
              type="button"
              aria-label={`Frame ${frame.frame_index} at ${formatTimestamp(frame.timestamp_ms)}, ${frame.finding_count} finding${frame.finding_count === 1 ? "" : "s"}`}
              aria-pressed={active}
              onClick={() => v.selectFrame(frame.frame_index)}
              style={{ left: `calc(${percentAlong(frame.timestamp_ms, span)}% - 6px)` }}
              className={`absolute top-1 h-5 w-3 rounded-sm border ${active ? "border-warn bg-warn" : frame.finding_count > 0 ? "border-accent bg-accent" : "border-line-strong bg-raised"} focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent`}
            />
          );
        })}
      </div>
      <div className="mt-1 flex justify-between font-mono text-[11px] text-fg-subtle">
        <span>0:00.000</span>
        <span>{formatTimestamp(span)}</span>
      </div>
      <p className="mt-1.5 text-xs text-fg-subtle">
        {frames.length} sampled frame{frames.length === 1 ? "" : "s"}, {frames.filter((f) => f.finding_count > 0).length} with findings. Each mark is one analysed
        frame; the video between the marks was not analysed. Filled marks have findings.
      </p>
    </div>
  );
}

export function FrameStrip({ v }: { v: VideoAnalysisState }) {
  const frames = v.frames.data?.frames ?? [];
  if (frames.length === 0) return null;
  return (
    <ul aria-label="Sampled frames" className="flex gap-2 overflow-x-auto pb-1">
      {frames.map((frame) => {
        const active = v.frame?.frame_index === frame.frame_index;
        return (
          <li key={frame.frame_index} className="shrink-0">
            <button
              type="button"
              onClick={() => v.selectFrame(frame.frame_index)}
              aria-pressed={active}
              aria-label={`Select frame ${frame.frame_index}`}
              className={`block w-32 overflow-hidden rounded-md border text-left ${active ? "border-warn ring-1 ring-warn" : "border-line hover:border-fg-subtle"}`}
            >
              <img src={videoFrameUrl(frame)} alt={`Frame ${frame.frame_index} at ${formatTimestamp(frame.timestamp_ms)}`} loading="lazy" className="block h-20 w-full bg-black object-cover" />
              <span className="flex items-center justify-between gap-1 px-1.5 py-1 text-[11px]">
                <span className="font-mono text-fg-muted">{formatTimestamp(frame.timestamp_ms)}</span>
                <span className={frame.finding_count > 0 ? "text-accent" : "text-fg-subtle"}>
                  {frame.finding_count} {frame.finding_count === 1 ? "finding" : "findings"}
                </span>
              </span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}

export function FrameViewer({ v, onSeek }: { v: VideoAnalysisState; onSeek: (ms: number) => void }) {
  const frame = v.frame;
  if (!frame) return null;
  const views = v.inFrame.data ?? [];
  const drawn = boxes(views);
  const selectedIndex = v.findingId ? views.findIndex((view) => view.observation.observation_id === v.findingId) + 1 : 0;
  return (
    <Panel
      title={`Frame ${frame.frame_index} · ${formatTimestamp(frame.timestamp_ms)}`}
      description="The stored frame image the model was run on, with its model-predicted regions."
      actions={
        <Button size="sm" variant="secondary" onClick={() => onSeek(frame.timestamp_ms)}>
          Show in the video player
        </Button>
      }
    >
      <div className="space-y-3">
        <div className="relative overflow-hidden rounded-md bg-black" data-testid="frame-viewer">
          <img src={videoFrameUrl(frame)} alt={`Frame ${frame.frame_index} of the video at ${formatTimestamp(frame.timestamp_ms)}`} className="block h-auto w-full" />
          <DetectionOverlay
            items={drawn}
            selected={selectedIndex > 0 ? selectedIndex : null}
            onSelect={(index) => v.selectFinding(views[index - 1]?.observation.observation_id ?? null)}
          />
        </div>
        {v.inFrame.isLoading ? <Loading label="Loading findings for this frame…" /> : null}
        {v.inFrame.isError ? <ErrorNotice error={v.inFrame.error} /> : null}
        {v.inFrame.isSuccess && views.length === 0 ? (
          <p className="text-[13px] text-fg-muted">No findings in this frame: the model reported nothing above its cutoff here. That is a valid result, not an error.</p>
        ) : null}
        <p className="text-xs text-fg-subtle">
          Frame {frame.frame_index} (position in presentation order) at {formatTimestamp(frame.timestamp_ms)} ({frame.timestamp_ms} ms from the first frame).
          {frame.also_stands_for_ms.length > 0
            ? ` It was also the frame on screen at ${frame.also_stands_for_ms.map((ms) => formatTimestamp(ms)).join(", ")}, so it was analysed once.`
            : ""}
        </p>
      </div>
    </Panel>
  );
}

// --- findings -----------------------------------------------------------------------------------------------------------

function FindingRow({ view, frame, active, onSelect }: { view: ObservationView; frame: { frameIndex: number; timestampMs: number }; active: boolean; onSelect: () => void }) {
  const { observation, current } = view;
  return (
    <li>
      <button
        type="button"
        onClick={onSelect}
        aria-pressed={active}
        aria-label={`Finding ${categoryLabel(current.category)} in frame ${frame.frameIndex} at ${formatTimestamp(frame.timestampMs)}`}
        className={`flex w-full flex-wrap items-center gap-x-2 gap-y-0.5 rounded-md px-2 py-1.5 text-left text-[13px] ${active ? "bg-warn-soft" : "hover:bg-raised"}`}
      >
        <span className="font-medium text-fg">{categoryLabel(current.category)}</span>
        <span className="font-mono text-xs text-fg-muted">{observation.score.raw_value == null ? "—" : observation.score.raw_value.toFixed(3)}</span>
        <span className="text-xs text-fg-subtle">{observation.score.calibrated ? "calibrated" : "uncalibrated"}</span>
        <Badge tone={reviewTone(current.status)} className="ml-auto">
          {REVIEW_STATUS_LABELS[current.status] ?? current.status}
        </Badge>
      </button>
    </li>
  );
}

export function VideoFindings({ v, onSeek }: { v: VideoAnalysisState; onSeek: (ms: number) => void }) {
  const frames = v.frames.data;
  const found = v.all.data ?? [];
  // Grouped by frame, so every finding sits under the frame it belongs to. A frame heading selects that frame.
  const groups = new Map<number, { timestampMs: number; views: ObservationView[] }>();
  for (const view of found) {
    const where = frameOf(view);
    if (!where) continue;
    const group = groups.get(where.frameIndex) ?? { timestampMs: where.timestampMs, views: [] };
    group.views.push(view);
    groups.set(where.frameIndex, group);
  }
  const ordered = [...groups.entries()].sort((a, b) => a[0] - b[0]);
  const total = frames?.findings_total ?? found.length;
  const selectedView = (v.inFrame.data ?? []).find((view) => view.observation.observation_id === v.findingId) ?? null;

  return (
    <>
      <Panel title="Findings" description={frames ? `${total} stored in ${frames.frames.length} sampled frame${frames.frames.length === 1 ? "" : "s"}` : undefined} padded={false}>
        <RunChooser v={v} />
        {v.all.isLoading ? <Loading className="p-4" label="Loading findings…" /> : null}
        {v.all.isError ? (
          <div className="p-4">
            <ErrorNotice error={v.all.error} />
          </div>
        ) : null}
        {v.all.isSuccess && found.length === 0 ? (
          <EmptyState title="No findings in the sampled frames">The model reported nothing above its cutoff in any analysed frame. That is a valid result, not an error.</EmptyState>
        ) : null}
        {ordered.length > 0 ? (
          <div className="max-h-96 space-y-2 overflow-y-auto p-2">
            {ordered.map(([frameIndex, group]) => (
              <section key={frameIndex} aria-label={`Findings in frame ${frameIndex}`}>
                <button
                  type="button"
                  onClick={() => v.selectFrame(frameIndex)}
                  className="mb-0.5 flex w-full items-center gap-2 rounded-md px-2 py-1 text-left text-xs font-medium text-fg-muted hover:bg-raised"
                  aria-label={`Go to frame ${frameIndex} at ${formatTimestamp(group.timestampMs)}`}
                >
                  <span>Frame {frameIndex}</span>
                  <span className="font-mono text-fg-subtle">{formatTimestamp(group.timestampMs)}</span>
                </button>
                <ul>
                  {group.views.map((view) => (
                    <FindingRow
                      key={view.observation.observation_id}
                      view={view}
                      frame={{ frameIndex, timestampMs: group.timestampMs }}
                      active={v.findingId === view.observation.observation_id}
                      onSelect={() => {
                        v.selectFrame(frameIndex);
                        v.selectFinding(view.observation.observation_id);
                      }}
                    />
                  ))}
                </ul>
              </section>
            ))}
          </div>
        ) : null}
        {total > found.length ? (
          <p className="px-4 py-2 text-xs text-warn">
            Showing the first {found.length} of {total} findings, highest score first. The rest are listed frame by frame when you select that frame.
          </p>
        ) : null}
      </Panel>

      {selectedView ? <SelectedFinding key={selectedView.observation.observation_id} view={selectedView} v={v} onSeek={onSeek} /> : null}
    </>
  );
}

function SelectedFinding({ view, v, onSeek }: { view: ObservationView; v: VideoAnalysisState; onSeek: (ms: number) => void }) {
  const { observation, current } = view;
  const where = frameOf(view);
  const region = observation.evidence[0]?.region_px;
  return (
    <Panel title={`Finding: ${categoryLabel(current.category)}`}>
      <div className="space-y-3">
        <p className="text-[13px] text-fg-muted">{observation.claim.description}</p>
        <DescriptionList
          rows={[
            ["Frame", where ? `${where.frameIndex} at ${formatTimestamp(where.timestampMs)}` : "—"],
            ["Model class", categoryLabel(observation.claim.category)],
            ["Raw score", observation.score.raw_value == null ? "—" : observation.score.raw_value.toFixed(3)],
            ["Calibration", observation.score.calibrated ? "Calibrated" : "Uncalibrated: not a probability"],
            [
              "Region in the frame",
              region ? <span className="font-mono text-xs">[{(region.coordinates as number[]).map((value) => value.toFixed(1)).join(", ")}] px</span> : "none",
            ],
            ["Evidence", <span className="wrap-anywhere font-mono text-[11px]">{observation.evidence[0]?.source_excerpt_or_metadata_key ?? "—"}</span>],
            ["Location", "None. The box is a position in the frame image."],
          ]}
        />
        {where ? (
          <Button size="sm" variant="secondary" onClick={() => onSeek(where.timestampMs)}>
            Show in the video player
          </Button>
        ) : null}
        <div className="border-t border-line pt-3">
          <ReviewPanel observationId={observation.observation_id} projectId={v.asset.project_id} />
        </div>
      </div>
    </Panel>
  );
}

// --- what to read before relying on a result, and how it was produced ---------------------------------------------------

export function VideoWarnings() {
  return (
    <Notice tone="warning" title="Sampled frames, model predictions, not confirmed findings">
      <ul className="list-disc space-y-0.5 pl-4 text-[13px]">
        <li>{SAMPLING_WARNING}</li>
        <li>{CONFIDENCE_WARNING}</li>
        <li>{NO_TRACKING_WARNING}</li>
        <li>{NO_LOCATION_WARNING}</li>
      </ul>
    </Notice>
  );
}

export function VideoProvenance({ v }: { v: VideoAnalysisState }) {
  const frames: VideoFrames | undefined = v.frames.data;
  const run = v.run.data;
  if (!frames || !run) return null;
  const settings = (run.settings ?? {}) as Record<string, unknown>;
  const libraries = run.library_versions as Record<string, unknown>;
  return (
    <Panel title="How this was produced">
      <div className="space-y-3">
        <DescriptionList
          rows={[
            ["Model", `${frames.model_name} ${frames.model_version}`],
            ["Sampling", describeSampling(frames.sampling)],
            ["Frames analysed", `${frames.frames.length} of the video's ${frames.frames_in_file ?? "unknown number of"} frames`],
            ["Cutoff used", `${String(settings.min_score ?? "none")}: an operating setting, not a validated threshold.`],
            ["Device", run.device ?? "unknown"],
            ["Run", <span className="font-mono text-xs">{run.id}</span>],
            ["Started", formatDateTime(run.started_at)],
            ["Source video", <span className="wrap-anywhere font-mono text-[11px]">{frames.source_sha256}</span>],
          ]}
        />
        {frames.failed.length > 0 ? (
          <div role="alert" className="rounded-md border border-warn/40 bg-warn-soft p-2.5 text-xs">
            <p className="font-medium text-fg">
              {frames.failed.length} sampled frame{frames.failed.length === 1 ? "" : "s"} could not be taken, so {frames.failed.length === 1 ? "it was" : "they were"} not analysed:
            </p>
            <ul className="mt-1 space-y-0.5" aria-label="Frames that could not be taken">
              {frames.failed.map((item) => (
                <li key={item.frame_index}>
                  Frame {item.frame_index} at {formatTimestamp(item.timestamp_ms)}: {item.message} <span className="font-mono text-fg-subtle">({item.code})</span>
                </li>
              ))}
            </ul>
          </div>
        ) : null}
        <details className="text-xs">
          <summary className="cursor-pointer text-fg-muted">Advanced technical details</summary>
          <div className="mt-2 space-y-2 text-fg-subtle">
            <p>{frames.policy}</p>
            <p>{frames.frame_numbering}</p>
            <p>{frames.coordinates}</p>
            <p>
              Tool: {String(libraries.ffmpeg ?? "ffmpeg (version not recorded)")}. Weights SHA-256:{" "}
              <span className="font-mono">{run.weights_sha256 ?? "not applicable"}</span>
            </p>
          </div>
        </details>
      </div>
    </Panel>
  );
}

/** Runs of this video (one per finished analysis job), for choosing which one to look at. */
export function RunChooser({ v }: { v: VideoAnalysisState }) {
  if (v.runIds.length < 2) return null;
  return (
    <div className="border-b border-line px-4 py-3">
      <label htmlFor="video-run-select" className="mb-1 block text-xs text-fg-muted">
        Analysis run
      </label>
      <select id="video-run-select" className={selectClass} value={v.runId ?? ""} onChange={(event) => v.chooseRun(event.target.value)}>
        {v.finished.map((job, position) => (
          <option key={job.id} value={v.runIds[position]}>
            {formatDateTime(job.created_at)} — {(job.payload as { model?: { name?: string } }).model?.name}
            {position === 0 ? " (latest)" : ""}
          </option>
        ))}
      </select>
    </div>
  );
}

export function NoRunYet({ v }: { v: VideoAnalysisState }) {
  if (v.jobs.isLoading) return <Loading />;
  if (v.runIds.length > 0) return null;
  const running = (v.jobs.data?.items ?? []).some((job) => isVideoJob(job) && isActive(job));
  return (
    <Panel title="Findings">
      <EmptyState title={running ? "The analysis is running" : "This video has not been analysed yet"}>
        {running ? "Results appear here when the job finishes." : "Choose how to sample it and press Analyse these frames. Nothing runs until you do."}
      </EmptyState>
    </Panel>
  );
}

export type { VideoFrame };
export type PlayerHandle = RefObject<HTMLVideoElement | null>;
