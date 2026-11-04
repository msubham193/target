import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, Navigate, useParams } from "react-router-dom";

import { type Asset, getRasterTools, listTerrain, type PixelValues } from "../api/client";
import { AssetPreview, AssetThumb } from "../components/AssetPreview";
import { DisplayToolbar } from "../components/DisplayToolbar";
import { BandStatistics, DisplayControls, DisplayCopyPanel, useDisplayQuery } from "../components/RasterTools";
import { ComparePanel, LineagePanel, PixelPanel, RasterWorkbench } from "../components/RasterWorkbench";
import { IconAlert, IconCheck, IconCube, IconLayers, IconSatellite, IconUpload } from "../components/icons";
import {
  Badge,
  Breadcrumbs,
  DescriptionList,
  EmptyState,
  ErrorNotice,
  LinkButton,
  Loading,
  muted,
  notAvailable,
  Notice,
  PageHeader,
  Panel,
} from "../components/ui";
import { describeGeometry, formatDateTime } from "../lib/format";
import { useAsset, useAssets } from "../lib/queries";
import { adjustFilter, bandsDeclaredRgb, DEFAULT_ADJUST } from "../lib/rasterDisplay";
import { CRS_STATUS, UnknownList } from "./AssetDetailPage";
import { projectCrumbs, useProject } from "./ProjectLayout";

/**
 * Which of the project's rasters are elevation data (a height grid, not a picture). The 3D Terrain check decides this on
 * the server from what each file declares; this page only asks. If it cannot be asked, nothing is hidden or relabelled.
 */
function useElevationIds(projectId: string): { ids: Set<string>; known: boolean } {
  const terrain = useQuery({ queryKey: ["terrain", projectId], queryFn: () => listTerrain(projectId) });
  const ids = new Set(
    (terrain.data?.items ?? []).filter((item) => item.status === "usable" || item.status === "needs_confirmation").map((item) => item.asset_id),
  );
  return { ids, known: terrain.isSuccess };
}

/** The project's satellite and aerial images as a row above the viewer, so the image can use the full width. */
function RasterStrip({ projectId, selectedId }: { projectId: string; selectedId: string }) {
  const rasters = useAssets(projectId, "overhead_raster");
  const elevation = useElevationIds(projectId);
  if (rasters.isLoading || rasters.isError) return null;
  const all = rasters.data!.items as Asset[];
  const images = all.filter((asset) => !elevation.ids.has(asset.id));
  const heights = all.filter((asset) => elevation.ids.has(asset.id));
  if (images.length < 2 && heights.length === 0) return null;
  return (
    <nav aria-label="Satellite and aerial images" className="flex items-stretch gap-2 overflow-x-auto pb-1">
      {images.map((asset) => {
        const selected = asset.id === selectedId;
        const placed = asset.georeferencing?.placed;
        return (
          <Link
            key={asset.id}
            to={`/projects/${projectId}/assets/${asset.id}/overhead`}
            aria-current={selected ? "page" : undefined}
            title={asset.original_filename}
            className={`flex w-52 shrink-0 items-center gap-2 rounded-lg border px-2 py-1.5 text-xs transition-colors ${
              selected ? "border-accent bg-accent-soft text-fg" : "border-line bg-surface text-fg-muted hover:bg-overlay hover:text-fg"
            }`}
          >
            <AssetThumb asset={asset} className="h-9 w-12" />
            <span className="min-w-0">
              <span className="block truncate">{asset.original_filename}</span>
              <span className={`block text-[11px] ${placed ? "text-ok" : "text-warn"}`}>{placed ? "Georeferenced" : "Not georeferenced"}</span>
            </span>
          </Link>
        );
      })}
      {heights.length > 0 ? (
        <Link
          to={`/projects/${projectId}/3d`}
          title={heights.map((asset) => asset.original_filename).join("\n")}
          className="flex shrink-0 items-center gap-2 rounded-lg border border-dashed border-line px-3 text-xs text-fg-subtle hover:bg-overlay hover:text-fg"
        >
          <IconCube size={15} />
          {heights.length} elevation file{heights.length === 1 ? "" : "s"} · open in 3D Terrain
        </Link>
      ) : null}
    </nav>
  );
}

