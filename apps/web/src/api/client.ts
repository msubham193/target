import createClient from "openapi-fetch";

import { loadProfile } from "../lib/profile";
import type { components, paths } from "./schema";

type Schemas = components["schemas"];
export type Project = Schemas["ProjectOut"];
export type ProjectPage = Schemas["ProjectPage"];
export type ProjectCreate = Schemas["ProjectCreate"];
export type ProjectUpdate = Schemas["ProjectUpdate"];
export type Profile = Schemas["ProfileOut"];
export type ProfileList = Schemas["ProfileList"];
export type Crs = Schemas["CrsOut"];
export type CrsSearchResult = Schemas["CrsSearchResult"];
export type Asset = Schemas["AssetOut"];
export type AssetUpload = Schemas["AssetUploadOut"];
export type AssetPage = Schemas["AssetPage"];
export type IngestionRule = Schemas["IngestionRule"];
export type IngestionRules = Schemas["IngestionRules"];
export type Modality = Asset["modality"];
export type Job = Schemas["JobOut"];
export type JobDetail = Schemas["JobDetail"];
export type JobCreate = Schemas["JobCreate"];
export type Run = Schemas["RunOut"];
export type ModelInfo = Schemas["ModelOut"];
export type Health = Schemas["HealthOut"];
export type ModelHealth = Schemas["ModelHealthOut"];

// RFC 9457 problem body returned for every API error.
export interface Problem {
  title: string;
  status: number;
  detail?: string | null;
  code: string;
  request_id?: string | null;
  errors?: { field?: string; message?: string }[];
  context?: Record<string, unknown> | null;
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    title: string,
    readonly detail?: string | null,
    readonly requestId?: string | null,
    readonly fieldErrors: { field?: string; message?: string }[] = [],
    /** Machine-readable detail, for example what has to be acknowledged (operation, accuracy, threshold). */
    readonly context: Record<string, unknown> | null = null,
  ) {
    super(title);
  }
}

const client = createClient<paths>({ baseUrl: globalThis.location?.origin ?? "http://127.0.0.1:5173" });

client.use({
  onRequest({ request }) {
    const profile = loadProfile();
    if (profile) request.headers.set("X-Workspace-Profile", profile.id);
    return request;
  },
});

function toApiError(error: unknown, response: Response): ApiError {
  const problem = (error ?? {}) as Partial<Problem>;
  return new ApiError(
    response.status,
    problem.code ?? "UNKNOWN_ERROR",
    problem.title ?? `Request failed (${response.status})`,
    problem.detail,
    problem.request_id ?? response.headers.get("X-Request-ID"),
    problem.errors ?? [],
    (problem.context as Record<string, unknown> | null | undefined) ?? null,
  );
}

async function unwrap<T>(call: Promise<{ data?: T; error?: unknown; response: Response }>): Promise<T> {
  let result;
  try {
    result = await call;
  } catch {
    throw new ApiError(0, "API_UNREACHABLE", "The local API is not reachable");
  }
  if (result.error !== undefined || result.data === undefined) throw toApiError(result.error, result.response);
  return result.data;
}

// /health answers 503 with the same body shape when a dependency is down, so read it without unwrap.
export async function getHealth(): Promise<Health> {
  let response: Response;
  try {
    response = await fetch("/health");
  } catch {
    throw new ApiError(0, "API_UNREACHABLE", "The local API is not reachable");
  }
  const body: unknown = await response.json().catch(() => null);
  if (body && typeof body === "object" && "status" in body) return body as Health;
  throw new ApiError(response.status, "API_UNREACHABLE", "The local API is not reachable");
}

export const getModelHealth = () => unwrap(client.GET("/health/models"));

export const listProfiles = () => unwrap(client.GET("/api/v1/workspace/profiles"));

export const createProfile = (displayName: string) =>
  unwrap(client.POST("/api/v1/workspace/profiles", { body: { display_name: displayName } }));

