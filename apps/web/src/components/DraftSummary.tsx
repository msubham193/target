import { useMutation, useQuery } from "@tanstack/react-query";

import { getReportSummaryStatus, type ReportSummaryDraft, writeReportSummary } from "../api/client";
import { formatDateTime } from "../lib/format";
import { Badge, Button, ErrorNotice, Loading } from "./ui";

/**
 * A draft summary of the report written by a local language model (Qwen, through Ollama on this computer). The facts are
 * counted by code; the model only words them, and what it writes is checked on the server before it is shown. It is never
 * stored, it is marked as machine-written and unreviewed, and it does not recommend or rank anything.
 */
export function DraftSummary({ projectId }: { projectId: string }) {
  const status = useQuery({ queryKey: ["report-summary-status"], queryFn: getReportSummaryStatus, retry: false });
  const write = useMutation({ mutationFn: () => writeReportSummary(projectId) });
  const ready = status.data ? status.data.enabled && status.data.reachable && status.data.model_installed : false;
  const draft: ReportSummaryDraft | undefined = write.data;

  return (
    <section
      aria-labelledby="draft-summary-heading"
      className="report-avoid-break mt-5 rounded border border-dashed border-line-strong p-4"
      data-testid="draft-summary"
    >
      <div className="flex flex-wrap items-center gap-2">
        <h2 id="draft-summary-heading" className="text-[15px] font-semibold">
          Draft summary
        </h2>
        <Badge tone="warn">Machine-written · not reviewed</Badge>
      </div>

      {!draft ? (
        <div className="mt-2 space-y-2 text-[13px] text-fg-muted print:hidden">
          <p>
            A language model running on this computer can put the counts in this report into plain sentences. It is given only
            the stored facts below, nothing is sent off the computer, and the text is not saved. It can be wrong: treat it as a
            draft and check it against the sections that follow. It describes what was detected; it does not say what anything
            means or what to do.
          </p>
          {status.data && !ready ? (
            <p className="text-warn" data-testid="draft-unavailable">
              Not available: {status.data.note}
            </p>
          ) : null}
          {status.isError ? <p className="text-warn">The model status could not be checked.</p> : null}
          <Button size="sm" disabled={!ready || write.isPending} onClick={() => write.mutate()}>
            {write.isPending ? "Writing…" : "Write a draft summary"}
          </Button>
          {write.isPending ? <Loading label="Asking the local model. This can take up to a minute." /> : null}
          {write.isError ? <ErrorNotice error={write.error} title="No draft was produced" /> : null}
        </div>
      ) : (
        <div className="mt-2 space-y-3 text-[13px]" data-testid="draft-text">
          <p className="rounded bg-warn-soft px-2.5 py-1.5 text-[12px] text-fg-muted">{draft.disclaimer}</p>
          <p>{draft.overview}</p>
          {draft.photos.length > 0 ? (
            <ul className="space-y-1.5" aria-label="Draft summary by photograph">
              {draft.photos.map((photo) => (
                <li key={photo.filename}>
                  <span className="font-medium wrap-anywhere">{photo.filename}</span>: {photo.text}
                </li>
              ))}
            </ul>
          ) : null}
          {draft.notes.length > 0 ? (
            <ul className="list-disc space-y-0.5 pl-5 text-fg-muted" aria-label="Draft summary notes">
              {draft.notes.map((note) => (
                <li key={note}>{note}</li>
              ))}
            </ul>
          ) : null}
          <p className="text-[11px] text-fg-subtle">
            Written {formatDateTime(draft.generated_at)} by {draft.model} on this computer ({draft.attempts === 1 ? "first attempt" : `attempt ${draft.attempts}`}).
          </p>
          <details className="text-[11px] text-fg-subtle print:hidden">
            <summary className="cursor-pointer">The exact facts the model was given</summary>
            <pre className="mt-1 max-h-60 overflow-auto rounded bg-raised p-2 whitespace-pre-wrap">{JSON.stringify(draft.facts_used, null, 2)}</pre>
          </details>
          <div className="flex gap-2 print:hidden">
            <Button size="sm" variant="secondary" disabled={write.isPending} onClick={() => write.mutate()}>
              Write again
            </Button>
            <Button size="sm" variant="ghost" onClick={() => write.reset()}>
              Discard
            </Button>
          </div>
        </div>
      )}
    </section>
  );
}
