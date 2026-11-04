import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Outlet, Route, Routes } from "react-router-dom";
import { vi } from "vitest";

import * as client from "../api/client";
import type { MapCanvasProps } from "../components/MapCanvas";
import { view } from "../test/fixtures";
import { renderPage } from "../test/render";
import { MapPage } from "./MapPage";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    getLayers: vi.fn(),
    listPhotoLocations: vi.fn(),
    getAsset: vi.fn(),
    listObservations: vi.fn(),
    getRasterTools: vi.fn(),
    getPixel: vi.fn(),
    transformPoint: vi.fn(),
    measurePoints: vi.fn(),
  };
});

// jsdom has no WebGL. The stand-in records what the page asks the map to draw and lets a test click a marker.
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
        <button type="button" onClick={() => props.onPick?.({ lon: 75, lat: 27 })}>
          click empty map
        </button>
        <button type="button" onClick={() => props.onPhotoChoice?.(["a3", "a1"])}>
          click between markers
        </button>
      </div>
    );
  },
}));

const getLayers = vi.mocked(client.getLayers);
const listPhotoLocations = vi.mocked(client.listPhotoLocations);
const getAsset = vi.mocked(client.getAsset);
const listObservations = vi.mocked(client.listObservations);

const PROJECT = { id: "p1", name: "Survey", archived_at: null } as client.Project;

const LAYERS = {
  rasters: [],
  excluded: [],
  aoi: null,
  observations: { with_location: 0, without_location: 4, note: "Only observations with an established geographic location can be drawn." },
  basemap: { configured: false, detail: "No offline basemap is configured (decision U-013 is open)." },
} as unknown as client.Layers;

function item(overrides: Partial<client.PhotoLocationItem>): client.PhotoLocationItem {
  return {
    asset_id: "a1", filename: "gate.jpg", state: "confirmed", source: "exif", latitude: 28.6, longitude: 77.2, version_no: 2,
    confirmed_by: "Asha", confirmed_at: "2026-10-28T10:05:00Z", observations: 2, ...overrides,
  };
}

function locations(items: client.PhotoLocationItem[]): client.PhotoLocations {
  return {
    items,
    confirmed: items.filter((i) => i.state === "confirmed").length,
    candidates: items.filter((i) => i.state === "candidate").length,
    without_location: items.filter((i) => i.state === "none").length,
    note: "Only confirmed locations are drawn.",
  };
}

const ASSET = {
  id: "a1", project_id: "p1", original_filename: "gate.jpg", modality: "ground_image", detected_format: "JPEG", media_type: "image/jpeg",
  size_bytes: 2048, sha256: "ab".repeat(32), status: "validated", duplicate_of_asset_id: null, quality_flags: [], unknowns: {}, metadata: { width: 400, height: 300 },
  acquisition_time: null, uploaded_by: { id: "u1", display_name: "Asha", created_at: "2026-10-12T00:00:00Z" }, ingested_at: "2026-10-12T00:00:00Z",
  raster: null, video: null, preview: { status: "ready", variants: ["thumb", "view"], job_id: "j0", error_code: null, error_message: null },
} as unknown as client.Asset;

function observation(index: number, runId: string, createdAt: string, label: string): client.Observation {
  return {
    schema_version: "3.0", observation_id: `o${index}`, project_id: "p1", asset_id: "a1", pipeline: "ground_image",
    model: { name: "detector", version: "v1", run_id: runId },
    claim: { category: `coco:${label}`, description: "Model-predicted feature.", claim_type: "inferred" },
    score: { raw_value: 0.9, semantics: "Class score after NMS.", calibrated: false, value_range: [0, 1] },
    evidence: [
      {
        asset_id: "a1",
        region_px: { type: "bbox_xyxy", coordinates: [10 * index, 20, 100 * index, 120], coordinate_space: "source_image_pixels", origin: "top_left", pixel_reference: "corner", image_width: 400, image_height: 300 },
        frame_index: null, timestamp_ms: null, source_excerpt_or_metadata_key: null,
      },
    ],
    geospatial: { geometry: null, crs_id: null, coordinate_units: null, derivation_method: null, uncertainty: null },
    measurements: [], quality_flags: ["NOT_GEOREFERENCED"],
    review: { status: "needs_review", reviewer_note: null, reviewed_at: null, reviewed_by: null },
    provenance: { source_checksum: "ab".repeat(32), ingested_at: "2026-10-12T00:00:00Z", transformations: [], created_at: createdAt },
  } as client.Observation;
}

