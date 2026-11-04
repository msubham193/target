import { Link, useParams } from "react-router-dom";

import { ApiError, type Asset, assetContentUrl } from "../api/client";
import { AssetPreview } from "../components/AssetPreview";
import { ControlPointsPanel } from "../components/ControlPointsPanel";
import { CrsAssessmentPanel } from "../components/CrsAssessmentPanel";
import { GeoreferencingPanel } from "../components/GeoreferencingPanel";
import { MapSheetPanel } from "../components/MapSheetPanel";
import { SidecarPanel } from "../components/SidecarPanel";
import { IconDownload } from "../components/icons";
import { JobsPanel } from "../components/JobsPanel";
import {
  Badge,
  Breadcrumbs,
  buttonClass,
  DescriptionList,
  EmptyState,
  ErrorNotice,
  Loading,
  muted,
  notAvailable,
  Notice,
  PageHeader,
  Panel,
} from "../components/ui";
import { formatDateTime } from "../lib/format";
import { flagLabel, formatBytes, formatCount, formatLabel, MODALITY_NAMES, STATUS_NAMES } from "../lib/ingestion";
import { useAsset } from "../lib/queries";
import { analysisPath } from "./AssetsPage";
import { projectCrumbs, useProject } from "./ProjectLayout";

export const CRS_STATUS: Record<string, string> = {
  valid: "Valid",
  missing: "Missing — the file declares none",
  invalid: "Not usable",
};

const UNKNOWN_LABELS: Record<string, string> = {
  crs: "Coordinate reference system",
  crs_identifier: "CRS registry code",
  transformation: "Datum transformation",
  transformation_accuracy: "Datum transformation accuracy",
  geolocation: "Location",
  acquisition_time: "Capture time",
  exif: "Embedded photo metadata",
  pdf_rendering: "PDF rendering",
  pdf_geo_metadata: "Geospatial metadata in the PDF",
  pdf_encryption: "PDF encryption",
};

export function UnknownList({ asset }: { asset: Asset }) {
  const unknowns = Object.entries(asset.unknowns);
  if (unknowns.length === 0) return <p className="text-[13px] text-fg-muted">Nothing is recorded as unknown for this file.</p>;
  return (
    <div className="space-y-2">
      <ul className="space-y-1.5 text-[13px]" aria-label="Unknown values">
        {unknowns.map(([field, reason]) => (
          <li key={field}>
            <span className="font-medium text-fg">{UNKNOWN_LABELS[field] ?? flagLabel(field)}:</span>{" "}
            <span className="text-fg-muted">{String(reason)}</span>
          </li>
        ))}
      </ul>
      <p className="text-xs text-fg-subtle">Unknown values are recorded as unknown. They are never estimated or filled in.</p>
    </div>
  );
}

export function FileFacts({ asset }: { asset: Asset }) {
  return (
    <DescriptionList
      labelWidth="md"
      rows={[
        ["Kind of input", MODALITY_NAMES[asset.modality]],
        ["Detected format", `${formatLabel(asset.detected_format)} (${asset.media_type})`],
        ["Size", `${formatBytes(asset.size_bytes)} (${formatCount(asset.size_bytes)} bytes)`],
        ["SHA-256", <span className="font-mono text-xs">{asset.sha256}</span>],
        ["Validation", STATUS_NAMES[asset.status]],
        ["Flags", asset.quality_flags.map(flagLabel).join(", ") || "none"],
        ["Capture time", asset.acquisition_time ? formatDateTime(asset.acquisition_time) : muted],
        ["Added", `${formatDateTime(asset.ingested_at)} by ${asset.uploaded_by.display_name}`],
        ["Asset ID", <span className="font-mono text-xs">{asset.id}</span>],
      ]}
    />
  );
}

