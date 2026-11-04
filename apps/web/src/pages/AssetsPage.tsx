import { useInfiniteQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";

import { type Asset, listAssets, type Modality } from "../api/client";
import { AssetThumb } from "../components/AssetPreview";
import { IconImage, IconSatellite, IconUpload, IconVideo } from "../components/icons";
import {
  Badge,
  Breadcrumbs,
  Button,
  EmptyState,
  ErrorNotice,
  inputClass,
  LinkButton,
  Loading,
  PageHeader,
  selectClass,
  TableFrame,
  tableClass,
  tdClass,
  thClass,
} from "../components/ui";
import { formatDateTime } from "../lib/format";
import { flagLabel, formatBytes, formatLabel, MODALITY_NAMES, STATUS_NAMES } from "../lib/ingestion";
import { IngestionNav } from "./ingestionNav";
import { projectCrumbs, useProject } from "./ProjectLayout";

const PAGE_SIZE = 50;

export const MODALITY_ICONS = { ground_image: IconImage, overhead_raster: IconSatellite, video: IconVideo };

/** Where each kind of file is analysed. */
export function analysisPath(asset: Pick<Asset, "id" | "modality" | "project_id">): string {
  const suffix = { ground_image: "ground", overhead_raster: "overhead", video: "video" }[asset.modality];
  return `/projects/${asset.project_id}/assets/${asset.id}/${suffix}`;
}

export function AssetsPage() {
  const project = useProject();
  const [modality, setModality] = useState<Modality | "">("");
  const [status, setStatus] = useState<"" | "validated" | "validated_with_warnings">("");
  const [search, setSearch] = useState("");

  const assets = useInfiniteQuery({
    queryKey: ["assets", project.id, modality],
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) => listAssets(project.id, { modality: modality || undefined, limit: PAGE_SIZE, cursor: pageParam }),
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    // Previews are made in the background: keep looking until none is pending, so thumbnails appear.
    refetchInterval: (query) =>
      query.state.data?.pages.some((page) => page.items.some((asset) => asset.preview.status === "pending")) ? 2000 : false,
  });
  const loaded = assets.data?.pages.flatMap((page) => page.items) ?? [];
  const needle = search.trim().toLowerCase();
  const items = loaded.filter(
    (asset) => (!status || asset.status === status) && (!needle || asset.original_filename.toLowerCase().includes(needle)),
  );

  return (
    <div className="mx-auto max-w-6xl">
      <Breadcrumbs items={projectCrumbs(project, { label: "Add Files", to: `/projects/${project.id}/assets/new` }, { label: "Assets" })} />
      <div className="mt-2">
        <PageHeader
          title="Add Files"
          description="Every file stored in this project, with its validation status and flags."
          actions={
            <LinkButton to={`/projects/${project.id}/assets/new`}>
              <IconUpload size={15} />
              Upload files
            </LinkButton>
          }
        />
      </div>
      <IngestionNav projectId={project.id} />

      <div className="mb-3 flex flex-wrap items-end gap-2">
        <div className="w-56">
          <label htmlFor="asset-search" className="mb-1 block text-xs text-fg-muted">
            File name contains
          </label>
          <input id="asset-search" className={inputClass} value={search} onChange={(e) => setSearch(e.target.value)} />
        </div>
        <div className="w-56">
          <label htmlFor="asset-modality" className="mb-1 block text-xs text-fg-muted">
            Kind of input
          </label>
          <select id="asset-modality" className={selectClass} value={modality} onChange={(e) => setModality(e.target.value as Modality | "")}>
            <option value="">All</option>
            {Object.entries(MODALITY_NAMES).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </div>
        <div className="w-56">
          <label htmlFor="asset-status" className="mb-1 block text-xs text-fg-muted">
            Validation
          </label>
          <select id="asset-status" className={selectClass} value={status} onChange={(e) => setStatus(e.target.value as typeof status)}>
            <option value="">Any</option>
            <option value="validated">Validated</option>
            <option value="validated_with_warnings">Validated with warnings</option>
          </select>
        </div>
      </div>

      {assets.isLoading ? <Loading label="Loading assets…" /> : null}
      {assets.isError ? <ErrorNotice error={assets.error} /> : null}
      {assets.isSuccess && loaded.length === 0 && !modality ? (
        <EmptyState
          title="No assets yet"
          action={
            <LinkButton to={`/projects/${project.id}/assets/new`} variant="secondary">
              Upload the first file
            </LinkButton>
          }
        >
          Photographs, rasters and video added to this project are listed here.
        </EmptyState>
      ) : null}
      {assets.isSuccess && items.length === 0 && (loaded.length > 0 || modality) ? (
        <EmptyState title={modality && loaded.length === 0 ? "No assets of this kind" : "No assets match these filters"} />
      ) : null}

      {items.length > 0 ? (
        <TableFrame>
          <table className={tableClass}>
            <caption className="sr-only">Assets in this project</caption>
            <thead>
              <tr>
                <th scope="col" className={thClass}>Preview</th>
                <th scope="col" className={thClass}>File</th>
                <th scope="col" className={thClass}>Kind</th>
                <th scope="col" className={thClass}>Format</th>
                <th scope="col" className={thClass}>Size</th>
                <th scope="col" className={thClass}>Validation</th>
                <th scope="col" className={thClass}>Flags</th>
                <th scope="col" className={thClass}>Added</th>
              </tr>
            </thead>
            <tbody>
              {items.map((asset) => {
                const Icon = MODALITY_ICONS[asset.modality];
                return (
                  <tr key={asset.id} className="hover:bg-raised/60">
                    <td className={tdClass}>
                      <AssetThumb asset={asset} />
                    </td>
                    <td className={`${tdClass} min-w-52`}>
                      <Link className="wrap-anywhere text-fg hover:text-accent" to={`/projects/${project.id}/assets/${asset.id}`}>
                        {asset.original_filename}
                      </Link>
                      {asset.duplicate_of_asset_id ? (
                        <span className="ml-2">
                          <Badge tone="warn">Duplicate</Badge>
                        </span>
                      ) : null}
                    </td>
                    <td className={`${tdClass} whitespace-nowrap text-fg-muted`}>
                      <span className="inline-flex items-center gap-1.5">
                        <Icon size={14} />
                        {MODALITY_NAMES[asset.modality]}
                      </span>
                    </td>
                    <td className={`${tdClass} text-fg-muted`}>{formatLabel(asset.detected_format)}</td>
                    <td className={`${tdClass} whitespace-nowrap text-fg-muted`}>{formatBytes(asset.size_bytes)}</td>
                    <td className={tdClass}>
                      <Badge tone={asset.status === "validated" ? "ok" : "warn"}>{STATUS_NAMES[asset.status]}</Badge>
                    </td>
                    <td className={`${tdClass} min-w-40 text-xs text-fg-muted`}>
                      {asset.quality_flags.map(flagLabel).join(", ") || "—"}
                    </td>
                    <td className={`${tdClass} whitespace-nowrap text-xs text-fg-subtle`}>{formatDateTime(asset.ingested_at)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </TableFrame>
      ) : null}

      {assets.hasNextPage ? (
        <div className="mt-3">
          <Button variant="secondary" size="sm" onClick={() => void assets.fetchNextPage()} disabled={assets.isFetchingNextPage}>
            Load more
          </Button>
        </div>
      ) : null}
    </div>
  );
}
