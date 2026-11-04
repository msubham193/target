import { useEffect, useRef, useState } from "react";

import type { ObservationView } from "../api/client";
import { badgeFor } from "../lib/avatar";
import { categoryLabel } from "../lib/jobs";
import { anchorOf, boxFromCorners, clamp, type Mark, type MarkShape } from "../lib/photoMarks";

export interface Box {
  index: number;
  x: number;
  y: number;
  width: number;
  height: number;
  imageWidth: number;
  imageHeight: number;
  label: string;
}

/** Boxes in the original's pixel grid. The overlay's viewBox is that grid, so no rescaling arithmetic is needed. */
export function boxes(views: ObservationView[]): Box[] {
  return views.flatMap(({ observation, current }, position) => {
    const region = observation.evidence[0]?.region_px;
    if (!region || region.type !== "bbox_xyxy") return [];
    const [x0, y0, x1, y1] = region.coordinates as number[];
    if (x0 === undefined || y0 === undefined || x1 === undefined || y1 === undefined) return [];
    return [
      {
        index: position + 1,
        x: x0,
        y: y0,
        width: x1 - x0,
        height: y1 - y0,
        imageWidth: region.image_width,
        imageHeight: region.image_height,
        label: categoryLabel(current.category),
      },
    ];
  });
}

/** A colour for a class name, the same everywhere it is shown (on the photo, in lists), so a class is recognised at a glance. */
export function classColour(label: string, lightness = 62): string {
  const { hue } = badgeFor(label.toLowerCase());
  return `hsl(${hue} 78% ${lightness}%)`;
}

export type DrawTool = "select" | "box" | "point" | "outline";

const TAG_MIN_PIXELS = 64; // a box narrower than this on screen shows no tag unless asked, so crowded photos stay readable
const MIN_PIXELS = 6; // a box smaller than this on screen is a stray click, not a box

/**
 * Model-predicted regions, and the person's own marks, drawn over a photograph. Positions in the image only: never a
 * geographic location. Sizes are given in screen pixels (through `scale`), so class tags stay readable at any zoom.
 */
