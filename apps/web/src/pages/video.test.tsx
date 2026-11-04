import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Outlet, Route, Routes } from "react-router-dom";
import { vi } from "vitest";

import * as client from "../api/client";
import { view } from "../test/fixtures";
import { renderPage } from "../test/render";
import { VideoPage } from "./VideoPage";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    listAssets: vi.fn(),
    getAsset: vi.fn(),
    listJobs: vi.fn(),
    createJob: vi.fn(),
    cancelJob: vi.fn(),
    retryJob: vi.fn(),
    getModelHealth: vi.fn(),
    getRun: vi.fn(),
    getVideoFrames: vi.fn(),
    getVideoSamplingPlan: vi.fn(),
    listObservations: vi.fn(),
  };
});
// The review panel has its own tests and its own API calls; here only what the video screen hands it matters.
vi.mock("../components/ReviewPanel", () => ({
  ReviewPanel: ({ observationId, projectId }: { observationId: string; projectId: string }) => (
    <div data-testid="review-panel">review of {observationId} in {projectId}</div>
  ),
}));

const listAssets = vi.mocked(client.listAssets);
const getAsset = vi.mocked(client.getAsset);
const listJobs = vi.mocked(client.listJobs);
const createJob = vi.mocked(client.createJob);
const getModelHealth = vi.mocked(client.getModelHealth);
const getRun = vi.mocked(client.getRun);
const getVideoFrames = vi.mocked(client.getVideoFrames);
const getVideoSamplingPlan = vi.mocked(client.getVideoSamplingPlan);
const listObservations = vi.mocked(client.listObservations);

const PROJECT = { id: "p1", name: "Survey", archived_at: null } as client.Project;
const SHA = "ab".repeat(32);

const VIDEO = {
  id: "v1", project_id: "p1", original_filename: "gate.mp4", modality: "video", detected_format: "MP4", media_type: "video/mp4", size_bytes: 4096, sha256: SHA,
  status: "validated", duplicate_of_asset_id: null, quality_flags: ["TIMESTAMP_UNKNOWN", "NOT_GEOREFERENCED"], unknowns: {}, metadata: {}, acquisition_time: null,
  uploaded_by: { id: "u1", display_name: "Asha", created_at: "2026-10-12T00:00:00Z" }, ingested_at: "2026-10-12T00:00:00Z", raster: null,
  video: { container: "mov,mp4", video_codec: "h264", width: 96, height: 72, duration_ms: 6000, avg_frame_rate: "10/1", frame_rate: 10, time_base: "1/10240", frame_count: 60, creation_time: null, has_audio: false },
  preview: { status: "ready", variants: ["thumb", "view"], job_id: "j0", error_code: null, error_message: null },
} as unknown as client.Asset;

function job(overrides: Partial<client.Job> = {}): client.Job {
  return {
    id: "j1", project_id: "p1", asset_id: "v1", kind: "analyze", status: "needs_review", stage: null, progress: null, cancel_requested: false, attempt: 1, max_attempts: 2,
    payload: { pipeline: "video", model: { name: "detector", version: "v1" }, sampling: { interval_s: 1, max_frames: 10, start_s: 0, end_s: null } },
    result: { run_id: "r1", observations: 4, frames_analysed: 3, frames_failed: 0 }, error: null, has_partial_outputs: false, retry_of_job_id: null,
    created_at: "2026-10-12T00:00:00Z", started_at: null, finished_at: null, ...overrides,
  } as client.Job;
}

