import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Outlet, Route, Routes } from "react-router-dom";
import { vi } from "vitest";

import * as client from "../api/client";
import { AssetPreview } from "../components/AssetPreview";
import { JobsPanel } from "../components/JobsPanel";
import { detail as detailOf, view } from "../test/fixtures";
import { renderPage } from "../test/render";
import { GroundAnalysisPage } from "./GroundAnalysisPage";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    listJobs: vi.fn(),
    createJob: vi.fn(),
    cancelJob: vi.fn(),
    retryJob: vi.fn(),
    getModelHealth: vi.fn(),
    getAsset: vi.fn(),
    getRun: vi.fn(),
    listObservations: vi.fn(),
    listAssets: vi.fn(),
    getObservation: vi.fn(),
    reviewObservation: vi.fn(),
  };
});

const listJobs = vi.mocked(client.listJobs);
const createJob = vi.mocked(client.createJob);
const cancelJob = vi.mocked(client.cancelJob);
const retryJob = vi.mocked(client.retryJob);
const getModelHealth = vi.mocked(client.getModelHealth);
const getAsset = vi.mocked(client.getAsset);
const getRun = vi.mocked(client.getRun);
const listObservations = vi.mocked(client.listObservations);

const PROJECT = { id: "p1", name: "Survey", archived_at: null } as client.Project;

function asset(overrides: Partial<client.Asset> = {}): client.Asset {
  return {
    id: "a1",
    project_id: "p1",
    original_filename: "photo.jpg",
    modality: "ground_image",
    detected_format: "JPEG",
    media_type: "image/jpeg",
    size_bytes: 2048,
    sha256: "ab".repeat(32),
    status: "validated",
    duplicate_of_asset_id: null,
    quality_flags: ["NOT_GEOREFERENCED"],
    unknowns: {},
    metadata: {},
    acquisition_time: null,
    uploaded_by: { id: "u1", display_name: "Asha", created_at: "2026-10-12T00:00:00Z" },
    ingested_at: "2026-10-12T00:00:00Z",
    raster: null,
    video: null,
    preview: { status: "ready", variants: ["thumb", "view"], job_id: "j0", error_code: null, error_message: null },
    ...overrides,
  };
}

function job(overrides: Partial<client.Job> = {}): client.Job {
  return {
    id: "j1",
    project_id: "p1",
    asset_id: "a1",
    kind: "analyze",
    status: "queued",
    stage: null,
    progress: null,
    cancel_requested: false,
    attempt: 0,
    max_attempts: 2,
    payload: { pipeline: "ground_image", model: { name: "detector", version: "v1" } },
    result: null,
    error: null,
    has_partial_outputs: false,
    retry_of_job_id: null,
    created_at: "2026-10-12T00:00:00Z",
    started_at: null,
    finished_at: null,
    ...overrides,
  };
}

function models(available = true, workerRunning = true): client.ModelHealth {
  return {
    manifest_found: true,
    errors: [],
    worker: { running: workerRunning, capabilities: workerRunning ? { cuda_available: true } : null },
    llm_runtime: { note: "" },
    models: [
      {
        name: "detector",
        version: "v1",
        pipeline: "ground_image",
        task: "object_detection",
        runtime: "torchvision",
        licence: "BSD",
        score_semantics: "Class score after NMS.",
        score_calibrated: false,
        settings: {},
        weights_sha256: "cd".repeat(32),
        weights_size_bytes: 10,
        available,
        reason_unavailable: available ? null : "The model weights are not installed on this machine.",
        custom: false,
      },
    ],
  };
}

