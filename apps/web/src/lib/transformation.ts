import type { TransformationOperation } from "../api/client";
import type { Tone } from "../components/ui";

type Classification = TransformationOperation["classification"];

export const CLASS_LABELS: Record<Classification, string> = {
  exact: "Exact",
  approximate: "Approximate",
  ballpark: "Ballpark — blocked",
  unavailable: "Grid missing",
};

export const CLASS_TONES: Record<Classification, Tone> = {
  exact: "ok",
  approximate: "warn",
  ballpark: "danger",
  unavailable: "danger",
};

/** What the documented accuracy of an operation means, in words. Never presents a missing figure as zero. */
export function accuracyText(classification: Classification, accuracyM: number | null | undefined): string {
  if (classification === "exact") return "exact (no datum change)";
  if (classification === "ballpark" || accuracyM == null) return "not stated";
  const metres = String(Number(accuracyM.toPrecision(3)));
  return classification === "unavailable" ? `about ${metres} m, if its grid were installed` : `about ${metres} m`;
}

export const PLAN_STATUS_LABELS: Record<string, string> = {
  exact: "Exact",
  approximate: "Approximate",
  selection_required: "Choice required",
  unavailable: "Unavailable",
};

export const PLAN_STATUS_TONES: Record<string, Tone> = {
  exact: "ok",
  approximate: "warn",
  selection_required: "warn",
  unavailable: "danger",
};

/** One line for a layer or a result: which operation, its class and accuracy. */
export function describeOperation(operation: {
  id: string;
  name?: string;
  classification: Classification;
  accuracy_m: number | null;
}): string {
  const label = CLASS_LABELS[operation.classification];
  if (operation.classification === "exact") return `${label}: no datum change`;
  const name = operation.name ? `${operation.name} (${operation.id})` : operation.id;
  return `${label}: ${name}, accuracy ${accuracyText(operation.classification, operation.accuracy_m)}`;
}

/** The refusal code for a datum shift worse than the limit (or with no stated accuracy) that nobody has acknowledged. */
export const ACK_REQUIRED = "TRANSFORMATION_ACKNOWLEDGEMENT_REQUIRED";

export interface AcknowledgementNeed {
  operationId: string;
  operationName: string;
  accuracyM: number | null;
  thresholdM: number;
  reason: "accuracy_above_threshold" | "accuracy_not_stated";
  stale: boolean;
}

/** What has to be acknowledged, read from the API's 409 context. Null if the context does not describe it. */
export function acknowledgementFromContext(context: Record<string, unknown> | null | undefined): AcknowledgementNeed | null {
  if (!context || typeof context.operation_id !== "string") return null;
  const reason = context.reason === "accuracy_not_stated" ? "accuracy_not_stated" : "accuracy_above_threshold";
  return {
    operationId: context.operation_id,
    operationName: typeof context.operation_name === "string" ? context.operation_name : context.operation_id,
    accuracyM: typeof context.accuracy_m === "number" ? context.accuracy_m : null,
    thresholdM: typeof context.threshold_m === "number" ? context.threshold_m : 10,
    reason,
    stale: context.stale_acknowledgement === true,
  };
}

/** What has to be acknowledged, read from a plan's operations: the one chosen, or the only usable one that needs it. */
export function acknowledgementFromPlan(
  plan: { operations: TransformationOperation[] },
  chosenId?: string | null,
): AcknowledgementNeed | null {
  const operation = plan.operations.find(
    (o) => o.requires_acknowledgement && o.usable && (chosenId ? o.id === chosenId : true),
  );
  if (!operation) return null;
  return {
    operationId: operation.id,
    operationName: operation.name,
    accuracyM: operation.accuracy_m,
    thresholdM: operation.acknowledgement_threshold_m,
    reason: operation.acknowledgement_reason ?? "accuracy_above_threshold",
    stale: false,
  };
}

/** The sentence a person agrees to. States the figure and the limit, never softens a missing accuracy. */
export function acknowledgementText(need: AcknowledgementNeed): string {
  const accuracy =
    need.reason === "accuracy_not_stated" || need.accuracyM == null
      ? "has no stated accuracy, so its error could be any size"
      : `is documented to about ${String(Number(need.accuracyM.toPrecision(3)))} m, worse than the ${need.thresholdM} m limit`;
  return `The datum shift ${need.operationName} (${need.operationId}) ${accuracy}. I accept that positions placed with it can be off by at least that much.`;
}