const lastMap = () => drawn[drawn.length - 1]!;

beforeEach(() => {
  vi.clearAllMocks();
  drawn.length = 0;
  getLayers.mockResolvedValue(LAYERS);
  listPhotoLocations.mockResolvedValue(locations([item({})]));
  getAsset.mockResolvedValue(ASSET);
  listObservations.mockResolvedValue([]);
});

function render() {
  return renderPage(
    <Routes>
      <Route path="/projects/:projectId" element={<Outlet context={PROJECT} />}>
        <Route path="map" element={<MapPage />} />
      </Route>
    </Routes>,
    "/projects/p1/map",
  );
}

describe("located photographs on the map", () => {
  it("draws only photographs with a confirmed location, and says how many are unconfirmed", async () => {
    listPhotoLocations.mockResolvedValue(
      locations([
        item({}),
        item({ asset_id: "a2", filename: "candidate.jpg", state: "candidate", source: null, latitude: null, longitude: null, observations: 0 }),
        item({ asset_id: "a3", filename: "plain.jpg", state: "none", source: null, latitude: null, longitude: null, observations: 0 }),
      ]),
    );
    render();
    const list = await screen.findByRole("list", { name: "Located photographs" });
    expect(within(list).getAllByRole("listitem")).toHaveLength(1);
    expect(within(list).getByRole("button", { name: /gate\.jpg/ })).toBeInTheDocument();
    expect(screen.getByText("Located photographs (1)")).toBeInTheDocument();
    await waitFor(() => expect(lastMap().photos).toEqual([{ id: "a1", lon: 77.2, lat: 28.6, label: "gate.jpg" }]));
    expect(screen.getByText(/1 photograph\(s\) have an unconfirmed GPS position/)).toBeInTheDocument();
    expect(screen.queryByText("candidate.jpg")).not.toBeInTheDocument();
  });

  it("never draws a photograph the server sent without coordinates, even if marked confirmed", async () => {
    listPhotoLocations.mockResolvedValue(locations([item({ latitude: null, longitude: null })]));
    render();
    await screen.findByTestId("no-located-photos");
    await waitFor(() => expect(lastMap().photos).toEqual([]));
  });

  it("says when no photograph is located and still offers the rest of the map", async () => {
    listPhotoLocations.mockResolvedValue(locations([item({ state: "none", source: null, latitude: null, longitude: null })]));
    render();
    expect(await screen.findByTestId("no-located-photos")).toHaveTextContent("No photograph has a confirmed location yet");
    expect(screen.getByText("Nothing in this project can be placed on a map")).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: /Located photographs \(0\)/ })).toBeDisabled();
  });

  it("states that there is no GeoTIFF imagery and that nothing is retrieved online", async () => {
    render();
    const message = await screen.findByTestId("no-imagery");
    expect(message).toHaveTextContent("No georeferenced rasters in this project.");
    expect(message).toHaveTextContent("not retrieved from any online source");
  });

  it("shows an error for the photograph locations without breaking the map", async () => {
    listPhotoLocations.mockRejectedValue(new client.ApiError(500, "INTERNAL", "Something failed"));
    render();
    expect(await screen.findByText("Photograph locations could not be loaded")).toBeInTheDocument();
    expect(screen.getByTestId("map")).toBeInTheDocument();
    await waitFor(() => expect(lastMap().photos).toEqual([]));
  });

  it("shows nothing about photographs when the layer is hidden", async () => {
    render();
    await screen.findByRole("list", { name: "Located photographs" });
    await userEvent.click(screen.getByRole("checkbox", { name: /Located photographs \(1\)/ }));
    await waitFor(() => expect(lastMap().showPhotos).toBe(false));
  });
});

// Three photographs a few tens of metres apart: their markers overlap on screen at the zoom the map fits to.
const CLOSE = [
  { asset_id: "a1", filename: "gate_gps.jpg", latitude: 27.113, longitude: 75.01, shown: "27.113000° N, 75.010000° E" },
  { asset_id: "a2", filename: "plain.jpg", latitude: 27.1135, longitude: 75.0105, shown: "27.113500° N, 75.010500° E" },
  { asset_id: "a3", filename: "void_gps.jpg", latitude: 27.1128, longitude: 75.0098, shown: "27.112800° N, 75.009800° E" },
];

