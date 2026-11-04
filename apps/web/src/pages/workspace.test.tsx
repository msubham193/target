import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Outlet, Route, Routes } from "react-router-dom";
import { vi } from "vitest";

import * as client from "../api/client";
import type { MapCanvasProps } from "../components/MapCanvas";
import { view } from "../test/fixtures";
import { renderPage } from "../test/render";
import { MAX_COMPARED, PhotoWorkspacePage } from "./PhotoWorkspacePage";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    listAssets: vi.fn(),
    listPhotoLocations: vi.fn(),
    listObservations: vi.fn(),
    createJob: vi.fn(),
    changePhotoLocation: vi.fn(),
    reviewObservation: vi.fn(),
  };
});

// jsdom has no WebGL: the map records what it is asked to draw and lets a test click a marker.
const drawn: MapCanvasProps[] = [];
vi.mock("../components/MapCanvas", () => ({
  MapCanvas: (props: MapCanvasProps) => {
    drawn.push(props);
    return (
      <div data-testid="map">
        {(props.photos ?? []).map((photo) => (
          <button key={photo.id} type="button" onClick={() => props.onPhotoSelect?.(photo.id)}>
            marker {photo.label}
          </button>
        ))}
        <button type="button" onClick={() => props.onPhotoChoice?.(["p2", "p1"])}>
          click between markers
        </button>
      </div>
    );
  },
}));

// The panels below have their own tests; here only what the workspace hands them matters.
vi.mock("../components/PhotoLocationPanel", () => ({
  PhotoLocationPanel: ({ asset }: { asset: client.Asset }) => <div data-testid="location-panel">location panel for {asset.id}</div>,
}));
vi.mock("../components/ReviewPanel", () => ({
  ReviewPanel: ({ observationId }: { observationId: string }) => <div data-testid="review-panel">review of {observationId}</div>,
}));

const PROJECT = { id: "proj", name: "Survey", archived_at: null } as client.Project;

function photo(id: string, over: Record<string, unknown> = {}): client.Asset {
  return {
    id,
    project_id: "proj",
    original_filename: `${id}.jpg`,
    modality: "ground_image",
    detected_format: "JPEG",
    media_type: "image/jpeg",
    size_bytes: 2048,
    sha256: id.padEnd(64, "a"),
    status: "validated",
    duplicate_of_asset_id: null,
    quality_flags: [],
    unknowns: { acquisition_time: "The file carries no capture time." },
    metadata: { width: 400, height: 300 },
    acquisition_time: null,
    uploaded_by: { id: "u1", display_name: "Asha", created_at: "2026-10-12T00:00:00Z" },
    ingested_at: "2026-10-12T00:00:00Z",
    raster: null,
    video: null,
    preview: { status: "ready", variants: ["thumb", "view"], job_id: "j0", error_code: null, error_message: null },
    ...over,
  } as unknown as client.Asset;
}

function place(id: string, over: Partial<client.PhotoLocationItem> = {}): client.PhotoLocationItem {
  return {
    asset_id: id,
    filename: `${id}.jpg`,
    state: "confirmed",
    source: "exif",
    latitude: 28.6,
    longitude: 77.2,
    version_no: 2,
    confirmed_by: "Asha",
    confirmed_at: "2026-10-28T10:05:00Z",
    observations: 0,
    ...over,
  };
}

function listing(items: client.PhotoLocationItem[]): client.PhotoLocations {
  return {
    items,
    confirmed: items.filter((i) => i.state === "confirmed").length,
    candidates: items.filter((i) => i.state === "candidate").length,
    without_location: items.filter((i) => i.state === "none").length,
    note: "Only confirmed locations are drawn.",
  };
}

