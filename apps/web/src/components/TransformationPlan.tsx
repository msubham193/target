import type { TransformationOperation, TransformationPlan as Plan } from "../api/client";
import { accuracyText, CLASS_LABELS, CLASS_TONES, PLAN_STATUS_LABELS, PLAN_STATUS_TONES } from "../lib/transformation";
import { Badge } from "./ui";

export function ClassBadge({ operation }: { operation: Pick<TransformationOperation, "classification"> }) {
  return <Badge tone={CLASS_TONES[operation.classification]}>{CLASS_LABELS[operation.classification]}</Badge>;
}

/**
 * Every coordinate operation PROJ offers between two CRSs, with its class, documented accuracy and any missing grid.
 * With `onChange`, the usable ones can be chosen (a ballpark or grid-missing operation never can). The suggestion is
 * only marked; nothing is selected until a person selects it.
 */
export function TransformationPlanView({
  plan,
  value,
  onChange,
  idPrefix,
  heading,
}: {
  plan: Plan;
  value?: string | null;
  onChange?: (id: string) => void;
  idPrefix: string;
  heading?: string;
}) {
  const needsChoice = plan.status === "selection_required";
  return (
    <div className="space-y-2" aria-label={heading ?? `Transformation from ${plan.source_crs} to ${plan.target_crs}`}>
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <Badge tone={PLAN_STATUS_TONES[plan.status] ?? "neutral"}>{PLAN_STATUS_LABELS[plan.status] ?? plan.status}</Badge>
        <span className="text-fg-muted">{plan.status_message}</span>
      </div>
      {plan.messages.map((message) => (
        <p key={message} className="text-xs text-fg-muted">
          {message}
        </p>
      ))}
      {plan.operations.length > 0 ? (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <caption className="sr-only">Coordinate operations from {plan.source_crs} to {plan.target_crs}</caption>
            <thead>
              <tr className="text-fg-subtle">
                {onChange ? (
                  <th scope="col" className="py-1 pr-2 font-medium">
                    <span className="sr-only">Choose</span>
                  </th>
                ) : null}
                <th scope="col" className="py-1 pr-3 font-medium">Operation</th>
                <th scope="col" className="py-1 pr-3 font-medium">Class</th>
                <th scope="col" className="py-1 pr-3 font-medium">Accuracy</th>
                <th scope="col" className="py-1 font-medium">Notes</th>
              </tr>
            </thead>
            <tbody>
              {plan.operations.map((operation) => {
                const missing = operation.grids.filter((grid) => !grid.available).map((grid) => grid.name);
                return (
                  <tr key={`${operation.id}-${operation.name}`} className="border-t border-line align-top" data-testid="operation">
                    {onChange ? (
                      <td className="py-1.5 pr-2">
                        <input
                          type="radio"
                          name={`${idPrefix}-operation`}
                          id={`${idPrefix}-${operation.id}`}
                          className="accent-accent"
                          disabled={!operation.usable}
                          checked={value === operation.id}
                          onChange={() => onChange(operation.id)}
                          aria-label={`Use ${operation.name} (${operation.id})`}
                        />
                      </td>
                    ) : null}
                    <td className="py-1.5 pr-3">
                      <span className="block text-fg">{operation.name}</span>
                      <span className="block font-mono text-[11px] text-fg-subtle">{operation.id}</span>
                      {operation.area_of_use ? <span className="block text-[11px] text-fg-subtle">{operation.area_of_use}</span> : null}
                    </td>
                    <td className="py-1.5 pr-3">
                      <ClassBadge operation={operation} />
                    </td>
                    <td className="py-1.5 pr-3 text-fg-muted">{accuracyText(operation.classification, operation.accuracy_m)}</td>
                    <td className="py-1.5 text-fg-muted">
                      {missing.length > 0 ? <span className="block text-danger">Needs {missing.join(", ")} (not installed)</span> : null}
                      {operation.classification === "ballpark" ? <span className="block">Blocked: no documented accuracy.</span> : null}
                      {operation.requires_acknowledgement ? (
                        <span className="block text-warn">
                          {operation.acknowledgement_reason === "accuracy_not_stated"
                            ? "Needs your acknowledgement: accuracy not stated."
                            : `Needs your acknowledgement: worse than ${operation.acknowledgement_threshold_m} m.`}
                        </span>
                      ) : null}
                      {needsChoice && operation.recommended ? <span className="block text-fg-subtle">Smallest documented accuracy (a suggestion only)</span> : null}
                      {operation.selected ? <span className="block text-accent">Selected</span> : null}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  );
}
