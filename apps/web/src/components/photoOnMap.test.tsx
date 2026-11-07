import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes, useLocation } from "react-router-dom";
import { vi } from "vitest";

import * as client from "../api/client";
import { distanceMetres } from "../lib/compare";
import { fromLocal, toLocal } from "../lib/groundMatch";
import { bearingTo } from "../lib/sightLines";
import { view } from "../test/fixtures";
import { renderPage } from "../test/render";
import type { MapCanvasProps } from "./MapCanvas";
import { popupPosition, SatelliteLocationView } from "./SatelliteLocationView";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    getLayers: vi.fn(),
    listPhotoLocations: vi.fn(),
    getAsset: vi.fn(),
    listObservations: vi.fn(),
    getCameraView: vi.fn(),
    createAnnotation: vi.fn(),
    getReportSummaryStatus: vi.fn(),
    describeScene: vi.fn(),
  };
});

// jsdom has no WebGL. The stand-in records what the view asks the map to draw, and lets a test click it.
const drawn: MapCanvasProps[] = [];
/** Where the stand-in's "click the map at the next spot" button clicks. */
const nextMapClick = { lon: 0, lat: 0 };
vi.mock("./MapCanvas", () => ({
  MapCanvas: (props: MapCanvasProps) => {
    drawn.push(props);
    return (
      <div data-testid="map">
        {(props.photos ?? []).map((photo) => (
          <button
            key={photo.id}
            type="button"
            onClick={() => props.onPhotoSelect?.(photo.id)}
            onMouseEnter={() => props.onPhotoHover?.({ id: photo.id, x: 500, y: 400 })}
            onMouseLeave={() => props.onPhotoHover?.(null)}
          >
            marker {photo.label}
          </button>
        ))}
        {/* About 111 m due north of the camera. */}
        <button type="button" onClick={() => props.onPick?.({ lon: 85.862393, lat: 20.300752 })}>
          click the map north of the camera
        </button>
        <button type="button" onClick={() => props.onPick?.({ ...nextMapClick })}>
          click the map at the next spot
        </button>
      </div>
    );
  },
}));

const getLayers = vi.mocked(client.getLayers);
const listPhotoLocations = vi.mocked(client.listPhotoLocations);
const getAsset = vi.mocked(client.getAsset);
const listObservations = vi.mocked(client.listObservations);
const getCameraView = vi.mocked(client.getCameraView);
const createAnnotation = vi.mocked(client.createAnnotation);
const getReportSummaryStatus = vi.mocked(client.getReportSummaryStatus);
const describeScene = vi.mocked(client.describeScene);

const PROJECT = { id: "p1", name: "Survey", archived_at: null, aoi: null } as unknown as client.Project;
const CAMERA = { lon: 85.862393, lat: 20.299752 };

const PHOTO: client.PhotoLocationItem = {
  asset_id: "a1",
  filename: "flyover.jpg",
  state: "confirmed",
  source: "exif",
  latitude: CAMERA.lat,
  longitude: CAMERA.lon,
  version_no: 2,
  confirmed_by: "Asha",
  confirmed_at: "2026-10-30T07:10:00Z",
  observations: 3,
};

const ASSET = {
  id: "a1", project_id: "p1", original_filename: "flyover.jpg", modality: "ground_image", detected_format: "JPEG", media_type: "image/jpeg",
  size_bytes: 2048, sha256: "ab".repeat(32), status: "validated", duplicate_of_asset_id: null, quality_flags: [], unknowns: {}, metadata: { width: 400, height: 300 },
  acquisition_time: null, uploaded_by: { id: "u1", display_name: "Asha", created_at: "2026-10-12T00:00:00Z" }, ingested_at: "2026-10-12T00:00:00Z",
  raster: null, video: null, preview: { status: "ready", variants: ["thumb", "view"], job_id: "j0", error_code: null, error_message: null },
} as unknown as client.Asset;