function withClosePhotographs() {
  listPhotoLocations.mockResolvedValue(locations(CLOSE.map((c) => item({ asset_id: c.asset_id, filename: c.filename, latitude: c.latitude, longitude: c.longitude, observations: 0 }))));
  getAsset.mockImplementation(async (id: string) => {
    const found = CLOSE.find((c) => c.asset_id === id)!;
    return { ...ASSET, id, original_filename: found.filename } as client.Asset;
  });
}

describe("closely spaced photographs", () => {
  it.each(CLOSE)("choosing $filename in the list opens that photograph and its own confirmed coordinates", async (target) => {
    withClosePhotographs();
    render();
    const list = await screen.findByRole("list", { name: "Located photographs" });
    await userEvent.click(within(list).getByRole("button", { name: new RegExp(target.filename.replace(".", "\\.")) }));
    expect(await screen.findByRole("img", { name: `Preview of ${target.filename}` })).toBeInTheDocument();
    expect(screen.getByText(target.shown)).toBeInTheDocument();
    await waitFor(() => expect(lastMap().selectedPhotoId).toBe(target.asset_id));
    for (const other of CLOSE.filter((c) => c.asset_id !== target.asset_id)) {
      expect(screen.queryByRole("img", { name: `Preview of ${other.filename}` })).not.toBeInTheDocument();
      expect(screen.queryByText(other.shown)).not.toBeInTheDocument();
    }
    expect(getAsset).toHaveBeenCalledWith(target.asset_id);
  });

  it("switching between photographs in the list swaps the photograph and its coordinates", async () => {
    withClosePhotographs();
    render();
    const list = await screen.findByRole("list", { name: "Located photographs" });
    await userEvent.click(within(list).getByRole("button", { name: /gate_gps/ }));
    expect(await screen.findByText("27.113000° N, 75.010000° E")).toBeInTheDocument();
    await userEvent.click(within(list).getByRole("button", { name: /void_gps/ }));
    expect(await screen.findByRole("img", { name: "Preview of void_gps.jpg" })).toBeInTheDocument();
    expect(screen.getByText("27.112800° N, 75.009800° E")).toBeInTheDocument();
    expect(screen.queryByText("27.113000° N, 75.010000° E")).not.toBeInTheDocument();
  });

  it("asks which photograph was meant when a click could mean several, and opens nothing until one is chosen", async () => {
    withClosePhotographs();
    render();
    await userEvent.click(await screen.findByRole("button", { name: "click between markers" }));
    const asking = await screen.findByRole("list", { name: "Photographs near the click" });
    expect(within(asking).getAllByRole("button").map((b) => b.textContent)).toEqual(["void_gps.jpg", "gate_gps.jpg", "Dismiss"]); // nearest first, as reported
    expect(screen.queryByRole("button", { name: "Close" })).not.toBeInTheDocument(); // no photograph opened yet
    await waitFor(() => expect(lastMap().selectedPhotoId).toBeNull());
    await userEvent.click(within(asking).getByRole("button", { name: "gate_gps.jpg" }));
    expect(await screen.findByRole("img", { name: "Preview of gate_gps.jpg" })).toBeInTheDocument();
    expect(screen.getByText("27.113000° N, 75.010000° E")).toBeInTheDocument();
    expect(screen.queryByRole("list", { name: "Photographs near the click" })).not.toBeInTheDocument();
  });

  it("lets the person dismiss the question without opening anything", async () => {
    withClosePhotographs();
    render();
    await userEvent.click(await screen.findByRole("button", { name: "click between markers" }));
    await userEvent.click(within(await screen.findByRole("list", { name: "Photographs near the click" })).getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByRole("list", { name: "Photographs near the click" })).not.toBeInTheDocument();
    await waitFor(() => expect(lastMap().selectedPhotoId).toBeNull());
  });

  it("selecting a marker directly clears an open question", async () => {
    withClosePhotographs();
    render();
    await userEvent.click(await screen.findByRole("button", { name: "click between markers" }));
    await screen.findByRole("list", { name: "Photographs near the click" });
    await userEvent.click(screen.getByRole("button", { name: "marker plain.jpg" }));
    expect(await screen.findByRole("img", { name: "Preview of plain.jpg" })).toBeInTheDocument();
    expect(screen.queryByRole("list", { name: "Photographs near the click" })).not.toBeInTheDocument();
  });
});

