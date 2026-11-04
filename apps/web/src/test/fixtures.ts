import type { Observation, ObservationDetail, ObservationView, ReviewStatus } from "../api/client";

// The review actions the API offers in each state (mirrors geopoc.contracts.review for reviewers).
const ALLOWED: Record<ReviewStatus, [string, string | null, boolean][]> = {
  proposed: [
    ["confirm", "verified", false],
    ["reject", "rejected", true],
    ["mark_inconclusive", "inconclusive", true],
  ],
  needs_review: [
    ["confirm", "verified", false],
    ["reject", "rejected", true],
    ["mark_inconclusive", "inconclusive", true],
  ],
  verified: [["reopen", "needs_review", true]],
  inconclusive: [
    ["reject", "rejected", true],
    ["reopen", "needs_review", true],
  ],
  disputed: [
    ["confirm", "verified", true],
    ["reject", "rejected", true],
    ["reopen", "needs_review", true],
  ],
  rejected: [["reopen", "needs_review", true]],
};

export function allowedActions(status: ReviewStatus): ObservationView["allowed_actions"] {
  const actions = ALLOWED[status].map(([action, target, note]) => ({
    action: action as ObservationView["allowed_actions"][number]["action"],
    target_status: target as ReviewStatus | null,
    note_required: note,
  }));
  if (status !== "rejected") actions.push({ action: "relabel", target_status: null, note_required: true });
  actions.push({ action: "note", target_status: null, note_required: true });
  return actions;
}

/** The API's view of a stored observation, in its model-produced state unless overridden. */
export function view(
  observation: Observation,
  overrides: Partial<Omit<ObservationView, "observation" | "current">> & { current?: Partial<ObservationView["current"]> } = {},
): ObservationView {
  const status = overrides.current?.status ?? (observation.review.status as ReviewStatus);
  return {
    observation,
    asset_filename: "photo.jpg",
    open_conflict_ids: [],
    possible_duplicate_group_ids: [],
    allowed_actions: allowedActions(status),
    ...overrides,
    current: {
      status,
      category: observation.claim.category,
      relabelled: false,
      revision: 0,
      last_changed_at: null,
      last_changed_by: null,
      last_note: null,
      ...overrides.current,
    },
  };
}

export function detail(base: ObservationView, extra: Partial<Pick<ObservationDetail, "revisions" | "conflicts" | "links">> = {}): ObservationDetail {
  return { ...base, revisions: [], conflicts: [], links: [], ...extra };
}
