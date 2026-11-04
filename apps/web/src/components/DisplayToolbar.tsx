import { ADJUST_RANGE, type DisplayAdjust, isDefaultAdjust } from "../lib/rasterDisplay";

const button = (active: boolean) =>
  `h-7 rounded-md border px-2.5 text-xs transition-colors disabled:cursor-not-allowed disabled:opacity-45 ${
    active ? "border-accent bg-accent-soft font-medium text-fg" : "border-line text-fg-muted hover:bg-overlay hover:text-fg"
  }`;

/**
 * The few display choices most people need: true colour or grey, brightness and contrast. All of them change only how the
 * picture is drawn; the stored raster is never touched.
 */
export function DisplayToolbar({
  bandCount,
  adjust,
  onAdjust,
  onReset,
  changed,
}: {
  bandCount: number;
  adjust: DisplayAdjust;
  onAdjust: (next: DisplayAdjust) => void;
  /** Back to true colour with no brightness or contrast change, and to the default bands. */
  onReset: () => void;
  /** Whether bands or contrast were changed under Advanced Inspection. */
  changed: boolean;
}) {
  const multiband = bandCount >= 3;
  // A one-band raster is already a grey picture: there is no colour to switch off.
  const grey = !multiband || adjust.mode === "grey";
  const slider = (key: "brightness" | "contrast", label: string) => (
    <label className="flex items-center gap-1.5 text-xs text-fg-muted">
      {label}
      <input
        type="range"
        aria-label={label}
        className="w-24 accent-accent"
        min={ADJUST_RANGE.min}
        max={ADJUST_RANGE.max}
        step={5}
        value={adjust[key]}
        onChange={(event) => onAdjust({ ...adjust, [key]: Number(event.target.value) })}
      />
      <span className="w-9 font-mono text-[11px] text-fg-subtle">{adjust[key]}%</span>
    </label>
  );
  return (
    <div className="flex basis-full flex-wrap items-center gap-x-4 gap-y-1.5 border-t border-line/70 pt-1.5" role="group" aria-label="Display">
      <div className="flex gap-1" role="group" aria-label="Colour">
        <button
          type="button"
          className={button(!grey)}
          aria-pressed={!grey}
          disabled={!multiband}
          title={multiband ? undefined : "This raster has one band, so there is no colour picture to show."}
          onClick={() => onAdjust({ ...adjust, mode: "colour" })}
        >
          True colour
        </button>
        <button type="button" className={button(grey)} aria-pressed={grey} onClick={() => onAdjust({ ...adjust, mode: "grey" })}>
          Grayscale
        </button>
      </div>
      {slider("brightness", "Brightness")}
      {slider("contrast", "Contrast")}
      <button type="button" className={`${button(false)} ml-auto`} disabled={isDefaultAdjust(adjust) && !changed} onClick={onReset}>
        Reset display
      </button>
    </div>
  );
}