/** An object whose box is centred on column `centre` of a 400-pixel-wide picture. */
function observation(index: number, label: string, centre: number): client.Observation {
  return {
    schema_version: "3.0", observation_id: `o${index}`, project_id: "p1", asset_id: "a1", pipeline: "ground_image",
    model: { name: "detector", version: "v1", run_id: "r1" },
    claim: { category: `coco:${label}`, description: "Model-predicted feature.", claim_type: "inferred" },
    score: { raw_value: 0.8, semantics: "Class score after NMS.", calibrated: false, value_range: [0, 1] },
    evidence: [
      {
        asset_id: "a1",
        region_px: { type: "bbox_xyxy", coordinates: [centre - 10, 100, centre + 10, 140], coordinate_space: "source_image_pixels", origin: "top_left", pixel_reference: "corner", image_width: 400, image_height: 300 },
        frame_index: null, timestamp_ms: null, source_excerpt_or_metadata_key: null,
      },
    ],
    geospatial: { geometry: null, crs_id: null, coordinate_units: null, derivation_method: null, uncertainty: null },
    measurements: [], quality_flags: ["NOT_GEOREFERENCED"],
    review: { status: "needs_review", reviewer_note: null, reviewed_at: null, reviewed_by: null },
    provenance: { source_checksum: "ab".repeat(32), ingested_at: "2026-10-12T00:00:00Z", transformations: [], created_at: "2026-10-30T08:00:00Z" },
  } as client.Observation;
}

const OBJECTS = [
  view(observation(1, "car", 200)), // the middle of the picture
  view(observation(2, "motorcycle", 0)), // the left edge
  view(observation(3, "bus", 380), { current: { status: "rejected" } }),
];

function cameraView(over: Partial<client.CameraView> = {}): client.CameraView {
  return {
    asset_id: "a1",
    heading_deg: null,
    heading_reference: null,
    focal_length_35mm_mm: null,
    focal_length_mm: null,
    problems: [],
    note: "Not checked.",
    ...over,
  };
}

const lastMap = () => drawn[drawn.length - 1]!;

function Search() {
  return <output data-testid="search">{useLocation().search}</output>;
}

function render(path = "/projects/p1/satellite?view=location") {
  return renderPage(
    <Routes>
      <Route
        path="/projects/:projectId/satellite"
        element={
          <>
            <Search />
            <div style={{ height: 800 }}>
              <SatelliteLocationView project={PROJECT} imageIds={[]} />
            </div>
          </>
        }
      />
    </Routes>,
    path,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  drawn.length = 0;
  window.localStorage.clear();
  getLayers.mockResolvedValue({ rasters: [] } as unknown as client.Layers);
  listPhotoLocations.mockResolvedValue({ items: [PHOTO], confirmed: 1, candidates: 1, without_location: 0, note: "" });
  getAsset.mockResolvedValue(ASSET);
  listObservations.mockResolvedValue(OBJECTS);
  getCameraView.mockResolvedValue(cameraView());
  createAnnotation.mockResolvedValue({} as Awaited<ReturnType<typeof client.createAnnotation>>);
  getReportSummaryStatus.mockResolvedValue({ enabled: false, reachable: false, model: "qwen2.5vl:latest", model_installed: false, note: "Switched off (GEOPOC_LLM_ENABLED)." });
});