export const listProjects = (query: {
  q?: string;
  tag?: string;
  archived?: boolean;
  limit?: number;
  cursor?: string;
}) => unwrap(client.GET("/api/v1/projects", { params: { query } }));

export const getProject = (projectId: string) =>
  unwrap(client.GET("/api/v1/projects/{project_id}", { params: { path: { project_id: projectId } } }));

export const createProject = (body: ProjectCreate) => unwrap(client.POST("/api/v1/projects", { body }));

export const updateProject = (projectId: string, body: ProjectUpdate) =>
  unwrap(client.PATCH("/api/v1/projects/{project_id}", { params: { path: { project_id: projectId } }, body }));

export const getIngestionRules = () => unwrap(client.GET("/api/v1/ingestion/rules"));

export const listAssets = (projectId: string, query: { modality?: Modality; limit?: number; cursor?: string }) =>
  unwrap(
    client.GET("/api/v1/projects/{project_id}/assets", { params: { path: { project_id: projectId }, query } }),
  );

export const getAsset = (assetId: string) =>
  unwrap(client.GET("/api/v1/assets/{asset_id}", { params: { path: { asset_id: assetId } } }));

/** `version` changes when the preview is regenerated, so the browser does not show a stale picture. */
export const assetPreviewUrl = (assetId: string, variant: "view" | "thumb", version?: string | null) =>
  `/api/v1/assets/${assetId}/preview?variant=${variant}${version ? `&v=${version}` : ""}`;

export const listJobs = (projectId: string, query: { asset_id?: string; kind?: "preview" | "analyze"; limit?: number }) =>
  unwrap(client.GET("/api/v1/projects/{project_id}/jobs", { params: { path: { project_id: projectId }, query } }));

export const getJob = (jobId: string) =>
  unwrap(client.GET("/api/v1/jobs/{job_id}", { params: { path: { job_id: jobId } } }));

export const createJob = (projectId: string, body: JobCreate) =>
  unwrap(client.POST("/api/v1/projects/{project_id}/jobs", { params: { path: { project_id: projectId } }, body }));

export const cancelJob = (jobId: string) =>
  unwrap(client.POST("/api/v1/jobs/{job_id}/cancel", { params: { path: { job_id: jobId } } }));

export const retryJob = (jobId: string) =>
  unwrap(client.POST("/api/v1/jobs/{job_id}/retry", { params: { path: { job_id: jobId } } }));

export const getRun = (runId: string) =>
  unwrap(client.GET("/api/v1/runs/{run_id}", { params: { path: { run_id: runId } } }));

export type ReviewAction = Schemas["ReviewRequest"]["action"];
export type ReviewStatus = Schemas["ObservationCurrent"]["status"];

export interface ObservationQuery {
  asset_id?: string;
  run_id?: string;
  /** Only findings in this sampled video frame. */
  frame_index?: number;
  status?: ReviewStatus[];
  category?: string;
  in_conflict?: boolean;
  limit?: number;
}

export const listObservations = (projectId: string, query: ObservationQuery) =>
  unwrap(
    client.GET("/api/v1/projects/{project_id}/observations", {
      params: { path: { project_id: projectId }, query },
      querySerializer: { array: { style: "form", explode: true } },
    }),
  );

export const getObservation = (observationId: string) =>
  unwrap(client.GET("/api/v1/observations/{observation_id}", { params: { path: { observation_id: observationId } } }));

export const reviewObservation = (observationId: string, body: Schemas["ReviewRequest"]) =>
  unwrap(
    client.PATCH("/api/v1/observations/{observation_id}/review", {
      params: { path: { observation_id: observationId } },
      body,
    }),
  );

export const listConflicts = (projectId: string, status?: "open" | "resolved_documented") =>
  unwrap(
    client.GET("/api/v1/projects/{project_id}/conflicts", {
      params: { path: { project_id: projectId }, query: { status } },
    }),
  );

/**
 * `operations` are datum transformations chosen for rasters, as `assetId:operationId`. They are only needed where the
 * CRS assessment says a choice is required, and nothing is stored by sending them.
 */
