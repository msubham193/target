import { useQuery, useQueryClient } from "@tanstack/react-query";
import { type ChangeEvent, type ReactNode, useRef, useState } from "react";
import { Link, Navigate, useNavigate, useParams } from "react-router-dom";

import {
  ApiError,
  type Asset,
  getIngestionRules,
  getRasterTools,
  listTerrain,
  renderUrl,
  uploadAsset,
} from "../api/client";
import { IconCube, IconSatellite, IconUpload } from "../components/icons";
import { PanZoomViewer } from "../components/PanZoomViewer";
import { Badge, Button, EmptyState, ErrorNotice, LinkButton, Loading, Notice } from "../components/ui";
import { checkFile } from "../lib/ingestion";
import { useAsset, useAssets } from "../lib/queries";
import { useProject } from "./ProjectLayout";

/** The longest side of the picture the server renders for the viewer (the server's own ceiling). */
const RENDER_SIDE = 2048;

/**
 * Which of the project's rasters are elevation data (a height grid, not a picture). The 3D Terrain check decides this on
 * the server from what each file declares; this page only asks. If it cannot be asked, nothing is hidden or relabelled.
 */
function useElevationIds(projectId: string): { ids: Set<string>; loading: boolean } {
  const terrain = useQuery({ queryKey: ["terrain", projectId], queryFn: () => listTerrain(projectId) });
  const ids = new Set(
    (terrain.data?.items ?? []).filter((item) => item.status === "usable" || item.status === "needs_confirmation").map((item) => item.asset_id),
  );
  return { ids, loading: terrain.isLoading };
}

function useRasterTools(assetId: string) {
  return useQuery({
    queryKey: ["raster-tools", assetId],
    queryFn: () => getRasterTools(assetId),
    // A picture that cannot be drawn yet (statistics or a display copy still being made) is asked for again when they land.
    refetchInterval: (query) => {
      const data = query.state.data;
      const active = (s?: string) => s === "queued" || s === "validating" || s === "processing";
      return data && (active(data.statistics_job?.status) || active(data.display_copy_job?.status)) ? 2000 : false;
    },
  });
}

interface UploadProblem {
  title: string;
  detail?: string | null;
}

/**
 * Uploads one GeoTIFF with the same call and the same pre-checks as Add Files, then opens it here. The server repeats
 * every check and stores the original unchanged; nothing about the file is decided on this page.
 */
function useImageUpload(projectId: string) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const rules = useQuery({ queryKey: ["ingestion-rules"], queryFn: getIngestionRules, staleTime: Infinity });
  const rule = rules.data?.rules.find((candidate) => candidate.modality === "overhead_raster");
  const [progress, setProgress] = useState<number | null>(null);
  const [problem, setProblem] = useState<UploadProblem | null>(null);

  const upload = async (file: File) => {
    setProblem(null);
    if (!rule) {
      setProblem({ title: "The accepted file formats could not be read", detail: "Try again in a moment." });
      return;
    }
    const rejected = checkFile(file, rule);
    if (rejected) {
      setProblem({ title: rejected.title, detail: rejected.detail });
      return;
    }
    setProgress(0);
    try {
      const result = await uploadAsset(projectId, "overhead_raster", file, setProgress);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["assets", projectId] }),
        queryClient.invalidateQueries({ queryKey: ["summary", projectId] }),
        queryClient.invalidateQueries({ queryKey: ["terrain", projectId] }),
      ]);
      navigate(`/projects/${projectId}/assets/${result.asset.id}/overhead`);
    } catch (error) {
      setProblem(
        error instanceof ApiError
          ? { title: error.message, detail: error.detail }
          : { title: "The upload failed unexpectedly", detail: "Nothing was added. Try again." },
      );
    } finally {
      setProgress(null);
    }
  };

  return { rule, progress, problem, dismiss: () => setProblem(null), upload };
}

type ImageUpload = ReturnType<typeof useImageUpload>;