describe("Photos on the satellite map", () => {
  it("marks where each located photo was taken, and opens one from its marker", async () => {
    render();
    const list = await screen.findByRole("region", { name: "Your photos" });
    expect(list).toHaveTextContent("flyover.jpg");
    expect(list).toHaveTextContent("1 more photo(s) have a GPS position that is not confirmed yet");
    await waitFor(() => expect(lastMap().photos).toEqual([{ id: "a1", lon: CAMERA.lon, lat: CAMERA.lat, label: "flyover.jpg" }]));

    await userEvent.click(screen.getByRole("button", { name: "marker flyover.jpg" }));
    const panel = await screen.findByRole("region", { name: "Photo on the map" });
    expect(within(panel).getByLabelText("Where the photo was taken")).toHaveTextContent("20.299752° N, 85.862393° E");
    expect(screen.getByTestId("search")).toHaveTextContent("photo=a1");
    // East of the camera by half the panel's width, so the camera shows in the middle of the uncovered map.
    await waitFor(() => expect(lastMap().goTo).toMatchObject({ lat: CAMERA.lat, zoom: 17 }));
    expect(lastMap().goTo!.lon - CAMERA.lon).toBeCloseTo((232 * 360) / (512 * 2 ** 17), 9);
    expect(lastMap().selectedPhotoId).toBe("a1");
    expect(await within(panel).findByRole("list", { name: "Objects" })).toHaveTextContent("car");
  });

  it("shows the photo in a popup at its marker while the pointer is over it", async () => {
    render();
    const marker = await screen.findByRole("button", { name: "marker flyover.jpg" });
    await userEvent.hover(marker);
    const popup = screen.getByRole("tooltip", { name: "Photo taken here" });
    expect(within(popup).getByRole("img", { name: "Photo taken here: flyover.jpg" })).toHaveAttribute("src", expect.stringContaining("/api/v1/assets/a1/preview?variant=view"));
    expect(popup).toHaveTextContent("20.299752° N, 85.862393° E");
    // What the AI found, drawn on the photo and counted (the bus a reviewer rejected is left out).
    expect(await within(popup).findByTestId("hover-boxes")).toBeInTheDocument();
    expect(within(popup).getByTestId("hover-boxes").querySelectorAll("rect")).toHaveLength(2);
    expect(within(popup).getByLabelText("What the AI found")).toHaveTextContent("AI analysis: 2 object(s)");
    expect(within(popup).getByLabelText("What the AI found")).toHaveTextContent("car 1");
    expect(within(popup).getByLabelText("What the AI found")).toHaveTextContent("motorcycle 1");
    expect(popup).toHaveTextContent("Click the marker to open it.");
    expect(popup).toHaveStyle({ left: "308px", top: "418px" }); // below the marker at (500, 400): there is no room above it
    await userEvent.unhover(marker);
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
  });

  it("draws a line towards each object from the direction the photo file records", async () => {
    getCameraView.mockResolvedValue(cameraView({ heading_deg: 90, heading_reference: "true_north", focal_length_35mm_mm: 26 }));
    render("/projects/p1/satellite?view=location&photo=a1");
    expect(await screen.findByTestId("heading-source")).toHaveTextContent("From the photo's own compass: 90° E, measured from true north");
    await waitFor(() => expect(lastMap().sightLines?.rays).toHaveLength(2)); // the rejected bus is not drawn
    const sight = lastMap().sightLines!;
    expect(sight.fan).not.toBeNull();
    const [car, motorcycle] = sight.rays;
    const end = (ray: typeof car) => ({ lon: ray!.coordinates[1]![0], lat: ray!.coordinates[1]![1] });
    expect(bearingTo(CAMERA, end(car))).toBeCloseTo(90, 3); // the middle of the picture looks along the camera
    expect(bearingTo(CAMERA, end(motorcycle))).toBeLessThan(65); // the left edge: about half the view to the left
    expect(distanceMetres(CAMERA, end(car))).toBeCloseTo(250, 0);
    expect(screen.getByText(/Width from the lens data in the file/)).toBeInTheDocument();
  });

  it("lets a person set the direction on the map when the file has none, and keeps it in this browser", async () => {
    render("/projects/p1/satellite?view=location&photo=a1");
    expect(await screen.findByTestId("heading-source")).toHaveTextContent("does not record which way the camera faced");
    expect(lastMap().sightLines?.rays).toEqual([]);

    await userEvent.click(screen.getByRole("button", { name: "Set on the map" }));
    expect(lastMap().onPhotoSelect).toBeUndefined(); // while aiming, a click is only for aiming
    await userEvent.click(screen.getByRole("button", { name: "click the map north of the camera" }));
    expect(screen.getByTestId("heading-source")).toHaveTextContent("Set by you: 0° N");
    expect(lastMap().pinned ?? null).toBeNull(); // the aiming click did not pin a point
    await waitFor(() => expect(lastMap().sightLines?.rays).toHaveLength(2));
    expect(bearingTo(CAMERA, { lon: lastMap().sightLines!.rays[0]!.coordinates[1]![0], lat: lastMap().sightLines!.rays[0]!.coordinates[1]![1] })).toBeCloseTo(0, 3);
    expect(JSON.parse(window.localStorage.getItem("photo-on-map:a1")!).heading).toBe(0);
    expect(screen.getByText(/typical of a phone camera/)).toBeInTheDocument();
  });

  it("places an object only where a person marks it, and can save that as a map note", async () => {
    render("/projects/p1/satellite?view=location&photo=a1");
    await userEvent.click(await screen.findByRole("button", { name: "Mark #1 car on the map" }));
    expect(screen.getByText("Click the map where #1 (car) is")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "click the map north of the camera" }));
    await waitFor(() => expect(lastMap().sightLines?.marked).toHaveLength(1));
    expect(lastMap().sightLines!.marked[0]).toMatchObject({ id: "o1", at: [85.862393, 20.300752], from: [CAMERA.lon, CAMERA.lat] });
    const selected = screen.getByLabelText("Selected object");
    expect(selected).toHaveTextContent("about 111.2 m from the camera (marked by you)");

    await userEvent.click(screen.getByRole("button", { name: "Save as a map note" }));
    await waitFor(() => expect(createAnnotation).toHaveBeenCalledTimes(1));
    const [projectId, body] = createAnnotation.mock.calls[0]!;
    expect(projectId).toBe("p1");
    expect(body).toMatchObject({ kind: "point", geometry: { type: "Point", coordinates: [85.862393, 20.300752] }, crs_id: "EPSG:4326" });
    expect(body.note).toContain("car (#1) seen in photo flyover.jpg");
    expect(await screen.findByRole("button", { name: "Saved as a map note" })).toBeDisabled();

    // Every mark can be removed at once.
    await userEvent.click(screen.getByRole("button", { name: "Remove all marks" }));
    await waitFor(() => expect(lastMap().sightLines?.marked).toEqual([]));
    expect(JSON.parse(window.localStorage.getItem("photo-on-map:a1")!).marks).toEqual({});
  });

  it("hides a class of objects from the map", async () => {
    getCameraView.mockResolvedValue(cameraView({ heading_deg: 90 }));
    render("/projects/p1/satellite?view=location&photo=a1");
    await waitFor(() => expect(lastMap().sightLines?.rays).toHaveLength(2));
    await userEvent.click(screen.getByRole("button", { name: "motorcycle (1)" }));
    await waitFor(() => expect(lastMap().sightLines?.rays.map((ray) => ray.id)).toEqual(["o1"]));
  });
});

