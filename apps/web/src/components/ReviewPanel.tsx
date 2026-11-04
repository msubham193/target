import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";

import {
  ApiError,
  type Conflict,
  getObservation,
  type ObservationDetail,
  type ReviewAction,
  reviewObservation,
  type Revision,
} from "../api/client";
import { formatDateTime } from "../lib/format";
import { categoryLabel, REVIEW_ACTION_LABELS, REVIEW_STATUS_LABELS, reviewTone } from "../lib/jobs";
import { Badge, Button, ErrorNotice, inputClass, Loading, Notice } from "./ui";

const BUTTON_ORDER: ReviewAction[] = ["confirm", "reject", "mark_inconclusive", "reopen", "relabel", "note"];

function ConflictBox({ conflict, observationId, onOpen }: { conflict: Conflict; observationId: string; onOpen?: (id: string) => void }) {
  const others = conflict.members.filter((member) => member.observation_id !== observationId);
  const open = conflict.status === "open";
  return (
    <div className={`rounded-md border p-3 text-xs ${open ? "border-danger/40 bg-danger-soft" : "border-line bg-raised"}`}>
      <p className="font-medium text-fg">
        {open ? "Open conflict" : "Resolved conflict"}: {conflict.attribute}
      </p>
      <p className="mt-1 text-fg-muted">{String(conflict.details.summary ?? "")}</p>
      <ul className="mt-2 space-y-1" aria-label="Conflicting findings">
        {others.map((member) => (
          <li key={member.observation_id} className="flex flex-wrap items-center gap-1.5">
            <span className="text-fg">{categoryLabel(member.current_category)}</span>
            <Badge tone={reviewTone(member.current_status)}>{REVIEW_STATUS_LABELS[member.current_status]}</Badge>
            <span className="truncate text-fg-subtle">in {member.asset_filename}</span>
            {onOpen ? (
              <button type="button" className="text-accent hover:underline" onClick={() => onOpen(member.observation_id)}>
                Open
              </button>
            ) : null}
          </li>
        ))}
      </ul>
      {conflict.resolution_note ? <p className="mt-2 text-fg-muted">{conflict.resolution_note}</p> : null}
      <p className="mt-1 text-fg-subtle">Rule: {conflict.rule}. No rule decides between the findings; a reviewer does.</p>
    </div>
  );
}

function History({ revisions }: { revisions: Revision[] }) {
  if (revisions.length === 0) {
    return <p className="text-xs text-fg-subtle">No review yet. The finding is as the model produced it.</p>;
  }
  return (
    <ol className="space-y-2" aria-label="Review history">
      {[...revisions].reverse().map((revision) => (
        <li key={revision.revision_no} className="border-l-2 border-line pl-2.5 text-xs">
          <p className="text-fg">
            <span className="text-fg-subtle">#{revision.revision_no}</span> {REVIEW_ACTION_LABELS[revision.action] ?? revision.action}
            {revision.status_before !== revision.status_after
              ? ` · ${REVIEW_STATUS_LABELS[revision.status_before]} → ${REVIEW_STATUS_LABELS[revision.status_after]}`
              : ""}
          </p>
          {revision.category_before !== revision.category_after ? (
            <p className="text-fg-muted">
              Category: {categoryLabel(revision.category_before)} → {categoryLabel(revision.category_after)}
            </p>
          ) : null}
          {revision.note ? <p className="whitespace-pre-wrap text-fg-muted">“{revision.note}”</p> : null}
          <p className="text-fg-subtle">
            {revision.actor_kind === "system" ? "System: " : ""}
            {revision.actor_label} · {formatDateTime(revision.created_at)}
          </p>
        </li>
      ))}
    </ol>
  );
}

/**
 * Review of one observation: its current state, the actions the state rules allow now, its conflicts and links,
 * and the full history. Every action is a new revision; the model's original output is never changed.
 */
