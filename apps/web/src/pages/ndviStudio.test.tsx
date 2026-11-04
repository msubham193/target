import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Outlet, Route, Routes } from "react-router-dom";
import { vi } from "vitest";

import * as client from "../api/client";
import type { MapCanvasProps } from "../components/MapCanvas";
import { POLL_MS } from "../lib/indices";
import { renderPage } from "../test/render";
import { NdviStudioPage } from "./NdviStudioPage";

const drawn: MapCanvasProps[] = [];
vi.mock("../components/MapCanvas", () => ({
  MapCanvas: (props: MapCanvasProps) => {
    drawn.push(props);
    return <div data-testid="map" />;
  },
}));

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    listIndices: vi.fn(),
    listIndexInputs: vi.fn(),
    listIndexRuns: vi.fn(),
    startIndexRun: vi.fn(),
    cancelIndexRun: vi.fn(),
    getLayers: vi.fn(),
    getHealth: vi.fn(),
  };
});

const listIndices = vi.mocked(client.listIndices);
const listIndexInputs = vi.mocked(client.listIndexInputs);
const listIndexRuns = vi.mocked(client.listIndexRuns);
const startIndexRun = vi.mocked(client.startIndexRun);
const cancelIndexRun = vi.mocked(client.cancelIndexRun);
const getLayers = vi.mocked(client.getLayers);
const getHealth = vi.mocked(client.getHealth);

const PROJECT = { id: "p1", name: "Coast", archived_at: null } as client.Project;
const SCENE_ID = "852a5c0b14886502";
const ASSET_ID = "a-ndvi";

const DISPLAY = {
  colormap: "ndvi",
  title: "NDVI",
  low: -0.2,
  high: 0.9,
  stops: [
    { value: -0.2, color: "#3b5b92" },
    { value: 0, color: "#e8dfc8" },
    { value: 0.9, color: "#0b5d2a" },
  ],
};

function band(role: "red" | "nir", overrides: Partial<client.IndexScene["bands"][number]> = {}): client.IndexScene["bands"][number] {
  const isRed = role === "red";
  return {
    role,
    band: isRed ? "B04" : "B08",
    file_name: isRed ? "T55HGS_20240115T235221_B04_10m.jp2" : "T55HGS_20240115T235221_B08_10m.jp2",
    found: true,
    readable: true,
    width: 10980,
    height: 10980,
    bands: 1,
    dtype: "uint16",
    crs: "EPSG:32755",
    pixel_size_m: [10, 10],
    scaling: { band: isRed ? "B4" : "B8", add_offset: -1000, quantification_value: 10000, nodata_dn: 0, saturated_dn: 65535 },
    problems: [],
    ...overrides,
  };
}

function scene(overrides: Partial<client.IndexScene> = {}): client.IndexScene {
  return {
    scene_id: SCENE_ID,
    product_uri: "S2A_MSIL2A_20240115T235221_N0510_R130_T55HGS_20240116T021554.SAFE",
    tile_id: "T55HGS",
    acquisition_start: "2024-01-15T23:52:21.024Z",
    processing_baseline: "05.10",
    spacecraft: "Sentinel-2A",
    resolution_m: 10,
    bands: [band("nir"), band("red")],
    compatible: true,
    problems: [],
    ...overrides,
  };
}

function inputs(overrides: Partial<client.IndexInputs> = {}): client.IndexInputs {
  return { index: "ndvi", configured: true, scenes: [scene()], note: "Scenes found.", ...overrides };
}

const RESULT: client.IndexRunResult = {
  derived_asset_id: ASSET_ID,
  duplicate_of_asset_id: null,
  index: "ndvi",
  scene_id: SCENE_ID,
  file_name: "T55HGS_20240115T235221_NDVI_10m.tif",
  output_sha256: "f".repeat(64),
  size_bytes: 523771724,
  width: 10980,
  height: 10980,
  crs: "EPSG:32755",
  resolution_m: 10,
  nodata: -9999,
  formula: "(NIR - Red) / (NIR + Red)",
  range_mask: [-1, 1],
  statistics: {
    min: -1,
    max: 0.9885714,
    mean: -0.013989,
    std: 0.054114,
    percentiles: { "1": -0.1705, "50": 0, "99": 0.1057 },
    percentile_resolution: 0.0001,
  },
  counts: {
    pixels: 120560400,
    valid_before_range_mask: 117518474,
    masked_out_of_range: 22081,
    valid: 117496393,
    invalid: 3064007,
    input_nodata: 3041524,
    input_saturated: 0,
    denominator_near_zero: 402,
    non_finite_result: 0,
    valid_negative: 58688829,
  },
  inputs: [
    { role: "nir", band: "B08", file_name: "T55HGS_20240115T235221_B08_10m.jp2", sha256: "09a91bd2c92c20e16eea2277dd34fc89f02a7c5453a290933f1684f7143e4e6b", scaling: null },
    { role: "red", band: "B04", file_name: "T55HGS_20240115T235221_B04_10m.jp2", sha256: "9ec59e43956e73cd096702a48fbc2fed8353a9bbe3a029942439587423965384", scaling: null },
  ],
  product: {
    uri: "S2A_MSIL2A_20240115T235221_N0510_R130_T55HGS_20240116T021554.SAFE",
    tile_id: "T55HGS",
    acquisition_start: "2024-01-15T23:52:21.024Z",
    processing_baseline: "05.10",
  },
  validation: { checks: 18, passed: 18, failures: [] },
};