export function DetectionOverlay({
  items,
  selected,
  onSelect,
  scale = 1,
  imageWidth,
  imageHeight,
  marks = [],
  selectedMark = null,
  onSelectMark,
  tool = "select",
  showAllLabels = false,
  onDraw,
}: {
  items: Box[];
  selected: number | null;
  onSelect: (index: number) => void;
  /** Screen pixels per image pixel. */
  scale?: number;
  imageWidth?: number;
  imageHeight?: number;
  marks?: Mark[];
  selectedMark?: string | null;
  onSelectMark?: (id: string) => void;
  tool?: DrawTool;
  /** Show every class tag. By default a small box shows its tag only when selected or when zoomed in enough to read it. */
  showAllLabels?: boolean;
  onDraw?: (shape: MarkShape) => void;
}) {
  const svg = useRef<SVGSVGElement>(null);
  const [start, setStart] = useState<[number, number] | null>(null);
  const [now, setNow] = useState<[number, number] | null>(null);
  const [corners, setCorners] = useState<[number, number][]>([]);
  const first = items[0];
  const width = first?.imageWidth ?? imageWidth;
  const height = first?.imageHeight ?? imageHeight;

  // Entering or leaving a drawing tool drops anything half drawn.
  useEffect(() => {
    setStart(null);
    setNow(null);
    setCorners([]);
  }, [tool]);

  const finishOutline = (points: [number, number][]) => {
    if (points.length >= 3) onDraw?.({ kind: "outline", points });
    setCorners([]);
    setNow(null);
  };

  // Enter finishes an outline, Escape abandons whatever is being drawn.
  useEffect(() => {
    if (tool === "select") return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setStart(null);
        setNow(null);
        setCorners([]);
      } else if (event.key === "Enter" && tool === "outline") {
        finishOutline(corners);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // finishOutline only reads `corners` and the draw callback
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tool, corners, onDraw]);

  if (!width || !height) return null;
  const u = 1 / Math.max(scale, 0.01); // one screen pixel, in image pixels
  const font = 12 * u;
  const tagHeight = 18 * u;

  const toImage = (event: { clientX: number; clientY: number }): [number, number] => {
    const rect = svg.current!.getBoundingClientRect();
    return [
      clamp(((event.clientX - rect.left) / rect.width) * width, 0, width),
      clamp(((event.clientY - rect.top) / rect.height) * height, 0, height),
    ];
  };

  const tagFor = (text: string, x: number, y: number, colour: string, dark: string, extra?: { dashed?: boolean }) => {
    const tagWidth = (text.length * 6.9 + 12) * u;
    const top = y - tagHeight < 0 ? y : y - tagHeight;
    return (
      <>
        <rect x={x} y={top} width={tagWidth} height={tagHeight} fill={colour} rx={3 * u} stroke={extra?.dashed ? dark : "none"} strokeWidth={u} />
        <text x={x + 6 * u} y={top + tagHeight * 0.72} fontSize={font} fill={dark} fontWeight={600} style={{ pointerEvents: "none" }}>
          {text}
        </text>
      </>
    );
  };

  const draft =
    tool === "box" && start && now
      ? { x: Math.min(start[0], now[0]), y: Math.min(start[1], now[1]), width: Math.abs(now[0] - start[0]), height: Math.abs(now[1] - start[1]) }
      : null;
  const drawing = tool !== "select";

  return (
    <svg
      ref={svg}
      className="absolute inset-0 h-full w-full"
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
      role="img"
      aria-label={`${items.length} model-predicted region(s) drawn on the image${marks.length > 0 ? `, and ${marks.length} mark(s) you drew` : ""}`}
    >
      {items.map((box) => {
        const active = selected === box.index;
        const colour = active ? "#e6b450" : classColour(box.label);
        return (
          <g key={box.index} onClick={() => !drawing && onSelect(box.index)} className={drawing ? undefined : "cursor-pointer"}>
            <rect
              data-testid="detection-box"
              x={box.x}
              y={box.y}
              width={box.width}
              height={box.height}
              fill={active ? "rgb(230 180 80 / 0.14)" : "transparent"}
              stroke={colour}
              strokeWidth={(active ? 3 : 2) * u}
              pointerEvents={drawing ? "none" : "all"}
            />
            {showAllLabels || active || box.width * scale >= TAG_MIN_PIXELS ? tagFor(`${box.index} ${box.label}`, box.x, box.y, colour, "#0b1220") : null}
          </g>
        );
      })}

      {marks.map((mark) => {
        const active = selectedMark === mark.id;
        const colour = classColour(mark.label, 70);
        const stroke = active ? "#ffffff" : colour;
        const shape = mark.shape;
        const [ax, ay] = anchorOf(shape);
        const common = {
          fill: active ? "rgb(255 255 255 / 0.12)" : "rgb(255 255 255 / 0.04)",
          stroke,
          strokeWidth: (active ? 3 : 2) * u,
          strokeDasharray: `${6 * u} ${4 * u}`,
          pointerEvents: (drawing ? "none" : "all") as "none" | "all",
        };
        return (
          <g key={mark.id} data-testid="user-mark" onClick={() => !drawing && onSelectMark?.(mark.id)} className={drawing ? undefined : "cursor-pointer"}>
            {shape.kind === "box" ? <rect x={shape.x} y={shape.y} width={shape.width} height={shape.height} {...common} /> : null}
            {shape.kind === "point" ? (
              <>
                <circle cx={shape.x} cy={shape.y} r={7 * u} {...common} fill={colour} />
                <circle cx={shape.x} cy={shape.y} r={12 * u} {...common} fill="transparent" />
              </>
            ) : null}
            {shape.kind === "outline" ? <polygon points={shape.points.map((p) => p.join(",")).join(" ")} {...common} /> : null}
            {tagFor(`✎ ${mark.label}`, ax, shape.kind === "point" ? ay - 14 * u : ay, colour, "#0b1220", { dashed: true })}
          </g>
        );
      })}

      {draft ? <rect {...draft} fill="rgb(255 255 255 / 0.1)" stroke="#ffffff" strokeWidth={2 * u} strokeDasharray={`${6 * u} ${4 * u}`} /> : null}
      {tool === "outline" && corners.length > 0 ? (
        <>
          <polyline
            points={[...corners, ...(now ? [now] : [])].map((p) => p.join(",")).join(" ")}
            fill="rgb(255 255 255 / 0.08)"
            stroke="#ffffff"
            strokeWidth={2 * u}
            strokeDasharray={`${6 * u} ${4 * u}`}
          />
          {corners.map((p, i) => (
            <circle key={i} cx={p[0]} cy={p[1]} r={4 * u} fill="#ffffff" />
          ))}
        </>
      ) : null}

      {drawing ? (
        <rect
          data-testid="draw-surface"
          x={0}
          y={0}
          width={width}
          height={height}
          fill="transparent"
          pointerEvents="all"
          style={{ cursor: "crosshair", touchAction: "none" }}
          onPointerDown={(event) => {
            if (event.button !== 0) return;
            const point = toImage(event);
            if (tool === "box") {
              (event.target as Element).setPointerCapture?.(event.pointerId);
              setStart(point);
              setNow(point);
            }
          }}
          onPointerMove={(event) => {
            if (tool === "box" && start) setNow(toImage(event));
            else if (tool === "outline" && corners.length > 0) setNow(toImage(event));
          }}
          onPointerUp={(event) => {
            if (tool !== "box" || !start) return;
            const shape = boxFromCorners(start, toImage(event), { width, height }, MIN_PIXELS * u);
            setStart(null);
            setNow(null);
            if (shape) onDraw?.(shape);
          }}
          onClick={(event) => {
            const point = toImage(event);
            if (tool === "point") onDraw?.({ kind: "point", x: point[0], y: point[1] });
            else if (tool === "outline") setCorners((current) => [...current, point]);
          }}
          onDoubleClick={() => {
            if (tool !== "outline") return;
            // the two clicks of the double-click each added a corner at the same place: keep one
            const points = corners.slice(0, -1);
            finishOutline(points.length >= 3 ? points : corners);
          }}
        />
      ) : null}
    </svg>
  );
}