function frames(extra: Partial<client.VideoFrames> = {}): client.VideoFrames {
  const frame = (frame_index: number, finding_count: number, also: number[] = []) => ({
    frame_index, timestamp_ms: frame_index * 100, sample_target_ms: frame_index * 100, also_stands_for_ms: also, width: 96, height: 72,
    sha256: String(frame_index).padStart(2, "0").repeat(32), size_bytes: 1000, finding_count,
    image_url: `/api/v1/projects/p1/assets/v1/video-analysis/runs/r1/frames/${frame_index}/image`,
  });
  return {
    run_id: "r1", asset_id: "v1", source_sha256: SHA, model_name: "detector", model_version: "v1",
    sampling: { interval_s: 1, max_frames: 10, start_s: 0, end_s: null },
    frames: [frame(0, 1), frame(10, 0, [1500]), frame(30, 3)], failed: [], findings_total: 4, frames_in_file: 60,
    policy: "Frames are taken at fixed time steps.", frame_numbering: "Frame number: 0-based position.", coordinates: "Box coordinates are in the pixel grid of the extracted frame image.",
    note: "Model predictions on sampled frames.", ...extra,
  } as client.VideoFrames;
}

function finding(id: string, frameIndex: number, label: string, score: number, status: client.ReviewStatus = "needs_review"): client.ObservationView {
  return view({
    schema_version: "3.0", observation_id: id, project_id: "p1", asset_id: "v1", pipeline: "video",
    model: { name: "detector", version: "v1", run_id: "r1" },
    claim: { category: `coco:${label}`, description: `Model-predicted '${label}'-like feature in frame ${frameIndex}.`, claim_type: "inferred" },
    score: { raw_value: score, semantics: "Class score.", calibrated: false, value_range: [0, 1] },
    evidence: [{ asset_id: "v1", region_px: { type: "bbox_xyxy", coordinates: [10, 10, 50, 40], coordinate_space: "source_image_pixels", origin: "top_left", pixel_reference: "corner", image_width: 96, image_height: 72 },
      frame_index: frameIndex, timestamp_ms: frameIndex * 100, source_excerpt_or_metadata_key: `video_frame_sha256:${"ab".repeat(32)}` }],
    geospatial: { geometry: null, crs_id: null, coordinate_units: null, derivation_method: null, uncertainty: null }, measurements: [],
    quality_flags: ["NOT_GEOREFERENCED", "UNCALIBRATED_SCORE"], review: { status: "needs_review", reviewer_note: null, reviewed_at: null, reviewed_by: null },
    provenance: { source_checksum: SHA, ingested_at: "2026-10-12T00:00:00Z", transformations: [], created_at: "2026-10-12T00:00:00Z" },
  } as unknown as client.Observation, { current: { status }, asset_filename: "gate.mp4" });
}

const ALL = [finding("o1", 0, "person", 0.91), finding("o2", 30, "car", 0.88), finding("o3", 30, "dog", 0.7), finding("o4", 30, "tree", 0.55)];

function models(over: Partial<client.ModelHealth["models"][number]> = {}, workerRunning = true): client.ModelHealth {
  return {
    manifest_found: true, errors: [], worker: { running: workerRunning, capabilities: { cuda_available: true } }, llm_runtime: { note: "" },
    models: [{ name: "detector", version: "v1", pipeline: "ground_image", task: "object_detection", runtime: "torchvision", licence: "BSD", score_semantics: "Class score after NMS.",
      score_calibrated: false, settings: {}, weights_sha256: "cd".repeat(32), weights_size_bytes: 10, available: true, reason_unavailable: null, ...over }],
  } as client.ModelHealth;
}

function plan(planned = 6): client.VideoSamplingPlan {
  return {
    sampling: { interval_s: 1, max_frames: 10, start_s: 0, end_s: null }, planned_frames: planned, wanted_ms: [0, 1000, 2000, 3000, 4000, 5000], duration_ms: 6000,
    basis: "Worked out from the container's stated duration.", min_interval_s: 0.2, max_frames_ceiling: 300, policy: "Frames are taken at fixed time steps.", warnings: [],
  } as client.VideoSamplingPlan;
}

function render(path = "/projects/p1/assets/v1/video") {
  return renderPage(
    <Routes>
      <Route path="/projects/:projectId" element={<Outlet context={PROJECT} />}>
        <Route path="assets/:assetId/video" element={<VideoPage />} />
      </Route>
    </Routes>,
    path,
  );
}