function finding(assetId: string, id: string, run: string, label: string, status = "needs_review") {
  const observation = {
    schema_version: "3.0",
    observation_id: id,
    project_id: "proj",
    asset_id: assetId,
    pipeline: "ground_image",
    model: { name: "detector", version: "v1", run_id: run },
    claim: { category: `coco:${label}`, description: "Model-predicted feature.", claim_type: "inferred" },
    score: { raw_value: 0.9, semantics: "Class score.", calibrated: false, value_range: [0, 1] },
    evidence: [
      {
        asset_id: assetId,
        region_px: {
          type: "bbox_xyxy",
          coordinates: [10, 20, 100, 120],
          coordinate_space: "source_image_pixels",
          origin: "top_left",
          pixel_reference: "corner",
          image_width: 400,
          image_height: 300,
        },
        frame_index: null,
        timestamp_ms: null,
        source_excerpt_or_metadata_key: null,
      },
    ],
    geospatial: { geometry: null, crs_id: null, coordinate_units: null, derivation_method: null, uncertainty: null },
    measurements: [],
    quality_flags: ["NOT_GEOREFERENCED"],
    review: { status: "needs_review", reviewer_note: null, reviewed_at: null, reviewed_by: null },
    provenance: { source_checksum: "ab".repeat(32), ingested_at: "2026-10-12T00:00:00Z", transformations: [], created_at: "2026-10-13T00:00:00Z" },
  } as unknown as client.Observation;
  return view(observation, { current: { status: status as never } });
}

const listAssets = vi.mocked(client.listAssets);
const listPhotoLocations = vi.mocked(client.listPhotoLocations);
const listObservations = vi.mocked(client.listObservations);
const lastMap = () => drawn[drawn.length - 1]!;

/** Three photographs: confirmed with a time, an unconfirmed GPS candidate, and one with nothing recorded. */
function setUp(over: { assets?: client.Asset[]; places?: client.PhotoLocationItem[] } = {}) {
  const assets = over.assets ?? [
    photo("p1", { acquisition_time: "2026-10-01T10:00:00Z", unknowns: {} }),
    photo("p2"),
    photo("p3"),
  ];
  listAssets.mockResolvedValue({ items: assets, next_cursor: null } as client.AssetPage);
  listPhotoLocations.mockResolvedValue(
    listing(
      over.places ?? [
        place("p1"),
        place("p2", { state: "candidate", source: null, latitude: null, longitude: null }),
        place("p3", { state: "none", source: null, latitude: null, longitude: null }),
      ],
    ),
  );
  listObservations.mockImplementation(async (_project, query) => {
    if (query.asset_id === "p1") return [finding("p1", "o1", "r1", "car", "verified"), finding("p1", "o2", "r1", "dog")];
    return [];
  });
}

function render() {
  return renderPage(
    <Routes>
      <Route path="/projects/:projectId" element={<Outlet context={PROJECT} />}>
        <Route path="workspace" element={<PhotoWorkspacePage />} />
      </Route>
    </Routes>,
    "/projects/proj/workspace",
  );
}

async function openTab(name: RegExp) {
  await userEvent.click(await screen.findByRole("tab", { name }));
}

beforeEach(() => {
  vi.clearAllMocks();
  drawn.length = 0;
  setUp();
});

