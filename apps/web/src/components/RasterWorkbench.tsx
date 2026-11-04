import { useMutation, useQuery } from "@tanstack/react-query";
import { type MouseEvent, type ReactNode, useState } from "react";

import {
  type Asset,
  type DisplayQuery,
  getAlignment,
  getPixel,
  listComparisons,
  measurePoints,
  type PixelValues,
  type RasterTools,
  renderUrl,
  requestComparison,
} from "../api/client";
import { formatDecimalDegrees } from "../lib/geo";
import { JOB_STATUS_LABELS, jobTone } from "../lib/jobs";
import { useAssets } from "../lib/queries";
import { ImageViewer } from "./ImageViewer";
import { MeasurementResult } from "./RasterTools";
import { Badge, Button, DescriptionList, ErrorNotice, inputClass, Notice, Panel, selectClass, TextLink } from "./ui";

type Tool = "inspect" | "distance" | "area";

function toRasterPixel(event: MouseEvent<SVGSVGElement>, width: number, height: number): [number, number] | null {
  const box = event.currentTarget.getBoundingClientRect();
  if (box.width === 0 || box.height === 0) return null;
  const col = ((event.clientX - box.left) / box.width) * width;
  const row = ((event.clientY - box.top) / box.height) * height;
  return [Math.max(0, Math.min(width, col)), Math.max(0, Math.min(height, row))];
}