function arrange({ jobs = [job()], findings = ALL, frameData = frames() }: { jobs?: client.Job[]; findings?: client.ObservationView[]; frameData?: client.VideoFrames } = {}) {
  getAsset.mockResolvedValue(VIDEO);
  listAssets.mockResolvedValue({ items: [VIDEO], next_cursor: null } as unknown as client.AssetPage);
  listJobs.mockResolvedValue({ items: jobs, next_cursor: null } as never);
  getModelHealth.mockResolvedValue(models());
  getRun.mockResolvedValue({ id: "r1", job_id: "j1", asset_id: "v1", pipeline: "video", model_name: "detector", model_version: "v1", weights_sha256: "cd".repeat(32), settings: { min_score: 0.5 },
    device: "cuda", library_versions: { ffmpeg: "ffmpeg version 9.0.1" }, summary: {}, started_at: "2026-10-12T00:00:00Z", finished_at: "2026-10-12T00:00:09Z" } as unknown as client.Run);
  getVideoFrames.mockResolvedValue(frameData);
  getVideoSamplingPlan.mockResolvedValue(plan());
  listObservations.mockImplementation(async (_project, query) =>
    findings.filter((f) => (query.frame_index === undefined ? true : f.observation.evidence[0]?.frame_index === query.frame_index)),
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  window.HTMLMediaElement.prototype.pause = vi.fn();
});

describe("opening a video with a stored analysis", () => {
  it("shows the stored frames, timeline and findings without starting or repeating any analysis", async () => {
    arrange();
    render();
    expect(await screen.findByRole("list", { name: "Sampled frames" })).toBeInTheDocument();
    const strip = screen.getByRole("list", { name: "Sampled frames" });
    expect(within(strip).getAllByRole("listitem")).toHaveLength(3);
    expect(strip).toHaveTextContent("0:00.000");
    expect(strip).toHaveTextContent("0:03.000");
    expect(strip).toHaveTextContent("3 findings");
    expect(screen.getByRole("group", { name: "Sampled frames on the video timeline" })).toHaveTextContent("3 sampled frames, 2 with findings");
    // reading only: nothing was queued, and the results came from the stored run
    expect(createJob).not.toHaveBeenCalled();
    expect(getVideoFrames).toHaveBeenCalledWith("p1", "v1", "r1");
    await waitFor(() => expect(listObservations).toHaveBeenCalledWith("p1", { run_id: "r1", limit: 500 }));
  });

  it("shows the first frame that has findings, with its boxes, and says what a frame number and time mean", async () => {
    arrange();
    render();
    const viewer = await screen.findByTestId("frame-viewer");
    expect(await screen.findByRole("heading", { name: "Frame 0 · 0:00.000" })).toBeInTheDocument();
    expect(within(viewer).getByRole("img", { name: /Frame 0 of the video at 0:00.000/ })).toHaveAttribute("src", expect.stringContaining("/api/v1/projects/p1/assets/v1/video-analysis/runs/r1/frames/0/image?v="));
    await waitFor(() => expect(within(viewer).getAllByTestId("detection-box")).toHaveLength(1));
    expect(screen.getByText(/position in presentation order\) at 0:00.000 \(0 ms from the first frame\)/)).toBeInTheDocument();
  });

  it("says when a frame stood for several wanted times and was analysed once", async () => {
    arrange();
    render();
    await userEvent.click(await screen.findByRole("button", { name: "Select frame 10" }));
    expect(await screen.findByText(/also the frame on screen at 0:01.500, so it was analysed once/)).toBeInTheDocument();
    expect(await screen.findByText(/No findings in this frame/)).toBeInTheDocument();
  });

  it("warns that sampling can miss events, that scores are not calibrated, and that nothing is tracked or located", async () => {
    arrange();
    render();
    const warning = (await screen.findByText("Sampled frames, model predictions, not confirmed findings")).closest("[role=status]")!;
    expect(warning).toHaveTextContent(/Anything that happens between two sampled frames/);
    expect(warning).toHaveTextContent(/not calibrated and no threshold has been validated/);
    expect(warning).toHaveTextContent(/Nothing is tracked, and the same class in two frames is not claimed to be the same object/);
    expect(warning).toHaveTextContent(/No geographic location/);
  });

  it("makes no request to any other address: every frame is a local path", async () => {
    arrange();
    const { container } = render();
    await screen.findByRole("list", { name: "Sampled frames" });
    const sources = [...container.querySelectorAll("img, video")].map((el) => el.getAttribute("src") ?? "");
    expect(sources.length).toBeGreaterThan(3);
    expect(sources.every((src) => src === "" || src.startsWith("/api/v1/"))).toBe(true);
    expect([...container.querySelectorAll("a")].every((a) => !(a.getAttribute("href") ?? "").startsWith("http"))).toBe(true);
  });
});

