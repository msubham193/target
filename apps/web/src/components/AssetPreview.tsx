import type { ReactNode } from "react";

import { type Asset, assetPreviewUrl } from "../api/client";
import { IconImage } from "./icons";
import { ImageViewer } from "./ImageViewer";
import { Loading, Notice } from "./ui";

function captionFor(asset: Asset): string {
  const kind =
    asset.modality === "video"
      ? "one frame of the video"
      : asset.modality === "overhead_raster"
        ? "the raster in image space, brightness adjusted for display"
        : "the photograph";
  return `Preview of ${kind}, reduced to at most 1280 px. For viewing only; the original is unchanged.`;
}

/** Why there is no picture to show, or null if there is one. */
export function PreviewProblem({ asset }: { asset: Asset }) {
  const { preview } = asset;
  if (preview.variants.includes("view")) return null;
  if (preview.status === "pending") {
    return (
      <Notice title="Preview is being generated">
        It is made in the background. If this does not change, check that the worker is running (Settings → Diagnostics).
      </Notice>
    );
  }
  if (preview.status === "failed") {
    return (
      <Notice tone="warning" title="No preview is available for this file">
        <p>{preview.error_message ?? "The preview could not be generated."}</p>
        {preview.error_code ? <p className="font-mono text-[11px] text-fg-subtle">Code: {preview.error_code}</p> : null}
        <p className="text-xs">The file itself is stored and valid.</p>
      </Notice>
    );
  }
  return <Notice title="No preview has been generated for this file" />;
}

/**
 * The preview picture of an asset in a zoomable viewer, or an honest statement of why there is none.
 * `overlay` is drawn in the picture's coordinate frame (for example detection boxes).
 */
export function AssetPreview({
  asset,
  overlay,
  toolbar,
  compact = false,
  large = false,
}: {
  asset: Asset;
  overlay?: ReactNode | ((scale: number) => ReactNode);
  toolbar?: ReactNode;
  compact?: boolean;
  large?: boolean;
}) {
  const { preview } = asset;
  if (!preview.variants.includes("view")) return <PreviewProblem asset={asset} />;
  const meta = asset.metadata as { width?: number; height?: number };
  const width = asset.raster?.width ?? asset.video?.width ?? meta.width ?? 1280;
  const height = asset.raster?.height ?? asset.video?.height ?? meta.height ?? 960;
  if (compact) {
    return (
      <figure className="space-y-1.5">
        <img
          src={assetPreviewUrl(asset.id, "view", preview.job_id)}
          alt={`Preview of ${asset.original_filename}`}
          className="block max-h-80 w-auto max-w-full rounded-md border border-line"
        />
        <figcaption className="text-xs text-fg-subtle">{captionFor(asset)}</figcaption>
      </figure>
    );
  }
  return (
    <ImageViewer
      src={assetPreviewUrl(asset.id, "view", preview.job_id)}
      alt={`Preview of ${asset.original_filename}`}
      width={width}
      height={height}
      overlay={overlay}
      toolbar={toolbar}
      large={large}
      caption={
        <>
          {captionFor(asset)}
          {preview.status === "pending" ? " A newer preview is being generated." : ""}
        </>
      }
    />
  );
}

/** A small thumbnail for lists, or a quiet placeholder saying why there is none. */
export function AssetThumb({ asset, className = "h-10 w-14" }: { asset: Asset; className?: string }) {
  if (asset.preview.variants.includes("thumb")) {
    return (
      <img
        src={assetPreviewUrl(asset.id, "thumb", asset.preview.job_id)}
        alt=""
        className={`${className} shrink-0 rounded-sm border border-line bg-canvas object-cover`}
      />
    );
  }
  return (
    <span
      className={`${className} flex shrink-0 items-center justify-center rounded-sm border border-dashed border-line-strong text-[10px] text-fg-subtle`}
      title={asset.preview.status === "pending" ? "Preview being generated" : "No preview"}
    >
      {asset.preview.status === "pending" ? <Loading label="" className="[&>span]:size-3" /> : <IconImage size={14} />}
    </span>
  );
}