export function PixelPanel({ pixel }: { pixel: PixelValues }) {
  return (
    <div className="space-y-2" role="status" aria-label="Pixel values">
      <p className="text-xs text-fg-muted">
        Pixel column {pixel.col}, row {pixel.row}
        {pixel.lon_lat
          ? ` · ${formatDecimalDegrees(pixel.lon_lat[1]!, "lat", 6)}, ${formatDecimalDegrees(pixel.lon_lat[0]!, "lon", 6)}`
          : pixel.lon_lat_note
            ? ` · no longitude/latitude: ${pixel.lon_lat_note}`
            : " · no map position (not georeferenced)"}
        {pixel.georeferencing_source === "recorded" ? " · from recorded georeferencing" : ""}
      </p>
      <table className="w-full text-left text-xs">
        <caption className="sr-only">Stored values at this pixel</caption>
        <thead>
          <tr className="text-fg-subtle">
            <th scope="col" className="py-1 pr-2 font-medium">Band</th>
            <th scope="col" className="py-1 pr-2 font-medium">Stored value</th>
            <th scope="col" className="py-1 font-medium">As declared by the file</th>
          </tr>
        </thead>
        <tbody>
          {pixel.values.map((v) => (
            <tr key={v.band} className="border-t border-line">
              <td className="py-1 pr-2">{v.band}</td>
              <td className="py-1 pr-2 font-mono">{v.nodata ? "nodata" : String(v.raw)}</td>
              <td className="py-1 text-fg-muted">
                {[v.description, v.scaled != null ? `scaled ${v.scaled}` : null, v.units ? `units: ${v.units}` : null]
                  .filter(Boolean)
                  .join(" · ") || "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="text-[11px] text-fg-subtle">{pixel.note}</p>
    </div>
  );
}

/**
 * The raster in image space with the chosen display settings. Clicking inspects a pixel's stored values, or adds
 * points to a distance or area measurement (ground units only when the raster's georeferencing is valid).
 */
export function RasterWorkbench({
  asset,
  tools,
  display,
  measurements = true,
  showPixelPanel = true,
  onPixel,
  imageFilter,
  large = false,
  allowFullscreen = false,
  toolbar,
}: {
  asset: Asset;
  tools: RasterTools;
  display: DisplayQuery;
  /** Offer the distance and area tools. Off, the picture only reads pixel values (measurements belong on the Map). */
  measurements?: boolean;
  /** Show the pixel-values panel here. Off, a screen shows the reading itself, from `onPixel`. */
  showPixelPanel?: boolean;
  onPixel?: (pixel: PixelValues) => void;
  imageFilter?: string;
  large?: boolean;
  allowFullscreen?: boolean;
  toolbar?: ReactNode;
}) {
  const [tool, setTool] = useState<Tool>("inspect");
  const [points, setPoints] = useState<[number, number][]>([]);
  const inspect = useMutation({
    mutationFn: ([col, row]: [number, number]) => getPixel(asset.id, { col, row }),
    onSuccess: (pixel) => onPixel?.(pixel),
  });
  const measure = useMutation({
    mutationFn: () =>
      measurePoints({ kind: tool === "area" ? "area" : "distance", space: "pixel", asset_id: asset.id, points }),
  });
  const width = tools.width;
  const height = tools.height;
  const stroke = Math.max(width, height) / 300;

  const click = (event: MouseEvent<SVGSVGElement>) => {
    const point = toRasterPixel(event, width, height);
    if (!point) return;
    if (tool === "inspect") inspect.mutate([Math.min(width - 1, Math.floor(point[0])), Math.min(height - 1, Math.floor(point[1]))]);
    else {
      measure.reset();
      setPoints((current) => [...current, point]);
    }
  };

  const chooseTool = (next: Tool) => {
    setTool(next);
    setPoints([]);
    measure.reset();
  };

  const overlay = (
    <svg
      className={`absolute inset-0 h-full w-full ${tool === "inspect" ? "cursor-crosshair" : "cursor-copy"}`}
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
      onClick={click}
      role="presentation"
      data-testid="raster-click-layer"
    >
      {points.length > 1 ? (
        tool === "area" ? (
          <polygon points={points.map((p) => p.join(",")).join(" ")} fill="rgb(91 155 255 / 0.15)" stroke="#5b9bff" strokeWidth={stroke} />
        ) : (
          <polyline points={points.map((p) => p.join(",")).join(" ")} fill="none" stroke="#5b9bff" strokeWidth={stroke} />
        )
      ) : null}
      {points.map(([x, y], i) => (
        <circle key={i} cx={x} cy={y} r={stroke * 2} fill="#e7eaf0" />
      ))}
      {tool === "inspect" && inspect.data ? (
        <rect x={inspect.data.col} y={inspect.data.row} width={1} height={1} fill="none" stroke="#e6b450" strokeWidth={stroke} />
      ) : null}
    </svg>
  );

  return (
    <div className="space-y-3">
      {measurements ? (
      <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Raster tools">
        {(
          [
            ["inspect", "Inspect pixel"],
            ["distance", "Measure distance"],
            ["area", "Measure area"],
          ] as const
        ).map(([name, label]) => (
          <Button key={name} size="sm" variant={tool === name ? "primary" : "secondary"} aria-pressed={tool === name} onClick={() => chooseTool(name)}>
            {label}
          </Button>
        ))}
        {tool !== "inspect" ? (
          <span className="text-xs text-fg-subtle">Click the image to add points ({points.length} so far).</span>
        ) : (
          <span className="text-xs text-fg-subtle">Click the image to read stored values.</span>
        )}
      </div>
      ) : null}
      <ImageViewer
        // The address changes when statistics or a display copy become available, so a picture that could not be
        // drawn before (for example a new 16-bit raster without statistics yet) is requested again.
        src={`${renderUrl(asset.id, display)}&ready=${tools.statistics ? "s" : "n"}-${tools.display_source}`}
        alt={`Rendering of ${asset.original_filename} with the chosen bands and contrast`}
        width={width}
        height={height}
        overlay={overlay}
        imageFilter={imageFilter}
        large={large}
        allowFullscreen={allowFullscreen}
        toolbar={toolbar}
        caption={<span className="text-xs text-fg-subtle">Image space: north is not necessarily up. Display only; values are not changed.</span>}
      />
      {!showPixelPanel ? null : tool === "inspect" ? (
        <Panel title="Pixel values">
          {inspect.isError ? <ErrorNotice error={inspect.error} /> : null}
          {inspect.data ? <PixelPanel pixel={inspect.data} /> : <p className="text-xs text-fg-subtle">No pixel chosen.</p>}
        </Panel>
      ) : (
        <Panel title={tool === "distance" ? "Distance" : "Area"}>
          <div className="space-y-2">
            <div className="flex gap-2">
              <Button size="sm" disabled={points.length < (tool === "area" ? 3 : 2) || measure.isPending} onClick={() => measure.mutate()}>
                Measure
              </Button>
              <Button size="sm" variant="secondary" disabled={points.length === 0} onClick={() => setPoints((p) => p.slice(0, -1))}>
                Remove last point
              </Button>
              <Button size="sm" variant="ghost" disabled={points.length === 0} onClick={() => chooseTool(tool)}>
                Clear
              </Button>
            </div>
            {measure.isError ? <ErrorNotice error={measure.error} /> : null}
            {measure.data ? <MeasurementResult result={measure.data} /> : null}
          </div>
        </Panel>
      )}
    </div>
  );
}

/** Pixel difference B − A of two aligned rasters, stored as a new derived raster. Not change interpretation. */
export function ComparePanel({ asset, tools, projectId }: { asset: Asset; tools: RasterTools; projectId: string }) {
  const rasters = useAssets(projectId, "overhead_raster");
  const others = (rasters.data?.items ?? []).filter((a) => a.id !== asset.id);
  const [otherId, setOtherId] = useState("");
  const [bandA, setBandA] = useState(1);
  const [bandB, setBandB] = useState(1);
  const [reason, setReason] = useState("");
  const other = others.find((a) => a.id === otherId);
  const alignment = useQuery({
    queryKey: ["alignment", otherId, asset.id],
    queryFn: () => getAlignment(otherId, asset.id),
    enabled: Boolean(otherId),
  });
  const comparisons = useQuery({
    queryKey: ["comparisons", projectId],
    queryFn: () => listComparisons(projectId),
    refetchInterval: (query) =>
      (query.state.data ?? []).some((c) => ["queued", "processing", "validating"].includes(c.job.status)) ? 2000 : false,
  });
  const start = useMutation({
    mutationFn: () => requestComparison(projectId, { asset_a: otherId, asset_b: asset.id, band_a: bandA, band_b: bandB, reason: reason || null }),
    onSuccess: () => void comparisons.refetch(),
  });
  const mine = (comparisons.data ?? []).filter((c) => c.asset_b === asset.id || c.asset_a === asset.id);
  const names = new Map((rasters.data?.items ?? []).map((a) => [a.id, a.original_filename]));

  return (
    <Panel title="Pixel comparison" description="B − A for two rasters on the same grid. A pixel difference, not an interpretation of change.">
      <div className="space-y-3 text-xs">
        <div>
          <label htmlFor="compare-other" className="mb-1 block text-fg-muted">
            Raster A (subtracted from this one, B)
          </label>
          <select id="compare-other" className={selectClass} value={otherId} onChange={(e) => setOtherId(e.target.value)}>
            <option value="">Choose a raster…</option>
            {others.map((a) => (
              <option key={a.id} value={a.id}>
                {a.original_filename}
              </option>
            ))}
          </select>
        </div>
        {otherId && alignment.data ? (
          alignment.data.aligned ? (
            <Notice tone="success" title="Same grid: same CRS, geotransform and size" />
          ) : (
            <Notice tone="warning" title="These rasters are not on the same grid">
              <ul className="list-disc pl-4">
                {alignment.data.reasons.map((r) => (
                  <li key={r}>{r}</li>
                ))}
              </ul>
              Nothing is resampled or shifted to make them fit.
            </Notice>
          )
        ) : null}
        {alignment.isError ? <ErrorNotice error={alignment.error} /> : null}
        {other && alignment.data?.aligned ? (
          <>
            <div className="grid grid-cols-2 gap-2">
              <div>
                <label htmlFor="compare-band-a" className="mb-1 block text-fg-muted">
                  Band of A
                </label>
                <select id="compare-band-a" className={selectClass} value={bandA} onChange={(e) => setBandA(Number(e.target.value))}>
                  {Array.from({ length: other.raster?.band_count ?? 1 }, (_, i) => (
                    <option key={i} value={i + 1}>
                      Band {i + 1}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label htmlFor="compare-band-b" className="mb-1 block text-fg-muted">
                  Band of B (this raster)
                </label>
                <select id="compare-band-b" className={selectClass} value={bandB} onChange={(e) => setBandB(Number(e.target.value))}>
                  {Array.from({ length: tools.band_count }, (_, i) => (
                    <option key={i} value={i + 1}>
                      Band {i + 1}
                    </option>
                  ))}
                </select>
              </div>
            </div>
            <div>
              <label htmlFor="compare-reason" className="mb-1 block text-fg-muted">
                Purpose (optional; kept with the result)
              </label>
              <input id="compare-reason" className={inputClass} value={reason} maxLength={1000} onChange={(e) => setReason(e.target.value)} />
            </div>
            <Button size="sm" disabled={start.isPending} onClick={() => start.mutate()}>
              Compute B − A
            </Button>
          </>
        ) : null}
        {start.isError ? <ErrorNotice error={start.error} /> : null}
        {mine.length > 0 ? (
          <ul className="space-y-1.5 border-t border-line pt-2" aria-label="Comparisons">
            {mine.map((c) => (
              <li key={c.job.id} className="flex flex-wrap items-center gap-2">
                <Badge tone={jobTone(c.job.status)}>{JOB_STATUS_LABELS[c.job.status] ?? c.job.status}</Badge>
                <span className="text-fg-muted">
                  {names.get(c.asset_b) ?? "B"} band {c.band_b} − {names.get(c.asset_a) ?? "A"} band {c.band_a}
                </span>
                {c.derived_asset_id ? (
                  <TextLink to={`/projects/${projectId}/assets/${c.derived_asset_id}/overhead`}>Open the result</TextLink>
                ) : null}
                {c.job.error_message ? <span className="text-danger">{c.job.error_message}</span> : null}
              </li>
            ))}
          </ul>
        ) : null}
      </div>
    </Panel>
  );
}

/** For a derived raster: what it was computed from. */
export function LineagePanel({ tools, projectId }: { tools: RasterTools; projectId: string }) {
  if (tools.derived_from.length === 0) return null;
  const a = tools.derived_from.find((row) => row.role === "a");
  const b = tools.derived_from.find((row) => row.role === "b");
  const parameters = (a?.parameters ?? {}) as { formula?: string; band_a?: number; band_b?: number; reason?: string | null };
  return (
    <Panel title="Derived raster" description="Computed by this application; it is not an original file.">
      <DescriptionList
        labelWidth="sm"
        rows={[
          ["Formula", `${parameters.formula ?? "B - A"} (pixel difference, not change interpretation)`],
          ["A", a ? <TextLink to={`/projects/${projectId}/assets/${String(a.asset_id)}`}>{`raster A, band ${parameters.band_a}`}</TextLink> : "—"],
          ["B", b ? <TextLink to={`/projects/${projectId}/assets/${String(b.asset_id)}`}>{`raster B, band ${parameters.band_b}`}</TextLink> : "—"],
          ["Tool", String(a?.tool ?? "")],
          ["Purpose", parameters.reason ?? "—"],
        ]}
      />
    </Panel>
  );
}