describe("the timeline", () => {
  it("places a mark for every sampled frame at its time, and a click selects it and moves the player", async () => {
    arrange();
    const { container } = render();
    const timeline = await screen.findByRole("group", { name: "Sampled frames on the video timeline" });
    const marks = within(timeline).getAllByRole("button");
    expect(marks.map((m) => m.getAttribute("aria-label"))).toEqual([
      "Frame 0 at 0:00.000, 1 finding",
      "Frame 10 at 0:01.000, 0 findings",
      "Frame 30 at 0:03.000, 3 findings",
    ]);
    expect(marks[2]).toHaveStyle({ left: "calc(50% - 6px)" }); // 3 s of a 6 s video
    await userEvent.click(marks[2]!);
    const player = container.querySelector("video") as HTMLVideoElement;
    expect(player.currentTime).toBe(3);
    expect(await screen.findByRole("heading", { name: "Frame 30 · 0:03.000" })).toBeInTheDocument();
    expect(marks[2]).toHaveAttribute("aria-pressed", "true");
    await waitFor(() => expect(listObservations).toHaveBeenCalledWith("p1", { run_id: "r1", frame_index: 30, limit: 500 }));
    await waitFor(() => expect(screen.getAllByTestId("detection-box")).toHaveLength(3));
  });

  it("moves the playhead as the video plays, without selecting or analysing anything", async () => {
    arrange();
    const { container } = render();
    await screen.findByTestId("playhead");
    const player = container.querySelector("video") as HTMLVideoElement;
    Object.defineProperty(player, "currentTime", { value: 1.5, configurable: true });
    fireEvent.timeUpdate(player);
    expect(screen.getByTestId("playhead")).toHaveStyle({ left: "25%" });
    expect(createJob).not.toHaveBeenCalled();
  });
});