describe("the photograph behind a marker", () => {
  it("opens beside the map with its latest analysis drawn over it", async () => {
    listObservations.mockResolvedValue([
      view(observation(1, "r-old", "2026-10-10T00:00:00Z", "boat")),
      view(observation(1, "r-new", "2026-10-20T00:00:00Z", "person")),
      view(observation(2, "r-new", "2026-10-20T00:00:00Z", "bicycle")),
    ]);
    render();
    expect(screen.queryByRole("img", { name: "Preview of gate.jpg" })).not.toBeInTheDocument();
    await userEvent.click(await screen.findByRole("button", { name: "marker gate.jpg" }));
    expect(await screen.findByRole("img", { name: "Preview of gate.jpg" })).toBeInTheDocument();
    await waitFor(() => expect(lastMap().selectedPhotoId).toBe("a1"));
    expect(listObservations).toHaveBeenCalledWith("p1", { asset_id: "a1" });
    // Only the most recent run is drawn and listed.
    expect(await screen.findAllByTestId("detection-box")).toHaveLength(2);
    const findings = screen.getByRole("list", { name: "Findings in this photograph" });
    expect(within(findings).getAllByRole("listitem")).toHaveLength(2);
    expect(within(findings).queryByText(/boat/i)).not.toBeInTheDocument();
    expect(screen.getByText("28.600000° N, 77.200000° E")).toBeInTheDocument();
    expect(screen.getByText(/GPS position from the photo file, confirmed by a person · Asha/)).toBeInTheDocument();
    expect(screen.getByText(/the findings have no\s+geographic location, only the photograph does/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Open in Photo Analysis/ })).toHaveAttribute("href", "/projects/p1/assets/a1/ground");
  });

  it("highlights a finding when its row is selected", async () => {
    listObservations.mockResolvedValue([view(observation(1, "r1", "2026-10-20T00:00:00Z", "person"))]);
    render();
    await userEvent.click(await screen.findByRole("button", { name: "marker gate.jpg" }));
    const row = await screen.findByRole("button", { name: /person/i });
    expect(row).toHaveAttribute("aria-pressed", "false");
    await userEvent.click(row);
    expect(row).toHaveAttribute("aria-pressed", "true");
  });

  it("says when the photograph has not been analysed", async () => {
    render();
    await userEvent.click(await screen.findByRole("button", { name: "marker gate.jpg" }));
    expect(await screen.findByText(/This photograph has not been analysed yet/)).toBeInTheDocument();
    expect(screen.queryByTestId("detection-box")).not.toBeInTheDocument();
  });

  it("can also be opened from the list, and closed", async () => {
    render();
    const list = await screen.findByRole("list", { name: "Located photographs" });
    await userEvent.click(within(list).getByRole("button", { name: /gate\.jpg/ }));
    expect(await screen.findByRole("img", { name: "Preview of gate.jpg" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("img", { name: "Preview of gate.jpg" })).not.toBeInTheDocument();
    await waitFor(() => expect(lastMap().selectedPhotoId).toBeNull());
  });

  it("shows why the photograph or its findings could not be loaded", async () => {
    getAsset.mockRejectedValue(new client.ApiError(404, "ASSET_NOT_FOUND", "Asset not found"));
    listObservations.mockRejectedValue(new client.ApiError(500, "INTERNAL", "Something failed"));
    render();
    await userEvent.click(await screen.findByRole("button", { name: "marker gate.jpg" }));
    expect(await screen.findByText("The photograph could not be loaded")).toBeInTheDocument();
    expect(await screen.findByText("The findings could not be loaded")).toBeInTheDocument();
  });

  it("does not select a photograph when an empty part of the map is clicked", async () => {
    render();
    await userEvent.click(await screen.findByRole("button", { name: "click empty map" }));
    await waitFor(() => expect(lastMap().selectedPhotoId).toBeNull());
    expect(screen.queryByRole("button", { name: "Close" })).not.toBeInTheDocument();
  });
});
