import { useQuery } from "@tanstack/react-query";

import { getAsset, getHealth, getModelHealth, listAssets, type Modality } from "../api/client";

/**
 * The assets of a project (up to 200), refreshed while any preview is still being generated so that thumbnails
 * appear without a reload. Shares the ["assets", projectId] prefix that job completion invalidates.
 */
export function useAssets(projectId: string, modality?: Modality) {
  return useQuery({
    queryKey: ["assets", projectId, "workspace", modality ?? "all"],
    queryFn: () => listAssets(projectId, { modality, limit: 200 }),
    refetchInterval: (query) =>
      query.state.data?.items.some((asset) => asset.preview.status === "pending") ? 2000 : false,
  });
}

/** One asset, refreshed while its preview is pending. */
export function useAsset(assetId: string | undefined) {
  return useQuery({
    queryKey: ["asset", assetId],
    queryFn: () => getAsset(assetId!),
    enabled: Boolean(assetId),
    refetchInterval: (query) => (query.state.data?.preview.status === "pending" ? 1500 : false),
  });
}

/** Service health. Re-checked every few seconds while something is not OK (a worker can take a while to start). */
export function useHealth() {
  return useQuery({
    queryKey: ["health"],
    queryFn: getHealth,
    refetchInterval: (query) => {
      const data = query.state.data;
      return !data || data.status !== "ok" || !data.worker.running ? 5000 : 30_000;
    },
  });
}

/**
 * Installed models and the worker, re-checked while no worker is running or while a freshly started worker is still
 * finding out whether PyTorch is available (that can take a minute on a cold start).
 */
export function useModelHealth() {
  return useQuery({
    queryKey: ["health-models"],
    queryFn: getModelHealth,
    refetchInterval: (query) => {
      const data = query.state.data;
      if (!data) return false;
      const probing = (data.worker.capabilities as { probe?: string } | null)?.probe === "running";
      return !data.worker.running || probing ? 5000 : false;
    },
  });
}
