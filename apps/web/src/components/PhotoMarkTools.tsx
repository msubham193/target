import { useState } from "react";

import { type Mark, MARK_KIND_LABELS } from "../lib/photoMarks";
import { classColour, type DrawTool } from "./DetectionOverlay";
import { IconClose } from "./icons";
import { Badge, Button, IconButton, inputClass, Panel } from "./ui";

const TOOLS: { value: DrawTool; label: string; hint: string }[] = [
  { value: "select", label: "Look", hint: "Click a box or a mark on the photo to select it." },
  { value: "box", label: "Box", hint: "Press, drag and release to draw a box around the target." },
  { value: "point", label: "Point", hint: "Click once on the target to place a point." },
  { value: "outline", label: "Outline", hint: "Click each corner of the target. Double-click, or press Enter, to finish. Esc cancels." },
];

/** Classes a person is likely to name, offered as suggestions next to the ones the model found in this photograph. */
const COMMON_CLASSES = ["target", "person", "car", "truck", "bus", "motorcycle", "bicycle", "building", "boat", "antenna", "tower"];

/** The drawing tools and the label the next mark will get. Sits in the photo viewer's toolbar. */
export function MarkToolbar({
  tool,
  onTool,
  label,
  onLabel,
  classes,
  allLabels,
  onAllLabels,
}: {
  tool: DrawTool;
  onTool: (tool: DrawTool) => void;
  label: string;
  onLabel: (label: string) => void;
  /** Class names the model found in this photograph. */
  classes: string[];
  allLabels: boolean;
  onAllLabels: (value: boolean) => void;
}) {
  const options = [...new Set([...classes, ...COMMON_CLASSES])];
  const hint = TOOLS.find((item) => item.value === tool)!.hint;
  return (
    <div className="flex basis-full flex-wrap items-center gap-2 border-t border-line/70 pt-1.5" role="group" aria-label="Drawing tools">
      <span className="text-xs font-medium text-fg-muted">Draw:</span>
      <div className="flex gap-1">
        {TOOLS.map((item) => (
          <button
            key={item.value}
            type="button"
            aria-pressed={tool === item.value}
            onClick={() => onTool(item.value)}
            className={`h-7 rounded-md border px-2.5 text-xs transition-colors ${
              tool === item.value ? "border-accent bg-accent-soft font-medium text-fg" : "border-line text-fg-muted hover:bg-overlay hover:text-fg"
            }`}
          >
            {item.label}
          </button>
        ))}
      </div>
      <label className="flex items-center gap-1.5 text-xs text-fg-muted">
        Label
        <input
          list="mark-classes"
          aria-label="Label for new marks"
          className={`${inputClass} h-7 w-36 py-0 text-xs`}
          value={label}
          maxLength={60}
          onChange={(event) => onLabel(event.target.value)}
        />
        <datalist id="mark-classes">
          {options.map((name) => (
            <option key={name} value={name} />
          ))}
        </datalist>
      </label>
      <label className="ml-auto flex items-center gap-1.5 text-xs text-fg-muted">
        <input type="checkbox" className="accent-accent" checked={allLabels} onChange={(event) => onAllLabels(event.target.checked)} />
        Show every class label
      </label>
      <p className="basis-full text-xs text-fg-subtle" aria-live="polite">
        {hint}
      </p>
    </div>
  );
}

/** The marks a person has drawn on this photograph, to rename, add a note to, or delete. */
export function PhotoMarksPanel({
  marks,
  selected,
  onSelect,
  onUpdate,
  onRemove,
  onClear,
}: {
  marks: Mark[];
  selected: string | null;
  onSelect: (id: string | null) => void;
  onUpdate: (id: string, patch: Partial<Pick<Mark, "label" | "note">>) => void;
  onRemove: (id: string) => void;
  onClear: () => void;
}) {
  const [confirming, setConfirming] = useState(false);
  return (
    <Panel
      title="Your marks"
      description="Drawn by you on this photograph. They are not model findings, they have no location, and they are kept in this browser only: not shared, not in reports."
      actions={
        marks.length > 0 ? (
          confirming ? (
            <span className="flex items-center gap-1.5">
              <Button
                size="sm"
                variant="danger"
                onClick={() => {
                  onClear();
                  setConfirming(false);
                }}
              >
                Delete all {marks.length}
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setConfirming(false)}>
                Keep
              </Button>
            </span>
          ) : (
            <Button size="sm" variant="ghost" onClick={() => setConfirming(true)}>
              Delete all
            </Button>
          )
        ) : null
      }
      padded={false}
    >
      {marks.length === 0 ? (
        <p className="px-4 py-4 text-[13px] text-fg-muted">
          Nothing drawn yet. Choose <span className="font-medium text-fg">Box</span>, <span className="font-medium text-fg">Point</span> or{" "}
          <span className="font-medium text-fg">Outline</span> above the photo, then draw on it.
        </p>
      ) : (
        <ul aria-label="Your marks" className="max-h-72 overflow-auto">
          {marks.map((mark, position) => (
            <li
              key={mark.id}
              className={`flex items-start gap-2.5 border-b border-line/70 px-4 py-2.5 last:border-b-0 ${selected === mark.id ? "bg-accent-soft" : ""}`}
              onClick={() => onSelect(selected === mark.id ? null : mark.id)}
            >
              <span aria-hidden="true" className="mt-1.5 size-2.5 shrink-0 rounded-full" style={{ background: classColour(mark.label, 70) }} />
              <div className="min-w-0 flex-1 space-y-1">
                <div className="flex items-center gap-2">
                  <span className="text-xs text-fg-subtle">{position + 1}</span>
                  <input
                    key={`${mark.id}-${mark.label}`}
                    aria-label={`Label of mark ${position + 1}`}
                    className={`${inputClass} h-7 min-w-0 flex-1 py-0 text-[13px]`}
                    defaultValue={mark.label}
                    maxLength={60}
                    onClick={(event) => event.stopPropagation()}
                    onBlur={(event) => {
                      const next = event.target.value.trim();
                      if (next && next !== mark.label) onUpdate(mark.id, { label: next });
                      else event.target.value = mark.label;
                    }}
                    onKeyDown={(event) => {
                      if (event.key === "Enter") (event.target as HTMLInputElement).blur();
                    }}
                  />
                  <Badge>{MARK_KIND_LABELS[mark.shape.kind]}</Badge>
                </div>
                <input
                  key={`${mark.id}-note-${mark.note}`}
                  aria-label={`Note on mark ${position + 1}`}
                  className={`${inputClass} h-7 py-0 text-xs`}
                  placeholder="Add a note (optional)"
                  defaultValue={mark.note}
                  maxLength={500}
                  onClick={(event) => event.stopPropagation()}
                  onBlur={(event) => event.target.value !== mark.note && onUpdate(mark.id, { note: event.target.value.trim() })}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") (event.target as HTMLInputElement).blur();
                  }}
                />
              </div>
              <IconButton
                label={`Delete mark ${position + 1}`}
                onClick={(event) => {
                  event.stopPropagation();
                  onRemove(mark.id);
                }}
              >
                <IconClose size={14} />
              </IconButton>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}
