import { type PointerEvent as ReactPointerEvent, type ReactNode, useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";

import { IconExitFullscreen, IconFit, IconFullscreen, IconZoomIn, IconZoomOut } from "./icons";
import { Button, IconButton, Loading } from "./ui";

/** Most screen pixels drawn per pixel of the rendered picture. Past this the picture only gets blurrier. */
const MAX_MAGNIFICATION = 6;
const WHEEL_SENSITIVITY = 0.0015;
const BUTTON_FACTOR = 1.5;
const EPSILON = 1e-6;

/**
 * Where the viewer is looking. `zoom` is relative to "fit" (1 = the whole picture is visible), and (cx, cy) is the point
 * of the picture, in its own pixels, at the centre of the viewer. Kept relative like this, entering or leaving full
 * screen (which changes the viewer's size) keeps the same part of the picture in view.
 */
interface View {
  zoom: number;
  cx: number;
  cy: number;
}

interface Frame {
  fit: number;
  maxZoom: number;
  box: { w: number; h: number };
  width: number;
  height: number;
}

const clamp = (value: number, low: number, high: number) => Math.min(high, Math.max(low, value));

/** Keeps the picture reachable: never zoomed out past "fit", and never panned so far that it leaves the viewer. */
function constrain(view: View, frame: Frame): View {
  const zoom = clamp(view.zoom, 1, frame.maxZoom);
  const scale = frame.fit * zoom;
  const halfW = frame.box.w / (2 * scale);
  const halfH = frame.box.h / (2 * scale);
  return {
    zoom,
    // A picture smaller than the viewer on an axis stays centred on that axis.
    cx: frame.width * scale <= frame.box.w ? frame.width / 2 : clamp(view.cx, halfW, frame.width - halfW),
    cy: frame.height * scale <= frame.box.h ? frame.height / 2 : clamp(view.cy, halfH, frame.height - halfH),
  };
}

/**
 * One picture in a viewer that fills its space: drag to pan, wheel to zoom at the pointer, buttons for zoom, fit and full
 * screen. The picture is the server's rendered preview (never the raster itself); `width` and `height` are the raster's
 * own size, which is the coordinate frame the view works in, and the preview is stretched to it. Nothing here changes or
 * reads the raster's values, and no position on the Earth is implied.
 */
export function PanZoomViewer({
  src,
  alt,
  width,
  height,
  renderedSide,
  header,
}: {
  src: string;
  alt: string;
  width: number;
  height: number;
  /** The longest side, in pixels, of the rendered preview. Limits how far it is worth zooming in. */
  renderedSide: number;
  /** Drawn above the picture inside the viewer, so it stays visible in full screen. */
  header?: ReactNode;
}) {
  const root = useRef<HTMLDivElement>(null);
  const port = useRef<HTMLDivElement>(null);
  const [box, setBox] = useState({ w: 0, h: 0 });
  const [view, setView] = useState<View>({ zoom: 1, cx: width / 2, cy: height / 2 });
  const [animate, setAnimate] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [full, setFull] = useState(false);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [attempt, setAttempt] = useState(0);

  const fit = box.w > 0 && box.h > 0 ? Math.min(box.w / width, box.h / height) : 1;
  const density = Math.min(1, renderedSide / Math.max(width, height));
  const maxZoom = Math.max(1, (MAX_MAGNIFICATION * density) / fit);
  const frame: Frame = { fit, maxZoom, box, width, height };
  const shown = constrain(view, frame);
  const scale = fit * shown.zoom;

  // Handlers that are not re-created on every render (the wheel listener) read the latest values from here.
  const live = useRef({ frame, shown });
  live.current = { frame, shown };

  useLayoutEffect(() => {
    const element = port.current;
    if (!element) return;
    const measure = () => setBox({ w: element.clientWidth, h: element.clientHeight });
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const onChange = () => setFull(document.fullscreenElement !== null && document.fullscreenElement === root.current);
    document.addEventListener("fullscreenchange", onChange);
    return () => document.removeEventListener("fullscreenchange", onChange);
  }, []);

  const settle = useRef<number | undefined>(undefined);
  useEffect(() => () => window.clearTimeout(settle.current), []);
  /** A short glide for the buttons. Dragging and the wheel are applied directly, so they follow the pointer. */
  const glideTo = useCallback((next: View) => {
    setAnimate(true);
    setView(next);
    window.clearTimeout(settle.current);
    settle.current = window.setTimeout(() => setAnimate(false), 220);
  }, []);

  // Wheel zoom, anchored on the point under the pointer. Registered by hand: React's wheel handler is passive, and the
  // page must not scroll while the picture zooms.
  useEffect(() => {
    const element = port.current;
    if (!element) return;
    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      const { frame: f, shown: v } = live.current;
      if (f.box.w === 0) return;
      const rect = element.getBoundingClientRect();
      const dx = event.clientX - rect.left - f.box.w / 2;
      const dy = event.clientY - rect.top - f.box.h / 2;
      const lines = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? rect.height : 1;
      const zoom = clamp(v.zoom * Math.exp(-event.deltaY * lines * WHEEL_SENSITIVITY), 1, f.maxZoom);
      const before = f.fit * v.zoom;
      const after = f.fit * zoom;
      setAnimate(false);
      // The picture point under the pointer stays under it.
      setView(constrain({ zoom, cx: v.cx + dx / before - dx / after, cy: v.cy + dy / before - dy / after }, f));
    };
    element.addEventListener("wheel", onWheel, { passive: false });
    return () => element.removeEventListener("wheel", onWheel);
  }, []);

  const drag = useRef<{ x: number; y: number; cx: number; cy: number } | null>(null);
  const onPointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return;
    event.currentTarget.setPointerCapture?.(event.pointerId);
    drag.current = { x: event.clientX, y: event.clientY, cx: shown.cx, cy: shown.cy };
    setAnimate(false);
    setDragging(true);
  };
  const onPointerMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    const start = drag.current;
    if (!start) return;
    setView(
      constrain(
        { zoom: shown.zoom, cx: start.cx - (event.clientX - start.x) / scale, cy: start.cy - (event.clientY - start.y) / scale },
        frame,
      ),
    );
  };
  const endDrag = () => {
    drag.current = null;
    setDragging(false);
  };

  const zoomBy = (factor: number) => glideTo(constrain({ ...shown, zoom: shown.zoom * factor }, frame));
  const fitToScreen = () => glideTo({ zoom: 1, cx: width / 2, cy: height / 2 });
  const toggleFullscreen = () => {
    if (document.fullscreenElement) void document.exitFullscreen();
    else void root.current?.requestFullscreen?.();
  };

  const measured = box.w > 0 && box.h > 0;
  return (
    <div ref={root} className="flex h-full min-h-0 flex-col bg-canvas">
      {header}
      <div className="relative min-h-0 flex-1 overflow-hidden bg-black/40">
        <div
          ref={port}
          data-testid="viewer-port"
          data-zoom={shown.zoom.toFixed(3)}
          role="application"
          aria-label={`${alt}. Drag to pan, scroll to zoom.`}
          className={`absolute inset-0 touch-none select-none ${dragging ? "cursor-grabbing" : "cursor-grab"}`}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={endDrag}
          onPointerCancel={endDrag}
        >
          {status !== "error" ? (
            <img
              key={attempt}
              src={attempt > 0 ? `${src}&retry=${attempt}` : src}
              alt={alt}
              draggable={false}
              onLoad={() => setStatus("ready")}
              onError={() => setStatus("error")}
              className="pointer-events-none absolute left-0 top-0 max-w-none"
              style={{
                width,
                height,
                transformOrigin: "0 0",
                transform: `translate(${box.w / 2 - shown.cx * scale}px, ${box.h / 2 - shown.cy * scale}px) scale(${scale})`,
                transition: animate ? "transform 200ms ease-out" : "none",
                visibility: measured && status === "ready" ? "visible" : "hidden",
              }}
            />
          ) : null}
        </div>

        {status === "loading" ? (
          <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
            <Loading label="Loading image…" />
          </div>
        ) : null}
        {status === "error" ? (
          <div role="alert" className="absolute inset-0 flex flex-col items-center justify-center gap-3 px-6 text-center">
            <p className="text-sm text-fg-muted">The image could not be loaded.</p>
            <Button
              variant="secondary"
              onClick={() => {
                setStatus("loading");
                setAttempt((n) => n + 1);
              }}
            >
              Try again
            </Button>
          </div>
        ) : null}

        <div
          role="toolbar"
          aria-label="Image view"
          className="absolute right-3 top-3 z-10 flex items-center gap-0.5 rounded-lg border border-white/10 bg-surface/80 p-0.5 shadow-panel backdrop-blur-md"
        >
          <IconButton label="Zoom out" onClick={() => zoomBy(1 / BUTTON_FACTOR)} disabled={shown.zoom <= 1 + EPSILON}>
            <IconZoomOut />
          </IconButton>
          <IconButton label="Zoom in" onClick={() => zoomBy(BUTTON_FACTOR)} disabled={shown.zoom >= maxZoom - EPSILON}>
            <IconZoomIn />
          </IconButton>
          <IconButton label="Fit to screen" onClick={fitToScreen}>
            <IconFit />
          </IconButton>
          {typeof document !== "undefined" && document.fullscreenEnabled ? (
            <IconButton label={full ? "Exit full screen" : "Full screen"} onClick={toggleFullscreen} aria-pressed={full}>
              {full ? <IconExitFullscreen /> : <IconFullscreen />}
            </IconButton>
          ) : null}
        </div>
      </div>
    </div>
  );
}
