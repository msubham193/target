import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Outlet, Route, Routes } from "react-router-dom";
import { vi } from "vitest";

import * as client from "../api/client";
import type { MapCanvasProps } from "../components/MapCanvas";
import { renderPage } from "../test/render";
import { MapPage } from "./MapPage";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    getLayers: vi.fn(),
    listPhotoLocations: vi.fn(),
    listAnnotations: vi.fn(),
    createAnnotation: vi.fn(),
    updateAnnotation: vi.fn(),
    deleteAnnotation: vi.fn(),
    getAnnotationHistory: vi.fn(),
    getRasterTools: vi.fn(),
    getPixel: vi.fn(),
    transformPoint: vi.fn(),
    measurePoints: vi.fn(),
  };
});

// jsdom has no WebGL. The stand-in records what the page asks the map to draw and lets a test click the map, a
// photograph marker or an annotation, exactly as the real map would call back.
const drawn: MapCanvasProps[] = [];
vi.mock("../components/MapCanvas", () => ({
  MapCanvas: (props: MapCanvasProps) => {
    drawn.push(props);
    return (
      <div data-testid="map">
        <button type="button" onClick={() => props.onPick?.({ lon: 75.0005, lat: 27.1222 })}>
          click the map
        </button>
        <button type="button" onClick={() => props.onPhotoSelect?.("a1")}>
          click the photograph marker
        </button>
        <button type="button" onClick={() => props.onAnnotationSelect?.("n1")}>
          click the annotation
        </button>
      </div>
    );
  },
}));

const getLayers = vi.mocked(client.getLayers);
const listPhotoLocations = vi.mocked(client.listPhotoLocations);
const listAnnotations = vi.mocked(client.listAnnotations);
const createAnnotation = vi.mocked(client.createAnnotation);

const PROJECT = { id: "p1", name: "Survey", archived_at: null } as client.Project;
const LAYERS = {
  rasters: [],
  excluded: [],
  aoi: null,
  observations: { with_location: 0, without_location: 0, note: "Only observations with an established geographic location can be drawn." },
  basemap: { configured: false, detail: "No offline basemap is configured (decision U-013 is open)." },
} as unknown as client.Layers;

const LINE = { type: "LineString", coordinates: [[75, 27], [75.01, 27]] };
const GATE = { type: "Point", coordinates: [75.2, 27.3] };

function annotation(over: Record<string, unknown> = {}): client.Annotation {
  return {
    feature_type: "user_annotation", annotation_id: "n1", project_id: "p1", kind: "line", status: "active", version_no: 1, geometry: LINE,
    crs_id: "EPSG:4326", note: "fence", measurement: { length_m: 991.9, area_m2: null, perimeter_m: null, method: "geodesic", limitations: ["not a survey measurement"] },
    created_by: "Asha", created_at: "2026-10-28T10:00:00Z", updated_by: "Asha", updated_at: "2026-10-28T10:00:00Z", deleted_by: null, deleted_at: null, ...over,
  } as unknown as client.Annotation;
}

function listing(items: client.Annotation[]): client.Annotations {
  return { items, active: items.filter((i) => i.status === "active").length, deleted: items.filter((i) => i.status === "deleted").length, include_deleted: false, note: "n" } as client.Annotations;
}

const PHOTO = {
  asset_id: "a1", filename: "gate.jpg", state: "confirmed", source: "exif", latitude: 28.6, longitude: 77.2, version_no: 2,
  confirmed_by: "Asha", confirmed_at: "2026-10-28T10:05:00Z", observations: 0,
} as client.PhotoLocationItem;

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

const last = () => drawn[drawn.length - 1]!;

beforeEach(() => {
  vi.clearAllMocks();
  drawn.length = 0;
  getLayers.mockResolvedValue(LAYERS);
  listPhotoLocations.mockResolvedValue({ items: [PHOTO], confirmed: 1, candidates: 0, without_location: 0, note: "n" } as client.PhotoLocations);
  listAnnotations.mockResolvedValue(listing([]));
});