describe("choosing photographs", () => {
  it("lists the project's photographs with their location state, and offers upload and the findings report", async () => {
    render();
    const list = await screen.findByRole("list", { name: "Project photographs" });
    const rows = within(list).getAllByRole("listitem");
    expect(rows).toHaveLength(3);
    expect(within(rows[0]!).getByText("Located")).toBeInTheDocument();
    expect(within(rows[1]!).getByText("GPS not confirmed")).toBeInTheDocument();
    expect(within(rows[2]!).getByText("No location")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Upload/ })).toHaveAttribute("href", "/projects/proj/assets/new");
    expect(screen.getByRole("link", { name: /Findings report/ })).toHaveAttribute("href", "/projects/proj/reports/findings");
  });

  it("says plainly when the project has no photographs", async () => {
    setUp({ assets: [], places: [] });
    render();
    expect(await screen.findByText("No photographs yet")).toBeInTheDocument();
  });

  it("shows an API failure instead of an empty list", async () => {
    listAssets.mockRejectedValue(new client.ApiError(404, "PROJECT_NOT_FOUND", "Project not found"));
    render();
    expect(await screen.findByRole("alert")).toHaveTextContent("Code: PROJECT_NOT_FOUND");
  });

  it("never starts an analysis or changes anything just by opening, selecting or switching tabs", async () => {
    render();
    await userEvent.click(await screen.findByRole("button", { name: /p1\.jpg/ }));
    for (const name of [/Location/, /Findings/, /Map/, /Compare/, /Inspect/]) await openTab(name);
    expect(client.createJob).not.toHaveBeenCalled();
    expect(client.changePhotoLocation).not.toHaveBeenCalled();
    expect(client.reviewObservation).not.toHaveBeenCalled();
  });
});

describe("inspecting a photograph", () => {
  it("shows the file, its stored capture time, and says so when there is none", async () => {
    render();
    await userEvent.click(await screen.findByRole("button", { name: /p2\.jpg/ }));
    expect(await screen.findByRole("heading", { name: "p2.jpg" })).toBeInTheDocument();
    expect(screen.getByText(/Unknown\. The file carries no capture time\./)).toBeInTheDocument();
    expect(screen.getByText("p2".padEnd(64, "a"))).toBeInTheDocument();
    expect(screen.getByText("Recorded as unknown")).toBeInTheDocument();
  });

  it("shows a stored capture time without adding one", async () => {
    render();
    await userEvent.click(await screen.findByRole("button", { name: /p1\.jpg/ }));
    await screen.findByRole("heading", { name: "p1.jpg" });
    expect(screen.queryByText(/Unknown\. /)).not.toBeInTheDocument();
    expect(screen.getByText(/01 Oct 2026/)).toBeInTheDocument();
  });
});

describe("location and findings", () => {
  it("hands the chosen photograph to the existing location panel", async () => {
    render();
    await userEvent.click(await screen.findByRole("button", { name: /p2\.jpg/ }));
    await openTab(/Location/);
    expect(await screen.findByTestId("location-panel")).toHaveTextContent("location panel for p2");
  });

  it("lists the findings of the existing analysis and opens the existing review panel for one", async () => {
    render();
    await userEvent.click(await screen.findByRole("button", { name: /p1\.jpg/ }));
    await openTab(/Findings/);
    const findings = await screen.findByRole("list", { name: "Findings in this photograph" });
    expect(within(findings).getByText("Verified")).toBeInTheDocument();
    expect(within(findings).getByText("Needs review")).toBeInTheDocument();
    await userEvent.click(within(findings).getByRole("button", { name: /dog/ }));
    expect(await screen.findByTestId("review-panel")).toHaveTextContent("review of o2");
    expect(listObservations).toHaveBeenCalledWith("proj", { asset_id: "p1" });
  });

  it("says a photograph was not analysed and points to the analysis screen without running anything", async () => {
    render();
    await userEvent.click(await screen.findByRole("button", { name: /p3\.jpg/ }));
    await openTab(/Findings/);
    expect(await screen.findByText("This photograph has not been analysed")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Run an analysis/ })).toHaveAttribute("href", "/projects/proj/assets/p3/ground");
    expect(client.createJob).not.toHaveBeenCalled();
  });
});