describe("findings", () => {
  it("lists every finding under the frame it belongs to, and a finding opens its frame and review", async () => {
    arrange();
    render();
    const group = await screen.findByRole("region", { name: "Findings in frame 30" });
    expect(within(group).getAllByRole("button", { name: /^Finding / })).toHaveLength(3);
    expect(screen.getByRole("region", { name: "Findings in frame 0" })).toHaveTextContent("person");
    expect(screen.getByText("4 stored in 3 sampled frames")).toBeInTheDocument();

    await userEvent.click(within(group).getByRole("button", { name: "Finding dog in frame 30 at 0:03.000" }));
    expect(await screen.findByRole("heading", { name: "Frame 30 · 0:03.000" })).toBeInTheDocument();
    expect(await screen.findByTestId("review-panel")).toHaveTextContent("review of o3 in p1"); // the existing review panel, unchanged
    const details = screen.getByRole("heading", { name: "Finding: dog" }).closest("section")!;
    expect(details).toHaveTextContent("30 at 0:03.000");
    expect(details).toHaveTextContent("Uncalibrated: not a probability");
    expect(details).toHaveTextContent(`video_frame_sha256:${"ab".repeat(32)}`);
    expect(details).toHaveTextContent("None. The box is a position in the frame image.");
    expect(within(group).getByRole("button", { name: "Finding dog in frame 30 at 0:03.000" })).toHaveAttribute("aria-pressed", "true");
  });

  it("does not suggest that findings in different frames are the same object", async () => {
    arrange();
    const { container } = render();
    await screen.findByRole("region", { name: "Findings in frame 30" });
    expect(container.textContent ?? "").not.toMatch(/same (person|car|object) as|tracked object|track \d|track id/i);
  });

  it("is honest when more findings are stored than the list can hold", async () => {
    arrange({ frameData: frames({ findings_total: 4200 }) });
    render();
    expect(await screen.findByText(/Showing the first 4 of 4200 findings, highest score first/)).toBeInTheDocument();
  });

  it("says when the model found nothing, as a result and not an error", async () => {
    arrange({ findings: [], frameData: frames({ findings_total: 0, frames: frames().frames.map((f) => ({ ...f, finding_count: 0 })) }) });
    render();
    expect(await screen.findByText("No findings in the sampled frames")).toBeInTheDocument();
    expect(screen.getByRole("list", { name: "Sampled frames" })).toBeInTheDocument(); // the frames are still there to look at
  });

  it("lists the frames that could not be taken, with the reason and code", async () => {
    arrange({ frameData: frames({ failed: [{ frame_index: 20, timestamp_ms: 2000, code: "FRAME_EXTRACTION_FAILED", message: "The frame could not be decoded from the video." }] }) });
    render();
    const failed = await screen.findByRole("list", { name: "Frames that could not be taken" });
    expect(failed).toHaveTextContent("Frame 20 at 0:02.000: The frame could not be decoded from the video. (FRAME_EXTRACTION_FAILED)");
    expect(screen.getByRole("alert")).toHaveTextContent("1 sampled frame could not be taken, so it was not analysed");
  });

  it("records how the result was produced", async () => {
    arrange();
    render();
    const panel = (await screen.findByRole("heading", { name: "How this was produced" })).closest("section")!;
    expect(panel).toHaveTextContent("detector v1");
    expect(panel).toHaveTextContent("one frame every 1 s (at most 10 frames)");
    expect(panel).toHaveTextContent("3 of the video's 60 frames");
    expect(panel).toHaveTextContent(SHA);
    expect(panel).toHaveTextContent("not a validated threshold");
    expect(panel).toHaveTextContent("ffmpeg version 9.0.1");
  });
});

describe("runs", () => {
  it("lets a person choose between runs, and reads the chosen one without re-running", async () => {
    arrange({ jobs: [job({ id: "j2", result: { run_id: "r2", observations: 0, frames_analysed: 3, frames_failed: 0 } }), job()] });
    render();
    const select = await screen.findByLabelText("Analysis run");
    expect(getVideoFrames).toHaveBeenCalledWith("p1", "v1", "r2"); // the latest run
    await userEvent.selectOptions(select, "r1");
    await waitFor(() => expect(getVideoFrames).toHaveBeenCalledWith("p1", "v1", "r1"));
    expect(createJob).not.toHaveBeenCalled();
  });

  it("opens the run a job's link names", async () => {
    arrange({ jobs: [job({ id: "j2", result: { run_id: "r2", observations: 0, frames_analysed: 3, frames_failed: 0 } }), job()] });
    render("/projects/p1/assets/v1/video?run=r1");
    await waitFor(() => expect(getVideoFrames).toHaveBeenCalledWith("p1", "v1", "r1"));
    expect(getVideoFrames).not.toHaveBeenCalledWith("p1", "v1", "r2");
  });

  it("says plainly that an unanalysed video has not been analysed, and that nothing runs until asked", async () => {
    arrange({ jobs: [] });
    render();
    expect(await screen.findByText("This video has not been analysed yet")).toBeInTheDocument();
    expect(screen.getByText(/Nothing runs until you do/)).toBeInTheDocument();
    expect(getVideoFrames).not.toHaveBeenCalled();
    expect(createJob).not.toHaveBeenCalled();
  });
});