function RasterDetails({ asset }: { asset: Asset }) {
  const raster = asset.raster!;
  const bounds = raster.bounds_native as { left: number; bottom: number; right: number; top: number } | null;
  const footprint = raster.footprint_4326
    ? describeGeometry(raster.footprint_4326 as { type?: unknown; coordinates?: unknown }, { x: "longitude", y: "latitude" }, 6)
    : null;
  return (
    <div className="space-y-4">
      <Panel
        title="Georeferencing"
        actions={raster.georeferenced ? <Badge tone="ok">Georeferenced</Badge> : <Badge tone="warn">Not georeferenced</Badge>}
      >
        <DescriptionList
          rows={[
            ["CRS status", CRS_STATUS[raster.crs_status] ?? raster.crs_status],
            ["CRS", raster.crs_identifier ?? muted],
            ["CRS name", raster.crs_name ?? muted],
            [
              "Pixel size",
              raster.pixel_size_x == null
                ? muted
                : `${raster.pixel_size_x} × ${raster.pixel_size_y} ${raster.pixel_units ?? "(units unknown)"}`,
            ],
            ["Geotransform", raster.geotransform ? <span className="font-mono text-xs">{raster.geotransform.join(", ")}</span> : muted],
          ]}
        />
      </Panel>

      <Panel title="Spatial extent">
        <DescriptionList
          rows={[
            [
              "In the file's CRS",
              bounds ? `left ${bounds.left}, bottom ${bounds.bottom}, right ${bounds.right}, top ${bounds.top}` : notAvailable,
            ],
            ["In EPSG:4326", footprint ? `${footprint} (outline extent, 6 decimals)` : notAvailable],
          ]}
        />
        {!raster.georeferenced ? (
          <p className="mt-2 text-xs text-fg-subtle">No extent is given because the raster's position on the Earth is not established.</p>
        ) : null}
      </Panel>

      <Panel title="Bands">
        <DescriptionList
          rows={[
            ["Size", `${raster.width} × ${raster.height} pixels`],
            ["Bands", String(raster.band_count)],
            ["Data types", raster.dtypes.join(", ")],
            ["No-data", raster.nodata.map((value) => (value === null ? "none" : String(value))).join(", ")],
          ]}
        />
      </Panel>

      <Panel title="On the map" actions={asset.georeferencing?.placed ? <Badge tone="ok">Placed</Badge> : <Badge tone="warn">Not placed</Badge>}>
        {asset.georeferencing?.placed ? (
          <div className="space-y-2 text-[13px] text-fg-muted">
            <p>
              {asset.georeferencing.source === "recorded"
                ? `Drawn on the Map using georeferencing recorded by a person (version ${asset.georeferencing.version}), not the file's own metadata.`
                : "Drawn on the Map from its own CRS and geotransform."}
            </p>
            <Link to={`/projects/${asset.project_id}/map`} className="text-xs text-accent hover:underline">
              Open the map
            </Link>
          </div>
        ) : (
          <div className="space-y-2 text-[13px] text-fg-muted">
            <p>Without valid georeferencing this raster is not placed on the map. No CRS is assumed or suggested.</p>
            <Link to={`/projects/${asset.project_id}/assets/${asset.id}#georeferencing`} className="text-xs text-accent hover:underline">
              Record georeferencing from a documented source…
            </Link>
          </div>
        )}
      </Panel>

      <Panel title="What is not known">
        <UnknownList asset={asset} />
      </Panel>
    </div>
  );
}

function useRasterTools(assetId: string) {
  return useQuery({
    queryKey: ["raster-tools", assetId],
    queryFn: () => getRasterTools(assetId),
    refetchInterval: (query) => {
      const data = query.state.data;
      const active = (s?: string) => s === "queued" || s === "validating" || s === "processing";
      return data && (active(data.statistics_job?.status) || active(data.display_copy_job?.status)) ? 2000 : false;
    },
  });
}

const FORMAT_NAMES: Record<string, string> = { GTiff: "GeoTIFF" };