export const getLayers = (projectId: string, operations: string[] = []) =>
  unwrap(
    client.GET("/api/v1/projects/{project_id}/layers", {
      params: { path: { project_id: projectId }, query: operations.length > 0 ? { operation: operations } : {} },
    }),
  );

export const transformPoint = (query: {
  source: string;
  target: string;
  x: number;
  y: number;
  operation?: string;
  accept_ballpark?: boolean;
  /** Acknowledge, for this request only, the datum shift that was refused (see TRANSFORMATION_ACKNOWLEDGEMENT_REQUIRED). */
  acknowledge_operation?: string;
  acknowledge_accuracy_m?: number;
}) => unwrap(client.GET("/api/v1/crs/transform", { params: { query } }));

/** CRS details, the eight statuses and every transformation candidate for a raster. Read-only. */
export const getCrsAssessment = (assetId: string, operation?: string | null) =>
  unwrap(
    client.GET("/api/v1/assets/{asset_id}/crs-assessment", {
      params: { path: { asset_id: assetId }, query: operation ? { operation } : {} },
    }),
  );

export type CrsAssessment = Schemas["CrsAssessmentOut"];
export type TransformationPlan = Schemas["TransformationPlanOut"];
export type TransformationOperation = Schemas["TransformationOperationOut"];
export type LayerTransformation = Schemas["LayerTransformationOut"];

// Response types are taken from the calls: openapi-fetch reads tuples in responses as plain arrays.
/** An observation with its current (reviewed) state, open conflicts, links and the review actions allowed now. */
export type ObservationView = Awaited<ReturnType<typeof listObservations>>[number];
/** The model's output in the canonical contract, exactly as stored. Never changed by review. */
export type Observation = ObservationView["observation"];
export type ObservationDetail = Awaited<ReturnType<typeof getObservation>>;
export type Conflict = ObservationDetail["conflicts"][number];
export type Revision = ObservationDetail["revisions"][number];
export type Layers = Awaited<ReturnType<typeof getLayers>>;
export type RasterLayer = Layers["rasters"][number];
export type CrsTransform = Awaited<ReturnType<typeof transformPoint>>;

export type ExportFormat = "csv" | "geojson" | "json";

/**
 * Downloads an export from the API. The profile header is required (exports are audited), so the file is fetched
 * and saved through a temporary object URL rather than by navigating to the address.
 */
export async function downloadExport(projectId: string, format: ExportFormat): Promise<{ filename: string; bytes: number }> {
  const headers = new Headers();
  const profile = loadProfile();
  if (profile) headers.set("X-Workspace-Profile", profile.id);
  let response: Response;
  try {
    response = await fetch(`/api/v1/projects/${projectId}/exports/observations.${format}`, { headers });
  } catch {
    throw new ApiError(0, "API_UNREACHABLE", "The local API is not reachable");
  }
  if (!response.ok) {
    const problem: unknown = await response.json().catch(() => null);
    throw toApiError(problem, response);
  }
  const blob = await response.blob();
  const disposition = response.headers.get("Content-Disposition") ?? "";
  const filename = /filename="([^"]+)"/.exec(disposition)?.[1] ?? `observations.${format}`;
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
  return { filename, bytes: blob.size };
}

export const assetContentUrl = (assetId: string) => `/api/v1/assets/${assetId}/content`;

/**
 * Sends the file as the raw request body so the server can stream it straight to storage.
 * XMLHttpRequest is used because fetch cannot report upload progress.
 */
export function uploadAsset(
  projectId: string,
  modality: Modality,
  file: File,
  onProgress?: (fraction: number) => void,
): Promise<AssetUpload> {
  const query = new URLSearchParams({ modality, filename: file.name });
  return uploadBinary<AssetUpload>(`/api/v1/projects/${projectId}/assets?${query.toString()}`, file, onProgress);
}