describe("the map", () => {
  it("draws only confirmed photographs; an unconfirmed candidate gets no marker", async () => {
    render();
    await openTab(/Map/);
    await screen.findByTestId("map");
    expect(lastMap().photos).toEqual([{ id: "p1", lon: 77.2, lat: 28.6, label: "p1.jpg" }]);
    expect(screen.queryByRole("button", { name: "marker p2.jpg" })).not.toBeInTheDocument();
  });

  it("selecting a marker selects its photograph and shows that photograph's findings count", async () => {
    render();
    await openTab(/Map/);
    await userEvent.click(await screen.findByRole("button", { name: "marker p1.jpg" }));
    await waitFor(() => expect(lastMap().selectedPhotoId).toBe("p1"));
    const summary = await screen.findByTestId("marker-summary");
    expect(summary).toHaveTextContent("where this photograph is recorded as taken or placed");
    expect(summary).toHaveTextContent("not the position of anything shown in it");
    await waitFor(() => expect(summary).toHaveTextContent("2 finding(s) in this photograph"));
  });

  it("selecting a photograph in the list moves the highlighted marker", async () => {
    render();
    await userEvent.click(await screen.findByRole("button", { name: /p1\.jpg/ }));
    await openTab(/Map/);
    await screen.findByTestId("map");
    await waitFor(() => expect(lastMap().selectedPhotoId).toBe("p1"));
  });

  it("explains that an unconfirmed photograph has no marker", async () => {
    render();
    await userEvent.click(await screen.findByRole("button", { name: /p2\.jpg/ }));
    await openTab(/Map/);
    expect(await screen.findByTestId("marker-summary")).toHaveTextContent("GPS position in its file that nobody has confirmed, so it has no marker");
  });

  it("asks which photograph is meant when markers overlap, and does not pick one itself", async () => {
    render();
    await openTab(/Map/);
    await userEvent.click(await screen.findByRole("button", { name: "click between markers" }));
    const chooser = await screen.findByText(/Several photographs are close together/);
    expect(lastMap().selectedPhotoId).toBeNull();
    await userEvent.click(within(chooser.closest("[role]") as HTMLElement).getByRole("button", { name: "p2.jpg" }));
    await waitFor(() => expect(lastMap().selectedPhotoId).toBe("p2"));
  });

  it("says no photograph has a confirmed location when none does", async () => {
    setUp({ places: [place("p1", { state: "candidate", latitude: null, longitude: null })] });
    render();
    await openTab(/Map/);
    expect(await screen.findByText("No photograph has a confirmed location yet")).toBeInTheDocument();
    expect(screen.queryByTestId("map")).not.toBeInTheDocument();
  });
});