/** One line saying whether the image is placed on the Earth. A failure is stated, never hidden. */
function GeoreferenceLine({ asset }: { asset: Asset }) {
  const raster = asset.raster!;
  const recorded = asset.georeferencing?.source === "recorded" && asset.georeferencing.placed;
  if (recorded) {
    return (
      <div className="flex items-start gap-2 rounded-lg border border-line bg-surface px-3 py-2 text-[13px] text-fg-muted" role="status">
        <IconCheck size={15} className="mt-0.5 shrink-0 text-accent" />
        <p>
          <span className="font-medium text-fg">Placed from recorded georeferencing</span> — the file's own metadata is missing or invalid.
          A person recorded the CRS {asset.georeferencing?.crs_id ?? ""} and geotransform (version {asset.georeferencing?.version}); the
          software does not establish that they are correct.
        </p>
      </div>
    );
  }
  if (raster.georeferenced) {
    return (
      <div className="flex items-center gap-2 rounded-lg border border-ok/35 bg-surface px-3 py-2 text-[13px] text-fg-muted" role="status">
        <IconCheck size={15} className="shrink-0 text-ok" />
        <p>
          <span className="font-medium text-ok">Georeferenced</span> — valid CRS ({raster.crs_identifier ?? raster.crs_name}) and usable geotransform.
        </p>
      </div>
    );
  }
  const reason =
    raster.crs_status === "missing"
      ? "The file declares no coordinate reference system."
      : raster.crs_status === "invalid"
        ? "The file's CRS cannot be used."
        : "The file's CRS is valid, but its geotransform or coordinates are not usable.";
  return (
    <div className="rounded-lg border border-warn/40 bg-surface px-3 py-2 text-[13px] text-fg-muted" role="alert">
      <div className="flex items-start gap-2">
        <IconAlert size={15} className="mt-0.5 shrink-0 text-warn" />
        <p>
          <span className="font-medium text-warn">Not georeferenced</span> — no coordinates are assigned. {reason} It can be viewed as an
          image, but not placed on a map or measured in ground units. Nothing has been assumed about its position.
        </p>
      </div>
      <Link to={`/projects/${asset.project_id}/assets/${asset.id}#georeferencing`} className="mt-1 inline-block pl-6 text-xs text-accent hover:underline">
        Record georeferencing from a documented source…
      </Link>
    </div>
  );
}

/** The few facts worth knowing at a glance. The full technical detail is under Advanced inspection. */
function ImageInformation({ asset }: { asset: Asset }) {
  const raster = asset.raster!;
  const footprint = raster.footprint_4326
    ? describeGeometry(raster.footprint_4326 as { type?: unknown; coordinates?: unknown }, { x: "longitude", y: "latitude" }, 4)
    : null;
  return (
    <section aria-labelledby="image-info-heading" className="rounded-xl border border-line/80 bg-surface p-4">
      <h2 id="image-info-heading" className="mb-2 text-sm font-semibold text-fg">
        Image information
      </h2>
      <DescriptionList
        labelWidth="sm"
        rows={[
          ["File", <span className="wrap-anywhere">{asset.original_filename}</span>],
          ["Format", FORMAT_NAMES[raster.driver] ?? raster.driver],
          ["Acquired", asset.acquisition_time ? formatDateTime(asset.acquisition_time) : "Not in the file"],
          ["CRS", raster.crs_identifier ? `${raster.crs_identifier}${raster.crs_name ? ` · ${raster.crs_name}` : ""}` : muted],
          ["Extent", raster.georeferenced && footprint ? footprint : "Not established"],
          [
            "Resolution",
            raster.pixel_size_x == null ? "Not available" : `${raster.pixel_size_x.toPrecision(4)} × ${(raster.pixel_size_y ?? raster.pixel_size_x).toPrecision(4)} ${raster.pixel_units ?? "(units unknown)"} per pixel`,
          ],
          ["Bands", `${raster.band_count} · ${raster.width} × ${raster.height} px`],
          ["Georeferencing", raster.georeferenced ? "Valid" : (CRS_STATUS[raster.crs_status] ?? raster.crs_status)],
        ]}
      />
      <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs">
        {asset.georeferencing?.placed ? (
          <Link to={`/projects/${asset.project_id}/map`} className="text-accent hover:underline">
            Open on the Map (measure distance and area there)
          </Link>
        ) : (
          <span className="text-fg-subtle">Not placed on the Map</span>
        )}
      </div>
    </section>
  );
}

/**
 * The imagery viewer and everything around it. The image comes first, with a small display toolbar; the technical tools
 * (band choice, statistics, pixel values, comparison, full metadata) sit under Advanced inspection and are all still here.
 */