function UploadButton({ upload, archived, variant = "primary" }: { upload: ImageUpload; archived: boolean; variant?: "primary" | "secondary" }) {
  const input = useRef<HTMLInputElement>(null);
  const busy = upload.progress !== null;
  const onChange = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = ""; // so choosing the same file again still fires
    if (file) void upload.upload(file);
  };
  return (
    <>
      <input
        ref={input}
        type="file"
        className="sr-only"
        tabIndex={-1}
        aria-label="Choose a satellite or aerial image file"
        accept={upload.rule?.extensions.join(",")}
        onChange={onChange}
      />
      <Button
        variant={variant}
        size={variant === "primary" ? "md" : "sm"}
        disabled={busy || archived || !upload.rule}
        title={archived ? "This project is archived. Restore it before adding files." : undefined}
        onClick={() => input.current?.click()}
      >
        <IconUpload size={15} />
        {busy ? `Uploading… ${Math.round((upload.progress ?? 0) * 100)}%` : "Upload image"}
      </Button>
    </>
  );
}

function UploadProblemNotice({ upload }: { upload: ImageUpload }) {
  if (!upload.problem) return null;
  return (
    <div className="px-3 pt-2">
      <Notice tone="error" title={upload.problem.title}>
        {upload.problem.detail ? <p>{upload.problem.detail}</p> : null}
        <button type="button" className="text-xs text-accent hover:underline" onClick={upload.dismiss}>
          Dismiss
        </button>
      </Notice>
    </div>
  );
}

/** The thin bar above the picture: which image this is, whether it is placed on the Earth, and the way to add another. */
function ViewerBar({
  projectId,
  asset,
  choices,
  upload,
  archived,
}: {
  projectId: string;
  asset: Asset;
  choices: Asset[];
  upload: ImageUpload;
  archived: boolean;
}) {
  const navigate = useNavigate();
  const placed = Boolean(asset.georeferencing?.placed ?? asset.raster?.georeferenced);
  const options = choices.some((item) => item.id === asset.id) ? choices : [asset, ...choices];
  return (
    <div className="flex h-11 shrink-0 items-center gap-3 border-b border-line/70 bg-surface/80 px-3 backdrop-blur-md">
      <IconSatellite size={16} className="shrink-0 text-fg-subtle" />
      {options.length > 1 ? (
        <select
          aria-label="Image"
          value={asset.id}
          onChange={(event) => navigate(`/projects/${projectId}/assets/${event.target.value}/overhead`)}
          className="h-8 min-w-0 max-w-[22rem] truncate rounded-lg border border-line-strong bg-raised px-2 text-[13px] text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
        >
          {options.map((item) => (
            <option key={item.id} value={item.id}>
              {item.original_filename}
            </option>
          ))}
        </select>
      ) : (
        <span className="min-w-0 truncate text-[13px] font-medium text-fg" title={asset.original_filename}>
          {asset.original_filename}
        </span>
      )}
      <span
        title={
          placed
            ? "This image is placed on the Earth. It is drawn on the Map."
            : "No coordinates are assigned to this image. It is shown as a picture only."
        }
      >
        <Badge tone={placed ? "ok" : "warn"}>{placed ? "Georeferenced" : "Not georeferenced"}</Badge>
      </span>
      <div className="ml-auto">
        <UploadButton upload={upload} archived={archived} variant="secondary" />
      </div>
    </div>
  );
}

/** One screen-filling area. The page's own content is the picture, so it bleeds to the edges of the content area. */
function Stage({ children }: { children: ReactNode }) {
  return <div className="-mx-4 -my-5 h-[calc(100vh-3rem)] min-h-[28rem] sm:-mx-6">{children}</div>;
}

function Centered({ children }: { children: ReactNode }) {
  return <div className="flex h-full items-center justify-center px-6">{children}</div>;
}

