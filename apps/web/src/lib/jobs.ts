import type { Job } from "../api/client";

export const ACTIVE_STATUSES = ["queued", "validating", "processing"];

export function isActive(job: Pick<Job, "status">): boolean {
  return ACTIVE_STATUSES.includes(job.status);
}

export const JOB_STATUS_LABELS: Record<string, string> = {
  draft: "Draft",
  queued: "Queued",
  validating: "Checking inputs",
  processing: "Processing",
  needs_review: "Needs review",
  completed: "Completed",
  completed_with_warnings: "Completed with warnings",
  failed: "Failed",
  cancelled: "Cancelled",
};

type Tone = "neutral" | "accent" | "ok" | "warn" | "danger";

const JOB_TONES: Record<string, Tone> = {
  draft: "neutral",
  queued: "neutral",
  validating: "accent",
  processing: "accent",
  needs_review: "warn",
  completed: "ok",
  completed_with_warnings: "warn",
  failed: "danger",
  cancelled: "neutral",
};

export function jobTone(status: string): Tone {
  return JOB_TONES[status] ?? "neutral";
}

/** Review states keep pending review, uncertainty, disagreement and rejection visually distinct. */
const REVIEW_TONES: Record<string, Tone> = {
  proposed: "accent",
  needs_review: "warn",
  verified: "ok",
  inconclusive: "neutral",
  disputed: "danger",
  rejected: "neutral",
};

export function reviewTone(status: string): Tone {
  return REVIEW_TONES[status] ?? "neutral";
}

export const REVIEW_STATUSES = ["proposed", "needs_review", "verified", "inconclusive", "disputed", "rejected"] as const;

export const JOB_KIND_LABELS: Record<string, string> = {
  preview: "Preview",
  analyze: "Ground-image analysis",
};

export const REVIEW_STATUS_LABELS: Record<string, string> = {
  proposed: "Proposed",
  needs_review: "Needs review",
  verified: "Verified",
  inconclusive: "Inconclusive",
  disputed: "Disputed",
  rejected: "Rejected",
};

/** "coco:traffic light" -> "traffic light" */
export function categoryLabel(category: string): string {
  const colon = category.indexOf(":");
  return colon >= 0 ? category.slice(colon + 1) : category;
}

/** What a reviewer does, in words. */
export const REVIEW_ACTION_LABELS: Record<string, string> = {
  confirm: "Confirm",
  reject: "Reject",
  mark_inconclusive: "Mark inconclusive",
  reopen: "Reopen for review",
  relabel: "Correct the category",
  note: "Add a note",
  system_dispute: "Marked disputed by conflict detection",
  system_resolve: "Conflict resolved",
};