function run(overrides: Partial<client.IndexRun> = {}): client.IndexRun {
  return {
    job_id: "j1",
    index: "ndvi",
    scene_id: SCENE_ID,
    status: "queued",
    stage: null,
    progress: null,
    cancel_requested: false,
    attempt: 0,
    max_attempts: 3,
    created_at: "2026-11-03T10:00:00Z",
    started_at: null,
    finished_at: null,
    error: null,
    inputs: [],
    range_mask: [-1, 1],
    asset_id: null,
    result: null,
    display: DISPLAY,
    ...overrides,
  };
}

const COMPLETED = run({ status: "completed", progress: 1, asset_id: ASSET_ID, result: RESULT, started_at: "2026-11-03T10:00:05Z", finished_at: "2026-11-03T10:01:40Z" });

const LAYERS = {
  rasters: [
    {
      asset_id: ASSET_ID,
      title: "ndvi",
      georef_version: 0,
      bounds_4326: [149.3, -39.8, 150.6, -38.8],
      native_zoom: 14,
      tile_url: null,
      display_unavailable_reason: "Band statistics are not ready.",
    },
  ],
  excluded: [],
  aoi: null,
} as unknown as client.Layers;

const lastRaster = () => drawn.at(-1)!.rasters[0]!;

function renderStudio() {
  return renderPage(
    <Routes>
      <Route element={<Outlet context={PROJECT} />}>
        <Route path="/" element={<NdviStudioPage />} />
      </Route>
    </Routes>,
  );
}

function health(revision: string | null = "0010"): client.Health {
  return {
    status: "ok",
    database: { reachable: true, postgis_version: "3.5.7", schema_revision: revision, name: "geopoc", environment: "working", role_is_superuser: true },
    worker: { running: true, worker_id: "w1", last_seen_at: "2026-11-03T10:00:00Z", note: "" },
  } as unknown as client.Health;
}

beforeEach(() => {
  vi.clearAllMocks();
  drawn.length = 0;
  getHealth.mockResolvedValue(health());
  listIndices.mockResolvedValue({
    indices: [
      {
        key: "ndvi",
        title: "NDVI",
        formula: "(NIR - Red) / (NIR + Red)",
        inputs: [],
        output: "",
        analytical_range: [-1, 1],
        display: DISPLAY,
      },
    ],
  });
  listIndexInputs.mockResolvedValue(inputs());
  listIndexRuns.mockResolvedValue({ runs: [] });
  getLayers.mockResolvedValue(LAYERS);
});