describe("starting an analysis", () => {
  it("is explicit: it shows what the sampling would take, and nothing is queued until the button is pressed", async () => {
    arrange({ jobs: [] });
    createJob.mockResolvedValue(job({ status: "queued", result: null }));
    render();
    const form = await screen.findByRole("form", { name: "Analyse frames" });
    expect(await within(form).findByText(/6 frames would be analysed: one frame every 1 s \(at most 10 frames\)/)).toBeInTheDocument();
    expect(createJob).not.toHaveBeenCalled();
    await userEvent.click(within(form).getByRole("button", { name: "Analyse these frames" }));
    await waitFor(() => expect(createJob).toHaveBeenCalledTimes(1));
    expect(createJob).toHaveBeenCalledWith("p1", {
      kind: "analyze", asset_id: "v1", pipeline: "video", model: { name: "detector", version: "v1" },
      sampling: { interval_s: 5, max_frames: 60, start_s: 0 }, submit: true,
    });
  });

  it("sends the sampling the person typed, including a range", async () => {
    arrange({ jobs: [] });
    createJob.mockResolvedValue(job({ status: "queued", result: null }));
    render();
    const form = await screen.findByRole("form", { name: "Analyse frames" });
    const set = async (label: string, value: string) => {
      await userEvent.clear(within(form).getByLabelText(label));
      await userEvent.type(within(form).getByLabelText(label), value);
    };
    await set("Take a frame every (seconds)", "2");
    await set("At most this many frames", "20");
    await set("From (seconds, optional)", "1");
    await set("To (seconds, optional)", "5.5");
    await waitFor(() => expect(getVideoSamplingPlan).toHaveBeenLastCalledWith("p1", "v1", { interval_s: 2, max_frames: 20, start_s: 1, end_s: 5.5 }));
    await waitFor(() => expect(within(form).getByRole("button", { name: "Analyse these frames" })).toBeEnabled());
    await userEvent.click(within(form).getByRole("button", { name: "Analyse these frames" }));
    await waitFor(() => expect(createJob).toHaveBeenCalled());
    expect(createJob.mock.calls[0]![1].sampling).toEqual({ interval_s: 2, max_frames: 20, start_s: 1, end_s: 5.5 });
  });

  it("refuses a sampling the server refuses, shows why, and cannot be started", async () => {
    arrange({ jobs: [] });
    getVideoSamplingPlan.mockRejectedValue(new client.ApiError(422, "SAMPLING_EXCEEDS_LIMIT", "This sampling is not allowed", "An interval of 0.5 s needs 13 frames, more than the limit of 10."));
    render();
    const form = await screen.findByRole("form", { name: "Analyse frames" });
    expect(await within(form).findByText("This sampling cannot be used")).toBeInTheDocument();
    expect(within(form).getByText(/more than the limit of 10/)).toBeInTheDocument();
    expect(within(form).getByText(/SAMPLING_EXCEEDS_LIMIT/)).toBeInTheDocument();
    expect(within(form).getByRole("button", { name: "Analyse these frames" })).toBeDisabled();
    await userEvent.click(within(form).getByRole("button", { name: "Analyse these frames" }));
    expect(createJob).not.toHaveBeenCalled();
  });

  it("cannot be started with an empty or invalid interval", async () => {
    arrange({ jobs: [] });
    render();
    const form = await screen.findByRole("form", { name: "Analyse frames" });
    await userEvent.clear(within(form).getByLabelText("Take a frame every (seconds)"));
    expect(within(form).getByText("Enter an interval and a maximum number of frames.")).toBeInTheDocument();
    expect(within(form).getByRole("button", { name: "Analyse these frames" })).toBeDisabled();
    await userEvent.type(within(form).getByLabelText("Take a frame every (seconds)"), "abc");
    await userEvent.clear(within(form).getByLabelText("At most this many frames"));
    await userEvent.type(within(form).getByLabelText("At most this many frames"), "2.5");
    expect(within(form).getByRole("button", { name: "Analyse these frames" })).toBeDisabled();
    expect(createJob).not.toHaveBeenCalled();
  });

  it("explains an unavailable model or a missing worker, and shows the server's refusal if one comes back", async () => {
    arrange({ jobs: [] });
    getModelHealth.mockResolvedValue(models({ available: false, reason_unavailable: "The model weights are not installed on this machine." }));
    render();
    const form = await screen.findByRole("form", { name: "Analyse frames" });
    expect(await within(form).findByText("This model cannot be run")).toBeInTheDocument();
    expect(within(form).getByRole("button", { name: "Analyse these frames" })).toBeDisabled();
  });

  it("tells the person when no worker is running", async () => {
    arrange({ jobs: [] });
    getModelHealth.mockResolvedValue(models({}, false));
    render();
    expect(await screen.findByText("No worker is running")).toBeInTheDocument();
  });

  it("shows a refusal from the server", async () => {
    arrange({ jobs: [] });
    createJob.mockRejectedValue(new client.ApiError(409, "JOB_ALREADY_ACTIVE", "The same job is already queued or running", "See job j9."));
    render();
    const form = await screen.findByRole("form", { name: "Analyse frames" });
    await waitFor(() => expect(within(form).getByRole("button", { name: "Analyse these frames" })).toBeEnabled());
    await userEvent.click(within(form).getByRole("button", { name: "Analyse these frames" }));
    expect(await screen.findByText("The same job is already queued or running")).toBeInTheDocument();
    expect(screen.getByText(/JOB_ALREADY_ACTIVE/)).toBeInTheDocument();
  });
});