describe("annotations on the map page", () => {
  it("draws active annotations only, and keeps them apart from photograph markers", async () => {
    // a deleted annotation in the list (a faulty response) must still never be drawn
    listAnnotations.mockResolvedValue(listing([annotation(), annotation({ annotation_id: "n2", kind: "point", geometry: GATE, note: "gate" }), annotation({ annotation_id: "n3", status: "deleted", version_no: 2, note: "old" })]));
    render();
    await waitFor(() => expect(last().annotations?.map((a) => a.id)).toEqual(["n1", "n2"]));
    expect(last().annotations?.[0]?.geometry).toEqual(LINE);
    expect(last().photos).toEqual([{ id: "a1", lon: 77.2, lat: 28.6, label: "gate.jpg" }]); // only the photograph
    expect(last().showAnnotations).toBe(true);
    expect(JSON.stringify(last().photos)).not.toMatch(/n1|n2|fence|gate"/);
  });

  it("asks only for annotations that are not deleted", async () => {
    render();
    await screen.findByRole("group", { name: "Draw an annotation" });
    expect(listAnnotations).toHaveBeenCalledWith("p1", false);
    expect(listAnnotations).not.toHaveBeenCalledWith("p1", true);
  });

  it("hides annotations from the map with the switch, without deleting them", async () => {
    listAnnotations.mockResolvedValue(listing([annotation()]));
    render();
    await userEvent.click(await screen.findByLabelText("Show on the map"));
    await waitFor(() => expect(last().showAnnotations).toBe(false));
    expect(last().annotations).toHaveLength(1);
  });

  it("selects an annotation from the map and from the list, and highlights it", async () => {
    listAnnotations.mockResolvedValue(listing([annotation()]));
    render();
    await waitFor(() => expect(last().onAnnotationSelect).toBeDefined());
    expect(last().selectedAnnotationId).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "click the annotation" }));
    await waitFor(() => expect(last().selectedAnnotationId).toBe("n1"));
    expect(screen.getByRole("list", { name: "Annotations in this project" }).children[0]).toHaveAttribute("aria-current", "true");
  });

  it("sends clicks to the shape being drawn, not to the pin, the photographs or the annotations", async () => {
    listAnnotations.mockResolvedValue(listing([annotation()]));
    createAnnotation.mockResolvedValue(annotation({ annotation_id: "n9", note: "new fence" }));
    render();
    await userEvent.click(await screen.findByRole("button", { name: "Add line" }));
    await waitFor(() => expect(last().onAnnotationSelect).toBeUndefined());
    expect(last().onPhotoSelect).toBeUndefined();
    expect(last().onPhotoChoice).toBeUndefined();

    await userEvent.click(screen.getByRole("button", { name: "click the map" }));
    await userEvent.click(screen.getByRole("button", { name: "click the map" }));
    await waitFor(() => expect(last().sketch).toEqual({ kind: "distance", points: [[75.0005, 27.1222], [75.0005, 27.1222]] }));
    expect(last().pinned).toBeNull();
    expect(screen.queryByRole("status", { name: "Pinned point" })).not.toBeInTheDocument();
    // clicking where a photograph marker is does not select the photograph
    await userEvent.click(screen.getByRole("button", { name: "click the photograph marker" }));
    expect(last().selectedPhotoId).toBeNull();

    await userEvent.type(screen.getByLabelText("Note"), "new fence");
    await userEvent.click(screen.getByRole("button", { name: "Save annotation" }));
    await waitFor(() => expect(createAnnotation).toHaveBeenCalledTimes(1));
    expect(createAnnotation).toHaveBeenCalledWith("p1", {
      kind: "line",
      geometry: { type: "LineString", coordinates: [[75.0005, 27.1222], [75.0005, 27.1222]] },
      crs_id: "EPSG:4326",
      note: "new fence",
    });
    // saved: the draft is gone and the map listens to photographs and annotations again
    await waitFor(() => expect(last().sketch).toBeNull());
    await waitFor(() => expect(last().onPhotoSelect).toBeDefined());
    expect(last().onAnnotationSelect).toBeDefined();
    expect(last().selectedAnnotationId).toBe("n9");
  });

  it("draws a polygon in progress as an area sketch and a point as the pinned marker", async () => {
    render();
    await userEvent.click(await screen.findByRole("button", { name: "Add polygon" }));
    for (let i = 0; i < 3; i++) await userEvent.click(screen.getByRole("button", { name: "click the map" }));
    await waitFor(() => expect(last().sketch?.kind).toBe("area"));
    expect(last().sketch?.points).toHaveLength(3);
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(last().sketch).toBeNull());

    await userEvent.click(screen.getByRole("button", { name: "Add point" }));
    await userEvent.click(screen.getByRole("button", { name: "click the map" }));
    await waitFor(() => expect(last().pinned).toEqual({ lon: 75.0005, lat: 27.1222 }));
    expect(last().sketch).toBeNull();
    // a second click moves the point rather than adding another
    await userEvent.click(screen.getByRole("button", { name: "click the map" }));
    expect(within(screen.getByRole("form", { name: "New annotation" })).getByText(/27\.122200°, 75\.000500°/)).toBeInTheDocument();
    expect(createAnnotation).not.toHaveBeenCalled(); // nothing is saved by clicking
  });

  it("leaves the map's pin and measurement tools to their own state", async () => {
    render();
    await userEvent.click(await screen.findByRole("button", { name: "click the map" }));
    await waitFor(() => expect(last().pinned).toEqual({ lon: 75.0005, lat: 27.1222 }));
    // choosing a measurement tool stops the map from selecting annotations, so its clicks add measurement points
    await userEvent.click(screen.getByRole("button", { name: "Measure distance" }));
    await waitFor(() => expect(last().onAnnotationSelect).toBeUndefined());
    await userEvent.click(screen.getByRole("button", { name: "click the map" }));
    await waitFor(() => expect(last().sketch?.kind).toBe("distance"));
    expect(createAnnotation).not.toHaveBeenCalled();
  });

  it("does not turn the empty-map message on when only annotations exist", async () => {
    listAnnotations.mockResolvedValue(listing([annotation()]));
    render();
    await waitFor(() => expect(last().annotations).toHaveLength(1));
    expect(screen.queryByText("Nothing to show on the map yet")).not.toBeInTheDocument();
  });

  it("says annotations are unavailable, without breaking the rest of the page, when the database is not updated", async () => {
    listAnnotations.mockRejectedValue(new client.ApiError(503, "ANNOTATIONS_NOT_MIGRATED", "Annotations are not available yet"));
    render();
    expect(await screen.findByText("Annotations are not available yet")).toBeInTheDocument();
    expect(screen.getByTestId("map")).toBeInTheDocument();
    expect(last().annotations).toEqual([]);
    expect(screen.getByRole("list", { name: "Located photographs" })).toHaveTextContent("gate.jpg"); // the rest still works
  });
});
