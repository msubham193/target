import { useQueries, useQuery } from "@tanstack/react-query";
import { lazy, Suspense, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { type Asset, listObservations, listPhotoLocations, type ObservationView, type PhotoLocationItem } from "../api/client";
import { AssetPreview, AssetThumb } from "../components/AssetPreview";
import { boxes, DetectionOverlay } from "../components/DetectionOverlay";
import { IconImage, IconReport, IconUpload } from "../components/icons";
import type { PhotoMarker } from "../components/MapCanvas";
import { readMapboxToken } from "../lib/mapbox";
import { PhotoCompare } from "../components/PhotoCompare";
import { PhotoLocationPanel } from "../components/PhotoLocationPanel";
import { ReviewPanel } from "../components/ReviewPanel";
import {
  Badge,
  Breadcrumbs,
  Button,
  DescriptionList,
  EmptyState,
  ErrorNotice,
  LinkButton,
  Loading,
  Notice,
  PageHeader,
  Panel,
  Tabs,
} from "../components/ui";
import { captureTime, comparePhotos, latestRun } from "../lib/compare";
import { formatDateTime } from "../lib/format";
import { formatBytes } from "../lib/ingestion";
import { categoryLabel, REVIEW_STATUS_LABELS, reviewTone } from "../lib/jobs";
import { useAssets } from "../lib/queries";
import { projectCrumbs, useProject } from "./ProjectLayout";

// The map library is large and needs WebGL, so it is loaded only when the map tab is opened.
const MapCanvas = lazy(() => import("../components/MapCanvas").then((module) => ({ default: module.MapCanvas })));

type TabId = "inspect" | "location" | "findings" | "map" | "compare";
/** More columns than this cannot be read side by side. */
export const MAX_COMPARED = 6;

const LOCATION_BADGE = {
  confirmed: { tone: "ok", label: "Located" },
  candidate: { tone: "warn", label: "GPS not confirmed" },
  none: { tone: "neutral", label: "No location" },
} as const;

function observationsKey(projectId: string, assetId: string) {
  return ["observations", projectId, "asset", assetId] as const;
}

function useAssetObservations(projectId: string, assetId: string | null) {
  return useQuery({
    queryKey: observationsKey(projectId, assetId ?? ""),
    queryFn: () => listObservations(projectId, { asset_id: assetId! }),
    enabled: Boolean(assetId),
  });
}

// --- step 1: choose photographs ----------------------------------------------------------------------------------------

function PhotoPicker({
  projectId,
  photos,
  locations,
  focus,
  selected,
  onFocus,
  onToggle,
}: {
  projectId: string;
  photos: Asset[];
  locations: Map<string, PhotoLocationItem>;
  focus: string | null;
  selected: string[];
  onFocus: (id: string) => void;
  onToggle: (id: string) => void;
}) {
  return (
    <Panel
      title={`Photographs (${photos.length})`}
      description="Click a photograph to work on it. Tick two or more to compare them."
      actions={
        <LinkButton variant="secondary" size="sm" to={`/projects/${projectId}/assets/new`}>
          <IconUpload size={14} />
          Upload
        </LinkButton>
      }
      padded={false}
    >
      {photos.length === 0 ? (
        <div className="p-3">
          <EmptyState icon={<IconImage size={26} />} title="No photographs yet">
            Upload JPEG, PNG or WebP photographs to this project, then come back here.
          </EmptyState>
        </div>
      ) : (
        <ul className="max-h-[70vh] divide-y divide-line overflow-y-auto" aria-label="Project photographs">
          {photos.map((photo) => {
            const state = locations.get(photo.id)?.state ?? "none";
            const badge = LOCATION_BADGE[state];
            const isSelected = selected.includes(photo.id);
            const full = !isSelected && selected.length >= MAX_COMPARED;
            return (
              <li key={photo.id} className={`flex items-center gap-2 px-2 py-2 ${focus === photo.id ? "bg-accent-soft" : ""}`}>
                <input
                  type="checkbox"
                  className="size-4 shrink-0 accent-[var(--color-accent)]"
                  checked={isSelected}
                  disabled={full}
                  onChange={() => onToggle(photo.id)}
                  aria-label={`Compare ${photo.original_filename}`}
                  title={full ? `At most ${MAX_COMPARED} photographs can be compared` : undefined}
                />
                <button
                  type="button"
                  onClick={() => onFocus(photo.id)}
                  aria-pressed={focus === photo.id}
                  className="flex min-w-0 flex-1 items-center gap-2 rounded text-left hover:bg-overlay"
                >
                  <AssetThumb asset={photo} />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-[13px] text-fg">{photo.original_filename}</span>
                    <Badge tone={badge.tone}>{badge.label}</Badge>
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </Panel>
  );
}

// --- step 2: inspect ----------------------------------------------------------------------------------------------------

function exifText(asset: Asset, key: string): string | null {
  const exif = (asset.metadata as { exif?: Record<string, unknown> }).exif;
  const value = exif?.[key];
  return typeof value === "string" && value.trim() ? value : null;
}

function InspectTab({ asset, views }: { asset: Asset; views: ObservationView[] | undefined }) {
  const [selected, setSelected] = useState<number | null>(null);
  const run = views ? latestRun(views) : null;
  const time = captureTime(asset);
  const meta = asset.metadata as { width?: number; height?: number };
  const unknowns = Object.entries(asset.unknowns ?? {});
  return (
    <div className="space-y-4">
      <AssetPreview asset={asset} overlay={run ? <DetectionOverlay items={boxes(run.views)} selected={selected} onSelect={setSelected} /> : null} />
      {run ? (
        <p className="text-xs text-fg-subtle">
          Boxes are the model's findings from the analysis of {formatDateTime(run.at)}. They are positions in the picture only.
        </p>
      ) : null}
      <Panel title="File and metadata" description="As stored when the file was added. Missing values are shown as missing.">
        <DescriptionList
          labelWidth="md"
          rows={[
            ["File", asset.original_filename],
            ["Format", `${asset.media_type}, ${formatBytes(asset.size_bytes)}`],
            ["Dimensions", meta.width && meta.height ? `${meta.width} × ${meta.height} px` : "not stored"],
            ["SHA-256", <code key="h" className="font-mono text-[11px] break-all">{asset.sha256}</code>],
            [
              "Capture time",
              time.state === "known" ? formatDateTime(time.iso) : <span className="italic text-fg-subtle">Unknown. {time.reason}</span>,
            ],
            ["Camera", [exifText(asset, "make"), exifText(asset, "model")].filter(Boolean).join(" ") || "not stored"],
            ["Added", `${formatDateTime(asset.ingested_at)} by ${asset.uploaded_by.display_name}`],
            ["Quality flags", asset.quality_flags.join(", ") || "none"],
          ]}
        />
        {unknowns.length > 0 ? (
          <div className="mt-3 border-t border-line pt-3">
            <h4 className="mb-1 text-xs font-medium text-fg-muted">Recorded as unknown</h4>
            <ul className="list-disc space-y-0.5 pl-4 text-xs text-fg-muted">
              {unknowns.map(([field, reason]) => (
                <li key={field}>
                  <b>{field}</b>: {String(reason)}
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </Panel>
    </div>
  );
}

// --- step 4: findings -----------------------------------------------------------------------------------------------------

function FindingsTab({ projectId, asset, views }: { projectId: string; asset: Asset; views: ObservationView[] | undefined }) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  if (!views) return <Loading label="Loading findings…" />;
  const run = latestRun(views);
  if (!run) {
    return (
      <Notice tone="info" title="This photograph has not been analysed">
        Nothing is stored, and opening this screen does not start an analysis.{" "}
        <Link className="text-accent hover:underline" to={`/projects/${projectId}/assets/${asset.id}/ground`}>
          Run an analysis in Photo Analysis
        </Link>
        .
      </Notice>
    );
  }
  return (
    <div className="space-y-3">
      <p className="text-xs text-fg-subtle">
        {run.views.length} finding(s) from the analysis of {formatDateTime(run.at)}
        {run.runs > 1 ? `; ${run.runs - 1} earlier run(s) are in Findings` : ""}.
      </p>
      <ul className="space-y-1" aria-label="Findings in this photograph">
        {run.views.map((view) => {
          const id = view.observation.observation_id;
          const active = selectedId === id;
          return (
            <li key={id}>
              <button
                type="button"
                aria-pressed={active}
                onClick={() => setSelectedId(active ? null : id)}
                className={`flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-[13px] ${active ? "bg-warn-soft" : "hover:bg-overlay"}`}
              >
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
      {selectedId ? <ReviewPanel key={selectedId} observationId={selectedId} projectId={projectId} /> : null}
      <Notice tone="warning" title="Model predictions, not confirmed findings">
        Scores are the model's own values, not probabilities. A finding is a region of the picture; it has no geographic location.
      </Notice>
    </div>
  );
}

// --- step 5: map --------------------------------------------------------------------------------------------------------

function MapTab({
  projectId,
  locations,
  focus,
  photos,
  onFocus,
}: {
  projectId: string;
  locations: PhotoLocationItem[];
  focus: string | null;
  photos: Asset[];
  onFocus: (id: string) => void;
}) {
  const [choice, setChoice] = useState<string[] | null>(null);
  const markers: PhotoMarker[] = useMemo(
    () =>
      locations.flatMap((item) =>
        item.state === "confirmed" && item.latitude != null && item.longitude != null
          ? [{ id: item.asset_id, lon: item.longitude, lat: item.latitude, label: item.filename }]
          : [],
      ),
    [locations],
  );
  const fitTo = useMemo<[number, number, number, number] | null>(() => {
    if (markers.length === 0) return null;
    const lons = markers.map((m) => m.lon);
    const lats = markers.map((m) => m.lat);
    const pad = 0.002;
    return [Math.min(...lons) - pad, Math.min(...lats) - pad, Math.max(...lons) + pad, Math.max(...lats) + pad];
  }, [markers]);
  const here = locations.find((item) => item.asset_id === focus);
  const views = useAssetObservations(projectId, focus);
  const run = views.data ? latestRun(views.data) : null;
  const names = new Map(photos.map((p) => [p.id, p.original_filename]));

  return (
    <div className="space-y-3">
      {markers.length === 0 ? (
        <Notice tone="info" title="No photograph has a confirmed location yet">
          A GPS position read from a file is not drawn until a person confirms it in the Location step.
        </Notice>
      ) : (
        <Suspense fallback={<Loading label="Loading the map…" />}>
          <MapCanvas
            basemap={readMapboxToken().ok ? "mapbox" : "offline"}
            rasters={[]}
            aoi={null}
            showAoi={false}
            showFootprints={false}
            selectedRasterId={null}
            fitTo={fitTo}
            photos={markers}
            showPhotos
            selectedPhotoId={focus}
            onPhotoSelect={(id) => {
              setChoice(null);
              onFocus(id);
            }}
            onPhotoChoice={(ids) => setChoice(ids)}
            className="h-96 w-full"
          />
        </Suspense>
      )}
      {choice ? (
        <Notice tone="info" title="Several photographs are close together here. Which one?">
          <ul className="mt-1 flex flex-wrap gap-2">
            {choice.map((id) => (
              <li key={id}>
                <Button
                  size="sm"
                  variant="secondary"
                  onClick={() => {
                    setChoice(null);
                    onFocus(id);
                  }}
                >
                  {names.get(id) ?? id}
                </Button>
              </li>
            ))}
          </ul>
        </Notice>
      ) : null}
      <Panel title={here ? here.filename : "Selected photograph"} description="Marker and findings of the photograph chosen in the list or on the map">
        {!here ? (
          <p className="text-[13px] text-fg-muted">Choose a photograph.</p>
        ) : here.state === "confirmed" ? (
          <div className="space-y-1 text-[13px]" data-testid="marker-summary">
            <p>
              The marker is where this photograph is recorded as taken or placed
              {here.confirmed_by ? `, confirmed by ${here.confirmed_by}` : ""}. It is not the position of anything shown in it.
            </p>
            <p className="text-fg-muted">
              {run ? `${run.views.length} finding(s) in this photograph (image regions only).` : views.isLoading ? "Loading findings…" : "Not analysed: no findings stored."}
            </p>
          </div>
        ) : (
          <p className="text-[13px] text-fg-muted" data-testid="marker-summary">
            {here.state === "candidate"
              ? "This photograph has a GPS position in its file that nobody has confirmed, so it has no marker."
              : "This photograph has no recorded location, so it has no marker."}{" "}
            Use the Location step.
          </p>
        )}
      </Panel>
    </div>
  );
}

// --- comparison ---------------------------------------------------------------------------------------------------------

function CompareTab({ projectId, photos, locations, selected }: { projectId: string; photos: Asset[]; locations: Map<string, PhotoLocationItem>; selected: string[] }) {
  const chosen = selected.flatMap((id) => photos.find((p) => p.id === id) ?? []);
  const queries = useQueries({
    queries: chosen.map((photo) => ({
      queryKey: observationsKey(projectId, photo.id),
      queryFn: () => listObservations(projectId, { asset_id: photo.id }),
    })),
  });
  const failed = queries.find((q) => q.isError);
  const comparison = comparePhotos(
    chosen.map((asset, i) => ({ asset, location: locations.get(asset.id), observations: queries[i]?.data })),
  );
  if (chosen.length < 2) {
    return (
      <Notice tone="info" title="Select at least two photographs">
        Tick the boxes beside two or more photographs in the list to compare them here.
      </Notice>
    );
  }
  return (
    <div className="space-y-3">
      {failed ? <ErrorNotice error={failed.error} title="Some findings could not be loaded" /> : null}
      <PhotoCompare comparison={comparison} />
    </div>
  );
}

// --- the page -----------------------------------------------------------------------------------------------------------

export function PhotoWorkspacePage() {
  const project = useProject();
  const photos = useAssets(project.id, "ground_image");
  const locations = useQuery({ queryKey: ["photo-locations", project.id], queryFn: () => listPhotoLocations(project.id) });
  const [focus, setFocus] = useState<string | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [tab, setTab] = useState<TabId>("inspect");

  const items = photos.data?.items ?? [];
  const byAsset = useMemo(() => new Map((locations.data?.items ?? []).map((item) => [item.asset_id, item])), [locations.data]);
  const current = items.find((p) => p.id === focus) ?? null;
  const views = useAssetObservations(project.id, current?.id ?? null);

  const toggle = (id: string) =>
    setSelected((now) => (now.includes(id) ? now.filter((x) => x !== id) : now.length >= MAX_COMPARED ? now : [...now, id]));

  const tabs: { value: TabId; label: string }[] = [
    { value: "inspect", label: "1. Inspect" },
    { value: "location", label: "2. Location" },
    { value: "findings", label: "3. Findings" },
    { value: "map", label: "4. Map" },
    { value: "compare", label: `5. Compare (${selected.length})` },
  ];

  return (
    <div>
      <Breadcrumbs items={projectCrumbs(project, { label: "Photos" })} />
      <div className="mt-2">
        <PageHeader
          title="Photos"
          description="Choose photographs, inspect them, confirm where they were taken, review what the model found, see them on the map, and compare them."
          actions={
            <LinkButton variant="secondary" to={`/projects/${project.id}/reports/findings`}>
              <IconReport size={15} />
              Findings report
            </LinkButton>
          }
        />
      </div>

      {photos.isLoading ? <Loading label="Loading photographs…" /> : null}
      {photos.isError ? <ErrorNotice error={photos.error} /> : null}
      {locations.isError ? <ErrorNotice error={locations.error} title="Photograph locations could not be loaded" /> : null}

      {photos.data ? (
        <div className="grid grid-cols-1 items-start gap-4 lg:grid-cols-[18rem_minmax(0,1fr)]">
          <PhotoPicker
            projectId={project.id}
            photos={items}
            locations={byAsset}
            focus={focus}
            selected={selected}
            onFocus={setFocus}
            onToggle={toggle}
          />
          <div className="min-w-0 space-y-3">
            <Tabs tabs={tabs} value={tab} onChange={setTab} label="Workspace steps" />
            {tab === "compare" ? (
              <CompareTab projectId={project.id} photos={items} locations={byAsset} selected={selected} />
            ) : tab === "map" ? (
              <MapTab projectId={project.id} locations={locations.data?.items ?? []} focus={focus} photos={items} onFocus={setFocus} />
            ) : !current ? (
              <Notice tone="info" title="Choose a photograph">
                {items.length === 0 ? "This project has no photographs yet." : "Click a photograph in the list to start."}
              </Notice>
            ) : (
              <div key={current.id} className="space-y-3">
                <h2 className="text-sm font-semibold wrap-anywhere text-fg">{current.original_filename}</h2>
                {tab === "inspect" ? <InspectTab asset={current} views={views.data} /> : null}
                {tab === "location" ? <PhotoLocationPanel asset={current} /> : null}
                {tab === "findings" ? <FindingsTab projectId={project.id} asset={current} views={views.data} /> : null}
                {views.isError ? <ErrorNotice error={views.error} title="The findings could not be loaded" /> : null}
              </div>
            )}
          </div>
        </div>
      ) : null}
    </div>
  );
}