function observation(index: number, box: number[], label: string, score: number): client.Observation {
  return {
    schema_version: "3.0",
    observation_id: `o${index}`,
    project_id: "p1",
    asset_id: "a1",
    pipeline: "ground_image",
    model: { name: "detector", version: "v1", run_id: "r1" },
    claim: { category: `coco:${label}`, description: "Model-predicted feature.", claim_type: "inferred" },
    score: { raw_value: score, semantics: "Class score after NMS.", calibrated: false, value_range: [0, 1] },
    evidence: [
      {
        asset_id: "a1",
        region_px: {
          type: "bbox_xyxy",
          coordinates: box,
          coordinate_space: "source_image_pixels",
          origin: "top_left",
          pixel_reference: "corner",
          image_width: 4000,
          image_height: 3000,
        },
        frame_index: null,
        timestamp_ms: null,
        source_excerpt_or_metadata_key: null,
      },
    ],
    geospatial: { geometry: null, crs_id: null, coordinate_units: null, derivation_method: null, uncertainty: null },
    measurements: [],
    quality_flags: ["NOT_GEOREFERENCED", "UNCALIBRATED_SCORE"],
    review: { status: "needs_review", reviewer_note: null, reviewed_at: null, reviewed_by: null },
    provenance: { source_checksum: "ab".repeat(32), ingested_at: "2026-10-12T00:00:00Z", transformations: [], created_at: "2026-10-12T00:00:00Z" },
  };
}

const page = (items: client.Job[]) => ({ items, next_cursor: null });

beforeEach(() => {
  vi.clearAllMocks();
  getModelHealth.mockResolvedValue(models());
});

describe("AssetPreview", () => {
  it("shows the derived picture and says it is not the original", () => {
    renderPage(<AssetPreview asset={asset()} />);
    expect(screen.getByRole("img", { name: "Preview of photo.jpg" })).toHaveAttribute("src", "/api/v1/assets/a1/preview?variant=view&v=j0");
    expect(screen.getByText(/the original is unchanged/i)).toBeInTheDocument();
  });

  it("says when the preview is still being generated", () => {
    renderPage(<AssetPreview asset={asset({ preview: { status: "pending", variants: [], job_id: "j0", error_code: null, error_message: null } })} />);
    expect(screen.getByRole("status")).toHaveTextContent("Preview is being generated");
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
  });

  it("explains an unsupported preview without implying the file is bad", () => {
    const failed = { status: "failed" as const, variants: [], job_id: "j0", error_code: "PREVIEW_UNSUPPORTED", error_message: "Rasters with complex-valued pixels cannot be shown as a picture." };
    renderPage(<AssetPreview asset={asset({ modality: "overhead_raster", preview: failed })} />);
    const notice = screen.getByRole("status");
    expect(notice).toHaveTextContent("complex-valued pixels cannot be shown");
    expect(notice).toHaveTextContent("Code: PREVIEW_UNSUPPORTED");
    expect(notice).toHaveTextContent("The file itself is stored and valid.");
  });
});