describe("comparing photographs", () => {
  async function tick(...names: string[]) {
    for (const name of names) await userEvent.click(await screen.findByRole("checkbox", { name: `Compare ${name}` }));
  }

  it("asks for at least two photographs", async () => {
    render();
    await openTab(/Compare/);
    expect(await screen.findByText("Select at least two photographs")).toBeInTheDocument();
    await tick("p1.jpg");
    expect(screen.getByText("Select at least two photographs")).toBeInTheDocument();
  });

  it("shows previews, times, camera positions, findings and gaps side by side without inventing anything", async () => {
    render();
    await tick("p1.jpg", "p2.jpg", "p3.jpg");
    await openTab(/Compare/);
    const compare = await screen.findByLabelText("Photo comparison");
    expect(within(compare).getAllByRole("columnheader")).toHaveLength(4);
    expect(within(compare).getByTestId("time-p1")).toHaveTextContent("01 Oct 2026");
    expect(within(compare).getByTestId("time-p2")).toHaveTextContent("Unknown. The file carries no capture time.");
    expect(within(compare).getByTestId("position-p1")).toHaveTextContent("Camera position confirmed");
    expect(within(compare).getByTestId("position-p1")).toHaveTextContent("28.600000° N");
    expect(within(compare).getByTestId("position-p2")).toHaveTextContent("GPS not confirmed");
    expect(within(compare).getByTestId("position-p2").textContent).not.toMatch(/\d+\.\d{3,}/);
    expect(within(compare).getByTestId("position-p3")).toHaveTextContent("No location recorded");
    await waitFor(() => expect(within(compare).getByTestId("findings-p1")).toHaveTextContent("2 finding(s)"));
    expect(within(compare).getByTestId("findings-p1")).toHaveTextContent("Verified 1");
    expect(within(compare).getByTestId("findings-p3")).toHaveTextContent("Not analysed");
    expect(within(compare).getByTestId("gaps-p2")).toHaveTextContent("has not been confirmed, so no location is used");
    expect(within(compare).getByTestId("gaps-p1")).toHaveTextContent("1 finding(s) not yet reviewed");
    // only p1 has a time and a confirmed position: nothing can be stated between photographs
    expect(within(compare).getByTestId("no-intervals")).toHaveTextContent("Fewer than two");
    expect(within(compare).getByTestId("no-distances")).toHaveTextContent("Fewer than two");
  });

  it("states time and camera distance between photographs only when both have the stored facts", async () => {
    setUp({
      assets: [
        photo("p1", { acquisition_time: "2026-10-01T10:00:00Z", unknowns: {} }),
        photo("p2", { acquisition_time: "2026-10-01T10:03:20Z", unknowns: {} }),
      ],
      places: [place("p1"), place("p2", { latitude: 28.601, longitude: 77.2 })],
    });
    render();
    await tick("p1.jpg", "p2.jpg");
    await openTab(/Compare/);
    expect(await screen.findByTestId("intervals")).toHaveTextContent("p1.jpg → p2.jpg: 3 min 20 s");
    expect(screen.getByTestId("distances")).toHaveTextContent(/p1\.jpg ↔ p2\.jpg: about 11\d(\.\d)? m between the recorded camera positions/);
  });

  it("does not claim that photographs, findings or camera positions describe the same object", async () => {
    setUp({
      assets: [photo("p1"), photo("p2")],
      places: [place("p1"), place("p2")], // identical camera positions
    });
    listObservations.mockImplementation(async (_p, query) => [finding(query.asset_id!, `o-${query.asset_id}`, `r-${query.asset_id}`, "car")]);
    render();
    await tick("p1.jpg", "p2.jpg");
    await openTab(/Compare/);
    const note = await screen.findByText("What this comparison does not say");
    expect(note.parentElement).toHaveTextContent("not evidence that they show the same object");
    expect(note.parentElement).toHaveTextContent("It is not the position of anything in the picture");
    const compare = screen.getByLabelText("Photo comparison");
    expect(compare.textContent).not.toMatch(/same car|duplicate|matched|identical object/i);
    expect(within(compare).queryByRole("link")).not.toBeInTheDocument();
  });

  it("stops at the largest number of photographs that can be read side by side", async () => {
    const many = Array.from({ length: MAX_COMPARED + 1 }, (_, i) => photo(`m${i}`));
    setUp({ assets: many, places: many.map((a) => place(a.id)) });
    render();
    for (let i = 0; i < MAX_COMPARED; i++) await tick(`m${i}.jpg`);
    expect(screen.getByRole("checkbox", { name: `Compare m${MAX_COMPARED}.jpg` })).toBeDisabled();
    expect(screen.getByRole("checkbox", { name: "Compare m0.jpg" })).toBeEnabled();
  });

  it("reports findings that could not be loaded instead of treating them as none", async () => {
    listObservations.mockRejectedValue(new client.ApiError(500, "INTERNAL", "Something broke"));
    render();
    await tick("p1.jpg", "p2.jpg");
    await openTab(/Compare/);
    expect(await screen.findByText("Some findings could not be loaded")).toBeInTheDocument();
    expect(screen.queryByText(/Not analysed yet/)).not.toBeInTheDocument();
  });

  it("only ever asks for the project's own data", async () => {
    render();
    await tick("p1.jpg", "p2.jpg");
    await openTab(/Compare/);
    await screen.findByLabelText("Photo comparison");
    for (const call of listObservations.mock.calls) expect(call[0]).toBe("proj");
    expect(listAssets).toHaveBeenCalledWith("proj", expect.objectContaining({ modality: "ground_image" }));
    expect(listPhotoLocations).toHaveBeenCalledWith("proj");
  });
});