/** Streams one file as the request body (not multipart) and reports progress. Shared by every upload that stores an original. */
function uploadBinary<T>(url: string, file: File, onProgress?: (fraction: number) => void): Promise<T> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", url);
    xhr.setRequestHeader("Content-Type", "application/octet-stream");
    const profile = loadProfile();
    if (profile) xhr.setRequestHeader("X-Workspace-Profile", profile.id);
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable && event.total > 0) onProgress?.(event.loaded / event.total);
    };
    xhr.onload = () => {
      let body: unknown = null;
      try {
        body = JSON.parse(xhr.responseText);
      } catch {
        // handled below as an unreadable response
      }
      if (xhr.status >= 200 && xhr.status < 300 && body) {
        resolve(body as T);
        return;
      }
      const problem = (body ?? {}) as Partial<Problem>;
      reject(
        new ApiError(
          xhr.status,
          problem.code ?? "UPLOAD_FAILED",
          problem.title ?? `The upload failed (${xhr.status})`,
          problem.detail,
          problem.request_id ?? xhr.getResponseHeader("X-Request-ID"),
          problem.errors ?? [],
        ),
      );
    };
    xhr.onerror = () =>
      reject(
        new ApiError(
          0,
          "UPLOAD_FAILED",
          "The upload did not complete",
          "The connection to the local API was lost. Check that it is running, then try again.",
        ),
      );
    xhr.send(file);
  });
}

// --- map sheets (OSM / ESM): intake, metadata assessment, control points, overlay alignment -------------------------

export type MapSheetUpload = Schemas["MapSheetUploadOut"];
export type MapSheetInfo = Schemas["MapSheetInfo"];
export type MapSheetSummary = Schemas["MapSheetSummaryOut"];
export type MapSheetList = Schemas["MapSheetListOut"];
export type MapSheetAssessment = Schemas["MapSheetAssessmentOut"];
export type MapSheetNote = Schemas["MapSheetNoteOut"];
export type MapSheetNoteRequest = Schemas["MapSheetNoteIn"];
export type MapSheetLayer = Schemas["MapSheetLayerOut"];
export type MapSeries = MapSheetInfo["declaration"]["series"];
export type ControlPointsRequest = Schemas["ControlPointsRequest"];
export type ControlPointsValidation = Schemas["ControlPointsValidation"];
export type OverlayAlignment = Schemas["OverlayAlignmentOut"];

export interface MapSheetDeclaration {
  series: MapSeries;
  sheetLabel?: string;
  scaleDenominator?: number;
  edition?: string;
}

/** Upload one map sheet (GeoTIFF, scanned TIFF / PNG / JPEG, or PDF). The series is attribution; it assigns no CRS. */
export function uploadMapSheet(
  projectId: string,
  file: File,
  declaration: MapSheetDeclaration,
  onProgress?: (fraction: number) => void,
): Promise<MapSheetUpload> {
  const query = new URLSearchParams({ filename: file.name, series: declaration.series });
  if (declaration.sheetLabel?.trim()) query.set("sheet_label", declaration.sheetLabel.trim());
  if (declaration.scaleDenominator) query.set("scale_denominator", String(declaration.scaleDenominator));
  if (declaration.edition?.trim()) query.set("edition", declaration.edition.trim());
  return uploadBinary<MapSheetUpload>(`/api/v1/projects/${projectId}/map-sheets?${query.toString()}`, file, onProgress);
}

export const listMapSheets = (projectId: string) =>
  unwrap(client.GET("/api/v1/projects/{project_id}/map-sheets", { params: { path: { project_id: projectId } } }));

/** What the sheet's file states about its datum, ellipsoid, projection, units, grid and extent, and what is unresolved. */
export const getMapSheetAssessment = (assetId: string) =>
  unwrap(client.GET("/api/v1/assets/{asset_id}/map-sheet", { params: { path: { asset_id: assetId } } }));

export const recordMapSheetNote = (assetId: string, body: MapSheetNoteRequest) =>
  unwrap(
    client.POST("/api/v1/assets/{asset_id}/map-sheet/metadata-notes", { params: { path: { asset_id: assetId } }, body }),
  );