// --- placing every object from matched ground spots ----------------------------------------------------------------

/**
 * A synthetic camera for the 400 × 300 test picture: 10 m above flat ground, facing north, tilted 20° down. It gives the
 * pixel where a ground point (east, north metres from the camera) appears, so placements can be checked exactly.
 */
const PITCH = (20 * Math.PI) / 180;
function pixelOf(e: number, n: number): { u: number; v: number } {
  const forward = n * Math.cos(PITCH) + 10 * Math.sin(PITCH);
  const up = n * Math.sin(PITCH) - 10 * Math.cos(PITCH);
  return { u: 200 + (200 * e) / forward, v: 150 - (200 * up) / forward };
}
const SPOTS: [number, number][] = [
  [-6, 15],
  [6, 15],
  [-10, 40],
  [10, 40],
  [0, 25],
];

function picture() {
  const element = screen.getByTestId("image-viewer-picture");
  element.getBoundingClientRect = () => ({ left: 0, top: 0, width: 400, height: 300, right: 400, bottom: 300, x: 0, y: 0, toJSON: () => ({}) });
  return element;
}

async function matchSpot(e: number, n: number) {
  const { u, v } = pixelOf(e, n);
  fireEvent.click(picture(), { clientX: u, clientY: v });
  Object.assign(nextMapClick, fromLocal(CAMERA, { e, n }));
  await userEvent.click(screen.getByRole("button", { name: "click the map at the next spot" }));
}

/** The pixel a map position appears at, through the synthetic camera. */
function pixelAt(at: [number, number]) {
  const local = toLocal(CAMERA, { lon: at[0], lat: at[1] });
  return pixelOf(local.e, local.n);
}