function ImageryWorkspace({ asset, projectId }: { asset: Asset; projectId: string }) {
  const tools = useRasterTools(asset.id);
  const [display, setDisplay] = useDisplayQuery(asset.id);
  const [adjust, setAdjust] = useState(DEFAULT_ADJUST);
  const [pixel, setPixel] = useState<PixelValues | null>(null);
  const [advanced, setAdvanced] = useState(false);
  const [resetKey, setResetKey] = useState(0);
  const raster = asset.raster!;
  const customBands = Boolean(display.bands || display.stretch);

  if (tools.isLoading) return <Loading label="Loading the image…" />;
  if (tools.isError) {
    return (
      <div className="space-y-3">
        <ErrorNotice error={tools.error} />
        <AssetPreview asset={asset} large />
      </div>
    );
  }
  const data = tools.data!;
  const multiband = raster.band_count >= 3;
  const caption = customBands
    ? "Showing the bands and contrast chosen under Advanced inspection."
    : multiband
      ? bandsDeclaredRgb(data.bands)
        ? "Natural colour: bands 1, 2 and 3 are declared red, green and blue by the file."
        : "Bands 1, 2 and 3 are shown as red, green and blue. The file does not say which bands are which."
      : "One band, shown in grey.";

  const technical = (
    <div className="space-y-4 pt-4">
      <div className="grid grid-cols-1 items-start gap-4 xl:grid-cols-2">
        <div className="min-w-0 space-y-4">
          <DisplayControls key={`${asset.id}-${resetKey}`} assetId={asset.id} tools={data} value={display} onChange={setDisplay} />
          <Panel title="Pixel values" description="Click the picture to read the stored values at that pixel.">
            {pixel ? <PixelPanel pixel={pixel} /> : <p className="text-xs text-fg-subtle">No pixel chosen.</p>}
          </Panel>
        </div>
        <div className="min-w-0 space-y-4">
          <BandStatistics assetId={asset.id} tools={data} />
          <DisplayCopyPanel assetId={asset.id} tools={data} />
        </div>
      </div>
      <LineagePanel tools={data} projectId={projectId} />
      <ComparePanel asset={asset} tools={data} projectId={projectId} />
      <RasterDetails asset={asset} />
      <p className="flex items-start gap-2 text-xs text-fg-subtle">
        <IconLayers size={14} className="mt-0.5 shrink-0" />
        AI raster analysis is planned. No raster model is approved or installed, so nothing is detected or classified here; the tools on this
        page show stored values and compare pixels.
      </p>
    </div>
  );

  return (
    <div className="space-y-3">
      <GeoreferenceLine asset={asset} />
      <div className="grid grid-cols-1 items-start gap-4 xl:grid-cols-[minmax(0,1fr)_21rem]">
        <div className="min-w-0 space-y-2">
          <RasterWorkbench
            key={`workbench-${asset.id}`}
            asset={asset}
            tools={data}
            display={display}
            measurements={false}
            showPixelPanel={false}
            onPixel={setPixel}
            imageFilter={adjustFilter({ ...adjust, mode: multiband ? adjust.mode : "colour" })}
            large
            allowFullscreen
            toolbar={
              <DisplayToolbar
                bandCount={raster.band_count}
                adjust={adjust}
                onAdjust={setAdjust}
                changed={customBands}
                onReset={() => {
                  setAdjust(DEFAULT_ADJUST);
                  setDisplay({});
                  setResetKey((n) => n + 1);
                }}
              />
            }
          />
          <p className="text-xs text-fg-subtle">
            {caption} Brightness, contrast and grey change only how the picture is drawn, never the stored values.
          </p>
          {pixel ? (
            <p className="rounded-lg border border-line bg-surface px-3 py-2 text-xs text-fg-muted" role="status" aria-label="Pixel reading">
              Pixel column {pixel.col}, row {pixel.row}:{" "}
              {pixel.values.map((v) => `band ${v.band} ${v.nodata ? "nodata" : String(v.raw)}`).join(" · ")}{" "}
              <button type="button" className="text-accent hover:underline" onClick={() => setAdvanced(true)}>
                Details in Advanced inspection
              </button>
            </p>
          ) : null}
        </div>
        <aside className="min-w-0">
          <ImageInformation asset={asset} />
        </aside>
      </div>

      <details
        className="rounded-xl border border-line/80 bg-surface"
        open={advanced}
        onToggle={(event) => setAdvanced((event.currentTarget as HTMLDetailsElement).open)}
      >
        <summary className="flex cursor-pointer items-center justify-between gap-2 px-4 py-3 text-[13px] font-medium text-fg">
          Advanced inspection
          <span className="text-xs font-normal text-fg-subtle">bands, statistics, pixel values, comparison, full details</span>
        </summary>
        <div className="border-t border-line/70 px-4 pb-4">{advanced ? technical : null}</div>
      </details>
    </div>
  );
}