export const validateControlPoints = (assetId: string, body: ControlPointsRequest) =>
  unwrap(
    client.POST("/api/v1/assets/{asset_id}/control-points/validate", { params: { path: { asset_id: assetId } }, body }),
  );

export const recordControlPoints = (assetId: string, body: Schemas["ControlPointsApply"]) =>
  unwrap(client.POST("/api/v1/assets/{asset_id}/control-points", { params: { path: { asset_id: assetId } }, body }));

export const getOverlayAlignment = (projectId: string, first: string, second: string) =>
  unwrap(
    client.GET("/api/v1/projects/{project_id}/alignment", {
      params: { path: { project_id: projectId }, query: { first, second } },
    }),
  );

export const searchCrs = (q: string, limit = 10) =>
  unwrap(client.GET("/api/v1/crs", { params: { query: { q, limit } } }));

export type ProjectSummary = Schemas["ProjectSummary"];
export type RecentJob = Schemas["RecentJob"];

export type FindingsReport = Schemas["FindingsReport"];

/** Builds the read-only findings report from stored data. Each call is recorded in the audit log. */
export const getFindingsReport = (projectId: string) =>
  unwrap(client.GET("/api/v1/projects/{project_id}/report", { params: { path: { project_id: projectId } } }));

export const getProjectSummary = (projectId: string) =>
  unwrap(client.GET("/api/v1/projects/{project_id}/summary", { params: { path: { project_id: projectId } } }));

export const listRecentJobs = (limit = 10) => unwrap(client.GET("/api/v1/jobs", { params: { query: { limit } } }));

// --- georeferencing recorded by a person ---------------------------------------------------------------------------

export type GeoreferencingRequest = Schemas["GeoreferencingRequest"];

export const getGeoreferencing = (assetId: string) =>
  unwrap(client.GET("/api/v1/assets/{asset_id}/georeferencing", { params: { path: { asset_id: assetId } } }));

export const validateGeoreferencing = (assetId: string, body: GeoreferencingRequest) =>
  unwrap(
    client.POST("/api/v1/assets/{asset_id}/georeferencing/validate", { params: { path: { asset_id: assetId } }, body }),
  );

export const recordGeoreferencing = (assetId: string, body: Schemas["GeoreferencingApply"]) =>
  unwrap(client.POST("/api/v1/assets/{asset_id}/georeferencing", { params: { path: { asset_id: assetId } }, body }));

export type Georeferencing = Awaited<ReturnType<typeof getGeoreferencing>>;
export type GeoreferenceValidation = Awaited<ReturnType<typeof validateGeoreferencing>>;

// --- sidecar metadata files (.tfw/.wld, .prj, .aux.xml) -------------------------------------------------------------

export type SidecarUpload = Schemas["SidecarUploadOut"];
export type SidecarRequest = Schemas["SidecarRequest"];

/** Add a sidecar file. With `assetId` it is attached to that raster; without, the raster whose name matches. */
export async function uploadSidecar(projectId: string, file: File, assetId?: string): Promise<SidecarUpload> {
  const query = new URLSearchParams({ filename: file.name, ...(assetId ? { asset_id: assetId } : {}) });
  const headers: Record<string, string> = { "Content-Type": "application/octet-stream" };
  const profile = loadProfile();
  if (profile) headers["X-Workspace-Profile"] = profile.id;
  let response: Response;
  try {
    response = await fetch(`/api/v1/projects/${projectId}/sidecars?${query.toString()}`, { method: "POST", headers, body: file });
  } catch {
    throw new ApiError(0, "API_UNREACHABLE", "The local API is not reachable");
  }
  const body: unknown = await response.json().catch(() => null);
  if (response.ok && body) return body as SidecarUpload;
  throw toApiError(body, response);
}

export const getSidecars = (assetId: string) =>
  unwrap(client.GET("/api/v1/assets/{asset_id}/sidecars", { params: { path: { asset_id: assetId } } }));