function KindFacts({ asset }: { asset: Asset }) {
  if (asset.raster) {
    const raster = asset.raster;
    return (
      <Panel
        title="Raster"
        actions={
          asset.georeferencing?.placed ? (
            <Badge tone={asset.georeferencing.source === "recorded" ? "accent" : "ok"}>
              {asset.georeferencing.source === "recorded" ? "Placed (recorded)" : "Georeferenced"}
            </Badge>
          ) : (
            <Badge tone="warn">Not georeferenced</Badge>
          )
        }
      >
        <DescriptionList
          labelWidth="md"
          rows={[
            ["Size", `${raster.width} × ${raster.height} pixels, ${raster.band_count} band(s)`],
            ["Data types", raster.dtypes.join(", ")],
            ["CRS status in the file", CRS_STATUS[raster.crs_status] ?? raster.crs_status],
            ["CRS in the file", raster.crs_identifier ?? raster.crs_name ?? muted],
            ...(asset.georeferencing?.source === "recorded"
              ? [["CRS in use", `${asset.georeferencing.crs_id ?? asset.georeferencing.crs_name ?? "none"} (recorded, version ${asset.georeferencing.version})`] as [string, string]]
              : []),
          ]}
        />
      </Panel>
    );
  }
  if (asset.video) {
    const video = asset.video;
    return (
      <Panel title="Video">
        <DescriptionList
          labelWidth="md"
          rows={[
            ["Frame size", `${video.width} × ${video.height} pixels`],
            ["Duration", video.duration_ms == null ? muted : `${(video.duration_ms / 1000).toFixed(2)} s`],
            ["Frame rate", video.frame_rate == null ? muted : `${video.frame_rate.toFixed(3)} frames/s`],
            ["Codec", video.video_codec ?? muted],
          ]}
        />
      </Panel>
    );
  }
  const meta = asset.metadata as { width?: number; height?: number; mode?: string; quality_indicators?: { sharpness?: { value?: number | null } } };
  const sharpness = meta.quality_indicators?.sharpness?.value;
  return (
    <Panel title="Image">
      <DescriptionList
        labelWidth="md"
        rows={[
          ["Size", meta.width ? `${meta.width} × ${meta.height} pixels` : notAvailable],
          ["Colour mode", meta.mode ?? muted],
          [
            "Sharpness indicator",
            sharpness == null
              ? muted
              : `${sharpness.toFixed(1)} — a raw measurement only. No validated threshold exists, so it makes no blur judgement.`,
          ],
        ]}
      />
    </Panel>
  );
}

const ANALYSIS_LABEL = {
  ground_image: "Open in Photo Analysis",
  overhead_raster: "Open in Satellite Images",
  video: "Open in Videos",
};

export function AssetDetailPage() {
  const project = useProject();
  const { assetId = "" } = useParams();
  const query = useAsset(assetId);

  if (query.isLoading) return <Loading label="Loading asset…" />;
  if (query.isError) {
    const missing = query.error instanceof ApiError && [404, 422].includes(query.error.status);
    return missing ? <EmptyState title="This asset does not exist" /> : <ErrorNotice error={query.error} />;
  }
  const asset = query.data!;
  if (asset.project_id !== project.id) return <Notice tone="error" title="This asset belongs to a different project" />;

  return (
    <div className="mx-auto max-w-6xl">
      <Breadcrumbs
        items={projectCrumbs(
          project,
          { label: "Add Files", to: `/projects/${project.id}/assets/new` },
          { label: "Assets", to: `/projects/${project.id}/assets` },
          { label: asset.original_filename },
        )}
      />
      <div className="mt-2">
        <PageHeader
          title={asset.original_filename}
          meta={
            <>
              <span>{MODALITY_NAMES[asset.modality]}</span>
              <Badge tone={asset.status === "validated" ? "ok" : "warn"}>{STATUS_NAMES[asset.status]}</Badge>
              {asset.duplicate_of_asset_id ? <Badge tone="warn">Duplicate content</Badge> : null}
            </>
          }
          actions={
            <>
              <Link to={analysisPath(asset)} className={buttonClass("primary")}>
                {ANALYSIS_LABEL[asset.modality]}
              </Link>
              <a className={buttonClass("secondary")} href={assetContentUrl(asset.id)} download={asset.original_filename}>
                <IconDownload size={15} />
                Download original
              </a>
            </>
          }
        />
      </div>

      {asset.duplicate_of_asset_id ? (
        <Notice tone="warning" title="Duplicate content" className="mb-4">
          This file is byte-for-byte identical to{" "}
          <Link className="text-accent hover:underline" to={`/projects/${project.id}/assets/${asset.duplicate_of_asset_id}`}>
            an earlier upload in this project
          </Link>
          . The two are one source, not independent evidence.
        </Notice>
      ) : null}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1fr)_24rem]">
        <div className="min-w-0 space-y-4">
          <AssetPreview asset={asset} />
          <JobsPanel asset={asset} />
        </div>
        <div className="min-w-0 space-y-4">
          <Panel title="File">
            <FileFacts asset={asset} />
          </Panel>
          <KindFacts asset={asset} />
          <Panel
            title="What is not known"
            description={
              asset.georeferencing?.source === "recorded"
                ? "As read from the file at upload. Georeferencing recorded later is under Spatial placement."
                : undefined
            }
          >
            <UnknownList asset={asset} />
          </Panel>
        </div>
      </div>
      {asset.map_sheet ? (
        <div className="mt-4 space-y-4">
          <MapSheetPanel assetId={asset.id} />
          {asset.raster ? <ControlPointsPanel asset={asset} projectId={project.id} /> : null}
        </div>
      ) : null}
      {asset.raster ? (
        <div className="mt-4 space-y-4">
          <CrsAssessmentPanel assetId={asset.id} />
          <GeoreferencingPanel assetId={asset.id} projectId={project.id} />
          <SidecarPanel assetId={asset.id} projectId={project.id} />
        </div>
      ) : null}
    </div>
  );
}
