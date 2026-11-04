import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { fetchTerrainGrid, getTerrain, listTerrain, type TerrainMeta, type TerrainRaster, type TerrainRequest, transformPoint } from "../api/client";
import { IconCube, IconUpload } from "../components/icons";
import { type ColourMode, TerrainViewer } from "../components/TerrainViewer";
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
  selectClass,
} from "../components/ui";
import { Workspace } from "../components/Workspace";
import { formatElevation, formatMetres, type Hit, nativePosition } from "../lib/terrain";
import { projectCrumbs, useProject } from "./ProjectLayout";

type Status = TerrainRaster["status"];

const STATUS_LABEL: Record<Status, string> = {
  usable: "Elevation",
  needs_confirmation: "Needs confirmation",
  not_elevation: "Not elevation",
  unusable: "Cannot be drawn",
};
const STATUS_TONE: Record<Status, "ok" | "warn" | "neutral" | "danger"> = {
  usable: "ok",
  needs_confirmation: "warn",
  not_elevation: "neutral",
  unusable: "danger",
};
const UNIT_OPTIONS = [
  ["metre", "Metres"],
  ["foot", "Feet"],
  ["us_survey_foot", "US survey feet"],
] as const;
const SIDES = [128, 256, 512];

/**
 * Key for a line of server text in a list that is never reordered. The text alone is not unique: a multi-band raster
 * repeats a band's reason once per band ("An 8-bit band holds picture brightness, not elevation." three times), so the
 * position is part of the key.
 */
const textKey = (index: number, text: string) => `${index}:${text}`;