export const validateSidecars = (assetId: string, body: SidecarRequest) =>
  unwrap(client.POST("/api/v1/assets/{asset_id}/sidecars/validate", { params: { path: { asset_id: assetId } }, body }));

export const applySidecars = (assetId: string, body: Schemas["SidecarApply"]) =>
  unwrap(client.POST("/api/v1/assets/{asset_id}/sidecars/apply", { params: { path: { asset_id: assetId } }, body }));

export type SidecarReview = Awaited<ReturnType<typeof getSidecars>>;
export type SidecarItem = SidecarReview["sidecars"][number];
export type SidecarCheck = Awaited<ReturnType<typeof validateSidecars>>;

// --- non-AI raster tools (Phase 3b) -----------------------------------------------------------------------------------

/** Display settings: which bands and how values map to brightness. Display only. */
export interface DisplayQuery {
  bands?: string;
  stretch?: string;
  low?: string;
  high?: string;
}

export function displayParams(query: DisplayQuery): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) if (value) params.set(key, value);
  return params.toString();
}

export const getRasterTools = (assetId: string) =>
  unwrap(client.GET("/api/v1/assets/{asset_id}/raster-tools", { params: { path: { asset_id: assetId } } }));

export const requestStatistics = (assetId: string) =>
  unwrap(client.POST("/api/v1/assets/{asset_id}/statistics", { params: { path: { asset_id: assetId } } }));

export const requestDisplayCopy = (assetId: string) =>
  unwrap(client.POST("/api/v1/assets/{asset_id}/display-copy", { params: { path: { asset_id: assetId } } }));

export const getDisplay = (assetId: string, query: DisplayQuery) =>
  unwrap(client.GET("/api/v1/assets/{asset_id}/display", { params: { path: { asset_id: assetId }, query } }));

export const renderUrl = (assetId: string, query: DisplayQuery, maxSide = 1280) => {
  const params = displayParams(query);
  return `/api/v1/assets/${assetId}/render.png?max_side=${maxSide}${params ? `&${params}` : ""}`;
};

export const getPixel = (
  assetId: string,
  query: { col?: number; row?: number; lon?: number; lat?: number; operation?: string },
) =>
  unwrap(client.GET("/api/v1/assets/{asset_id}/pixel", { params: { path: { asset_id: assetId }, query } }));

export const measurePoints = (body: Schemas["MeasureRequest"]) => unwrap(client.POST("/api/v1/measurements", { body }));

export const getAlignment = (assetId: string, otherId: string) =>
  unwrap(
    client.GET("/api/v1/assets/{asset_id}/alignment/{other_id}", {
      params: { path: { asset_id: assetId, other_id: otherId } },
    }),
  );

export const requestComparison = (projectId: string, body: Schemas["ComparisonRequest"]) =>
  unwrap(client.POST("/api/v1/projects/{project_id}/comparisons", { params: { path: { project_id: projectId } }, body }));

export const listComparisons = (projectId: string) =>
  unwrap(client.GET("/api/v1/projects/{project_id}/comparisons", { params: { path: { project_id: projectId } } }));

export type RasterTools = Awaited<ReturnType<typeof getRasterTools>>;
export type PixelValues = Awaited<ReturnType<typeof getPixel>>;
export type Measurement = Awaited<ReturnType<typeof measurePoints>>;
export type Comparison = Awaited<ReturnType<typeof listComparisons>>[number];

// --- photo locations: EXIF candidates and positions recorded by a person --------------------------------------------

export type PhotoLocationApply = Schemas["PhotoLocationApply"];

export const getPhotoLocation = (assetId: string) =>
  unwrap(client.GET("/api/v1/assets/{asset_id}/location", { params: { path: { asset_id: assetId } } }));

export const changePhotoLocation = (assetId: string, body: PhotoLocationApply) =>
  unwrap(client.POST("/api/v1/assets/{asset_id}/location", { params: { path: { asset_id: assetId } }, body }));