function ImageViewer({
  projectId,
  asset,
  choices,
  isElevation,
  upload,
  archived,
}: {
  projectId: string;
  asset: Asset;
  choices: Asset[];
  isElevation: boolean;
  upload: ImageUpload;
  archived: boolean;
}) {
  const tools = useRasterTools(asset.id);
  const bar = <ViewerBar projectId={projectId} asset={asset} choices={choices} upload={upload} archived={archived} />;
  const header = (
    <>
      {bar}
      <UploadProblemNotice upload={upload} />
      {isElevation ? (
        <div className="px-3 pt-2">
          <Notice tone="warning" title="This file is elevation data, not a satellite image">
            <p>
              It holds a grid of heights, not colours, so it looks like a grey relief map here.{" "}
              <Link to={`/projects/${projectId}/3d`} className="text-accent hover:underline">
                Open it in 3D Terrain
              </Link>
              .
            </p>
          </Notice>
        </div>
      ) : null}
    </>
  );

  if (tools.isLoading || tools.isError || !(tools.data!.width > 0 && tools.data!.height > 0)) {
    return (
      <div className="flex h-full min-h-0 flex-col bg-canvas">
        {header}
        <Centered>
          {tools.isLoading ? (
            <Loading label="Loading the image…" />
          ) : tools.isError ? (
            <ErrorNotice error={tools.error} />
          ) : (
            <Notice tone="error" title="This image has no readable size, so it cannot be shown" />
          )}
        </Centered>
      </div>
    );
  }

  const data = tools.data!;
  return (
    <PanZoomViewer
      key={asset.id}
      // The address changes when statistics or a display copy become available, so a picture that could not be drawn
      // before (for example a new 16-bit raster without statistics yet) is requested again.
      src={`${renderUrl(asset.id, {}, RENDER_SIDE)}&ready=${data.statistics ? "s" : "n"}-${data.display_source}`}
      alt={`Satellite or aerial image ${asset.original_filename}`}
      width={data.width}
      height={data.height}
      renderedSide={RENDER_SIDE}
      header={header}
    />
  );
}

export function SatellitePage() {
  const project = useProject();
  const { assetId } = useParams();
  const asset = useAsset(assetId);
  const rasters = useAssets(project.id, "overhead_raster");
  const elevation = useElevationIds(project.id);
  const upload = useImageUpload(project.id);
  const archived = Boolean(project.archived_at);
  const images = (rasters.data?.items ?? []).filter((item) => !elevation.ids.has(item.id));

  if (!assetId) {
    if (rasters.isLoading || elevation.loading) {
      return (
        <Stage>
          <Centered>
            <Loading label="Loading images…" />
          </Centered>
        </Stage>
      );
    }
    if (rasters.isError) {
      return (
        <Stage>
          <Centered>
            <ErrorNotice error={rasters.error} />
          </Centered>
        </Stage>
      );
    }
    // The first real picture, never an elevation grid: a height map opened here is confusing.
    if (images[0]) return <Navigate to={`/projects/${project.id}/assets/${images[0].id}/overhead`} replace />;
    const onlyHeights = rasters.data!.items.length > 0;
    return (
      <Stage>
        <Centered>
          <div className="w-full max-w-md space-y-3">
            <EmptyState
              icon={<IconSatellite size={28} />}
              title="No satellite or aerial images"
              action={<UploadButton upload={upload} archived={archived} />}
            >
              Upload a GeoTIFF satellite or aerial image to view it here.
              {onlyHeights ? (
                <span className="mt-2 block text-xs">
                  The rasters in this project are elevation files (height grids).{" "}
                  <Link to={`/projects/${project.id}/3d`} className="inline-flex items-center gap-1 text-accent hover:underline">
                    <IconCube size={12} />
                    View them in 3D Terrain
                  </Link>
                </span>
              ) : null}
            </EmptyState>
            {upload.problem ? (
              <Notice tone="error" title={upload.problem.title}>
                {upload.problem.detail ? <p>{upload.problem.detail}</p> : null}
              </Notice>
            ) : null}
          </div>
        </Centered>
      </Stage>
    );
  }

  const failure = (content: ReactNode) => (
    <Stage>
      <Centered>{content}</Centered>
    </Stage>
  );
  if (asset.isLoading) {
    return failure(<Loading label="Loading the image…" />);
  }
  if (asset.isError) return failure(<ErrorNotice error={asset.error} />);
  const file = asset.data!;
  if (file.project_id !== project.id) return failure(<Notice tone="error" title="This asset belongs to a different project" />);
  if (file.modality !== "overhead_raster" || !file.raster) {
    return failure(
      <Notice tone="error" title="This screen shows satellite and aerial rasters; this file is a different kind of input">
        <LinkButton size="sm" variant="secondary" to={`/projects/${project.id}/satellite`} className="mt-2">
          Back to Satellite Images
        </LinkButton>
      </Notice>,
    );
  }

  return (
    <Stage>
      <ImageViewer
        projectId={project.id}
        asset={file}
        choices={images}
        isElevation={elevation.ids.has(file.id)}
        upload={upload}
        archived={archived}
      />
    </Stage>
  );
}