export function SatellitePage() {
  const project = useProject();
  const { assetId } = useParams();
  const asset = useAsset(assetId);
  const rasters = useAssets(project.id, "overhead_raster");
  const elevation = useElevationIds(project.id);

  const header = (subtitle?: string, acquired?: string | null) => (
    <>
      <Breadcrumbs
        items={projectCrumbs(
          project,
          { label: "Satellite Images", to: `/projects/${project.id}/satellite` },
          ...(subtitle ? [{ label: subtitle }] : []),
        )}
      />
      <div className="mt-2">
        <PageHeader
          title="Satellite Images"
          description={subtitle ? undefined : "Satellite and aerial pictures: where they are, how far they reach, and their colour bands."}
          meta={
            subtitle ? (
              <>
                <span className="wrap-anywhere text-fg-muted">{subtitle}</span>
                {acquired ? <span>Acquired {formatDateTime(acquired)}</span> : null}
              </>
            ) : undefined
          }
          actions={
            assetId ? (
              <LinkButton variant="secondary" to={`/projects/${project.id}/assets/${assetId}`}>
                File details
              </LinkButton>
            ) : undefined
          }
        />
      </div>
    </>
  );

  if (!assetId) {
    if (rasters.isLoading) return <Loading label="Loading rasters…" />;
    if (rasters.isError) return <ErrorNotice error={rasters.error} />;
    // The first real picture, never an elevation grid: a height map opened here is confusing.
    const first = rasters.data!.items.find((item) => !elevation.ids.has(item.id));
    if (first) return <Navigate to={`/projects/${project.id}/assets/${first.id}/overhead`} replace />;
    const onlyHeights = rasters.data!.items.length > 0 && elevation.known;
    return (
      <div>
        {header()}
        <EmptyState
          icon={<IconSatellite size={28} />}
          title="No satellite or aerial images in this project"
          action={
            onlyHeights ? (
              <LinkButton to={`/projects/${project.id}/3d`}>
                <IconCube size={15} />
                Open 3D Terrain
              </LinkButton>
            ) : (
              <LinkButton to={`/projects/${project.id}/assets/new`}>
                <IconUpload size={15} />
                Upload a satellite image
              </LinkButton>
            )
          }
        >
          {onlyHeights
            ? "The rasters in this project are elevation files (height grids). They are viewed in 3D Terrain. Upload a satellite or aerial image (GeoTIFF) to inspect it here."
            : "Upload a satellite or aerial image (GeoTIFF) to inspect its location, extent and bands here."}
        </EmptyState>
      </div>
    );
  }

  if (asset.isLoading) return <Loading label="Loading raster…" />;
  if (asset.isError) return <ErrorNotice error={asset.error} />;
  const file = asset.data!;
  if (file.project_id !== project.id) return <Notice tone="error" title="This asset belongs to a different project" />;
  if (file.modality !== "overhead_raster" || !file.raster) {
    return (
      <div>
        {header(file.original_filename)}
        <Notice tone="error" title="This screen shows satellite and aerial rasters; this file is a different kind of input" />
      </div>
    );
  }

  const isElevation = elevation.ids.has(file.id);
  return (
    <div className="space-y-4">
      {header(file.original_filename, file.acquisition_time)}
      <RasterStrip projectId={project.id} selectedId={file.id} />
      {isElevation ? (
        <Notice tone="warning" title="This file is elevation data, not a satellite image">
          <p>
            It holds a grid of heights, not colours, so it looks like a grey relief map here. For a satellite picture, choose one from
            the row above. To see this file as terrain, open it in 3D Terrain.
          </p>
          <div className="mt-2 flex flex-wrap gap-2">
            <LinkButton size="sm" to={`/projects/${project.id}/3d`}>
              <IconCube size={14} />
              Open 3D Terrain
            </LinkButton>
            <LinkButton size="sm" variant="secondary" to={`/projects/${project.id}/map`}>
              Show on the map
            </LinkButton>
          </div>
        </Notice>
      ) : null}
      <ImageryWorkspace key={file.id} asset={file} projectId={project.id} />
    </div>
  );
}