export const readPhotoExif = (assetId: string) =>
  unwrap(client.POST("/api/v1/assets/{asset_id}/location/read-exif", { params: { path: { asset_id: assetId } } }));

export const listPhotoLocations = (projectId: string) =>
  unwrap(client.GET("/api/v1/projects/{project_id}/photo-locations", { params: { path: { project_id: projectId } } }));

export type PhotoLocation = Awaited<ReturnType<typeof getPhotoLocation>>;
export type PhotoLocations = Awaited<ReturnType<typeof listPhotoLocations>>;
export type PhotoLocationItem = PhotoLocations["items"][number];

// --- map annotations: marks a person draws on the map (not findings, not photograph locations) -------------------------

export type AnnotationCreate = Schemas["AnnotationCreate"];
export type AnnotationUpdate = Schemas["AnnotationUpdate"];

export const listAnnotations = (projectId: string, includeDeleted = false) =>
  unwrap(
    client.GET("/api/v1/projects/{project_id}/annotations", {
      params: { path: { project_id: projectId }, query: { include_deleted: includeDeleted } },
    }),
  );

export const createAnnotation = (projectId: string, body: AnnotationCreate) =>
  unwrap(client.POST("/api/v1/projects/{project_id}/annotations", { params: { path: { project_id: projectId } }, body }));

export const updateAnnotation = (projectId: string, annotationId: string, body: AnnotationUpdate) =>
  unwrap(
    client.PATCH("/api/v1/projects/{project_id}/annotations/{annotation_id}", {
      params: { path: { project_id: projectId, annotation_id: annotationId } },
      body,
    }),
  );

export const deleteAnnotation = (projectId: string, annotationId: string, expectedVersion: number, reason?: string) =>
  unwrap(
    client.DELETE("/api/v1/projects/{project_id}/annotations/{annotation_id}", {
      params: {
        path: { project_id: projectId, annotation_id: annotationId },
        query: { expected_version: expectedVersion, ...(reason ? { reason } : {}) },
      },
    }),
  );

export const getAnnotationHistory = (projectId: string, annotationId: string) =>
  unwrap(
    client.GET("/api/v1/projects/{project_id}/annotations/{annotation_id}/history", {
      params: { path: { project_id: projectId, annotation_id: annotationId } },
    }),
  );

export type Annotation = Awaited<ReturnType<typeof createAnnotation>>;
export type Annotations = Awaited<ReturnType<typeof listAnnotations>>;
export type AnnotationHistory = Awaited<ReturnType<typeof getAnnotationHistory>>;

// --- video-frame analysis: a bounded sample of frames, analysed one by one (nothing is tracked) -----------------------

export type VideoSampling = Schemas["VideoSampling"];

export const getVideoSamplingPlan = (
  projectId: string,
  assetId: string,
  query: { interval_s: number; max_frames: number; start_s?: number; end_s?: number },
) =>
  unwrap(
    client.GET("/api/v1/projects/{project_id}/assets/{asset_id}/video-analysis/plan", {
      params: { path: { project_id: projectId, asset_id: assetId }, query },
    }),
  );

export const getVideoFrames = (projectId: string, assetId: string, runId: string) =>
  unwrap(
    client.GET("/api/v1/projects/{project_id}/assets/{asset_id}/video-analysis/runs/{run_id}/frames", {
      params: { path: { project_id: projectId, asset_id: assetId, run_id: runId } },
    }),
  );

export type VideoSamplingPlan = Awaited<ReturnType<typeof getVideoSamplingPlan>>;
export type VideoFrames = Awaited<ReturnType<typeof getVideoFrames>>;
export type VideoFrame = VideoFrames["frames"][number];

/** The stored frame image. Its checksum is part of the address, so a different frame is never shown from the cache. */
export const videoFrameUrl = (frame: Pick<VideoFrame, "image_url" | "sha256">) => `${frame.image_url}?v=${frame.sha256.slice(0, 12)}`;