describe("Placing every object from matched ground spots", () => {
  it("pairs a photo click with a map click, and places every object once four spots fit", async () => {
    render("/projects/p1/satellite?view=location&photo=a1");
    await userEvent.click(await screen.findByRole("button", { name: "Match spots" }));
    expect(lastMap().onPhotoSelect).toBeUndefined();

    // A map click before a photo click is not a pair.
    await userEvent.click(screen.getByRole("button", { name: "click the map at the next spot" }));
    expect(screen.getByText("Click the spot in the photo first, then the same spot on the map.")).toBeInTheDocument();

    const { u, v } = pixelOf(-6, 15);
    fireEvent.click(picture(), { clientX: u, clientY: v });
    expect(screen.getByText("Now click the same spot on the satellite map")).toBeInTheDocument();
    expect(screen.getByTestId("pending-spot")).toBeInTheDocument();
    Object.assign(nextMapClick, fromLocal(CAMERA, { e: -6, n: 15 }));
    await userEvent.click(screen.getByRole("button", { name: "click the map at the next spot" }));
    expect(within(screen.getByRole("list", { name: "Matched spots" })).getAllByRole("listitem")).toHaveLength(1);
    expect(lastMap().pinned ?? null).toBeNull(); // matching clicks never pin a point

    for (const [e, n] of SPOTS.slice(1, 3)) await matchSpot(e, n);
    expect(screen.getByTestId("fit-problem")).toHaveTextContent("Match at least 4 spots (3 so far).");

    await matchSpot(...SPOTS[3]!);
    const result = await screen.findByTestId("fit-result");
    expect(result).toHaveTextContent("2 object(s) placed from 4 matched spots"); // the rejected bus is not placed
    expect(result).toHaveTextContent("Add a 5th spot to check the accuracy.");

    const estimated = lastMap().sightLines!.estimated!;
    expect(estimated.map((spot) => spot.id)).toEqual(["o1", "o2"]);
    // Each object sits where the bottom-centre of its box touches the ground.
    const car = pixelAt(estimated[0]!.at);
    expect(car.u).toBeCloseTo(200, 4);
    expect(car.v).toBeCloseTo(140, 4);
    const motorcycle = pixelAt(estimated[1]!.at);
    expect(motorcycle.u).toBeCloseTo(0, 4);
    expect(motorcycle.v).toBeCloseTo(140, 4);
    expect(lastMap().sightLines!.controls).toHaveLength(4);
    expect(lastMap().sightLines!.rays).toEqual([]); // direction lines give way to positions

    await matchSpot(...SPOTS[4]!);
    expect(screen.getByTestId("fit-result")).toHaveTextContent("About ±0.0 m where you matched spots");
    expect(JSON.parse(window.localStorage.getItem("photo-on-map:a1")!).controls).toHaveLength(5);

    await userEvent.click(screen.getByRole("button", { name: "Done matching" }));
    expect(lastMap().onPhotoSelect).toBeDefined();
    await userEvent.click(screen.getByRole("button", { name: "Remove spot 5" }));
    expect(within(screen.getByRole("list", { name: "Matched spots" })).getAllByRole("listitem")).toHaveLength(4);
  });

  it("shows an object's estimated position, and lets a person's own mark win", async () => {
    window.localStorage.setItem(
      "photo-on-map:a1",
      JSON.stringify({ controls: SPOTS.slice(0, 4).map(([e, n]) => ({ ...pixelOf(e, n), ...fromLocal(CAMERA, { e, n }) })) }),
    );
    render("/projects/p1/satellite?view=location&photo=a1");
    await waitFor(() => expect(lastMap().sightLines?.estimated).toHaveLength(2));
    const row = within(screen.getByRole("list", { name: "Objects" })).getAllByRole("listitem")[0]!;
    await userEvent.click(within(row).getAllByRole("button")[0]!);
    expect(screen.getByTestId("estimated-position")).toHaveTextContent("from 4 matched spots");

    await userEvent.click(screen.getByRole("button", { name: "Mark #1 car on the map" }));
    await userEvent.click(screen.getByRole("button", { name: "click the map north of the camera" }));
    await waitFor(() => expect(lastMap().sightLines?.estimated?.map((spot) => spot.id)).toEqual(["o2"]));
    expect(lastMap().sightLines!.marked.map((mark) => mark.id)).toEqual(["o1"]);
  });

  it("saves the positions as map notes only after confirming, and downloads them as GeoJSON", async () => {
    window.localStorage.setItem(
      "photo-on-map:a1",
      JSON.stringify({ controls: SPOTS.map(([e, n]) => ({ ...pixelOf(e, n), ...fromLocal(CAMERA, { e, n }) })) }),
    );
    render("/projects/p1/satellite?view=location&photo=a1");
    await userEvent.click(await screen.findByRole("button", { name: "Save positions as map notes" }));
    expect(createAnnotation).not.toHaveBeenCalled();
    const confirm = screen.getByRole("group", { name: "Confirm saving map notes" });
    // The motorcycle is at the photo's edge, outside the matched spots: left out unless asked for.
    expect(within(confirm).getByRole("button", { name: "Save 1" })).toBeInTheDocument();
    await userEvent.click(within(confirm).getByRole("checkbox", { name: "Include objects outside the matched area" }));
    await userEvent.click(within(confirm).getByRole("button", { name: "Save 2" }));
    await waitFor(() => expect(createAnnotation).toHaveBeenCalledTimes(2));
    const [, body] = createAnnotation.mock.calls[0]!;
    expect(body).toMatchObject({ kind: "point", crs_id: "EPSG:4326", geometry: { type: "Point" } });
    expect(body.note).toContain("car (#1) in photo flyover.jpg");
    expect(body.note).toContain("estimated from 5 ground spots matched between the photo and the satellite map");
    expect(await screen.findByText("2 map note(s) saved. They show on the Map page.")).toBeInTheDocument();

    let saved: Blob | null = null;
    const createObjectURL = vi.fn((blob: Blob) => {
      saved = blob;
      return "blob:objects";
    });
    Object.assign(URL, { createObjectURL, revokeObjectURL: vi.fn() });
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    await userEvent.click(screen.getByRole("button", { name: "Download positions (GeoJSON)" }));
    expect(click).toHaveBeenCalled();
    const text = await new Promise<string>((resolve) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result));
      reader.readAsText(saved!);
    });
    const geojson = JSON.parse(text) as { features: { properties: Record<string, unknown> }[] };
    expect(geojson.features.map((feature) => feature.properties.class)).toEqual(["car", "motorcycle"]);
    expect(geojson.features[0]!.properties).toMatchObject({ object_number: 1, photo: "flyover.jpg", extrapolated: false });
    expect(geojson.features[1]!.properties).toMatchObject({ object_number: 2, extrapolated: true });
    click.mockRestore();
  });
});