function RasterList({ items, selectedId, onSelect }: { items: TerrainRaster[]; selectedId: string | null; onSelect: (id: string) => void }) {
  return (
    <div className="rounded-lg border border-line bg-surface">
      <p className="border-b border-line px-3 py-2 text-xs font-medium text-fg-muted">
        Rasters <span className="text-fg-subtle">({items.length})</span>
      </p>
      <ul aria-label="Rasters and whether they are elevation" className="max-h-[60vh] overflow-y-auto p-1">
        {items.map((item) => {
          const selected = item.asset_id === selectedId;
          return (
            <li key={item.asset_id}>
              <button
                type="button"
                onClick={() => onSelect(item.asset_id)}
                aria-current={selected ? "true" : undefined}
                className={`block w-full rounded-md px-2 py-1.5 text-left text-[13px] ${selected ? "bg-accent-soft text-fg" : "text-fg-muted hover:bg-overlay hover:text-fg"}`}
              >
                <span className="block truncate">{item.filename}</span>
                <span className="mt-0.5 flex items-center gap-1.5">
                  <Badge tone={STATUS_TONE[item.status]}>{STATUS_LABEL[item.status]}</Badge>
                  <span className="text-[11px] text-fg-subtle">
                    {item.width} × {item.height}
                  </span>
                </span>
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function WhyNot({ raster }: { raster: TerrainRaster }) {
  return (
    <Notice tone={raster.status === "unusable" ? "warning" : "info"} title={`${raster.filename} cannot be shown in 3D: ${STATUS_LABEL[raster.status].toLowerCase()}`}>
      <ul className="list-disc space-y-0.5 pl-4 text-[13px]">
        {raster.reasons.map((reason, index) => (
          <li key={textKey(index, reason)}>{reason}</li>
        ))}
      </ul>
      {raster.bands.length > 0 ? (
        <ul className="mt-1 space-y-0.5 text-xs text-fg-subtle" aria-label="Bands">
          {raster.bands.map((band) => (
            <li key={band.band}>
              Band {band.band} ({band.dtype}
              {band.colour_interpretation ? `, ${band.colour_interpretation.toLowerCase()}` : ""}): {band.reasons[0]}
            </li>
          ))}
        </ul>
      ) : null}
      <p className="mt-1 text-xs">No terrain is generated and no elevation is guessed for a raster that is not elevation data.</p>
    </Notice>
  );
}

/** A raster the file does not fully describe: the person says which band is elevation and in what unit, for this view only. */
function Confirm({ raster, onShow }: { raster: TerrainRaster; onShow: (request: TerrainRequest) => void }) {
  const candidates = raster.bands.filter((b) => b.role !== "not_elevation");
  const [band, setBand] = useState(candidates[0]?.band ?? 1);
  const chosen = candidates.find((b) => b.band === band) ?? candidates[0]!;
  const needsConfirm = chosen.role === "possible";
  const needsUnit = chosen.declared_unit === null;
  const [confirmed, setConfirmed] = useState(false);
  const [unit, setUnit] = useState("");
  const ready = (!needsConfirm || confirmed) && (!needsUnit || unit !== "");
  return (
    <form
      aria-label="State what this raster holds"
      className="space-y-3 rounded-lg border border-line bg-surface p-4"
      onSubmit={(event) => {
        event.preventDefault();
        if (!ready) return;
        onShow({
          band: chosen.band,
          ...(needsConfirm ? { confirm_elevation: true } : {}),
          ...(needsUnit ? { unit: unit as TerrainRequest["unit"] } : {}),
        });
      }}
    >
      <p className="text-sm font-medium text-fg">The file does not say everything the 3D view needs</p>
      <ul className="list-disc space-y-0.5 pl-4 text-[13px] text-fg-muted">
        {raster.reasons.map((reason, index) => (
          <li key={textKey(index, reason)}>{reason}</li>
        ))}
      </ul>
      {candidates.length > 1 ? (
        <div>
          <label htmlFor="terrain-band" className="mb-1 block text-[13px] font-medium">
            Band
          </label>
          <select id="terrain-band" className={selectClass} value={band} onChange={(event) => setBand(Number(event.target.value))}>
            {candidates.map((b) => (
              <option key={b.band} value={b.band}>
                Band {b.band} ({b.dtype})
              </option>
            ))}
          </select>
        </div>
      ) : null}
      {needsConfirm ? (
        <label className="flex items-start gap-2 text-[13px]">
          <input type="checkbox" className="mt-0.5" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} />
          <span>I confirm that band {chosen.band} holds elevation values (not image brightness or any other measurement).</span>
        </label>
      ) : null}
      {needsUnit ? (
        <div>
          <label htmlFor="terrain-unit" className="mb-1 block text-[13px] font-medium">
            Elevation unit
          </label>
          <select id="terrain-unit" className={selectClass} value={unit} onChange={(event) => setUnit(event.target.value)}>
            <option value="">Choose the unit…</option>
            {UNIT_OPTIONS.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </div>
      ) : null}
      <p className="text-xs text-fg-subtle">
        This statement is used for this view only. It is not saved, the file is not changed, and the software cannot check it. The view says
        it came from you.
      </p>
      <Button type="submit" disabled={!ready}>
        Show in 3D
      </Button>
    </form>
  );
}

function Readout({ meta, hover, pinned }: { meta: TerrainMeta; hover: Hit | null; pinned: Hit | null }) {
  const e = meta.elevation;
  const lonLat = useQuery({
    queryKey: ["terrain-lonlat", meta.asset_id, pinned?.col, pinned?.row],
    queryFn: () => {
      const at = nativePosition(meta, pinned!.col, pinned!.row);
      return transformPoint({ source: meta.crs_id!, target: "EPSG:4326", x: at.x, y: at.y });
    },
    enabled: pinned !== null && meta.crs_id !== null,
    retry: false,
  });
  const describe = (hit: Hit) => {
    const at = nativePosition(meta, hit.col, hit.row);
    const unit = meta.horizontal_unit === "degree" ? "°" : " (CRS units)";
    return (
      <>
        <p className="font-mono text-xs">
          cell {hit.col}, {hit.row} · {meta.crs_id ?? "CRS"} x {at.x.toFixed(meta.horizontal_unit === "degree" ? 6 : 1)}, y {at.y.toFixed(meta.horizontal_unit === "degree" ? 6 : 1)}
          {unit === "°" ? "°" : ""}
        </p>
        <p className="text-[13px] text-fg">
          Elevation {formatElevation(hit.value, e.unit_label)}
          {e.unit_source === "stated_for_this_view" ? " (unit stated by you)" : ""}
        </p>
      </>
    );
  };
  return (
    <Panel title="Position and elevation" description="Values are the stored cell values, not interpolated.">
      <div className="space-y-3" aria-live="polite">
        <div data-testid="hover-readout">
          <p className="mb-1 text-xs font-medium text-fg-muted">Under the pointer</p>
          {hover ? describe(hover) : <p className="text-[13px] text-fg-subtle">Move the pointer over the surface. Holes (no data) have no elevation.</p>}
        </div>
        <div data-testid="pinned-readout" className="border-t border-line pt-3">
          <p className="mb-1 text-xs font-medium text-fg-muted">Selected (click the surface)</p>
          {pinned ? (
            <div className="space-y-1">
              {describe(pinned)}
              {lonLat.isSuccess ? (
                <p className="font-mono text-xs">
                  lat {lonLat.data.y.toFixed(6)}°, lon {lonLat.data.x.toFixed(6)}° (WGS 84)
                </p>
              ) : meta.crs_id === null ? (
                <p className="text-xs text-fg-subtle">Longitude and latitude are not shown: the CRS has no registry code.</p>
              ) : lonLat.isError ? (
                <p className="text-xs text-fg-subtle">Longitude and latitude could not be worked out for this cell.</p>
              ) : null}
            </div>
          ) : (
            <p className="text-[13px] text-fg-subtle">Nothing selected.</p>
          )}
        </div>
        <p className="text-xs text-fg-subtle">
          {meta.vertical_datum.state === "stated"
            ? `Heights are relative to ${meta.vertical_datum.name}.`
            : "The file does not say what zero height means (the vertical datum), so heights are not tied to sea level or any datum."}
        </p>
      </div>
    </Panel>
  );
}

function TerrainWorkspace({ raster, maxSide }: { raster: TerrainRaster; maxSide: number }) {
  const project = useProject();
  const [request, setRequest] = useState<TerrainRequest | null>(null);
  const [side, setSide] = useState(256);
  const [exaggeration, setExaggeration] = useState(1);
  const [colour, setColour] = useState<ColourMode>("elevation");
  const [hover, setHover] = useState<Hit | null>(null);
  const [pinned, setPinned] = useState<Hit | null>(null);

  // A raster the file fully describes is read at once (reading it creates nothing); one that needs a statement waits for it.
  const effective: TerrainRequest | null = useMemo(
    () => (raster.status === "usable" ? { ...(request ?? {}), max_side: side } : request ? { ...request, max_side: side } : null),
    [raster.status, request, side],
  );
  const meta = useQuery({
    queryKey: ["terrain", project.id, raster.asset_id, effective],
    queryFn: () => getTerrain(project.id, raster.asset_id, effective!),
    enabled: effective !== null && (raster.status === "usable" || raster.status === "needs_confirmation"),
    retry: false,
  });
  const cells = meta.data ? meta.data.grid.width * meta.data.grid.height : 0;
  const grid = useQuery({
    queryKey: ["terrain-grid", meta.data?.grid_url],
    queryFn: () => fetchTerrainGrid(meta.data!.grid_url, cells),
    enabled: meta.data !== undefined,
    retry: false,
    staleTime: Number.POSITIVE_INFINITY,
  });
  useEffect(() => {
    setHover(null);
    setPinned(null);
  }, [meta.data?.grid_url]);

  if (raster.status === "not_elevation" || raster.status === "unusable") return <WhyNot raster={raster} />;

  const data = meta.data;
  return (
    <div className="space-y-4">
      {raster.status === "needs_confirmation" && !request ? <Confirm raster={raster} onShow={setRequest} /> : null}
      {meta.isLoading ? <Loading label="Reading the height grid…" /> : null}
      {meta.isError ? <ErrorNotice error={meta.error} title="This raster cannot be shown in 3D" /> : null}
      {grid.isLoading && data ? <Loading label="Loading the surface…" /> : null}
      {grid.isError ? <ErrorNotice error={grid.error} title="The height grid could not be loaded" /> : null}
      {data && grid.data ? (
        <>
          <TerrainViewer values={grid.data} meta={data} exaggeration={exaggeration} colour={colour} onHover={setHover} onSelect={setPinned} />
          <Readout meta={data} hover={hover} pinned={pinned} />
        </>
      ) : null}
      {data ? (
        <div className="grid gap-4 lg:grid-cols-2">
          <Panel title="Display">
            <div className="space-y-3">
              <div>
                <label htmlFor="terrain-exaggeration" className="mb-1 block text-[13px] font-medium">
                  Vertical exaggeration: ×{exaggeration}
                </label>
                <input
                  id="terrain-exaggeration"
                  type="range"
                  min={1}
                  max={10}
                  step={0.5}
                  value={exaggeration}
                  onChange={(event) => setExaggeration(Number(event.target.value))}
                  className="w-full"
                />
                <p className={`text-xs ${exaggeration === 1 ? "text-fg-subtle" : "font-medium text-warn"}`}>
                  {exaggeration === 1
                    ? "×1 draws heights at true scale against distances."
                    : `Heights are drawn ${exaggeration} times taller than they are. Slopes look steeper than they are. This changes the picture only, not the data.`}
                </p>
              </div>
              <div>
                <label htmlFor="terrain-colour" className="mb-1 block text-[13px] font-medium">
                  Colour
                </label>
                <select id="terrain-colour" className={selectClass} value={colour} onChange={(event) => setColour(event.target.value as ColourMode)}>
                  <option value="elevation">By elevation</option>
                  <option value="grey">Grey (shading only)</option>
                </select>
              </div>
              <div>
                <label htmlFor="terrain-detail" className="mb-1 block text-[13px] font-medium">
                  Detail (cells per side, at most)
                </label>
                <select id="terrain-detail" className={selectClass} value={side} onChange={(event) => setSide(Number(event.target.value))}>
                  {SIDES.filter((s) => s <= maxSide).map((s) => (
                    <option key={s} value={s}>
                      {s}
                    </option>
                  ))}
                </select>
              </div>
            </div>
          </Panel>
          <Panel title="Elevation data">
            <DescriptionList
              rows={[
                ["Source", <span className="wrap-anywhere">{data.filename}</span>],
                ["Band", `${data.band}${data.elevation.role_source === "confirmed_for_this_view" ? " (confirmed by you for this view)" : " (declared by the file)"}`],
                ["CRS", `${data.crs_id ?? data.crs_name ?? "unknown"} · ${data.georeferencing_source === "recorded" ? "recorded by a person" : "from the file"}`],
                ["Source size", `${data.source_size[0]} × ${data.source_size[1]} pixels`],
                ["Grid", `${data.grid.width} × ${data.grid.height} cells (every ${data.grid.decimation === 1 ? "" : `${data.grid.decimation}th `}source pixel)`],
                ["One cell", `${formatMetres(data.cell_size_m[0]!)} × ${formatMetres(data.cell_size_m[1]!)}${data.horizontal.approximate ? " (approximate)" : ""}`],
                ["Elevation unit", `${data.elevation.unit_label} · ${data.elevation.unit_source === "declared" ? "declared by the file" : "stated by you"}`],
                ["Range", `${formatElevation(data.elevation.min, data.elevation.unit_label)} to ${formatElevation(data.elevation.max, data.elevation.unit_label)}`],
                ["Cells with data", `${data.elevation.valid_cells.toLocaleString("en")} (${data.elevation.nodata_cells.toLocaleString("en")} with no data)`],
                ["Vertical datum", data.vertical_datum.state === "stated" ? (data.vertical_datum.name ?? "stated") : "not stated by the file"],
              ]}
            />
          </Panel>
        </div>
      ) : null}
      {data ? (
        <Notice title="What you are looking at">
          <ul className="list-disc space-y-0.5 pl-4 text-[13px]" aria-label="Disclosures">
            {data.disclosures.map((line, index) => (
              <li key={textKey(index, line)}>{line}</li>
            ))}
            {exaggeration !== 1 ? <li>The view is drawn with the heights exaggerated ×{exaggeration}.</li> : null}
          </ul>
          {data.warnings.length > 0 ? (
            <ul className="mt-1.5 list-disc space-y-0.5 pl-4 text-[13px] text-warn" aria-label="Warnings">
              {data.warnings.map((line, index) => (
                <li key={textKey(index, line)}>{line}</li>
              ))}
            </ul>
          ) : null}
        </Notice>
      ) : null}
    </div>
  );
}

export function ThreeDPage() {
  const project = useProject();
  const [params, setParams] = useSearchParams();
  const list = useQuery({ queryKey: ["terrain-list", project.id], queryFn: () => listTerrain(project.id) });
  const items = list.data?.items ?? [];
  const requested = params.get("asset");
  // Open the first raster that can be drawn, when none is asked for.
  const selected =
    items.find((i) => i.asset_id === requested) ??
    items.find((i) => i.status === "usable") ??
    items.find((i) => i.status === "needs_confirmation") ??
    items[0] ??
    null;

  const header = (
    <>
      <Breadcrumbs items={projectCrumbs(project, { label: "3D Terrain" })} />
      <div className="mt-2">
        <PageHeader
          title="3D Terrain"
          description="Terrain from elevation rasters in this project, drawn on this computer."
          meta={list.data ? <Badge tone="neutral">{list.data.usable + list.data.needs_confirmation} of {items.length} usable</Badge> : undefined}
        />
      </div>
    </>
  );

  if (list.isLoading) return <div>{header}<Loading label="Checking this project's rasters…" /></div>;
  if (list.isError) return <div>{header}<ErrorNotice error={list.error} /></div>;

  const usable = (list.data?.usable ?? 0) + (list.data?.needs_confirmation ?? 0);
  const main =
    items.length === 0 ? (
      <EmptyState
        icon={<IconCube size={36} />}
        title="This project has no rasters"
        className="min-h-[24rem]"
        action={
          <LinkButton to={`/projects/${project.id}/assets/new`}>
            <IconUpload size={15} />
            Upload a raster
          </LinkButton>
        }
      >
        The 3D view draws elevation rasters (a GeoTIFF whose pixels are heights). Photographs, video and colour imagery are not elevation. Nothing is
        generated here.
      </EmptyState>
    ) : (
      <div className="space-y-4">
        {usable === 0 ? (
          <EmptyState icon={<IconCube size={36} />} title="No suitable elevation data in this project" className="py-6">
            None of this project's {items.length === 1 ? "raster is" : `${items.length} rasters are`} elevation data that can be drawn. Each one is listed below
            with the reason. Nothing is generated, and colour imagery is never treated as elevation.
          </EmptyState>
        ) : null}
        {selected ? <TerrainWorkspace key={selected.asset_id} raster={selected} maxSide={list.data!.max_grid_side} /> : null}
      </div>
    );

  return (
    <div>
      {header}
      <Workspace
        left={items.length > 0 ? <RasterList items={items} selectedId={selected?.asset_id ?? null} onSelect={(id) => setParams({ asset: id })} /> : undefined}
        main={main}
        right={
          <div className="space-y-4">
            <Notice title="Only elevation is drawn">
              {list.data?.note}
            </Notice>
            <Panel title="Photographs to 3D">
              <p className="text-[13px] text-fg-muted">
                Building a 3D model from overlapping photographs (photogrammetry) is not part of this view and is not available. What it would need is
                written down in the project documentation (docs/photogrammetry-feasibility.md). Nothing here is reconstructed from photographs.
              </p>
            </Panel>
            <p className="text-xs text-fg-subtle">
              Rendering is local. No online terrain, tiles or basemap is requested.{" "}
              <Link className="text-accent hover:underline" to={`/projects/${project.id}/assets`}>
                Files in this project
              </Link>
            </p>
          </div>
        }
        leftLabel="Rasters"
        rightLabel="About the 3D view"
      />
    </div>
  );
}
