import { type ReactNode, useEffect, useRef, useState } from "react";

import { IconFit, IconZoomIn, IconZoomOut } from "./icons";
import { IconButton } from "./ui";

const STEPS = [0.25, 0.5, 0.75, 1, 1.5, 2, 3, 4, 6, 8];

/**
 * A pannable, zoomable view of one picture. "Fit" shows the whole picture in the available space; zoom is
 * relative to the picture's own pixels. `overlay` is drawn in the picture's coordinate frame and scales with it.
 * Scrolling the frame pans; Ctrl/Cmd + wheel zooms.
 */
export function ImageViewer({
  src,
  alt,
  width,
  height,
  overlay,
  caption,
  toolbar,
  large = false,
  imageFilter,
  allowFullscreen = false,
}: {
  src: string;
  alt: string;
  /** Natural size of the picture, so zoom levels mean something before it loads. */
  width: number;
  height: number;
  /** Drawn in the picture's frame. A function receives the zoom (screen pixels per image pixel). */
  overlay?: ReactNode | ((scale: number) => ReactNode);
  caption?: ReactNode;
  toolbar?: ReactNode;
  /** Use most of the screen's height for the picture (the screen's main subject). */
  large?: boolean;
  /** A CSS filter applied to the picture only (display-only brightness, contrast, grey). The overlay is not filtered. */
  imageFilter?: string;
  /** Offer a full-screen button. */
  allowFullscreen?: boolean;
}) {
  const frame = useRef<HTMLDivElement>(null);
  const shell = useRef<HTMLDivElement>(null);
  const [full, setFull] = useState(false);
  const [fitScale, setFitScale] = useState(1);
  const [scale, setScale] = useState<number | null>(null); // null = fit
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    const element = frame.current;
    if (!element) return;
    const measure = () => {
      const available = element.clientWidth - 2;
      const maxHeight = Math.max(240, window.innerHeight * (full ? 0.84 : large ? 0.8 : 0.66));
      if (available <= 0) return; // not laid out yet
      setFitScale(Math.min(available / width, maxHeight / height, 4));
    };
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, [width, height, large, full]);

  useEffect(() => {
    const onChange = () => setFull(document.fullscreenElement !== null && document.fullscreenElement === shell.current);
    document.addEventListener("fullscreenchange", onChange);
    return () => document.removeEventListener("fullscreenchange", onChange);
  }, []);
  const toggleFullscreen = () => {
    if (document.fullscreenElement) void document.exitFullscreen();
    else void shell.current?.requestFullscreen?.();
  };

  useEffect(() => setFailed(false), [src]);

  const current = scale ?? fitScale;
  const zoom = (direction: 1 | -1) => {
    const next =
      direction > 0 ? STEPS.find((step) => step > current + 1e-6) : [...STEPS].reverse().find((step) => step < current - 1e-6);
    if (next) setScale(next);
  };

  const shownWidth = Math.round(width * current);
  const shownHeight = Math.round(height * current);

  return (
    <div ref={shell} className="overflow-hidden rounded-lg border border-line bg-surface">
      <div className="flex flex-wrap items-center gap-1 border-b border-line px-2 py-1.5">
        <IconButton label="Zoom out" onClick={() => zoom(-1)} disabled={current <= STEPS[0]! + 1e-6}>
          <IconZoomOut />
        </IconButton>
        <span className="w-14 text-center font-mono text-xs text-fg-muted" aria-live="polite" aria-label="Zoom level">
          {Math.round(current * 100)}%
        </span>
        <IconButton label="Zoom in" onClick={() => zoom(1)} disabled={current >= STEPS[STEPS.length - 1]! - 1e-6}>
          <IconZoomIn />
        </IconButton>
        <IconButton label="Fit to view" onClick={() => setScale(null)} aria-pressed={scale === null}>
          <IconFit />
        </IconButton>
        <button
          type="button"
          onClick={() => setScale(1)}
          aria-pressed={scale === 1}
          className="h-8 rounded-md px-2 text-xs text-fg-muted hover:bg-overlay hover:text-fg"
        >
          Actual size
        </button>
        <span className="ml-auto text-xs text-fg-subtle">
          {width} × {height} px
        </span>
        {allowFullscreen && typeof document !== "undefined" && document.fullscreenEnabled ? (
          <button
            type="button"
            onClick={toggleFullscreen}
            aria-pressed={full}
            className="h-8 rounded-md px-2 text-xs text-fg-muted hover:bg-overlay hover:text-fg"
          >
            {full ? "Exit full screen" : "Full screen"}
          </button>
        ) : null}
        {toolbar}
      </div>
      <div
        ref={frame}
        className="overflow-auto bg-canvas"
        style={{ maxHeight: full ? "calc(100vh - 9rem)" : large ? "84vh" : "70vh" }}
        tabIndex={0}
        aria-label={`${alt}. Scroll to pan.`}
        onWheel={(event) => {
          if (event.ctrlKey || event.metaKey) {
            event.preventDefault();
            zoom(event.deltaY < 0 ? 1 : -1);
          }
        }}
      >
        <div className="relative mx-auto" style={{ width: shownWidth, height: shownHeight }}>
          {failed ? (
            <p className="flex h-full items-center justify-center text-[13px] text-fg-muted">The preview picture could not be loaded.</p>
          ) : (
            <img
              src={src}
              alt={alt}
              draggable={false}
              onError={() => setFailed(true)}
              className="block h-full w-full select-none"
              style={imageFilter ? { filter: imageFilter } : undefined}
            />
          )}
          {overlay && !failed ? <div className="absolute inset-0">{typeof overlay === "function" ? overlay(current) : overlay}</div> : null}
        </div>
      </div>
      {caption ? <div className="border-t border-line px-3 py-2 text-xs text-fg-subtle">{caption}</div> : null}
    </div>
  );
}
