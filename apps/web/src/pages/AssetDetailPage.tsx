import { useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { Link, useParams } from "react-router-dom";

import { ApiError, type Asset, assetContentUrl, getAsset } from "../api/client";
import { ErrorNotice, Loading, Notice, PageTitle } from "../components/ui";
import { flagLabel, formatBytes, MODALITY_NAMES, STATUS_NAMES } from "../lib/ingestion";
import { useProject } from "./ProjectLayout";

function Rows({ rows }: { rows: [string, ReactNode][] }) {
  return (
    <dl className="grid grid-cols-[12rem_1fr] gap-y-1 text-sm">
      {rows.map(([label, value]) => (
        <div key={label} className="contents">
          <dt className="text-slate-600">{label}</dt>
          <dd className="break-words">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

const unknown = <span className="text-slate-500">unknown</span>;

function RasterSection({ asset }: { asset: Asset }) {
  const raster = asset.raster!;
  return (
    <section className="space-y-2">
      <h2 className="font-semibold">Raster and georeferencing</h2>
      {raster.georeferenced ? (
        <Notice title="Georeferenced">
          The file declares a valid CRS and a usable geotransform, and its coordinates fit that CRS.
        </Notice>
      ) : (
        <Notice tone="warning" title="Not georeferenced — no coordinates are assigned">
          This raster can be inspected as an image, but it cannot be placed on a map or measured in ground units.
          Nothing has been assumed about its CRS or position.
        </Notice>
      )}
      <Rows
        rows={[
          ["Size", `${raster.width} × ${raster.height} pixels, ${raster.band_count} band(s)`],
          ["Data types", raster.dtypes.join(", ")],
          ["CRS status", raster.crs_status],
          ["CRS", raster.crs_identifier ?? raster.crs_name ?? unknown],
          ["CRS name", raster.crs_name ?? unknown],
          [
            "Pixel size",
            raster.pixel_size_x == null
              ? unknown
              : `${raster.pixel_size_x} × ${raster.pixel_size_y} ${raster.pixel_units ?? "(units unknown)"}`,
          ],
          ["Geotransform (as stored in the file)", raster.geotransform ? raster.geotransform.join(", ") : unknown],
          [
            "Bounds in the file's CRS",
            raster.bounds_native ? JSON.stringify(raster.bounds_native) : <span className="text-slate-500">not available</span>,
          ],
          [
            "Footprint (EPSG:4326)",
            raster.footprint_4326 ? "available" : <span className="text-slate-500">not available</span>,
          ],
        ]}
      />
    </section>
  );
}

function VideoSection({ asset }: { asset: Asset }) {
  const video = asset.video!;
  return (
    <section className="space-y-2">
      <h2 className="font-semibold">Video</h2>
      <Rows
        rows={[
          ["Frame size", `${video.width} × ${video.height} pixels`],
          ["Container", video.container],
          ["Codec", video.video_codec ?? unknown],
          ["Duration", video.duration_ms == null ? unknown : `${(video.duration_ms / 1000).toFixed(2)} s`],
          ["Frame rate", video.frame_rate == null ? unknown : `${video.frame_rate.toFixed(3)} frames/s (${video.avg_frame_rate})`],
          ["Frame count", video.frame_count ?? unknown],
          ["Time base", video.time_base ?? unknown],
          ["Audio track", video.has_audio ? "yes" : "no"],
          ["Container creation time", video.creation_time ? new Date(video.creation_time).toISOString() : unknown],
        ]}
      />
    </section>
  );
}

function ImageSection({ asset }: { asset: Asset }) {
  const meta = asset.metadata as {
    width?: number;
    height?: number;
    mode?: string;
    quality_indicators?: { sharpness?: { value?: number | null; method?: string; note?: string } };
  };
  const sharpness = meta.quality_indicators?.sharpness;
  return (
    <section className="space-y-2">
      <h2 className="font-semibold">Image</h2>
      <Rows
        rows={[
          ["Size", `${meta.width} × ${meta.height} pixels`],
          ["Colour mode", meta.mode ?? unknown],
          [
            "Sharpness indicator",
            sharpness?.value == null ? unknown : `${sharpness.value.toFixed(1)} (raw value; ${sharpness.note ?? ""})`,
          ],
        ]}
      />
    </section>
  );
}

export function AssetDetailPage() {
  const project = useProject();
  const { assetId = "" } = useParams();
  const query = useQuery({ queryKey: ["asset", assetId], queryFn: () => getAsset(assetId) });

  if (query.isLoading) return <Loading label="Loading asset…" />;
  if (query.isError) {
    const missing = query.error instanceof ApiError && [404, 422].includes(query.error.status);
    return missing ? <Notice tone="error" title="This asset does not exist" /> : <ErrorNotice error={query.error} />;
  }
  const asset = query.data!;
  if (asset.project_id !== project.id) return <Notice tone="error" title="This asset belongs to a different project" />;
  const unknowns = Object.entries(asset.unknowns);

  return (
    <div className="max-w-3xl space-y-6">
      <PageTitle
        title={asset.original_filename}
        actions={
          <a className="text-sm text-blue-700 underline" href={assetContentUrl(asset.id)} download={asset.original_filename}>
            Download original
          </a>
        }
      />

      {asset.duplicate_of_asset_id ? (
        <Notice tone="warning" title="Duplicate content">
          This file is byte-for-byte identical to{" "}
          <Link className="underline" to={`/projects/${project.id}/assets/${asset.duplicate_of_asset_id}`}>
            an earlier upload in this project
          </Link>
          . The two are one source, not independent evidence.
        </Notice>
      ) : null}

      <section className="space-y-2">
        <h2 className="font-semibold">File</h2>
        <Rows
          rows={[
            ["Kind of input", MODALITY_NAMES[asset.modality]],
            ["Detected format", `${asset.detected_format} (${asset.media_type})`],
            ["Size", `${formatBytes(asset.size_bytes)} (${asset.size_bytes.toLocaleString()} bytes)`],
            ["SHA-256", <span className="font-mono text-xs">{asset.sha256}</span>],
            ["Validation", STATUS_NAMES[asset.status]],
            ["Flags", asset.quality_flags.map(flagLabel).join(", ") || "none"],
            ["Capture time", asset.acquisition_time ? new Date(asset.acquisition_time).toISOString() : unknown],
            ["Added", `${new Date(asset.ingested_at).toLocaleString()} by ${asset.uploaded_by.display_name}`],
            ["Asset ID", <span className="font-mono text-xs">{asset.id}</span>],
          ]}
        />
      </section>

      {asset.raster ? <RasterSection asset={asset} /> : null}
      {asset.video ? <VideoSection asset={asset} /> : null}
      {asset.modality === "ground_image" ? <ImageSection asset={asset} /> : null}

      <section className="space-y-2">
        <h2 className="font-semibold">What is not known</h2>
        {unknowns.length === 0 ? (
          <p className="text-sm text-slate-600">Nothing is recorded as unknown for this file.</p>
        ) : (
          <ul className="list-disc space-y-1 pl-5 text-sm" aria-label="Unknown values">
            {unknowns.map(([field, reason]) => (
              <li key={field}>
                <span className="font-medium">{field.replaceAll("_", " ")}:</span> {String(reason)}
              </li>
            ))}
          </ul>
        )}
        <p className="text-xs text-slate-600">Unknown values are recorded as unknown. They are never estimated or filled in.</p>
      </section>

      <Notice>Analysis of this file is not part of this phase. No observations exist for it.</Notice>
    </div>
  );
}
