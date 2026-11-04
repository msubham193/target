import { type RefObject, useRef, useState } from "react";
import { Link, Navigate, useParams } from "react-router-dom";

import { type Asset, assetContentUrl, assetPreviewUrl } from "../api/client";
import { AssetThumb } from "../components/AssetPreview";
import { IconStepBack, IconStepForward, IconUpload, IconVideo } from "../components/icons";
import { JobRow, useAssetJobs } from "../components/JobsPanel";
import {
  FrameStrip,
  FrameViewer,
  NoRunYet,
  RunVideoAnalysis,
  useVideoAnalysis,
  VideoFindings,
  VideoProvenance,
  VideoTimeline,
  VideoWarnings,
} from "../components/VideoAnalysis";
import {
  Breadcrumbs,
  Button,
  DescriptionList,
  EmptyState,
  ErrorNotice,
  LinkButton,
  Loading,
  muted,
  Notice,
  PageHeader,
  Panel,
} from "../components/ui";
import { FileList, Workspace } from "../components/Workspace";
import { formatDateTime } from "../lib/format";
import { isVideoJob } from "../lib/video";
import { useAsset, useAssets } from "../lib/queries";
import { UnknownList } from "./AssetDetailPage";
import { projectCrumbs, useProject } from "./ProjectLayout";

function VideoList({ projectId, selectedId }: { projectId: string; selectedId: string | null }) {
  const videos = useAssets(projectId, "video");
  if (videos.isLoading) return <Loading label="Loading videos…" />;
  if (videos.isError) return <ErrorNotice error={videos.error} />;
  return (
    <FileList
      title="Videos"
      items={videos.data!.items}
      selectedId={selectedId}
      empty="No videos in this project."
      renderItem={(item, selected) => {
        const asset = item as Asset;
        return (
          <Link
            to={`/projects/${projectId}/assets/${asset.id}/video`}
            aria-current={selected ? "page" : undefined}
            className={`flex items-center gap-2.5 rounded-md px-2 py-1.5 text-[13px] ${selected ? "bg-accent-soft text-fg" : "text-fg-muted hover:bg-overlay hover:text-fg"}`}
          >
            <AssetThumb asset={asset} className="h-9 w-12" />
            <span className="min-w-0 flex-1">
              <span className="block truncate">{asset.original_filename}</span>
              <span className="block text-[11px] text-fg-subtle">
                {asset.video?.duration_ms != null ? `${(asset.video.duration_ms / 1000).toFixed(1)} s` : "duration unknown"}
              </span>
            </span>
          </Link>
        );
      }}
    />
  );
}

function formatTime(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  return `${minutes}:${(seconds - minutes * 60).toFixed(3).padStart(6, "0")}`;
}

/**
 * Plays the stored original in the browser's own player. Frame stepping uses the container's average frame rate,
 * so the frame number shown is an estimate and is labelled as one.
 */
function Player({ asset, playerRef, onTime }: { asset: Asset; playerRef: RefObject<HTMLVideoElement | null>; onTime: (seconds: number) => void }) {
  const video = asset.video!;
  const element = playerRef;
  const [time, setTime] = useState(0);
  const [unplayable, setUnplayable] = useState(false);
  const rate = video.frame_rate;

  const step = (frames: number) => {
    const player = element.current;
    if (!player || !rate) return;
    player.pause();
    player.currentTime = Math.max(0, Math.min(player.duration || Infinity, player.currentTime + frames / rate));
  };

  return (
    <div className="overflow-hidden rounded-lg border border-line bg-surface">
      {unplayable ? (
        <div className="space-y-3 p-4">
          <Notice tone="warning" title="This browser cannot play this video">
            Its codec ({video.video_codec ?? "unknown"}) is not supported by the browser's built-in player. The file is
            stored and valid; one frame from it is shown instead.
          </Notice>
          {asset.preview.variants.includes("view") ? (
            <img
              src={assetPreviewUrl(asset.id, "view", asset.preview.job_id)}
              alt={`Preview frame of ${asset.original_filename}`}
              className="mx-auto block max-h-[60vh] max-w-full rounded-md"
            />
          ) : null}
        </div>
      ) : (
        <video
          ref={element}
          src={assetContentUrl(asset.id)}
          poster={asset.preview.variants.includes("view") ? assetPreviewUrl(asset.id, "view", asset.preview.job_id) : undefined}
          controls
          preload="metadata"
          aria-label={`Video player: ${asset.original_filename}`}
          className="block max-h-[65vh] w-full bg-black"
          onTimeUpdate={(event) => {
            setTime(event.currentTarget.currentTime);
            onTime(event.currentTarget.currentTime);
          }}
          onSeeked={(event) => {
            setTime(event.currentTarget.currentTime);
            onTime(event.currentTarget.currentTime);
          }}
          onError={() => setUnplayable(true)}
        />
      )}
      <div className="flex flex-wrap items-center gap-2 border-t border-line px-3 py-2 text-[13px]">
        <Button variant="secondary" size="sm" onClick={() => step(-1)} disabled={!rate || unplayable} aria-label="Previous frame">
          <IconStepBack size={14} />
          Frame
        </Button>
        <Button variant="secondary" size="sm" onClick={() => step(1)} disabled={!rate || unplayable} aria-label="Next frame">
          Frame
          <IconStepForward size={14} />
        </Button>
        <span className="font-mono text-xs text-fg-muted" aria-live="polite">
          {formatTime(time)}
          {rate ? ` · frame ≈ ${Math.floor(time * rate + 1e-6)}` : ""}
        </span>
        <span className="ml-auto text-xs text-fg-subtle">
          {rate
            ? `Frame number estimated from the average rate (${rate.toFixed(3)} fps); exact frame timing is not used.`
            : "Frame rate unknown: frame stepping is unavailable."}
        </span>
      </div>
    </div>
  );
}

