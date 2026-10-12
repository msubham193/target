import { useInfiniteQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";

import { listAssets, type Modality } from "../api/client";
import { Button, ErrorNotice, inputClass, Loading, Notice, PageTitle } from "../components/ui";
import { flagLabel, formatBytes, MODALITY_NAMES, STATUS_NAMES } from "../lib/ingestion";
import { useProject } from "./ProjectLayout";

const PAGE_SIZE = 25;

export function AssetsPage() {
  const project = useProject();
  const [modality, setModality] = useState<Modality | "">("");

  const assets = useInfiniteQuery({
    queryKey: ["assets", project.id, modality],
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) =>
      listAssets(project.id, { modality: modality || undefined, limit: PAGE_SIZE, cursor: pageParam }),
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });
  const items = assets.data?.pages.flatMap((page) => page.items) ?? [];

  return (
    <div className="space-y-4">
      <PageTitle
        title="Assets"
        actions={
          <Link
            to={`/projects/${project.id}/assets/new`}
            className="rounded bg-slate-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-slate-700"
          >
            Upload files
          </Link>
        }
      />

      <div className="max-w-xs">
        <label htmlFor="asset-modality" className="block text-sm font-medium">
          Kind of input
        </label>
        <select
          id="asset-modality"
          className={inputClass}
          value={modality}
          onChange={(event) => setModality(event.target.value as Modality | "")}
        >
          <option value="">All</option>
          {Object.entries(MODALITY_NAMES).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
      </div>

      {assets.isLoading ? <Loading label="Loading assets…" /> : null}
      {assets.isError ? <ErrorNotice error={assets.error} /> : null}
      {assets.isSuccess && items.length === 0 ? (
        <Notice title={modality ? "No assets of this kind" : "No assets yet"}>
          {modality ? null : (
            <Link className="text-blue-700 underline" to={`/projects/${project.id}/assets/new`}>
              Upload the first file
            </Link>
          )}
        </Notice>
      ) : null}

      {items.length > 0 ? (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse bg-white text-left text-sm">
            <caption className="sr-only">Assets in this project</caption>
            <thead>
              <tr className="border-b border-slate-300 text-slate-600">
                <th scope="col" className="p-2 font-medium">File</th>
                <th scope="col" className="p-2 font-medium">Kind</th>
                <th scope="col" className="p-2 font-medium">Format</th>
                <th scope="col" className="p-2 font-medium">Size</th>
                <th scope="col" className="p-2 font-medium">Status</th>
                <th scope="col" className="p-2 font-medium">Flags</th>
                <th scope="col" className="p-2 font-medium">Added</th>
              </tr>
            </thead>
            <tbody>
              {items.map((asset) => (
                <tr key={asset.id} className="border-b border-slate-200 align-top">
                  <td className="p-2">
                    <Link className="break-all text-blue-800 underline" to={`/projects/${project.id}/assets/${asset.id}`}>
                      {asset.original_filename}
                    </Link>
                  </td>
                  <td className="p-2">{MODALITY_NAMES[asset.modality]}</td>
                  <td className="p-2">{asset.detected_format}</td>
                  <td className="p-2 whitespace-nowrap">{formatBytes(asset.size_bytes)}</td>
                  <td className="p-2">{STATUS_NAMES[asset.status]}</td>
                  <td className="p-2">{asset.quality_flags.map(flagLabel).join(", ") || "—"}</td>
                  <td className="p-2 whitespace-nowrap">{new Date(asset.ingested_at).toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {assets.hasNextPage ? (
        <Button variant="secondary" onClick={() => void assets.fetchNextPage()} disabled={assets.isFetchingNextPage}>
          Load more
        </Button>
      ) : null}
    </div>
  );
}