describe("where the photo popup goes", () => {
  it("opens above the marker, else below, else beside it, inside the map and never over the marker", () => {
    expect(popupPosition({ x: 600, y: 700 }, 1360, 868)).toEqual({ left: 408, top: 242 });
    expect(popupPosition({ x: 600, y: 100 }, 1360, 868)).toEqual({ left: 408, top: 118 });
    // In the middle of a map too short for either: beside the marker, on the right.
    expect(popupPosition({ x: 448, y: 434 }, 1360, 868)).toEqual({ left: 466, top: 214 });
    // Near the right edge: on the left instead.
    expect(popupPosition({ x: 1200, y: 434 }, 1360, 868)).toEqual({ left: 798, top: 214 });
  });
});

describe("What the AI sees", () => {
  const DRAFT: client.SceneDescriptionDraft = {
    machine_written: true,
    disclaimer: "Draft.",
    asset_id: "a1",
    model: "qwen2.5vl:latest",
    generated_at: "2026-11-07T12:27:28Z",
    attempts: 1,
    scene: "A busy urban road with several lanes of traffic.",
    activity: "Cars and motorcycles are moving; people are walking on the pavement.",
    details: ["Overcast sky.", "Shops line the far side of the road."],
    detector_check: "The counts look consistent with the picture.",
    facts_used: {},
  };

  it("describes the photo once when its panel opens, and shows it in the popup too", async () => {
    getReportSummaryStatus.mockResolvedValue({ enabled: true, reachable: true, model: "qwen2.5vl:latest", model_installed: true, note: "Ready." });
    describeScene.mockResolvedValue(DRAFT);
    render("/projects/p1/satellite?view=location&photo=a1");
    const section = await screen.findByRole("region", { name: "What the AI sees" });
    const text = await within(section).findByTestId("scene-description");
    expect(text).toHaveTextContent("A busy urban road with several lanes of traffic.");
    expect(text).toHaveTextContent("Cars and motorcycles are moving");
    expect(text).toHaveTextContent("Detector check: The counts look consistent with the picture.");
    expect(text).toHaveTextContent("Not reviewed by a person");
    expect(describeScene).toHaveBeenCalledTimes(1);
    expect(describeScene).toHaveBeenCalledWith("a1");

    await userEvent.hover(screen.getByRole("button", { name: "marker flyover.jpg" }));
    expect(await screen.findByTestId("hover-scene")).toHaveTextContent("AI sees: A busy urban road");
    expect(describeScene).toHaveBeenCalledTimes(1); // the popup reuses the description, it never asks again

    await userEvent.click(within(section).getByRole("button", { name: "Describe again" }));
    await waitFor(() => expect(describeScene).toHaveBeenCalledTimes(2));
  });

  it("says why when the local model is not available, and asks nothing", async () => {
    render("/projects/p1/satellite?view=location&photo=a1");
    const section = await screen.findByRole("region", { name: "What the AI sees" });
    expect(await within(section).findByText(/not available: Switched off/)).toBeInTheDocument();
    expect(describeScene).not.toHaveBeenCalled();
  });
});
