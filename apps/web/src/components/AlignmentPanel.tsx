import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { getOverlayAlignment, type OverlayAlignment, type RasterLayer } from "../api/client";
import { accuracyText } from "../lib/transformation";
import { Badge, ErrorNotice, Loading, Panel, selectClass } from "./ui";

type Side = OverlayAlignment["first"];

function SideSummary({ title, side }: { title: string; side: Side }) {
  return (
    <div className="min-w-0 rounded-md border border-line bg-raised p-2.5 text-xs" aria-label={title}>
      <p className="truncate font-medium text-fg">{side.title}</p>
      <p className="mt-0.5 text-fg-muted">
        {side.kind === "map_sheet" ? `Map sheet${side.series ? ` · ${side.series.toUpperCase()}` : ""}` : "Raster"} · {side.crs_id ?? side.crs_name ?? "no CRS name"}
      </p>
      <p className="text-fg-muted">
        {side.placement === "recorded_by_person" ? "Placed from georeferencing a person recorded" : "Placed from the file's own metadata"}
      </p>
      <div className="mt-1 flex flex-wrap gap-1">
        {side.transformation_class ? (
          <Badge tone={side.transformation_class === "exact" ? "ok" : side.transformation_class === "approximate" ? "warn" : "danger"}>
            {side.needs_acknowledgement ? "Datum shift: needs acknowledgement" : side.selection_required ? "Datum shift: choice needed" : `Datum shift: ${accuracyText(side.transformation_class as never, side.datum_shift_accuracy_m)}`}
          </Badge>
        ) : null}
        {side.control_point_rms_m != null ? <Badge tone="accent">Control points: {side.control_point_rms_m.toFixed(1)} m RMS</Badge> : null}
        {side.ground_pixel_m != null ? <Badge>Pixel ≈ {side.ground_pixel_m.toFixed(2)} m</Badge> : null}
      </div>
    </div>
  );
}

/**
 * Compares two placed layers, for example a map sheet and a satellite image: how much they overlap, how each is positioned and
 * what each position is documented to be good to. It cannot measure alignment from the pixels, and says so.
 */
export function AlignmentPanel({ projectId, layers }: { projectId: string; layers: RasterLayer[] }) {
  const [first, setFirst] = useState("");
  const [second, setSecond] = useState("");
  const enabled = Boolean(first) && Boolean(second) && first !== second;
  const query = useQuery({
    queryKey: ["overlay-alignment", projectId, first, second],
    queryFn: () => getOverlayAlignment(projectId, first, second),
    enabled,
    retry: false,
  });
  if (layers.length < 2) return null;
  const options = (exclude: string) =>
    layers.map((layer) => (
      <option key={layer.asset_id} value={layer.asset_id} disabled={layer.asset_id === exclude}>
        {layer.title}
        {layer.map_sheet ? " (map sheet)" : ""}
      </option>
    ));
  return (
    <Panel title="Overlay alignment" description="How two placed layers relate. Footprints only: this does not measure whether features line up.">
      <div className="space-y-3">
        <div className="grid grid-cols-1 gap-2">
          <label className="text-xs text-fg-muted" htmlFor="alignment-first">
            First layer
          </label>
          <select id="alignment-first" className={selectClass} value={first} onChange={(event) => setFirst(event.target.value)}>
            <option value="">Choose…</option>
            {options(second)}
          </select>
          <label className="text-xs text-fg-muted" htmlFor="alignment-second">
            Second layer
          </label>
          <select id="alignment-second" className={selectClass} value={second} onChange={(event) => setSecond(event.target.value)}>
            <option value="">Choose…</option>
            {options(first)}
          </select>
        </div>
        {enabled && query.isLoading ? <Loading label="Comparing the layers…" /> : null}
        {query.isError ? <ErrorNotice error={query.error} title="These layers cannot be compared" /> : null}
        {query.data ? (
          <div className="space-y-2" data-testid="alignment-result">
            <div className="grid grid-cols-1 gap-2">
              <SideSummary title="First layer" side={query.data.first} />
              <SideSummary title="Second layer" side={query.data.second} />
            </div>
            <ul className="list-disc space-y-1 pl-4 text-xs text-fg-muted" aria-label="Findings">
              {query.data.findings.map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ul>
            <ul className="list-disc space-y-1 pl-4 text-xs text-warn" aria-label="Cautions">
              {query.data.cautions.map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ul>
          </div>
        ) : null}
      </div>
    </Panel>
  );
}
