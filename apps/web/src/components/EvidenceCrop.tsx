import { useState } from "react";
import { Link } from "react-router-dom";

export interface EvidenceRegion {
  type: string;
  coordinates: unknown;
  image_width: number;
  image_height: number;
}

interface Box {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

/** The box (or the outer box of a polygon) in the picture's own pixels. */
function boxOf(region: EvidenceRegion): Box | null {
  if (region.type === "bbox_xyxy") {
    const [x0, y0, x1, y1] = region.coordinates as number[];
    return x0 === undefined || y0 === undefined || x1 === undefined || y1 === undefined ? null : { x0, y0, x1, y1 };
  }
  const points = region.coordinates as number[][];
  if (!Array.isArray(points) || points.length === 0) return null;
  const xs = points.map((p) => p[0] ?? 0);
  const ys = points.map((p) => p[1] ?? 0);
  return { x0: Math.min(...xs), y0: Math.min(...ys), x1: Math.max(...xs), y1: Math.max(...ys) };
}

/**
 * The part of the picture to show: the box with room around it, at a 4:3 shape, never smaller than `minimum` pixels across
 * (so a tiny box is not blown up into mush) and never outside the picture.
 */
export function cropWindow(box: Box, imageWidth: number, imageHeight: number, minimum = 160, aspect = 4 / 3) {
  const boxWidth = Math.max(1, box.x1 - box.x0);
  const boxHeight = Math.max(1, box.y1 - box.y0);
  let w = Math.max(boxWidth * 2.2, boxHeight * 2.2 * aspect, minimum);
  let h = w / aspect;
  if (w > imageWidth) {
    w = imageWidth;
    h = w / aspect;
  }
  if (h > imageHeight) {
    h = imageHeight;
    w = h * aspect;
  }
  const x = Math.min(Math.max((box.x0 + box.x1) / 2 - w / 2, 0), Math.max(0, imageWidth - w));
  const y = Math.min(Math.max((box.y0 + box.y1) / 2 - h / 2, 0), Math.max(0, imageHeight - h));
  return { x, y, w, h };
}

/**
 * Evidence for one finding that can actually be read: a zoomed view of the detected object with its box drawn, and a small
 * picture of the whole image showing where it is. `src` is the full-size picture; `fallback` is used if it cannot be loaded.
 * Boxes are in the picture's own pixel grid, so the picture is drawn without applying any rotation stored in the file.
 */
export function EvidenceCrop({
  src,
  fallback,
  region,
  alt,
  openTo,
  className = "w-48",
}: {
  src: string;
  fallback?: string;
  region: EvidenceRegion;
  alt: string;
  /** Where the full photograph can be opened in the application. */
  openTo?: string;
  className?: string;
}) {
  const [source, setSource] = useState(src);
  const box = boxOf(region);
  if (!box) return null;
  const { image_width: W, image_height: H } = region;
  const win = cropWindow(box, W, H);
  const boxWidth = Math.round(box.x1 - box.x0);
  const boxHeight = Math.round(box.y1 - box.y0);
  const onError = () => {
    if (fallback && source !== fallback) setSource(fallback);
  };
  const stroke = { fill: "none", stroke: "#e11d48", vectorEffect: "non-scaling-stroke" as const };

  return (
    <figure className={`${className} shrink-0 space-y-1`}>
      <div className="relative overflow-hidden rounded border border-line bg-raised" style={{ aspectRatio: `${win.w} / ${win.h}` }}>
        <img
          src={source}
          alt={alt}
          loading="lazy"
          onError={onError}
          draggable={false}
          style={{
            position: "absolute",
            maxWidth: "none",
            width: `${(W / win.w) * 100}%`,
            left: `${(-win.x / win.w) * 100}%`,
            top: `${(-win.y / win.h) * 100}%`,
            imageOrientation: "none",
          }}
        />
        <svg className="absolute inset-0 size-full" viewBox={`${win.x} ${win.y} ${win.w} ${win.h}`} preserveAspectRatio="none" aria-hidden="true">
          <rect x={box.x0} y={box.y0} width={box.x1 - box.x0} height={box.y1 - box.y0} strokeWidth={2} {...stroke} />
        </svg>
        {/* the whole picture, small, with the box and the zoomed area marked, so the object can be placed */}
        <div
          className="absolute right-1 bottom-1 w-1/4 overflow-hidden rounded-sm border border-white/80 bg-raised shadow"
          style={{ aspectRatio: `${W} / ${H}` }}
          aria-hidden="true"
        >
          <img src={source} alt="" loading="lazy" onError={onError} className="absolute inset-0 size-full object-fill" style={{ imageOrientation: "none" }} />
          <svg className="absolute inset-0 size-full" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none">
            <rect x={win.x} y={win.y} width={win.w} height={win.h} strokeWidth={1} {...stroke} stroke="#ffffff" />
            <rect x={box.x0} y={box.y0} width={box.x1 - box.x0} height={box.y1 - box.y0} strokeWidth={1.5} {...stroke} />
          </svg>
        </div>
      </div>
      <figcaption className="text-[10px] leading-snug text-fg-subtle">
        Zoomed in: box {boxWidth}×{boxHeight} px of a {W}×{H} px picture.
        {openTo ? (
          <>
            {" "}
            <Link to={openTo} className="text-accent hover:underline print:hidden">
              Open the photograph
            </Link>
          </>
        ) : null}
      </figcaption>
    </figure>
  );
}