export function ReviewPanel({
  observationId,
  projectId,
  onOpenObservation,
}: {
  observationId: string;
  projectId: string;
  onOpenObservation?: (id: string) => void;
}) {
  const queryClient = useQueryClient();
  const detail = useQuery({ queryKey: ["observation", observationId], queryFn: () => getObservation(observationId) });
  const [action, setAction] = useState<ReviewAction | null>(null);
  const [note, setNote] = useState("");
  const [category, setCategory] = useState("");
  const [saved, setSaved] = useState<string | null>(null);

  const save = useMutation({
    mutationFn: (current: ObservationDetail) =>
      reviewObservation(observationId, {
        action: action!,
        expected_revision: current.current.revision,
        note: note.trim() ? note : null,
        category: action === "relabel" ? category : null,
      }),
    onSuccess: (updated) => {
      queryClient.setQueryData(["observation", observationId], updated);
      void queryClient.invalidateQueries({ queryKey: ["observations", projectId] });
      void queryClient.invalidateQueries({ queryKey: ["conflicts", projectId] });
      void queryClient.invalidateQueries({ queryKey: ["summary", projectId] });
      for (const conflict of updated.conflicts) {
        for (const member of conflict.members) void queryClient.invalidateQueries({ queryKey: ["observation", member.observation_id] });
      }
      setSaved(`${REVIEW_ACTION_LABELS[action!]} saved as revision ${updated.current.revision}.`);
      setAction(null);
      setNote("");
      setCategory("");
    },
    onError: (error) => {
      // Someone else changed it: show their change instead of keeping a stale form.
      if (error instanceof ApiError && error.code === "REVISION_CONFLICT") void detail.refetch();
    },
  });

  if (detail.isLoading) return <Loading label="Loading review…" />;
  if (detail.isError) return <ErrorNotice error={detail.error} />;
  const data = detail.data!;
  const allowed = new Map(data.allowed_actions.map((a) => [a.action, a]));
  const chosen = action ? allowed.get(action) : undefined;
  const openConflicts = data.conflicts.filter((c) => c.status === "open");

  const submit = (event: FormEvent) => {
    event.preventDefault();
    setSaved(null);
    save.mutate(data);
  };

  return (
    <section aria-label="Review" className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs text-fg-muted">Current state</span>
        <Badge tone={reviewTone(data.current.status)}>{REVIEW_STATUS_LABELS[data.current.status]}</Badge>
        <span className="text-xs text-fg-subtle">revision {data.current.revision}</span>
      </div>
      {data.current.relabelled ? (
        <p className="text-xs text-fg-muted">
          Category corrected by a reviewer to <span className="font-medium text-fg">{categoryLabel(data.current.category)}</span>.
          The model said “{categoryLabel(data.observation.claim.category)}”.
        </p>
      ) : null}

      {openConflicts.map((conflict) => (
        <ConflictBox key={conflict.id} conflict={conflict} observationId={observationId} onOpen={onOpenObservation} />
      ))}
      {data.links.length > 0 ? (
        <p className="rounded-md border border-line bg-raised p-2.5 text-xs text-fg-muted">
          Possible duplicate of{" "}
          {data.links.reduce((n, link) => n + link.observation_ids.filter((id) => id !== observationId).length, 0)} other
          finding(s) of the same category in the same image content. This is a candidate link only: the findings are not
          merged and do not confirm one another.
        </p>
      ) : null}

      <div className="flex flex-wrap gap-1.5" role="group" aria-label="Review actions">
        {BUTTON_ORDER.filter((name) => allowed.has(name)).map((name) => (
          <Button
            key={name}
            size="sm"
            variant={action === name ? "primary" : "secondary"}
            aria-pressed={action === name}
            onClick={() => {
              setAction(action === name ? null : name);
              setSaved(null);
              save.reset();
              if (name === "relabel") setCategory(categoryLabel(data.current.category));
            }}
          >
            {REVIEW_ACTION_LABELS[name]}
          </Button>
        ))}
      </div>

      {action && chosen ? (
        <form onSubmit={submit} className="space-y-2.5 rounded-md border border-line bg-raised p-3" aria-label={REVIEW_ACTION_LABELS[action]}>
          {chosen.target_status ? (
            <p className="text-xs text-fg-muted">
              The state becomes <span className="font-medium text-fg">{REVIEW_STATUS_LABELS[chosen.target_status]}</span>.
            </p>
          ) : null}
          {action === "relabel" ? (
            <div className="space-y-1">
              <label htmlFor={`category-${observationId}`} className="block text-xs font-medium text-fg">
                Correct category
              </label>
              <input
                id={`category-${observationId}`}
                className={inputClass}
                value={category}
                maxLength={100}
                onChange={(event) => setCategory(event.target.value)}
              />
            </div>
          ) : null}
          <div className="space-y-1">
            <label htmlFor={`note-${observationId}`} className="block text-xs font-medium text-fg">
              {chosen.note_required ? "Reason (required)" : "Note (optional)"}
            </label>
            <textarea
              id={`note-${observationId}`}
              className={`${inputClass} min-h-16 py-1.5`}
              value={note}
              maxLength={2000}
              required={chosen.note_required}
              onChange={(event) => setNote(event.target.value)}
            />
          </div>
          {save.isError ? (
            save.error instanceof ApiError && save.error.code === "REVISION_CONFLICT" ? (
              <Notice tone="error" title="Someone else changed this finding">
                {save.error.detail} The latest version is now shown; check it before saving again.
              </Notice>
            ) : (
              <ErrorNotice error={save.error} />
            )
          ) : null}
          <div className="flex gap-2">
            <Button type="submit" size="sm" disabled={save.isPending || (chosen.note_required && !note.trim()) || (action === "relabel" && !category.trim())}>
              {save.isPending ? "Saving…" : `Save: ${REVIEW_ACTION_LABELS[action]}`}
            </Button>
            <Button type="button" size="sm" variant="ghost" onClick={() => setAction(null)}>
              Cancel
            </Button>
          </div>
        </form>
      ) : null}
      {saved ? (
        <p role="status" className="text-xs text-ok">
          {saved}
        </p>
      ) : null}
      <p className="text-xs text-fg-subtle">
        Reviews are attributed to your local profile, which is not a login. The model's original output is kept unchanged.
      </p>

      <div>
        <p className="mb-1.5 text-xs font-medium text-fg-muted">History</p>
        <History revisions={data.revisions} />
      </div>
    </section>
  );
}