describe("progress, completion and failure", () => {
  it("shows a running analysis with its stage and progress, and no results yet", async () => {
    arrange({ jobs: [job({ status: "processing", stage: "running model", progress: 0.42, result: null })] });
    render();
    const latest = await screen.findByRole("list", { name: "Latest analysis job" });
    expect(latest).toHaveTextContent("Video-frame analysis");
    expect(latest).toHaveTextContent("Stage: running model");
    expect(within(latest).getByLabelText("Job progress")).toHaveAttribute("value", "0.42");
    expect(screen.getByText("The analysis is running")).toBeInTheDocument();
    expect(getVideoFrames).not.toHaveBeenCalled();
  });

  it("summarises a finished analysis with the frame counts and links to its own run", async () => {
    arrange({ jobs: [job({ status: "completed_with_warnings", result: { run_id: "r1", observations: 4, frames_analysed: 5, frames_failed: 1 } })] });
    render();
    const latest = await screen.findByRole("list", { name: "Latest analysis job" });
    expect(latest).toHaveTextContent("Completed with warnings");
    expect(latest).toHaveTextContent("4 findings stored · 5 frames analysed · 1 frame could not be taken");
    expect(within(latest).getByRole("link", { name: "View results" })).toHaveAttribute("href", "/projects/p1/assets/v1/video?run=r1");
  });

  it("shows a failure in words with its code, and offers no retry that would end the same way", async () => {
    arrange({ jobs: [job({ status: "failed", result: null, error: { code: "SAMPLING_EXCEEDS_LIMIT", message: "An interval of 0.2 s needs 31 frames, more than the limit of 10." } })] });
    render();
    const latest = await screen.findByRole("list", { name: "Latest analysis job" });
    expect(within(latest).getByRole("alert")).toHaveTextContent("more than the limit of 10");
    expect(latest).toHaveTextContent("Code: SAMPLING_EXCEEDS_LIMIT");
    expect(latest).toHaveTextContent("Running this again would end the same way, so no retry is offered.");
    expect(within(latest).queryByRole("button", { name: "Retry" })).not.toBeInTheDocument();
  });

  it("offers a retry for a failure that may not repeat, such as missing tooling", async () => {
    arrange({ jobs: [job({ status: "failed", result: null, error: { code: "MEDIA_TOOLING_UNAVAILABLE", message: "ffmpeg is not installed or not on PATH." } })] });
    render();
    const latest = await screen.findByRole("list", { name: "Latest analysis job" });
    expect(latest).toHaveTextContent("ffmpeg is not installed");
    expect(within(latest).getByRole("button", { name: "Retry" })).toBeEnabled();
  });
});