describe("JobsPanel", () => {
  it("queues an analysis with the chosen model", async () => {
    listJobs.mockResolvedValue(page([]));
    createJob.mockResolvedValue(job());
    renderPage(<JobsPanel asset={asset()} />);
    expect(await screen.findByText("No jobs for this file yet.")).toBeInTheDocument();
    expect(screen.getByText(/No validated threshold exists, so every finding will need review/)).toBeInTheDocument();
    await userEvent.click(await screen.findByRole("button", { name: "Run analysis" }));
    await waitFor(() =>
      expect(createJob).toHaveBeenCalledWith("p1", { kind: "analyze", asset_id: "a1", pipeline: "ground_image", model: { name: "detector", version: "v1" }, submit: true }),
    );
  });

  it("does not offer to run a model that is unavailable, and says why", async () => {
    listJobs.mockResolvedValue(page([]));
    getModelHealth.mockResolvedValue(models(false));
    renderPage(<JobsPanel asset={asset()} />);
    expect(await screen.findByText("The model weights are not installed on this machine.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run analysis" })).toBeDisabled();
  });

  it("warns that a queued job waits when no worker is running", async () => {
    listJobs.mockResolvedValue(page([]));
    getModelHealth.mockResolvedValue(models(true, false));
    renderPage(<JobsPanel asset={asset()} />);
    expect(await screen.findByText("No worker is running")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run analysis" })).toBeEnabled();
  });

  it("says plainly that raster analysis does not exist", async () => {
    listJobs.mockResolvedValue(page([]));
    renderPage(<JobsPanel asset={asset({ modality: "overhead_raster" })} />);
    expect(await screen.findByText("Analysis is not available for this kind of input yet")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Run analysis" })).not.toBeInTheDocument();
  });

  it("sends a video to the Videos screen, where its sampling is chosen, and never starts it from here", async () => {
    listJobs.mockResolvedValue(page([]));
    renderPage(<JobsPanel asset={asset({ modality: "video" })} />);
    expect(await screen.findByText("Video is analysed from the Videos screen")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Run analysis" })).not.toBeInTheDocument();
    expect(createJob).not.toHaveBeenCalled();
  });

  it("shows a running job's stage and measured progress, and can cancel it", async () => {
    listJobs.mockResolvedValue(page([job({ status: "processing", stage: "running model", progress: 0.5, attempt: 1 })]));
    cancelJob.mockResolvedValue(job({ status: "processing", cancel_requested: true }));
    renderPage(<JobsPanel asset={asset()} />);
    const row = await screen.findByRole("listitem", { name: "Ground-image analysis job" });
    expect(within(row).getByText("Processing")).toBeInTheDocument();
    expect(within(row).getByText("Stage: running model")).toBeInTheDocument();
    expect(within(row).getByRole("progressbar", { name: "Job progress" })).toHaveAttribute("value", "0.5");
    await userEvent.click(within(row).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(cancelJob).toHaveBeenCalledWith("j1"));
  });

  it("does not invent a progress figure when progress cannot be measured", async () => {
    listJobs.mockResolvedValue(page([job({ status: "queued" })]));
    renderPage(<JobsPanel asset={asset()} />);
    const row = await screen.findByRole("listitem", { name: "Ground-image analysis job" });
    expect(within(row).getByText("Waiting for the worker")).toBeInTheDocument();
    expect(within(row).queryByRole("progressbar")).not.toBeInTheDocument();
    expect(within(row).getByText("Progress cannot be measured for this step.")).toBeInTheDocument();
  });

  it("shows a failed job's message and code, and can retry it", async () => {
    const failed = job({ status: "failed", attempt: 2, error: { code: "INFERENCE_FAILED", message: "The model failed on this image." } });
    listJobs.mockResolvedValue(page([failed]));
    retryJob.mockResolvedValue(job({ id: "j2", retry_of_job_id: "j1" }));
    renderPage(<JobsPanel asset={asset()} />);
    const row = await screen.findByRole("listitem", { name: "Ground-image analysis job" });
    expect(within(row).getByRole("alert")).toHaveTextContent("The model failed on this image.");
    expect(within(row).getByRole("alert")).toHaveTextContent("Code: INFERENCE_FAILED");
    expect(within(row).getByText(/attempt 2 of 2/)).toBeInTheDocument();
    expect(within(row).queryByRole("button", { name: "Cancel" })).not.toBeInTheDocument();
    await userEvent.click(within(row).getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(retryJob).toHaveBeenCalledWith("j1"));
  });

  it("does not offer a retry that cannot succeed", async () => {
    const unsupported = job({
      kind: "preview",
      payload: {},
      status: "failed",
      error: { code: "PREVIEW_UNSUPPORTED", message: "Rasters with complex-valued pixels cannot be shown as a picture." },
    });
    listJobs.mockResolvedValue(page([unsupported]));
    renderPage(<JobsPanel asset={asset({ modality: "overhead_raster" })} />);
    const row = await screen.findByRole("listitem", { name: "Preview job" });
    expect(within(row).getByRole("alert")).toHaveTextContent("complex-valued pixels");
    expect(within(row).queryByRole("button", { name: "Retry" })).not.toBeInTheDocument();
    expect(within(row).getByText(/no retry is offered/)).toBeInTheDocument();
  });

  it("words finding counts as a person would", async () => {
    const jobs = [
      job({ id: "j1", status: "completed", result: { run_id: "r1", observations: 0, below_min_score: 87 } }),
      job({ id: "j2", status: "needs_review", result: { run_id: "r2", observations: 1, below_min_score: 0 } }),
      job({ id: "j3", status: "completed_with_warnings", result: { run_id: "r3", observations: 3, rejected_outputs: 1 } }),
    ];
    listJobs.mockResolvedValue(page(jobs));
    renderPage(<JobsPanel asset={asset()} />);
    expect(await screen.findByText("No findings stored · 87 below the model's cutoff, not stored")).toBeInTheDocument();
    expect(screen.getByText("1 finding stored")).toBeInTheDocument();
    expect(screen.getByText("3 findings stored · 1 model output failed validation")).toBeInTheDocument();
  });

  it("states whether a cancelled job left outputs", async () => {
    listJobs.mockResolvedValue(page([job({ status: "cancelled", cancel_requested: true })]));
    renderPage(<JobsPanel asset={asset()} />);
    const row = await screen.findByRole("listitem", { name: "Ground-image analysis job" });
    expect(within(row).getByText("No outputs exist from this job.")).toBeInTheDocument();
    expect(within(row).getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });

  it("summarises a finished analysis, including what was not stored, and links to the results", async () => {
    const done = job({ status: "needs_review", progress: 1, result: { run_id: "r1", observations: 2, below_min_score: 298, rejected_outputs: 0 } });
    listJobs.mockResolvedValue(page([done, job({ id: "j0", kind: "preview", status: "completed", payload: {} })]));
    renderPage(<JobsPanel asset={asset()} />);
    const row = await screen.findByRole("listitem", { name: "Ground-image analysis job" });
    expect(within(row).getByText("Needs review")).toBeInTheDocument();
    expect(within(row).getByText("2 findings stored · 298 below the model's cutoff, not stored")).toBeInTheDocument();
    expect(within(row).getByRole("link", { name: "View results" })).toHaveAttribute("href", "/projects/p1/assets/a1/ground?run=r1");
    expect(screen.getByRole("listitem", { name: "Preview job" })).toHaveTextContent("Completed");
  });
});

function renderGround(route = "/projects/p1/assets/a1/ground") {
  return renderPage(
    <Routes>
      <Route path="/projects/:projectId" element={<Outlet context={PROJECT} />}>
        <Route path="assets/:assetId/ground" element={<GroundAnalysisPage />} />
      </Route>
    </Routes>,
    route,
  );
}

describe("GroundAnalysisPage", () => {
  const run = {
    id: "r1",
    job_id: "j1",
    project_id: "p1",
    asset_id: "a1",
    pipeline: "ground_image",
    model_name: "detector",
    model_version: "v1",
    weights_sha256: "cd".repeat(32),
    settings: { min_score: 0.5 },
    device: "cuda:0 (NVIDIA GeForce RTX 5060)",
    library_versions: {},
    summary: { below_min_score: 298 },
    started_at: "2026-10-12T00:00:00Z",
    finished_at: "2026-10-12T00:00:01Z",
  } as client.Run;

  beforeEach(() => {
    getAsset.mockResolvedValue(asset());
    getRun.mockResolvedValue(run);
    vi.mocked(client.listAssets).mockResolvedValue({ items: [asset()], next_cursor: null });
  });

  it("says when the photograph has not been analysed", async () => {
    listJobs.mockResolvedValue(page([]));
    renderGround();
    expect(await screen.findByText("This photograph has not been analysed yet")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(listObservations).not.toHaveBeenCalled();
  });

  it("draws each finding in the original's pixel grid and lists it as a prediction needing review", async () => {
    listJobs.mockResolvedValue(page([job({ status: "needs_review", result: { run_id: "r1" } })]));
    const person = view(observation(1, [400, 600, 2000, 2400], "person", 0.994));
    listObservations.mockResolvedValue([person, view(observation(2, [0, 0, 100, 50], "traffic light", 0.681))]);
    vi.mocked(client.getObservation).mockResolvedValue(detailOf(person));
    renderGround();

    const drawn = await screen.findAllByTestId("detection-box");
    expect(drawn).toHaveLength(2);
    // Source-pixel coordinates are used directly; the overlay's own coordinate system is the original image.
    expect(drawn[0]).toHaveAttribute("x", "400");
    expect(drawn[0]).toHaveAttribute("y", "600");
    expect(drawn[0]).toHaveAttribute("width", "1600");
    expect(drawn[0]).toHaveAttribute("height", "1800");
    expect(drawn[0]!.closest("svg")).toHaveAttribute("viewBox", "0 0 4000 3000");

    const rows = within(screen.getByRole("table", { name: "Model findings in this photograph" })).getAllByRole("row");
    const cells = within(rows[1]!).getAllByRole("cell");
    expect(rows[1]).toHaveTextContent("person");
    // The raw score and its calibration status are shown apart, so the number is not read as a probability.
    expect(cells[2]).toHaveTextContent("0.994");
    expect(cells[3]).toHaveTextContent("uncalibrated");
    expect(rows[1]).toHaveTextContent("Needs review");
    expect(rows[2]).toHaveTextContent("traffic light");
    expect(screen.getByRole("navigation", { name: "Breadcrumb" })).toHaveTextContent(
      "Projects/Survey/Photo Analysis/photo.jpg",
    );

    // Selecting a finding shows its details, with flags as labels (never raw codes), and the review actions
    // the state rules allow for it.
    await userEvent.click(rows[1]!);
    expect(rows[1]).toHaveAttribute("aria-selected", "true");
    const detail = (await screen.findByText("Finding 1: person")).closest("section, div[class*='rounded']")!;
    expect(detail).toHaveTextContent("Not georeferenced, Score not calibrated");
    expect(detail).not.toHaveTextContent("UNCALIBRATED_SCORE");
    expect(detail).toHaveTextContent("[400.0, 600.0, 2000.0, 2400.0] px");
    const actions = await screen.findByRole("group", { name: "Review actions" });
    expect(within(actions).getAllByRole("button").map((b) => b.textContent)).toEqual([
      "Confirm",
      "Reject",
      "Mark inconclusive",
      "Correct the category",
      "Add a note",
    ]);
    expect(client.getObservation).toHaveBeenCalledWith(person.observation.observation_id);

    expect(screen.getByText("Model predictions, not confirmed findings")).toBeInTheDocument();
    expect(screen.getByText(/no geographic location is established/)).toBeInTheDocument();
    expect(screen.getByText(/298 raw detection\(s\)/)).toBeInTheDocument();
    expect(screen.getByText("cuda:0 (NVIDIA GeForce RTX 5060)")).toBeInTheDocument();
    expect(listObservations).toHaveBeenCalledWith("p1", { run_id: "r1" });
  });

  it("shows the run a job linked to, not simply the latest", async () => {
    listJobs.mockResolvedValue(
      page([job({ id: "j2", status: "needs_review", result: { run_id: "r2" } }), job({ status: "needs_review", result: { run_id: "r1" } })]),
    );
    listObservations.mockResolvedValue([]);
    renderGround("/projects/p1/assets/a1/ground?run=r1");
    expect(await screen.findByText("No findings")).toBeInTheDocument();
    expect(listObservations).toHaveBeenCalledWith("p1", { run_id: "r1" });
    expect(screen.getByLabelText("Analysis run")).toHaveValue("r1");
  });

  it("presents zero findings as a valid result", async () => {
    listJobs.mockResolvedValue(page([job({ status: "completed", result: { run_id: "r1" } })]));
    listObservations.mockResolvedValue([]);
    renderGround();
    expect(await screen.findByText("No findings")).toBeInTheDocument();
    expect(screen.getByText(/That is a valid result, not an error/)).toBeInTheDocument();
    expect(screen.queryByTestId("detection-box")).not.toBeInTheDocument();
  });

  it("refuses to show ground-image results for another kind of input", async () => {
    getAsset.mockResolvedValue(asset({ modality: "video" }));
    listJobs.mockResolvedValue(page([]));
    renderGround();
    expect(await screen.findByRole("alert")).toHaveTextContent("this file is a different kind of input");
  });
});