export function VideoPage() {
  const project = useProject();
  const { assetId } = useParams();
  const asset = useAsset(assetId);
  const videos = useAssets(project.id, "video");

  const header = (subtitle?: string) => (
    <>
      <Breadcrumbs
        items={projectCrumbs(
          project,
          { label: "Videos", to: `/projects/${project.id}/video` },
          ...(subtitle ? [{ label: subtitle }] : []),
        )}
      />
      <div className="mt-2">
        <PageHeader
          title="Videos"
          description={subtitle ? undefined : "Play and inspect videos stored in this project."}
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
    if (videos.isLoading) return <Loading label="Loading videos…" />;
    if (videos.isError) return <ErrorNotice error={videos.error} />;
    const first = videos.data!.items[0];
    if (first) return <Navigate to={`/projects/${project.id}/assets/${first.id}/video`} replace />;
    return (
      <div>
        {header()}
        <EmptyState
          icon={<IconVideo size={28} />}
          title="No videos in this project"
          action={
            <LinkButton to={`/projects/${project.id}/assets/new`}>
              <IconUpload size={15} />
              Upload a video
            </LinkButton>
          }
        >
          Upload MP4, MOV or MKV files to play and inspect them here.
        </EmptyState>
      </div>
    );
  }

  if (asset.isLoading) return <Loading label="Loading video…" />;
  if (asset.isError) return <ErrorNotice error={asset.error} />;
  const file = asset.data!;
  if (file.project_id !== project.id) return <Notice tone="error" title="This asset belongs to a different project" />;
  if (file.modality !== "video" || !file.video) {
    return (
      <div>
        {header(file.original_filename)}
        <Notice tone="error" title="This screen shows videos; this file is a different kind of input" />
      </div>
    );
  }
  return (
    <div>
      {header(file.original_filename)}
      <VideoWorkspace key={file.id} asset={file} projectId={project.id} />
    </div>
  );
}

/** The player, the analysis of its frames and the results, for one video. */
function VideoWorkspace({ asset, projectId }: { asset: Asset; projectId: string }) {
  const video = asset.video!;
  const player = useRef<HTMLVideoElement>(null);
  const [timeMs, setTimeMs] = useState(0);
  const v = useVideoAnalysis(asset);
  const { jobs: assetJobs, refresh } = useAssetJobs(asset);
  const latest = (assetJobs.data?.items ?? []).find(isVideoJob);

  // Selecting a frame or a finding can also move the player to that time. The player is only ever moved by a person's click.
  const seek = (ms: number) => {
    const element = player.current;
    if (!element) return;
    element.pause?.();
    element.currentTime = ms / 1000;
    setTimeMs(ms);
  };
  const select = (index: number) => {
    v.selectFrame(index);
    const frame = v.frames.data?.frames.find((f) => f.frame_index === index);
    if (frame) seek(frame.timestamp_ms);
  };
  const selecting = { ...v, selectFrame: select };

  const main = (
    <div className="space-y-4">
      <Player asset={asset} playerRef={player} onTime={(seconds) => setTimeMs(Math.round(seconds * 1000))} />
      <VideoTimeline v={selecting} durationMs={video.duration_ms} timeMs={timeMs} />
      <Panel title="Analyse frames" description="Take a bounded sample of frames and run the local detector on each one.">
        <div className="space-y-3">
          <RunVideoAnalysis asset={asset} onQueued={refresh} />
          {latest ? (
            <div>
              <p className="mb-1.5 text-xs text-fg-subtle">Latest analysis job</p>
              <ul aria-label="Latest analysis job">
                <JobRow job={latest} projectId={projectId} onChanged={refresh} />
              </ul>
            </div>
          ) : null}
        </div>
      </Panel>
      {v.frames.isLoading ? <Loading label="Loading sampled frames…" /> : null}
      {v.frames.isError ? <ErrorNotice error={v.frames.error} /> : null}
      <FrameStrip v={selecting} />
      <FrameViewer v={selecting} onSeek={seek} />
    </div>
  );

  const right = (
    <div className="space-y-4">
      <VideoWarnings />
      <NoRunYet v={selecting} />
      {v.runId ? <VideoFindings v={selecting} onSeek={seek} /> : null}
      <VideoProvenance v={v} />
      <Panel title="Video">
        <DescriptionList
          rows={[
            ["Frame size", `${video.width} × ${video.height} pixels`],
            ["Duration", video.duration_ms == null ? muted : `${(video.duration_ms / 1000).toFixed(2)} s`],
            ["Frame rate", video.frame_rate == null ? muted : `${video.frame_rate.toFixed(3)} fps (${video.avg_frame_rate})`],
            ["Frame count", video.frame_count ?? muted],
            ["Codec", video.video_codec ?? muted],
            ["Container", <span className="wrap-anywhere">{video.container}</span>],
            ["Time base", video.time_base ?? muted],
            ["Audio track", video.has_audio ? "yes" : "no"],
            ["Container created", video.creation_time ? formatDateTime(video.creation_time) : muted],
          ]}
        />
      </Panel>
      <Panel title="What is not known">
        <UnknownList asset={asset} />
      </Panel>
    </div>
  );

  return <Workspace left={<VideoList projectId={projectId} selectedId={asset.id} />} main={main} right={right} leftLabel="Videos" rightLabel="Findings and video metadata" />;
}