// --- 3D terrain view: which rasters are elevation, and the height grid made from one -----------------------------------

export const listTerrain = (projectId: string) =>
  unwrap(client.GET("/api/v1/projects/{project_id}/terrain", { params: { path: { project_id: projectId } } }));

export interface TerrainRequest {
  band?: number;
  /** Only for a raster that declares no unit: a person's statement, for this view only. */
  unit?: "metre" | "foot" | "us_survey_foot";
  /** Only for a band the file does not declare as elevation: a person's statement, for this view only. */
  confirm_elevation?: boolean;
  max_side?: number;
}

export const getTerrain = (projectId: string, assetId: string, query: TerrainRequest) =>
  unwrap(
    client.GET("/api/v1/projects/{project_id}/assets/{asset_id}/terrain", {
      params: { path: { project_id: projectId, asset_id: assetId }, query },
    }),
  );

export type TerrainList = Awaited<ReturnType<typeof listTerrain>>;
export type TerrainRaster = TerrainList["items"][number];
export type TerrainMeta = Awaited<ReturnType<typeof getTerrain>>;

/** The height grid: raw little-endian float32, north row first, NaN = no data. Checked against the size the metadata gave. */
export async function fetchTerrainGrid(gridUrl: string, cells: number): Promise<Float32Array> {
  let response: Response;
  try {
    response = await fetch(gridUrl);
  } catch {
    throw new ApiError(0, "API_UNREACHABLE", "The local API is not reachable");
  }
  if (!response.ok) {
    const problem = (await response.json().catch(() => ({}))) as Partial<Problem>;
    throw new ApiError(response.status, problem.code ?? "UNKNOWN_ERROR", problem.title ?? `Request failed (${response.status})`, problem.detail, problem.request_id);
  }
  const buffer = await response.arrayBuffer();
  if (buffer.byteLength !== cells * 4) {
    throw new ApiError(500, "TERRAIN_GRID_SIZE_MISMATCH", "The height grid is not the size its description gave", `Expected ${cells * 4} bytes, received ${buffer.byteLength}.`);
  }
  const view = new DataView(buffer);
  const values = new Float32Array(cells);
  for (let i = 0; i < cells; i++) values[i] = view.getFloat32(i * 4, true);
  return values;
}

// --- derived spectral indices (NDVI Studio) -----------------------------------------------------------------------

export const listIndices = () => unwrap(client.GET("/api/v1/indices"));

export const listIndexInputs = (projectId: string, index = "ndvi") =>
  unwrap(
    client.GET("/api/v1/projects/{project_id}/indices/{index_key}/inputs", {
      params: { path: { project_id: projectId, index_key: index } },
    }),
  );

export const listIndexRuns = (projectId: string, index = "ndvi") =>
  unwrap(
    client.GET("/api/v1/projects/{project_id}/indices/{index_key}/runs", {
      params: { path: { project_id: projectId, index_key: index } },
    }),
  );

/** Queues a background run. The scene is named by its opaque id; no path or URL is ever sent. */
export const startIndexRun = (projectId: string, sceneId: string, index = "ndvi") =>
  unwrap(
    client.POST("/api/v1/projects/{project_id}/indices/{index_key}/runs", {
      params: { path: { project_id: projectId, index_key: index } },
      body: { scene_id: sceneId },
    }),
  );

export const cancelIndexRun = (projectId: string, jobId: string, index = "ndvi") =>
  unwrap(
    client.POST("/api/v1/projects/{project_id}/indices/{index_key}/runs/{job_id}/cancel", {
      params: { path: { project_id: projectId, index_key: index, job_id: jobId } },
    }),
  );

export type IndexInputs = Awaited<ReturnType<typeof listIndexInputs>>;
export type IndexScene = IndexInputs["scenes"][number];
export type IndexRun = Schemas["IndexRunOut"];
export type IndexRunResult = Schemas["IndexRunResult"];
export type IndexDefinition = Awaited<ReturnType<typeof listIndices>>["indices"][number];
