import { type AcknowledgementNeed, acknowledgementText } from "../lib/transformation";
import { Notice } from "./ui";

/**
 * Asks a person to accept the accuracy of a datum shift that is worse than the limit (or has no stated accuracy) before it
 * is used. The statement names the operation and the figure. `where` says where the acknowledgement is kept, because that
 * differs: with a georeferencing version, with an area of interest, or nowhere (a one-off readout).
 */
export function AccuracyAcknowledgement({
  need,
  checked,
  onChange,
  where,
  idPrefix,
}: {
  need: AcknowledgementNeed;
  checked: boolean;
  onChange: (checked: boolean) => void;
  where: string;
  idPrefix: string;
}) {
  return (
    <div className="space-y-2" data-testid="accuracy-acknowledgement" aria-label="Datum shift accuracy">
      <Notice tone="warning" title="This datum shift is not accurate enough to use without your acknowledgement">
        <p className="text-xs">
          {need.stale ? "An earlier acknowledgement no longer matches this operation or its accuracy. " : ""}
          {need.reason === "accuracy_not_stated"
            ? "The registry states no accuracy for it."
            : `Its documented accuracy is worse than ${need.thresholdM} m.`}{" "}
          Nothing has been converted with it.
        </p>
      </Notice>
      <label htmlFor={`${idPrefix}-ack`} className="flex items-start gap-2 text-xs text-fg">
        <input
          id={`${idPrefix}-ack`}
          type="checkbox"
          className="mt-0.5 accent-accent"
          checked={checked}
          onChange={(event) => onChange(event.target.checked)}
        />
        <span>{acknowledgementText(need)}</span>
      </label>
      <p className="text-[11px] text-fg-subtle">{where}</p>
    </div>
  );
}
