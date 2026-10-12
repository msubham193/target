import { useQuery, useQueryClient } from "@tanstack/react-query";
import { type DragEvent, useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";

import { ApiError, type AssetUpload, getIngestionRules, type IngestionRule, type Modality, uploadAsset } from "../api/client";
import { ErrorNotice, Loading, Notice, PageTitle } from "../components/ui";
import { checkFile, flagLabel, formatBytes, STATUS_NAMES } from "../lib/ingestion";
import { useProject } from "./ProjectLayout";

type ItemState = "queued" | "uploading" | "stored" | "duplicate" | "rejected" | "failed";

interface QueueItem {
  id: number;
  file: File;
  modality: Modality;
  state: ItemState;
  progress: number;
  result?: AssetUpload;
  error?: { title: string; detail?: string | null; code: string; requestId?: string | null };
}

const STATE_LABELS: Record<ItemState, string> = {
  queued: "Waiting",
  uploading: "Uploading and validating",
  stored: "Stored",
  duplicate: "Duplicate — linked",
  rejected: "Not accepted",
  failed: "Failed",
};

const STATE_STYLES: Record<ItemState, string> = {
  queued: "bg-slate-200 text-slate-800",
  uploading: "bg-blue-100 text-blue-900",
  stored: "bg-emerald-100 text-emerald-900",
  duplicate: "bg-amber-100 text-amber-900",
  rejected: "bg-red-100 text-red-900",
  failed: "bg-red-100 text-red-900",
};

let nextId = 1;

function QueueRow({ item, projectId }: { item: QueueItem; projectId: string }) {
  const asset = item.result?.asset;
  return (
    <li className="rounded border border-slate-300 bg-white p-3 text-sm" aria-label={item.file.name}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="break-all font-medium">{item.file.name}</span>
        <span className="text-slate-600">{formatBytes(item.file.size)}</span>
        <span className={`ml-auto rounded px-2 py-0.5 text-xs font-medium ${STATE_STYLES[item.state]}`}>
          {STATE_LABELS[item.state]}
        </span>
      </div>

      {item.state === "uploading" ? (
        <progress className="mt-2 w-full" max={1} value={item.progress} aria-label={`Upload progress for ${item.file.name}`} />
      ) : null}

      {asset && item.result ? (
        <div className="mt-2 space-y-1">
          <p role="status">{item.result.message}</p>
          <p className="text-slate-600">
            {STATUS_NAMES[asset.status]} · {asset.detected_format} · SHA-256{" "}
            <span className="font-mono text-xs">{asset.sha256.slice(0, 16)}…</span>
          </p>
          {asset.quality_flags.length > 0 ? (
            <p className="text-slate-600">Flags: {asset.quality_flags.map(flagLabel).join(", ")}</p>
          ) : null}
          <p>
            <Link className="text-blue-700 underline" to={`/projects/${projectId}/assets/${asset.id}`}>
              View details
            </Link>
            {item.result.duplicate_of_asset_id ? (
              <>
                {" · "}
                <Link className="text-blue-700 underline" to={`/projects/${projectId}/assets/${item.result.duplicate_of_asset_id}`}>
                  View the first copy
                </Link>
              </>
            ) : null}
          </p>
        </div>
      ) : null}

      {item.error ? (
        <div role="alert" className="mt-2">
          <p className="font-medium text-red-900">{item.error.title}</p>
          {item.error.detail ? <p>{item.error.detail}</p> : null}
          <p className="text-xs text-slate-600">
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
  const input = useRef<HTMLInputElement>(null);

  const update = useCallback((id: number, patch: Partial<QueueItem>) => {
    setItems((current) => current.map((item) => (item.id === id ? { ...item, ...patch } : item)));
  }, []);

  // Upload one file at a time, in the order added.
  useEffect(() => {
    if (busy.current) return;
    const next = items.find((item) => item.state === "queued");
    if (!next) return;
    busy.current = true;
    update(next.id, { state: "uploading", progress: 0 });
    uploadAsset(project.id, next.modality, next.file, (progress) => update(next.id, { progress }))
      .then((result) => {
        update(next.id, { state: result.duplicate ? "duplicate" : "stored", result, progress: 1 });
        void queryClient.invalidateQueries({ queryKey: ["assets", project.id] });
      })
      .catch((error: unknown) => {
        const known = error instanceof ApiError;
        // 4xx means the file itself was not acceptable; anything else is a failure of the system.
        const rejected = known && error.status >= 400 && error.status < 500;
        update(next.id, {
          state: rejected ? "rejected" : "failed",
          error: known
            ? { title: error.message, detail: error.detail, code: error.code, requestId: error.requestId }
            : { title: "The upload failed unexpectedly", code: "UPLOAD_FAILED" },
        });
      })
      .finally(() => {
        busy.current = false;
        setItems((current) => [...current]); // look for the next queued file
      });
  }, [items, project.id, queryClient, update]);

  if (rules.isLoading) return <Loading label="Loading accepted formats…" />;
  if (rules.isError) return <ErrorNotice error={rules.error} />;
  const allRules = rules.data!.rules;
  const rule = allRules.find((candidate) => candidate.modality === modality) as IngestionRule;

  const addFiles = (files: FileList | File[]) => {
    const added: QueueItem[] = Array.from(files).map((file) => {
      const problem = checkFile(file, rule);
      const base = { id: nextId++, file, modality, progress: 0 };
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
    <div className="max-w-3xl space-y-5">
      <PageTitle
        title="Data Upload & Ingestion"
        actions={
          <Link className="text-sm text-blue-700 underline" to={`/projects/${project.id}/assets`}>
            View all assets
          </Link>
        }
      />

      {project.archived_at ? (
        <Notice tone="warning" title="This project is archived">
          Restore it from the project overview before adding files.
        </Notice>
      ) : null}

      <div role="tablist" aria-label="Kind of input" className="flex flex-wrap gap-1 border-b border-slate-300">
        {allRules.map((candidate) => (
          <button
            key={candidate.modality}
            type="button"
            role="tab"
            aria-selected={candidate.modality === modality}
            className={`rounded-t px-3 py-1.5 text-sm ${
              candidate.modality === modality ? "border border-b-0 border-slate-300 bg-white font-medium" : "hover:bg-slate-100"
            }`}
            onClick={() => setModality(candidate.modality)}
          >
            {candidate.label}
          </button>
        ))}
      </div>

      <section className="space-y-3">
        <p className="text-sm">
          Accepted: <span className="font-medium">{rule.extensions.join(", ")}</span> · up to{" "}
          <span className="font-medium">{formatBytes(rule.max_bytes)}</span> per file. Each kind of input works on its
          own; you do not need the others.
        </p>

        <div
          onDragOver={(event) => {
            event.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
          className={`rounded border-2 border-dashed p-6 text-center text-sm ${
            dragging ? "border-blue-600 bg-blue-50" : "border-slate-400 bg-white"
          }`}
        >
          <p>Drop files here, or</p>
          <label
            htmlFor="asset-files"
            className={`mt-2 inline-block rounded bg-slate-900 px-3 py-1.5 font-medium text-white ${
              project.archived_at ? "cursor-not-allowed opacity-50" : "cursor-pointer hover:bg-slate-700"
            }`}
          >
            Choose files
          </label>
          <input
            ref={input}
            id="asset-files"
            type="file"
            multiple
            className="sr-only"
            accept={rule.extensions.join(",")}
            disabled={Boolean(project.archived_at)}
            onChange={(event) => {
              if (event.target.files) addFiles(event.target.files);
              event.target.value = ""; // allow choosing the same file again
            }}
          />
        </div>

        <ul className="list-disc space-y-0.5 pl-5 text-xs text-slate-600">
          {rules.data!.notes.map((note) => (
            <li key={note}>{note}</li>
          ))}
          <li>Originals are stored unmodified on this computer. Nothing is sent anywhere else.</li>
        </ul>
      </section>

      <section aria-labelledby="queue-heading" className="space-y-2">
        <h2 id="queue-heading" className="font-semibold">
          Upload queue
        </h2>
        {active ? <p role="status" className="text-sm text-slate-600">Uploading… keep this page open.</p> : null}
        {items.length === 0 ? (
          <p className="text-sm text-slate-600">No files added yet.</p>
        ) : (
          <ul className="space-y-2" aria-label="Upload queue">
            {items.map((item) => (
              <QueueRow key={item.id} item={item} projectId={project.id} />
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
