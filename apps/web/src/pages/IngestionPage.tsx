import { useQuery, useQueryClient } from "@tanstack/react-query";
import { type DragEvent, useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";

import {
  ApiError,
  type AssetUpload,
  getIngestionRules,
  type IngestionRule,
  type Modality,
  type SidecarUpload,
  uploadAsset,
  uploadSidecar,
} from "../api/client";
import { IconUpload } from "../components/icons";
import { Badge, Breadcrumbs, ErrorNotice, Loading, Notice, PageHeader, Panel, Tabs, type Tone } from "../components/ui";
import { checkFile, checkSidecar, flagLabel, formatBytes, isSidecar, sidecarPairsWith, STATUS_NAMES } from "../lib/ingestion";
import { IngestionNav } from "./ingestionNav";
import { projectCrumbs, useProject } from "./ProjectLayout";

type ItemState = "queued" | "uploading" | "stored" | "kept" | "duplicate" | "rejected" | "failed";

interface QueueItem {
  id: number;
  file: File;
  modality: Modality;
  /** A sidecar metadata file (world file, .prj, .aux.xml) is kept for review with its raster, not stored as an asset. */
  sidecar: boolean;
  state: ItemState;
  progress: number;
  result?: AssetUpload;
  sidecarResult?: SidecarUpload;
  error?: { title: string; detail?: string | null; code: string; requestId?: string | null };
}

/** Everyday names for the kinds of input; any other kind keeps the name the server gave it. */
const PLAIN_KIND: Record<string, string> = {
  ground_image: "Photographs",
  overhead_raster: "Satellite or aerial images (GeoTIFF)",
  video: "Video",
};
const plainKind = (modality: string, fallback: string): string => PLAIN_KIND[modality] ?? fallback;

const STATE: Record<ItemState, { label: string; tone: Tone }> = {
  queued: { label: "Waiting", tone: "neutral" },
  uploading: { label: "Uploading and validating", tone: "accent" },
  stored: { label: "Stored", tone: "ok" },
  kept: { label: "Kept for review", tone: "accent" },
  duplicate: { label: "Duplicate — linked", tone: "warn" },
  rejected: { label: "Not accepted", tone: "danger" },
  failed: { label: "Failed", tone: "danger" },
};

let nextId = 1;

function QueueRow({ item, projectId }: { item: QueueItem; projectId: string }) {
  const asset = item.result?.asset;
  const state = STATE[item.state];
  return (
    <li className="rounded-md border border-line bg-raised p-3 text-[13px]" aria-label={item.file.name}>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span className="min-w-0 wrap-anywhere font-medium text-fg">{item.file.name}</span>
        <span className="text-xs text-fg-subtle">{formatBytes(item.file.size)}</span>
        <span className="ml-auto">
          <Badge tone={state.tone}>{state.label}</Badge>
        </span>
      </div>

      {item.state === "uploading" ? (
        <progress className="mt-2 h-1.5 w-full" max={1} value={item.progress} aria-label={`Upload progress for ${item.file.name}`} />
      ) : null}

      {asset && item.result ? (
        <div className="mt-2 space-y-1">
          <p role="status" className="text-fg">
            {item.result.message}
          </p>
          <p className="text-xs text-fg-subtle">
            {STATUS_NAMES[asset.status]} · {asset.detected_format} · SHA-256{" "}
            <span className="font-mono">{asset.sha256.slice(0, 16)}…</span>
          </p>
          {asset.quality_flags.length > 0 ? (
            <p className="text-xs text-fg-subtle">Flags: {asset.quality_flags.map(flagLabel).join(", ")}</p>
          ) : null}
          <p className="flex flex-wrap gap-3 text-xs">
            <Link className="text-accent hover:underline" to={`/projects/${projectId}/assets/${asset.id}`}>
              View details
            </Link>
            {item.result.duplicate_of_asset_id ? (
              <Link className="text-accent hover:underline" to={`/projects/${projectId}/assets/${item.result.duplicate_of_asset_id}`}>
                View the first copy
              </Link>
            ) : null}
          </p>
        </div>
      ) : null}

      {item.sidecarResult ? (
        <div className="mt-2 space-y-1">
          <p role="status" className="text-fg">
            {item.sidecarResult.message}
          </p>
          <p className="text-xs text-fg-subtle">
            {item.sidecarResult.sidecar.kind_label} · {item.sidecarResult.sidecar.summary}
          </p>
          {item.sidecarResult.sidecar.problems.length > 0 ? (
            <p className="text-xs text-warn">Problems found: {item.sidecarResult.sidecar.problems.join(" ")}</p>
          ) : null}
          <p className="text-xs">
            <Link className="text-accent hover:underline" to={`/projects/${projectId}/assets/${item.sidecarResult.asset_id}#sidecars`}>
              Review with {item.sidecarResult.raster_filename}
            </Link>
          </p>
        </div>
      ) : null}

      {item.error ? (
        <div role="alert" className="mt-2 space-y-0.5">
          <p className="font-medium text-danger">{item.error.title}</p>
          {item.error.detail ? <p className="text-fg-muted">{item.error.detail}</p> : null}
          <p className="font-mono text-[11px] text-fg-subtle">
            Code: {item.error.code}
            {item.error.requestId ? ` · Request: ${item.error.requestId}` : ""}
          </p>
        </div>
      ) : null}
    </li>
  );
}

export function IngestionPage() {
  const project = useProject();
  const queryClient = useQueryClient();
  const rules = useQuery({ queryKey: ["ingestion-rules"], queryFn: getIngestionRules, staleTime: Infinity });
  const [modality, setModality] = useState<Modality>("ground_image");
  const [items, setItems] = useState<QueueItem[]>([]);
  const [dragging, setDragging] = useState(false);
  const busy = useRef(false);

  const update = useCallback((id: number, patch: Partial<QueueItem>) => {
    setItems((current) => current.map((item) => (item.id === id ? { ...item, ...patch } : item)));
  }, []);

  // Upload one file at a time, in the order added. A sidecar waits until the rasters added before it are done, so it
  // can be paired with a raster from the same batch.
  useEffect(() => {
    if (busy.current) return;
    const pendingAsset = items.some((item) => !item.sidecar && (item.state === "queued" || item.state === "uploading"));
    const next = items.find((item) => item.state === "queued" && (!item.sidecar || !pendingAsset));
    if (!next) return;
    busy.current = true;
    update(next.id, { state: "uploading", progress: 0 });
    const fail = (error: unknown) => {
      const known = error instanceof ApiError;
      // 4xx means the file itself was not acceptable; anything else is a failure of the system.
      const rejected = known && error.status >= 400 && error.status < 500;
      update(next.id, {
        state: rejected ? "rejected" : "failed",
        error: known
          ? { title: error.message, detail: error.detail, code: error.code, requestId: error.requestId }
          : { title: "The upload failed unexpectedly", code: "UPLOAD_FAILED" },
      });
    };
    const done = () => {
      busy.current = false;
      setItems((current) => [...current]); // look for the next queued file
    };
    if (next.sidecar) {
      // The raster with the matching name from this batch, if there is one; otherwise the server pairs by name.
      const raster = [...items]
        .reverse()
        .find((item) => !item.sidecar && item.result && item.modality === "overhead_raster" && sidecarPairsWith(next.file.name, item.file.name));
      uploadSidecar(project.id, next.file, raster?.result?.asset.id)
        .then((sidecarResult) => {
          update(next.id, { state: "kept", sidecarResult, progress: 1 });
          void queryClient.invalidateQueries({ queryKey: ["sidecars", sidecarResult.asset_id] });
        })
        .catch(fail)
        .finally(done);
      return;
    }
    uploadAsset(project.id, next.modality, next.file, (progress) => update(next.id, { progress }))
      .then((result) => {
        update(next.id, { state: result.duplicate ? "duplicate" : "stored", result, progress: 1 });
        void queryClient.invalidateQueries({ queryKey: ["assets", project.id] });
        void queryClient.invalidateQueries({ queryKey: ["summary", project.id] });
      })
      .catch(fail)
      .finally(done);
  }, [items, project.id, queryClient, update]);

  const header = (
    <>
      <Breadcrumbs items={projectCrumbs(project, { label: "Add Files" })} />
      <div className="mt-2">
        <PageHeader
          title="Add Files"
          description="Add photographs, satellite or aerial images, and video. Each file is checked, kept exactly as you uploaded it, and given a preview."
        />
      </div>
      <IngestionNav projectId={project.id} />
    </>
  );

  if (rules.isLoading) {
    return (
      <div className="mx-auto max-w-5xl">
        {header}
        <Loading label="Loading accepted formats…" />
      </div>
    );
  }
  if (rules.isError) {
    return (
      <div className="mx-auto max-w-5xl">
        {header}
        <ErrorNotice error={rules.error} />
      </div>
    );
  }
  const allRules = rules.data!.rules;
  const rule = allRules.find((candidate) => candidate.modality === modality) as IngestionRule;

  const addFiles = (files: FileList | File[]) => {
    const added: QueueItem[] = Array.from(files).map((file) => {
      const sidecar = isSidecar(file.name, rule);
      const problem = sidecar ? checkSidecar(file, rule) : checkFile(file, rule);
      const base = { id: nextId++, file, modality, sidecar, progress: 0 };
      return problem
        ? { ...base, state: "rejected" as const, error: { title: problem.title, detail: problem.detail, code: problem.code } }
        : { ...base, state: "queued" as const };
    });
    setItems((current) => [...current, ...added]);
  };

  const onDrop = (event: DragEvent) => {
    event.preventDefault();
    setDragging(false);
    if (!project.archived_at && event.dataTransfer.files.length > 0) addFiles(event.dataTransfer.files);
  };

  const active = items.some((item) => item.state === "queued" || item.state === "uploading");

  return (
    <div className="mx-auto max-w-5xl">
      {header}

      {project.archived_at ? (
        <Notice tone="warning" title="This project is archived" className="mb-4">
          Restore it from the project overview before adding files.
        </Notice>
      ) : null}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1fr)_17rem]">
        <div className="min-w-0 space-y-4">
          <Panel padded={false}>
            <div className="px-4 pt-2">
              <Tabs
                label="Kind of input"
                value={modality}
                onChange={setModality}
                tabs={allRules.map((candidate) => ({ value: candidate.modality, label: plainKind(candidate.modality, candidate.label) }))}
              />
            </div>
            <div className="space-y-3 p-4">
              <p className="text-[13px] text-fg-muted">
                Accepted: <span className="font-medium text-fg">{rule.extensions.join(", ")}</span> · up to{" "}
                <span className="font-medium text-fg">{formatBytes(rule.max_bytes)}</span> per file. Each kind of input
                works on its own; you do not need the others.
              </p>
              {rule.sidecar_extensions && rule.sidecar_extensions.length > 0 ? (
                <p className="text-[13px] text-fg-muted">
                  Sidecar files ({rule.sidecar_extensions.join(", ")}) can be added with their raster. They are kept for review on
                  the raster's page and are never applied on their own.
                </p>
              ) : null}
              <div
                onDragOver={(event) => {
                  event.preventDefault();
                  setDragging(true);
                }}
                onDragLeave={() => setDragging(false)}
                onDrop={onDrop}
                className={`flex flex-col items-center gap-2 rounded-lg border-2 border-dashed px-6 py-8 text-center text-[13px] transition-colors ${
                  dragging ? "border-accent bg-accent-soft" : "border-line-strong bg-canvas"
                }`}
              >
                <IconUpload size={26} className="text-fg-subtle" />
                <p className="text-fg-muted">Drop files here, or</p>
                <label
                  htmlFor="asset-files"
                  className={`inline-flex h-8.5 items-center rounded-md bg-accent px-3 text-sm font-medium text-accent-fg ${
                    project.archived_at ? "cursor-not-allowed opacity-45" : "cursor-pointer hover:bg-accent-strong"
                  }`}
                >
                  Choose files
                </label>
                <input
                  id="asset-files"
                  type="file"
                  multiple
                  className="sr-only"
                  accept={[...rule.extensions, ...(rule.sidecar_extensions ?? []), ...(rule.sidecar_extensions?.length ? [".xml"] : [])].join(",")}
                  disabled={Boolean(project.archived_at)}
                  onChange={(event) => {
                    if (event.target.files) addFiles(event.target.files);
                    event.target.value = ""; // allow choosing the same file again
                  }}
                />
              </div>
            </div>
          </Panel>

          <Panel
            title="Upload queue"
            description="Files are uploaded one at a time, in the order added."
            actions={
              <Link className="text-xs text-accent hover:underline" to={`/projects/${project.id}/assets`}>
                View all assets
              </Link>
            }
          >
            {active ? (
              <p role="status" className="mb-2 text-[13px] text-fg-muted">
                Uploading… keep this page open.
              </p>
            ) : null}
            {items.length === 0 ? (
              <p className="text-[13px] text-fg-subtle">No files added yet.</p>
            ) : (
              <ul className="space-y-2" aria-label="Upload queue">
                {items.map((item) => (
                  <QueueRow key={item.id} item={item} projectId={project.id} />
                ))}
              </ul>
            )}
          </Panel>
        </div>

        <aside aria-label="How files are checked" className="space-y-3 lg:sticky lg:top-16 lg:self-start">
          <Panel title="How files are checked">
            <ul className="list-disc space-y-1.5 pl-4 text-xs text-fg-muted">
              {rules.data!.notes.map((note) => (
                <li key={note}>{note}</li>
              ))}
              <li>Originals are stored unmodified on this computer. Nothing is sent anywhere else.</li>
              <li>A preview is made in the background after each upload.</li>
            </ul>
          </Panel>
          <Panel title="All accepted formats">
            <dl className="space-y-2 text-xs">
              {allRules.map((candidate) => (
                <div key={candidate.modality}>
                  <dt className="font-medium text-fg">{plainKind(candidate.modality, candidate.label)}</dt>
                  <dd className="text-fg-muted">
                    {candidate.extensions.join(", ")} · up to {formatBytes(candidate.max_bytes)}
                  </dd>
                </div>
              ))}
            </dl>
          </Panel>
        </aside>
      </div>
    </div>
  );
}
