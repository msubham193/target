import type { ReactNode } from "react";

import { type CameraDistance, type ComparedPhoto, type Comparison, formatInterval, type TimeInterval } from "../lib/compare";
import { formatDateTime } from "../lib/format";
import { formatDecimalDegrees, formatLength } from "../lib/geo";
import { REVIEW_STATUS_LABELS } from "../lib/jobs";
import { AssetThumb } from "./AssetPreview";
import { Badge, Notice, tableClass, tdClass, thClass } from "./ui";

const SOURCE_LABELS: Record<string, string> = {
  exif: "GPS position from the file, confirmed by a person",
  manual_entry: "coordinates typed in by a person",
  map_selection: "point chosen on the map by a person",
};

function Unavailable({ children }: { children: ReactNode }) {
  return <span className="italic text-fg-subtle">{children}</span>;
}

function Time({ photo }: { photo: ComparedPhoto }) {
  if (photo.time.state === "known") return <>{formatDateTime(photo.time.iso)}</>;
  return <Unavailable>Unknown. {photo.time.reason}</Unavailable>;
}

function Position({ photo }: { photo: ComparedPhoto }) {
  const p = photo.position;
  if (p.state === "confirmed") {
    return (
      <div className="space-y-0.5">
        <Badge tone="ok">Camera position confirmed</Badge>
        <p className="font-mono text-xs">
          {formatDecimalDegrees(p.lat, "lat")}, {formatDecimalDegrees(p.lon, "lon")}
        </p>
        <p className="text-xs text-fg-subtle">
          {SOURCE_LABELS[p.source ?? ""] ?? "recorded by a person"}
          {p.confirmedBy ? ` · ${p.confirmedBy}` : ""}
        </p>
      </div>
    );
  }
  if (p.state === "candidate") {
    return (
      <div className="space-y-0.5">
        <Badge tone="warn">GPS not confirmed</Badge>
        <p className="text-xs text-fg-subtle">A position in the file is waiting for a person to confirm it. It is not used as a location.</p>
      </div>
    );
  }
  return <Unavailable>No location recorded</Unavailable>;
}

function Findings({ photo }: { photo: ComparedPhoto }) {
  const f = photo.findings;
  if (f === undefined) return <Unavailable>Loading findings…</Unavailable>;
  if (f === null) return <Unavailable>Not analysed. Nothing is stored.</Unavailable>;
  return (
    <div className="space-y-1">
      <p>
        {f.total} finding(s) from the analysis of {formatDateTime(f.runAt)}
        {f.earlierRuns > 0 ? ` (${f.earlierRuns} earlier analysis run(s) are not counted here)` : ""}
      </p>
      <p className="text-xs text-fg-muted">
        {Object.entries(f.byStatus)
          .map(([status, n]) => `${REVIEW_STATUS_LABELS[status] ?? status} ${n}`)
          .join(", ")}
      </p>
      <ul className="text-xs text-fg-muted">
        {f.categories.map((c) => (
          <li key={c.category}>
            {c.category} × {c.count}
          </li>
        ))}
      </ul>
    </div>
  );
}

function nameOf(photos: ComparedPhoto[], id: string): string {
  return photos.find((p) => p.assetId === id)?.filename ?? id;
}

/**
 * Side-by-side view of the selected photographs. It shows each photograph's own stored facts and what is missing. It
 * draws no link between findings of different photographs.
 */
