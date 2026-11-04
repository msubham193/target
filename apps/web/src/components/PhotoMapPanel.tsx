import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { listObservations, type PhotoLocationItem } from "../api/client";
import { formatDateTime } from "../lib/format";
import { latestRun } from "../lib/compare";
import { formatDecimalDegrees } from "../lib/geo";
import { categoryLabel, REVIEW_STATUS_LABELS, reviewTone } from "../lib/jobs";
import { useAsset } from "../lib/queries";
import { AssetPreview } from "./AssetPreview";
import { boxes, DetectionOverlay } from "./DetectionOverlay";
import { Badge, Button, ErrorNotice, Loading, Notice, Panel } from "./ui";

const SOURCE_LABELS: Record<string, string> = {
  exif: "GPS position from the photo file, confirmed by a person",
  manual_entry: "Coordinates typed in by a person",
  map_selection: "Point chosen on the map by a person",
};

/**
 * The photograph behind a map marker, with its latest analysis drawn over it. The marker is where a person recorded
 * the photograph as taken or placed; the boxes are positions in the picture and are not placed on the map.
 */
export function PhotoMapPanel({ projectId, item, onClose }: { projectId: string; item: PhotoLocationItem; onClose: () => void }) {
  const asset = useAsset(item.asset_id);
  const observations = useQuery({
    queryKey: ["observations", projectId, "asset", item.asset_id],
    queryFn: () => listObservations(projectId, { asset_id: item.asset_id }),
  });
  const [selected, setSelected] = useState<number | null>(null);

  // Several analyses can exist for one photograph; the most recent run is the one shown.
  const latest = useMemo(() => latestRun(observations.data ?? []), [observations.data]);

  const drawn = latest ? boxes(latest.views) : [];
  const hasPosition = item.latitude != null && item.longitude != null;

  return (
    <Panel
      title={item.filename}
      description="Photograph at the selected map marker"
      actions={
        <Button size="sm" variant="ghost" onClick={onClose}>
          Close
        </Button>
      }
    >
      <div className="space-y-3">
        {hasPosition ? (
          <div className="space-y-0.5 text-[13px]">
            <p className="font-mono text-xs text-fg">
              {formatDecimalDegrees(item.latitude!, "lat")}, {formatDecimalDegrees(item.longitude!, "lon")}
            </p>
            <p className="text-xs text-fg-muted">
              {SOURCE_LABELS[item.source ?? ""] ?? "Recorded by a person"}
              {item.confirmed_by ? ` · ${item.confirmed_by}` : ""}
              {item.confirmed_at ? ` · ${formatDateTime(item.confirmed_at)}` : ""}
            </p>
          </div>
        ) : null}

        {asset.isLoading ? <Loading label="Loading the photograph…" /> : null}
        {asset.isError ? <ErrorNotice error={asset.error} title="The photograph could not be loaded" /> : null}
        {asset.data ? (
          <AssetPreview asset={asset.data} overlay={<DetectionOverlay items={drawn} selected={selected} onSelect={setSelected} />} />
        ) : null}

        <div className="space-y-2" aria-label="Analysis of this photograph">
          <h3 className="text-[13px] font-medium text-fg">Analysis</h3>
          {observations.isLoading ? <Loading label="Loading findings…" /> : null}
          {observations.isError ? <ErrorNotice error={observations.error} title="The findings could not be loaded" /> : null}
          {observations.isSuccess && !latest ? (
            <p className="text-xs text-fg-subtle">This photograph has not been analysed yet. Run an analysis in Photo Analysis.</p>
          ) : null}
          {latest ? (
            <>
              <p className="text-xs text-fg-subtle">
                {latest.views.length} finding(s) from the analysis of {formatDateTime(latest.at)}. Select a row or a box.
              </p>
              <ul className="space-y-1" aria-label="Findings in this photograph">
                {latest.views.map((view, position) => {
                  const index = position + 1;
                  const active = selected === index;
                  return (
                    <li key={view.observation.observation_id}>
                      <button
                        type="button"
                        aria-pressed={active}
                        onClick={() => setSelected(active ? null : index)}
                        className={`flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-[13px] ${active ? "bg-warn-soft" : "hover:bg-overlay"}`}
                      >
                        <span className="w-4 shrink-0 text-fg-subtle">{index}</span>
                        <span className="min-w-0 flex-1 truncate text-fg">{categoryLabel(view.current.category)}</span>
                        <span className="font-mono text-[11px] text-fg-muted">
                          {view.observation.score.raw_value == null ? "—" : view.observation.score.raw_value.toFixed(2)}
                        </span>
                        <Badge tone={reviewTone(view.current.status)}>{REVIEW_STATUS_LABELS[view.current.status] ?? view.current.status}</Badge>
                      </button>
                    </li>
                  );
                })}
              </ul>
              <Notice tone="warning" title="Model predictions, not confirmed findings">
                Scores are the model's own values, not probabilities. Boxes are positions in the picture only: the findings have no
                geographic location, only the photograph does.
              </Notice>
            </>
          ) : null}
        </div>

        <Link to={`/projects/${projectId}/assets/${item.asset_id}/ground`} className="inline-block text-xs text-accent hover:underline">
          Open in Photo Analysis (review findings, change the location)
        </Link>
      </div>
    </Panel>
  );
}