describe("the inputs", () => {
  it("shows the red and near-infrared bands as separate cards with file, resolution, size, CRS and acquisition date", async () => {
    renderStudio();
    const list = await screen.findByRole("list", { name: "Input bands" });
    const [red, nir] = within(list).getAllByRole("listitem");
    expect(red).toHaveTextContent("Red band");
    expect(red).toHaveTextContent("B04");
    expect(red).toHaveTextContent("T55HGS_20240115T235221_B04_10m.jp2");
    expect(nir).toHaveTextContent("Near-infrared band");
    expect(nir).toHaveTextContent("B08");
    for (const card of [red, nir]) {
      expect(card).toHaveTextContent("Readable");
      expect(card).toHaveTextContent("Resolution10 m");
      expect(card).toHaveTextContent("Dimensions10,980 × 10,980 px");
      expect(card).toHaveTextContent("CRSEPSG:32755");
      expect(card).toHaveTextContent("Acquired2024-01-15 23:52:21 UTC");
    }
    expect(screen.getByText("Compatible")).toBeInTheDocument();
    expect(screen.getByText(/Same grid \(10 m, EPSG:32755\)/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Calculate NDVI" })).toBeEnabled();
  });

  it("puts the compatibility result above the band cards", async () => {
    renderStudio();
    const list = await screen.findByRole("list", { name: "Input bands" });
    const banner = screen.getByText("Compatible");
    expect(banner.compareDocumentPosition(list) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("states the formula, scaling, nodata handling, masking and resolution before anything is run", async () => {
    renderStudio();
    await screen.findByRole("list", { name: "Input bands" });
    expect(screen.getByText("NDVI = (NIR - Red) / (NIR + Red)")).toBeInTheDocument();
    expect(screen.getByText("reflectance = (DN + offset) ÷ 10,000")).toBeInTheDocument();
    expect(screen.getByText("B04: offset -1000")).toBeInTheDocument();
    expect(screen.getByText("B08: offset -1000")).toBeInTheDocument();
    expect(screen.getByText("Read per band from the product metadata, not assumed.")).toBeInTheDocument();
    expect(screen.getByText(/DN 0 \(NODATA\) or 65,535 \(SATURATED\) in either band/)).toBeInTheDocument();
    expect(screen.getByText(/Values outside \[-1, 1\] become nodata\. They are masked, not clamped/)).toBeInTheDocument();
    expect(screen.getByText(/10 m, the bands' own grid/)).toBeInTheDocument();
  });

  it("keeps the left column between 340 and 380 px and lets the map take the rest", async () => {
    const { container } = renderStudio();
    await screen.findByRole("list", { name: "Input bands" });
    const layout = container.querySelector("[class*='grid-cols-[minmax(340px']") as HTMLElement;
    expect(layout.className).toContain("min-[1180px]:grid-cols-[minmax(340px,380px)_minmax(0,1fr)]"); // 340px to 380px in px, then 1fr
    expect(layout.className).not.toMatch(/(^| )grid-cols-\[/); // a single column below the breakpoint
  });

  it("shows why incompatible bands cannot be processed and keeps the action off", async () => {
    listIndexInputs.mockResolvedValue(
      inputs({
        scenes: [
          scene({
            compatible: false,
            problems: ["The sizes differ (10980 × 10980 and 10972 × 10980 pixels)."],
            bands: [band("nir"), band("red", { found: false, readable: false, file_name: null, problems: ["No local file named T55HGS_B04_10m.jp2 (or .tif) was found."] })],
          }),
        ],
      }),
    );
    renderStudio();
    expect(await screen.findByText("These bands cannot be combined")).toBeInTheDocument();
    expect(screen.getByText(/The sizes differ/)).toBeInTheDocument();
    expect(screen.getAllByText(/No local file named/).length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "Calculate NDVI" })).toBeDisabled();
    expect(screen.getByText("The bands of this scene cannot be combined.")).toBeInTheDocument();
  });

  it("explains an unconfigured band folder by the setting's name", async () => {
    listIndexInputs.mockResolvedValue(inputs({ configured: false, scenes: [] }));
    renderStudio();
    expect(await screen.findByText("No band folder is configured")).toBeInTheDocument();
    expect(screen.getByText("GEOPOC_IMAGERY_BAND_ROOT")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Calculate NDVI" })).not.toBeInTheDocument();
  });

  it("says so when the folder holds no scene", async () => {
    listIndexInputs.mockResolvedValue(inputs({ scenes: [] }));
    renderStudio();
    expect(await screen.findByText("No Sentinel-2 scene was found")).toBeInTheDocument();
  });

  it("offers a choice when there are several scenes and starts the one chosen", async () => {
    const user = userEvent.setup();
    listIndexInputs.mockResolvedValue(
      inputs({ scenes: [scene(), scene({ scene_id: "1111111111111111", acquisition_start: "2023-06-01T00:00:00Z" })] }),
    );
    startIndexRun.mockResolvedValue(run());
    renderStudio();
    await user.selectOptions(await screen.findByLabelText("Scene"), "1111111111111111");
    await user.click(screen.getByRole("button", { name: "Calculate NDVI" }));
    await waitFor(() => expect(startIndexRun).toHaveBeenCalledWith("p1", "1111111111111111", "ndvi"));
  });

  it("shows a failure to load as an error, not an empty screen", async () => {
    listIndexInputs.mockRejectedValue(new client.ApiError(0, "API_UNREACHABLE", "The local API is not reachable"));
    renderStudio();
    expect(await screen.findByText("NDVI Studio could not load")).toBeInTheDocument();
    expect(screen.getByText(/API_UNREACHABLE/)).toBeInTheDocument();
  });
});

describe("a run", () => {
  it("is started with the scene's opaque id only, and shown as queued", async () => {
    const user = userEvent.setup();
    startIndexRun.mockImplementation(async () => {
      listIndexRuns.mockResolvedValue({ runs: [run()] });
      return run();
    });
    renderStudio();
    await user.click(await screen.findByRole("button", { name: "Calculate NDVI" }));
    expect(startIndexRun).toHaveBeenCalledWith("p1", SCENE_ID, "ndvi");
    expect(await screen.findByText("Queued", { selector: "span" })).toBeInTheDocument();
    expect(screen.getByText("Waiting for the worker to pick this up.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Calculate NDVI" })).toBeDisabled(); // one run at a time
  });

  it("follows the job's real status and progress until it completes, then shows the result", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      const states = [
        run(),
        run({ status: "processing", stage: "calculating NDVI", progress: 0.42, started_at: "2026-11-03T10:00:05Z", attempt: 1 }),
        COMPLETED,
      ];
      let call = 0;
      listIndexRuns.mockImplementation(async () => ({ runs: call === 0 ? [] : [states[Math.min(call - 1, 2)] as client.IndexRun] }));
      startIndexRun.mockImplementation(async () => {
        call = 1; // from now on the queue holds the new run, so the refetch that follows the start sees it
        return run();
      });
      renderStudio();
      await user.click(await screen.findByRole("button", { name: "Calculate NDVI" }));
      expect(await screen.findByText("Queued", { selector: "span" })).toBeInTheDocument();

      call = 2;
      await act(async () => {
        await vi.advanceTimersByTimeAsync(POLL_MS + 100);
      });
      expect(await screen.findByText("Running")).toBeInTheDocument();
      const bar = screen.getByRole("progressbar", { name: "NDVI run progress" });
      expect(bar).toHaveAttribute("aria-valuenow", "42");
      expect(screen.getByText("42% done")).toBeInTheDocument();
      expect(screen.getByText("calculating NDVI")).toBeInTheDocument();

      call = 3;
      await act(async () => {
        await vi.advanceTimersByTimeAsync(POLL_MS + 100);
      });
      expect(await screen.findByText("Completed")).toBeInTheDocument();
      expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
      expect(await screen.findByText("Statistics of valid pixels")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Calculate NDVI" })).toBeEnabled();
    } finally {
      vi.useRealTimers();
    }
  });

  it("does not invent progress when the job reports none", async () => {
    listIndexRuns.mockResolvedValue({ runs: [run({ status: "processing", stage: "checking the inputs", progress: null })] });
    renderStudio();
    const bar = await screen.findByRole("progressbar", { name: "NDVI run progress" });
    expect(bar).not.toHaveAttribute("aria-valuenow");
    expect(screen.getByText("Working…")).toBeInTheDocument();
  });

  it("shows a failed run's message and code", async () => {
    listIndexRuns.mockResolvedValue({
      runs: [run({ status: "failed", error: { code: "INPUTS_INCOMPATIBLE", message: "The sizes differ (10980 × 10980 and 10972 × 10980 pixels)." } })],
    });
    renderStudio();
    expect(await screen.findByText("The run failed")).toBeInTheDocument();
    expect(screen.getByText(/The sizes differ/, { selector: "p" })).toBeInTheDocument();
    expect(screen.getByText("Code: INPUTS_INCOMPATIBLE")).toBeInTheDocument();
    expect(screen.getByText("Failed")).toBeInTheDocument();
  });

  it("reports a refused start (a run already active) with the server's reason", async () => {
    const user = userEvent.setup();
    startIndexRun.mockRejectedValue(
      new client.ApiError(409, "RUN_ALREADY_ACTIVE", "This scene is already being processed", "Run j0 is processing."),
    );
    renderStudio();
    await user.click(await screen.findByRole("button", { name: "Calculate NDVI" }));
    expect(await screen.findByText("The run could not be started")).toBeInTheDocument();
    expect(screen.getByText("Run j0 is processing.")).toBeInTheDocument();
    expect(screen.getByText(/RUN_ALREADY_ACTIVE/)).toBeInTheDocument();
  });

  it("can be cancelled while it runs", async () => {
    const user = userEvent.setup();
    listIndexRuns.mockResolvedValue({ runs: [run({ status: "processing", progress: 0.3, stage: "calculating NDVI" })] });
    cancelIndexRun.mockResolvedValue(run({ status: "processing", cancel_requested: true }));
    renderStudio();
    await user.click(await screen.findByRole("button", { name: "Cancel run" }));
    await waitFor(() => expect(cancelIndexRun).toHaveBeenCalledWith("p1", "j1", "ndvi"));
  });

  it("says a cancelled run kept nothing", async () => {
    listIndexRuns.mockResolvedValue({ runs: [run({ status: "cancelled" })] });
    renderStudio();
    expect(await screen.findByText("Cancelled. Nothing from this run was kept.")).toBeInTheDocument();
  });
});

describe("the result", () => {
  beforeEach(() => {
    listIndexRuns.mockResolvedValue({ runs: [COMPLETED] });
  });

  it("shows the statistics, the pixel counts including the masked ones, the raster and the provenance", async () => {
    renderStudio();
    const stats = await screen.findByRole("region", { name: "Statistics" });
    expect(stats).toHaveTextContent("Minimum-1.0000");
    expect(stats).toHaveTextContent("Maximum0.9886");
    expect(stats).toHaveTextContent("Mean-0.0140");
    expect(stats).toHaveTextContent("Std deviation0.0541");
    expect(stats).toHaveTextContent("Valid117,496,393");
    expect(stats).toHaveTextContent("Invalid (nodata)3,064,007");
    expect(stats).toHaveTextContent("Masked: outside the range22,081");
    expect(stats).toHaveTextContent("Near-zero denominator402");
    const raster = screen.getByRole("region", { name: "Raster and provenance" });
    expect(raster).toHaveTextContent("Resolution10 m");
    expect(raster).toHaveTextContent("CRSEPSG:32755");
    expect(raster).toHaveTextContent("Acquisition2024-01-15 23:52:21 UTC");
    const provenance = within(screen.getByRole("list", { name: "Input provenance" })).getAllByRole("listitem");
    expect(provenance[0]).toHaveTextContent("T55HGS_20240115T235221_B08_10m.jp2");
    expect(provenance[0]).toHaveTextContent("SHA-256 09a91bd2c92c…");
    expect(provenance[1]).toHaveTextContent("9ec59e43956e…");
    expect(screen.getByRole("link", { name: "Open the stored raster" })).toHaveAttribute("href", `/projects/p1/assets/${ASSET_ID}`);
  });

  it("draws the stored raster through the NDVI ramp using the existing tile route", async () => {
    renderStudio();
    await screen.findByTestId("map");
    const raster = lastRaster();
    expect(raster.layer.asset_id).toBe(ASSET_ID);
    expect(raster.layer.tile_url).toBe(`/api/v1/assets/${ASSET_ID}/tiles/{z}/{x}/{y}.png?v=0`);
    expect(raster.query).toBe("colormap=ndvi&colormap_range=-0.2,0.9");
    expect(raster.visible).toBe(true);
    expect(drawn.at(-1)!.fitTo).toEqual([149.3, -39.8, 150.6, -38.8]);
    expect(JSON.stringify(drawn.at(-1)!.rasters)).not.toMatch(/scratchpad|C:\\|file:/);
  });

  it("switches the NDVI layer off and on", async () => {
    const user = userEvent.setup();
    renderStudio();
    await screen.findByTestId("map");
    const toggle = screen.getByRole("checkbox", { name: "NDVI layer" });
    expect(toggle).toBeChecked();
    await user.click(toggle);
    await waitFor(() => expect(lastRaster().visible).toBe(false));
    await user.click(toggle);
    await waitFor(() => expect(lastRaster().visible).toBe(true));
  });

  it("changes the layer's opacity without touching its colours", async () => {
    renderStudio();
    await screen.findByTestId("map");
    const slider = screen.getByRole("slider", { name: "NDVI layer opacity" });
    await act(async () => {
      slider.focus();
    });
    const { fireEvent } = await import("@testing-library/react");
    fireEvent.change(slider, { target: { value: "50" } });
    await waitFor(() => expect(lastRaster().opacity).toBe(0.5));
    expect(lastRaster().query).toBe("colormap=ndvi&colormap_range=-0.2,0.9");
  });

  it("shows a legend with negative, zero and positive values drawn from the API's stops", async () => {
    renderStudio();
    const legend = await screen.findByRole("group", { name: "NDVI legend" });
    expect(legend).toHaveTextContent("-0.2");
    expect(legend).toHaveTextContent("negative");
    expect(legend).toHaveTextContent("zero");
    expect(legend).toHaveTextContent("0.9");
    expect(legend).toHaveTextContent("positive");
    expect(legend).toHaveTextContent("Nodata is transparent");
    expect(legend).toHaveTextContent("The colours are a display choice; the stored values are unchanged.");
    const bar = legend.querySelector("[style*='linear-gradient']") as HTMLElement;
    expect(bar.style.backgroundImage).toContain("rgb(59, 91, 146)"); // #3b5b92, the first stop
    expect(bar.style.backgroundImage).toContain("rgb(11, 93, 42)"); // #0b5d2a, the last stop
  });

  it("explains a raster that is stored but cannot be placed on the map", async () => {
    getLayers.mockResolvedValue({ ...LAYERS, rasters: [] } as unknown as client.Layers);
    renderStudio();
    expect(await screen.findByText("This raster cannot be placed on the map")).toBeInTheDocument();
    expect(screen.queryByTestId("map")).not.toBeInTheDocument();
  });

  it("keeps showing the newest completed result while a later run is in progress", async () => {
    listIndexRuns.mockResolvedValue({ runs: [run({ job_id: "j2", status: "processing", progress: 0.1 }), COMPLETED] });
    renderStudio();
    expect(await screen.findByText("Statistics of valid pixels")).toBeInTheDocument();
    expect(screen.getByRole("progressbar", { name: "NDVI run progress" })).toHaveAttribute("aria-valuenow", "10");
  });

  it("shows a meaningful map placeholder and a structured results empty state before a run has completed", async () => {
    listIndexRuns.mockResolvedValue({ runs: [] });
    renderStudio();
    expect(await screen.findByText(/will be drawn here once a run completes/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "NDVI layer" })).toBeInTheDocument();
    expect(screen.getByRole("group", { name: "NDVI colour ramp preview" })).toHaveTextContent("negative");
    expect(screen.queryByRole("group", { name: "NDVI legend" })).not.toBeInTheDocument();
    expect(screen.queryByTestId("map")).not.toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "NDVI layer" })).toBeDisabled();
    expect(screen.getByRole("heading", { name: "No analytical NDVI result is registered yet" })).toBeInTheDocument();
    const readiness = within(screen.getByRole("list", { name: "Readiness" }));
    expect(readiness.getByText("Input imagery has already passed the compatibility checks.")).toBeInTheDocument();
    expect(readiness.getByText("Processing setup is ready. Waiting for a run to be started.")).toBeInTheDocument();
    expect(readiness.getByText(/will be masked as nodata, not clamped/)).toBeInTheDocument();
    expect(within(screen.getByRole("list", { name: "What the results will show" })).getAllByRole("listitem")).toHaveLength(4);
  });

  it("invents no statistics before there is a result", async () => {
    listIndexRuns.mockResolvedValue({ runs: [] });
    const { container } = renderStudio();
    await screen.findByText("No analytical NDVI result is registered yet");
    expect(screen.queryByRole("region", { name: "Statistics" })).not.toBeInTheDocument();
    expect(screen.queryByText("Minimum")).not.toBeInTheDocument();
    expect(container.textContent).not.toMatch(/\d+\.\d{4}|Std deviation|Masked: outside/);
  });

  it("says the imagery has not passed the checks when it has not", async () => {
    listIndexRuns.mockResolvedValue({ runs: [] });
    listIndexInputs.mockResolvedValue(inputs({ scenes: [scene({ compatible: false, problems: ["The sizes differ."] })] }));
    renderStudio();
    expect(await screen.findByText("Input imagery has not passed the compatibility checks.")).toBeInTheDocument();
  });

  it("never shows a filesystem path anywhere on the page", async () => {
    const { container } = renderStudio();
    await screen.findByText("Statistics of valid pixels");
    expect(container.textContent).not.toMatch(/scratchpad|AppData|C:\\|\/Users\//);
  });
});

describe("while the database setup is pending (schema older than the job kind)", () => {
  beforeEach(() => {
    getHealth.mockResolvedValue(health("0009"));
  });

  it("explains the state in plain words and names it, before anything is submitted", async () => {
    renderStudio();
    expect((await screen.findAllByText("Processing setup pending")).length).toBeGreaterThanOrEqual(2);
    expect(
      screen.getByText(
        "NDVI processing is temporarily unavailable because the database job configuration has not yet been upgraded. Your selected imagery is compatible and no source data has been modified.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("You can still inspect the selected inputs. No run was requested.")).toBeInTheDocument();
    expect(screen.getByText("No run can start until the database setup is upgraded. Nothing has been submitted.")).toBeInTheDocument();
  });

  it("disables Calculate NDVI and never calls the run route", async () => {
    const user = userEvent.setup();
    renderStudio();
    const button = await screen.findByRole("button", { name: "Calculate NDVI" });
    await waitFor(() => expect(button).toBeDisabled());
    await user.click(button);
    await user.click(button);
    expect(startIndexRun).not.toHaveBeenCalled();
    expect(listIndexRuns).toHaveBeenCalledTimes(1); // nor does the page poll the run route because of it
  });

  it("still shows the selected inputs and their compatibility", async () => {
    renderStudio();
    const list = await screen.findByRole("list", { name: "Input bands" });
    expect(within(list).getAllByRole("listitem")).toHaveLength(2);
    expect(screen.getByText("Compatible")).toBeInTheDocument();
    expect(screen.getByText("T55HGS_20240115T235221_B04_10m.jp2")).toBeInTheDocument();
  });

  it("does not claim the imagery is compatible when it is not", async () => {
    listIndexInputs.mockResolvedValue(inputs({ scenes: [scene({ compatible: false, problems: ["The sizes differ."] })] }));
    renderStudio();
    expect(await screen.findByText(/database job configuration has not yet been upgraded\./)).toBeInTheDocument();
    expect(screen.queryByText(/Your selected imagery is compatible/)).not.toBeInTheDocument();
  });

  it("marks the results checklist as pending for the same reason", async () => {
    renderStudio();
    expect(await screen.findByText(/the database job configuration is not upgraded yet/)).toBeInTheDocument();
  });

  it("does not hide a result that already exists", async () => {
    listIndexRuns.mockResolvedValue({ runs: [COMPLETED] });
    renderStudio();
    expect(await screen.findByText("Statistics of valid pixels")).toBeInTheDocument();
  });
});

describe("when the setup state cannot be established or is fine", () => {
  it("does not block when the health check fails", async () => {
    getHealth.mockRejectedValue(new client.ApiError(0, "API_UNREACHABLE", "The local API is not reachable"));
    renderStudio();
    expect(await screen.findByRole("button", { name: "Calculate NDVI" })).toBeEnabled();
    expect(screen.queryByText("Processing setup pending")).not.toBeInTheDocument();
  });

  it("does not block when the revision is not reported", async () => {
    getHealth.mockResolvedValue(health(null));
    renderStudio();
    expect(await screen.findByRole("button", { name: "Calculate NDVI" })).toBeEnabled();
    expect(screen.queryByText("Processing setup pending")).not.toBeInTheDocument();
  });

  it("is ready at the revision that added the job kind", async () => {
    getHealth.mockResolvedValue(health("0010"));
    renderStudio();
    expect(await screen.findByRole("button", { name: "Calculate NDVI" })).toBeEnabled();
    expect(screen.queryByText("Processing setup pending")).not.toBeInTheDocument();
  });

  it("still shows an unrelated server error as it is", async () => {
    const user = userEvent.setup();
    startIndexRun.mockRejectedValue(new client.ApiError(500, "INTERNAL_ERROR", "An unexpected error occurred", "See the API log."));
    renderStudio();
    await user.click(await screen.findByRole("button", { name: "Calculate NDVI" }));
    expect(await screen.findByText("The run could not be started")).toBeInTheDocument();
    expect(screen.getByText(/INTERNAL_ERROR/)).toBeInTheDocument();
    expect(screen.queryByText(/database job configuration/)).not.toBeInTheDocument(); // not mislabelled as the known state
  });
});

describe("the visual refinements", () => {
  it("sets the page title at 24 px and the section headings at 16 px", async () => {
    renderStudio();
    await screen.findByRole("list", { name: "Input bands" });
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("NDVI Studio");
    expect(screen.getByRole("heading", { level: 1 }).querySelector("span")).toHaveClass("text-2xl");
    for (const name of ["Input bands", "Processing", "Progress", "Map", "Results"]) {
      const heading = screen.getByRole("heading", { level: 2, name });
      expect(heading.querySelector("span"), name).toHaveClass("text-base");
    }
  });

  it("keeps technical text at 12 px or more and body text at 13 px or more", async () => {
    const { container } = renderStudio();
    await screen.findByRole("list", { name: "Input bands" });
    const classes = Array.from(container.querySelectorAll("[class]")).map((el) => el.getAttribute("class") ?? "");
    expect(classes.some((c) => /text-\[(9|10|11)px\]/.test(c))).toBe(false);
    expect(classes.some((c) => c.split(" ").includes("text-[15px]"))).toBe(true); // the compatibility verdict
  });

  it("tells the two bands apart by colour as well as by name", async () => {
    renderStudio();
    const [red, nir] = within(await screen.findByRole("list", { name: "Input bands" })).getAllByRole("listitem");
    expect(red).toHaveClass("border-l-danger");
    expect(nir).toHaveClass("border-l-accent");
    expect(red).toHaveTextContent("Red band");
    expect(nir).toHaveTextContent("Near-infrared band");
  });

  it("groups the processing details under five labelled headings", async () => {
    renderStudio();
    await screen.findByRole("list", { name: "Input bands" });
    for (const label of ["NDVI formula", "Reflectance scaling", "Data validity", "Out-of-range masking", "Processing resolution"]) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
  });

  it("gives the map placeholder a prominent card, the real colour ramp and the scene's grid, but no preview", async () => {
    listIndexRuns.mockResolvedValue({ runs: [] });
    const { container } = renderStudio();
    await screen.findByText(/will be drawn here once a run completes/);
    expect(screen.getByRole("heading", { name: "NDVI layer" })).toHaveClass("text-[17px]");
    expect(screen.getByRole("group", { name: "NDVI colour ramp preview" })).toBeInTheDocument();
    expect(screen.getByText("Tile T55HGS · EPSG:32755 · 10 m")).toBeInTheDocument();
    expect(screen.getByText(/No preview is shown before then\./)).toBeInTheDocument();
    expect(container.querySelector("canvas, img")).toBeNull(); // nothing that looks like a raster
    expect(container.querySelector("[class*='h-[28rem]']")).not.toBeNull();
  });

  it("lays the empty results out in two balanced columns with the future categories grouped", async () => {
    listIndexRuns.mockResolvedValue({ runs: [] });
    const { container } = renderStudio();
    await screen.findByText("No analytical NDVI result is registered yet");
    expect(container.querySelector("[class*='min-[1180px]:grid-cols-2']")).not.toBeNull();
    const groups = within(screen.getByRole("list", { name: "What the results will show" })).getAllByRole("listitem");
    expect(groups.map((g) => g.querySelector("p")?.textContent)).toEqual(["Statistics", "Pixel counts", "Raster", "Provenance"]);
    expect(screen.getByText("This panel will show")).toBeInTheDocument();
  });

  it("reports the setup status in the results checklist for each state", async () => {
    listIndexRuns.mockResolvedValue({ runs: [] });
    getHealth.mockResolvedValue(health("0009"));
    const first = renderStudio();
    expect(await screen.findByText(/Processing setup pending: the database job configuration is not upgraded yet\./)).toBeInTheDocument();
    first.unmount();
    getHealth.mockResolvedValue(health(null));
    renderStudio();
    expect(await screen.findByText("Waiting for a run to be started.")).toBeInTheDocument();
  });
});

describe("the layout at 768-1179 px and below 768 px (rules, not measurements: jsdom has no layout)", () => {
  const classesOf = (el: Element | null) => (el?.getAttribute("class") ?? "").split(/\s+/);

  it("keeps the desktop layout for 1180 px and wider, and one column below it", async () => {
    const { container } = renderStudio();
    await screen.findByRole("list", { name: "Input bands" });
    const layout = container.querySelector("[class*='grid-cols-[minmax(340px']") as HTMLElement;
    expect(classesOf(layout)).toContain("min-[1180px]:grid-cols-[minmax(340px,380px)_minmax(0,1fr)]");
    expect(classesOf(layout).filter((c) => /^(sm|md|lg|xl):grid-cols/.test(c))).toEqual([]); // no other column rule
    expect(classesOf(layout)).not.toContain("grid-cols-2");
  });

  it("puts the band cards side by side once the input panel is 480 px wide, and stacks them narrower", async () => {
    renderStudio();
    const list = await screen.findByRole("list", { name: "Input bands" });
    expect(classesOf(list)).toEqual(expect.arrayContaining(["grid", "@min-[480px]:grid-cols-2"]));
    expect(classesOf(list.parentElement)).toContain("@container"); // measured against the panel, not the window
    expect(classesOf(list)).not.toContain("grid-cols-2"); // stacked (one column) by default, which the 380 px desktop column keeps
  });

  it("uses a two-column label | value metadata grid in the side-by-side cards", async () => {
    renderStudio();
    const card = within(await screen.findByRole("list", { name: "Input bands" })).getAllByRole("listitem")[0]!;
    const facts = card.querySelector("dl") as HTMLElement;
    expect(classesOf(facts)).toContain("@min-[480px]:grid-cols-[auto_minmax(0,1fr)]");
    expect(classesOf(facts.firstElementChild)).toContain("@min-[480px]:contents"); // each fact's label and value become grid cells
    expect(facts.querySelectorAll("dt").length).toBe(facts.querySelectorAll("dd").length);
    expect(classesOf(card.querySelector("p.font-mono"))).toContain("break-all"); // a long file name never forces a scrollbar
  });

  it("keeps the processing details compact: two columns from 480 px, with the long lines spanning both", async () => {
    renderStudio();
    await screen.findByRole("list", { name: "Input bands" });
    const rows = screen.getByText("NDVI formula").closest("dl") as HTMLElement;
    expect(classesOf(rows)).toEqual(expect.arrayContaining(["@min-[480px]:grid", "@min-[480px]:grid-cols-2"]));
    expect(classesOf(rows.parentElement)).toContain("@container");
    for (const label of ["NDVI formula", "Reflectance scaling", "Processing resolution"]) {
      expect(classesOf(screen.getByText(label).parentElement), label).toContain("@min-[480px]:col-span-2");
    }
    for (const label of ["Data validity", "Out-of-range masking"]) {
      expect(classesOf(screen.getByText(label).parentElement), label).not.toContain("@min-[480px]:col-span-2");
    }
    expect(classesOf(screen.getByText("NDVI = (NIR - Red) / (NIR + Red)"))).toContain("wrap-anywhere");
    expect(classesOf(screen.getByText("reflectance = (DN + offset) ÷ 10,000"))).toContain("wrap-anywhere");
  });

  it("gives the map the full width and a height that suits a phone and a tablet", async () => {
    listIndexRuns.mockResolvedValue({ runs: [] });
    const { container } = renderStudio();
    await screen.findByText(/will be drawn here once a run completes/);
    const area = container.querySelector("[class*='h-[26rem]']") as HTMLElement;
    expect(classesOf(area)).toEqual(expect.arrayContaining(["h-[26rem]", "sm:h-[28rem]", "place-items-center", "grid"])); // centred
    expect(classesOf(area)).toEqual(expect.arrayContaining(["p-4", "sm:p-6"]));
    expect(classesOf(screen.getByRole("heading", { name: "NDVI layer" }).parentElement)).toEqual(expect.arrayContaining(["w-full", "max-w-lg"]));
    expect(screen.getByText("Tile T55HGS · EPSG:32755 · 10 m")).toBeInTheDocument();
  });

  it("keeps the results checklist in a single readable column until its panel is 640 px wide", async () => {
    listIndexRuns.mockResolvedValue({ runs: [] });
    renderStudio();
    const list = await screen.findByRole("list", { name: "Readiness" });
    const grid = list.parentElement?.parentElement as HTMLElement;
    expect(classesOf(grid)).toEqual(expect.arrayContaining(["grid", "min-[1180px]:grid-cols-2", "@min-[640px]:grid-cols-2"]));
    expect(classesOf(grid)).not.toContain("grid-cols-2");
    expect(classesOf(grid.parentElement)).toContain("@container");
    const categories = screen.getByRole("list", { name: "What the results will show" });
    expect(classesOf(categories)).toEqual(expect.arrayContaining(["grid-cols-1", "@min-[360px]:grid-cols-2"]));
  });

  it("uses no fixed pixel widths that could force horizontal scrolling", async () => {
    listIndexRuns.mockResolvedValue({ runs: [COMPLETED] });
    const { container } = renderStudio();
    await screen.findByText("Statistics of valid pixels");
    const classes = Array.from(container.querySelectorAll("[class]")).flatMap((el) => classesOf(el));
    const fixed = classes.filter((c) => /(^|:)(w|min-w)-\[\d+(px|rem)\]$/.test(c) || /^(w|min-w)-\d{3,}$/.test(c));
    expect(fixed).toEqual([]);
  });
});