export function PhotoCompare({ comparison }: { comparison: Comparison }) {
  const { photos, intervals, distances, timeline } = comparison;
  const untimed = photos.filter((p) => p.time.state === "unknown");
  return (
    <div className="space-y-4" aria-label="Photo comparison">
      <div className="overflow-x-auto rounded-lg border border-line">
        <table className={tableClass}>
          <caption className="sr-only">Selected photographs side by side</caption>
          <thead>
            <tr>
              <th className={thClass} scope="col">
                &nbsp;
              </th>
              {photos.map((p) => (
                <th key={p.assetId} className={`${thClass} min-w-48 whitespace-normal`} scope="col">
                  <span className="wrap-anywhere text-fg">{p.filename}</span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            <tr>
              <th scope="row" className={`${tdClass} text-xs font-medium text-fg-muted`}>
                Preview
              </th>
              {photos.map((p) => (
                <td key={p.assetId} className={tdClass}>
                  <AssetThumb asset={p.asset} className="h-24 w-36" />
                </td>
              ))}
            </tr>
            <tr>
              <th scope="row" className={`${tdClass} text-xs font-medium text-fg-muted`}>
                Capture time
              </th>
              {photos.map((p) => (
                <td key={p.assetId} className={tdClass} data-testid={`time-${p.assetId}`}>
                  <Time photo={p} />
                </td>
              ))}
            </tr>
            <tr>
              <th scope="row" className={`${tdClass} text-xs font-medium text-fg-muted`}>
                Camera position
              </th>
              {photos.map((p) => (
                <td key={p.assetId} className={tdClass} data-testid={`position-${p.assetId}`}>
                  <Position photo={p} />
                </td>
              ))}
            </tr>
            <tr>
              <th scope="row" className={`${tdClass} text-xs font-medium text-fg-muted`}>
                Findings and review
              </th>
              {photos.map((p) => (
                <td key={p.assetId} className={tdClass} data-testid={`findings-${p.assetId}`}>
                  <Findings photo={p} />
                </td>
              ))}
            </tr>
            <tr>
              <th scope="row" className={`${tdClass} text-xs font-medium text-fg-muted`}>
                Missing or unresolved
              </th>
              {photos.map((p) => (
                <td key={p.assetId} className={tdClass} data-testid={`gaps-${p.assetId}`}>
                  {p.gaps.length === 0 ? (
                    <span className="text-fg-subtle">Nothing missing</span>
                  ) : (
                    <ul className="list-disc space-y-0.5 pl-4 text-xs">
                      {p.gaps.map((gap) => (
                        <li key={gap}>{gap}</li>
                      ))}
                    </ul>
                  )}
                </td>
              ))}
            </tr>
          </tbody>
        </table>
      </div>

      <section aria-label="Between the selected photographs" className="space-y-2 rounded-lg border border-line p-3 text-[13px]">
        <h3 className="text-sm font-semibold text-fg">Between the photographs</h3>
        <div>
          <h4 className="text-xs font-medium text-fg-muted">Time</h4>
          {intervals.length === 0 ? (
            <p className="text-xs text-fg-subtle" data-testid="no-intervals">
              {timeline.length < 2
                ? "Fewer than two of the selected photographs have a stored capture time, so no time between them can be stated."
                : ""}
            </p>
          ) : (
            <ul className="text-xs" data-testid="intervals">
              {intervals.map((i: TimeInterval) => (
                <li key={`${i.fromId}-${i.toId}`}>
                  {nameOf(photos, i.fromId)} → {nameOf(photos, i.toId)}: {formatInterval(i.seconds)}
                </li>
              ))}
            </ul>
          )}
          {untimed.length > 0 && timeline.length >= 2 ? (
            <p className="text-xs text-fg-subtle">Not placed in time (no stored capture time): {untimed.map((p) => p.filename).join(", ")}.</p>
          ) : null}
        </div>
        <div>
          <h4 className="text-xs font-medium text-fg-muted">Recorded camera positions</h4>
          {distances.length === 0 ? (
            <p className="text-xs text-fg-subtle" data-testid="no-distances">
              Fewer than two of the selected photographs have a confirmed position, so no distance between them can be stated.
            </p>
          ) : (
            <ul className="text-xs" data-testid="distances">
              {distances.map((d: CameraDistance) => (
                <li key={`${d.aId}-${d.bId}`}>
                  {nameOf(photos, d.aId)} ↔ {nameOf(photos, d.bId)}: about {formatLength(d.metres)} between the recorded camera positions
                </li>
              ))}
            </ul>
          )}
        </div>
      </section>

      <Notice tone="warning" title="What this comparison does not say">
        A camera position is where a photograph was recorded as taken or placed. It is not the position of anything in the picture. A
        similar label or a nearby camera position in two photographs is not evidence that they show the same object, and no such
        relationship is stored or inferred here.
      </Notice>
    </div>
  );
}
