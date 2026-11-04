import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Outlet, Route, Routes } from "react-router-dom";
import { vi } from "vitest";

import * as client from "../api/client";
import type { MapCanvasProps } from "../components/MapCanvas";
import { MeasurementResult } from "../components/RasterTools";
import { renderPage } from "../test/render";
import { MapPage } from "./MapPage";
import { NewProjectPage } from "./NewProjectPage";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    getLayers: vi.fn(),
    getPixel: vi.fn(),
    createProject: vi.fn(),
    searchCrs: vi.fn(),
    listAnnotations: vi.fn(),
    listPhotoLocations: vi.fn(),
    transformPoint: vi.fn(),
    getRasterTools: vi.fn(),
  };
});

vi.mock("../components/MapCanvas", () => ({
  MapCanvas: (props: MapCanvasProps) => (
    <button type="button" onClick={() => props.onPick?.({ lon: 100.1, lat: 14.1 })}>
      click the map
    </button>
  ),
}));

const PROJECT = { id: "p1", name: "Survey", archived_at: null } as client.Project;

// --- an area of interest that needs a datum choice ----------------------------------------------------------------------

const KALIANPUR = {
  identifier: "EPSG:24047",
  name: "Indian 1975 / UTM zone 47N",
  kind: "projected",
  units: ["metre"],
  axis_order: [],
  area_of_use: null,
  deprecated: false,
} as unknown as client.Crs;

describe("an area of interest in a CRS with several datum transformations", () => {
  beforeEach(() => {
    vi.mocked(client.createProject).mockReset();
    vi.mocked(client.searchCrs).mockResolvedValue({ items: [KALIANPUR], registry: "PROJ (local)" } as never);
  });

  it("asks which transformation to use, chooses none for the person, and sends the choice with the retry", async () => {
    vi.mocked(client.createProject)
      .mockRejectedValueOnce(
        new client.ApiError(409, "TRANSFORMATION_SELECTION_REQUIRED", "Choose a transformation", "Two apply.", null, [
          { field: "EPSG:1812", message: "Indian 1975 to WGS 84 (4): about 3 m" },
          { field: "EPSG:1304", message: "Indian 1975 to WGS 84 (2): about 5 m" },
        ]),
      )
      .mockResolvedValueOnce({ id: "p9" } as client.Project);
    const user = userEvent.setup();
    renderPage(<NewProjectPage />);
    await user.type(screen.getByLabelText("Name"), "Thailand survey");
    await user.click(screen.getByLabelText("Add an area of interest"));
    await user.type(screen.getByLabelText("X (first coordinate)"), "619000");
    await user.type(screen.getByLabelText("Y (second coordinate)"), "1558000");
    await user.type(screen.getByPlaceholderText(/EPSG:4326/), "24047");
    await user.click(screen.getByRole("button", { name: "Search" }));
    await user.click(await screen.findByRole("button", { name: /EPSG:24047/ }));
    await user.click(screen.getByRole("button", { name: "Create project" }));

    const choice = await screen.findByTestId("aoi-transformation-choice");
    expect(client.createProject).toHaveBeenCalledTimes(1);
    expect(vi.mocked(client.createProject).mock.calls[0]![0].aoi).not.toHaveProperty("transformation_operation");
    const select = within(choice).getByLabelText("Datum transformation to WGS 84");
    expect(select).toHaveValue(""); // never pre-selected
    expect(within(select).getAllByRole("option").map((o) => (o as HTMLOptionElement).value)).toEqual(["", "EPSG:1812", "EPSG:1304"]);

    await user.selectOptions(select, "EPSG:1812");
    await user.click(screen.getByRole("button", { name: "Create project" }));
    expect(client.createProject).toHaveBeenCalledTimes(2);
    expect(vi.mocked(client.createProject).mock.calls[1]![0].aoi).toMatchObject({
      crs_id: "EPSG:24047",
      transformation_operation: "EPSG:1812",
    });
  });

  it("asks for an acknowledgement of a datum shift worse than 10 m, never pre-ticked, and sends it with the retry", async () => {
    vi.mocked(client.createProject)
      .mockRejectedValueOnce(
        new client.ApiError(409, "TRANSFORMATION_ACKNOWLEDGEMENT_REQUIRED", "Acknowledge the transformation accuracy", "Needs it.", null, [], {
          operation_id: "EPSG:1247",
          operation_name: "Kalianpur 1962 to WGS 84 (1)",
          accuracy_m: 999,
          threshold_m: 10,
          reason: "accuracy_above_threshold",
          stale_acknowledgement: false,
        }),
      )
      .mockResolvedValueOnce({ id: "p9" } as client.Project);
    const user = userEvent.setup();
    renderPage(<NewProjectPage />);
    await user.type(screen.getByLabelText("Name"), "Delhi survey");
    await user.click(screen.getByLabelText("Add an area of interest"));
    await user.type(screen.getByLabelText("X (first coordinate)"), "500000");
    await user.type(screen.getByLabelText("Y (second coordinate)"), "3170000");
    await user.type(screen.getByPlaceholderText(/EPSG:4326/), "24047");
    await user.click(screen.getByRole("button", { name: "Search" }));
    await user.click(await screen.findByRole("button", { name: /EPSG:24047/ }));
    await user.click(screen.getByRole("button", { name: "Create project" }));

    const box = await screen.findByTestId("accuracy-acknowledgement");
    expect(vi.mocked(client.createProject).mock.calls[0]![0].aoi).toMatchObject({ acknowledge_accuracy: false });
    expect(box).toHaveTextContent("about 999 m, worse than the 10 m limit");
    expect(box).toHaveTextContent("recorded with this area of interest, with your name and the time");
    const tick = within(box).getByRole("checkbox");
    expect((tick as HTMLInputElement).checked).toBe(false);

    await user.click(tick);
    await user.click(screen.getByRole("button", { name: "Create project" }));
    expect(vi.mocked(client.createProject).mock.calls[1]![0].aoi).toMatchObject({ crs_id: "EPSG:24047", acknowledge_accuracy: true });
  });

  it("does not offer a choice for other failures", async () => {
    vi.mocked(client.createProject).mockRejectedValue(
      new client.ApiError(409, "TRANSFORMATION_BALLPARK_BLOCKED", "Only a rough ballpark transformation is available", "Blocked."),
    );
    const user = userEvent.setup();
    renderPage(<NewProjectPage />);
    await user.type(screen.getByLabelText("Name"), "India");
    await user.click(screen.getByLabelText("Add an area of interest"));
    await user.type(screen.getByLabelText("X (first coordinate)"), "1");
    await user.type(screen.getByLabelText("Y (second coordinate)"), "1");
    await user.type(screen.getByPlaceholderText(/EPSG:4326/), "24047");
    await user.click(screen.getByRole("button", { name: "Search" }));
    await user.click(await screen.findByRole("button", { name: /EPSG:24047/ }));
    await user.click(screen.getByRole("button", { name: "Create project" }));
    expect(await screen.findByText("Only a rough ballpark transformation is available")).toBeInTheDocument();
    expect(screen.queryByTestId("aoi-transformation-choice")).not.toBeInTheDocument();
  });
});

// --- the map readout follows the layer's transformation ----------------------------------------------------------------------

const LAYER = {
  asset_id: "r1",
  title: "thai.tif",
  crs_id: "EPSG:24047",
  crs_name: "Indian 1975 / UTM zone 47N",
  placement: "trusted",
  georef_version: 0,
  band_count: 1,
  dtypes: ["uint8"],
  bounds_4326: [100, 14, 100.2, 14.2],
  footprint_4326: { type: "Polygon", coordinates: [] },
  tile_url: null,
  display_unavailable_reason: "Choose a datum transformation to place this layer",
  min_zoom: 0,
  max_zoom: 17,
  native_zoom: 14,
  bands_shown: [1],
  display_stretch: "none",
  pixel_size: [10, 10],
  pixel_units: "metre",
  footprint_transformation: null,
  transformation: {
    status: "selection_required",
    classification: null,
    operation_id: null,
    accuracy_m: null,
    chosen_by: null,
    selection_required: true,
    message: "Choose",
    candidates: [
      { id: "EPSG:1812", name: "Indian 1975 to WGS 84 (4)", classification: "approximate", accuracy_m: 3, summary: "about 3 m", usable: true, recommended: true, selected: false, grids: [], authority_code: "EPSG:1812", area_of_use: null },
      { id: "EPSG:1304", name: "Indian 1975 to WGS 84 (2)", classification: "approximate", accuracy_m: 5, summary: "about 5 m", usable: true, recommended: false, selected: false, grids: [], authority_code: "EPSG:1304", area_of_use: null },
    ],
    missing_grids: [],
  },
} as unknown as client.RasterLayer;

function renderMap() {
  vi.mocked(client.getLayers).mockResolvedValue({
    rasters: [LAYER],
    excluded: [],
    aoi: null,
    observations: { with_location: 0, without_location: 0, note: "" },
    basemap: { configured: false, detail: "No offline basemap." },
  } as client.Layers);
  vi.mocked(client.listAnnotations).mockResolvedValue({ items: [] } as never);
  vi.mocked(client.listPhotoLocations).mockResolvedValue({ items: [] } as never);
  return renderPage(
    <Routes>
      <Route path="/projects/:projectId" element={<Outlet context={PROJECT} />}>
        <Route path="map" element={<MapPage />} />
      </Route>
    </Routes>,
    "/projects/p1/map",
  );
}

describe("the map's pixel readout", () => {
  beforeEach(() => {
    vi.mocked(client.getPixel).mockReset();
    vi.mocked(client.getLayers).mockReset();
  });

  it("explains why a position could not be looked up when no transformation has been chosen", async () => {
    vi.mocked(client.getPixel).mockRejectedValue(
      new client.ApiError(409, "TRANSFORMATION_SELECTION_REQUIRED", "Choose a transformation", "Two apply."),
    );
    const user = userEvent.setup();
    renderMap();
    await user.click(await screen.findByRole("button", { name: "click the map" }));
    const values = await screen.findByRole("list", { name: "Raster values at the pinned point" });
    expect(values).toHaveTextContent("not looked up: Choose a transformation");
    expect(values).not.toHaveTextContent("outside the raster");
    expect(client.getPixel).toHaveBeenCalledWith("r1", { lon: 100.1, lat: 14.1, operation: undefined });
  });

  it("looks the point up with the transformation chosen for that layer", async () => {
    vi.mocked(client.getPixel).mockResolvedValue({
      col: 3, row: 4, values: [{ band: 1, raw: 7, nodata: false, scaled: null, units: null, description: null }], dtypes: ["uint8"],
      native: null, lon_lat: null, lon_lat_note: null, transformation: null, georeferencing_source: "file", note: "",
    } as client.PixelValues);
    const user = userEvent.setup();
    renderMap();
    await user.selectOptions(await screen.findByLabelText("Datum transformation for thai.tif"), "EPSG:1812");
    await user.click(await screen.findByRole("button", { name: "click the map" }));
    const values = await screen.findByRole("list", { name: "Raster values at the pinned point" });
    expect(await within(values).findByText(/pixel 3, 4/)).toBeInTheDocument();
    expect(client.getPixel).toHaveBeenLastCalledWith("r1", { lon: 100.1, lat: 14.1, operation: "EPSG:1812" });
  });
});

// --- a measurement whose points have no longitude/latitude ----------------------------------------------------------------

describe("a measurement with a conversion that may not be used", () => {
  it("keeps the measured value and says why the points have no longitude/latitude", () => {
    renderPage(
      <MeasurementResult
        result={{
          kind: "distance", validity: "valid", value: 911.5, unit: "m",
          method: "pixel grid → Kalianpur 1880 / India zone I via the affine geotransform → geodesic on the Everest ellipsoid",
          perimeter_m: null, planar: null, pixel_value: 100, pixel_unit: "pixels",
          flags: ["TRANSFORMATION_BALLPARK_BLOCKED"], note: "Measured between the points given.",
          points_lonlat: null,
          points_lonlat_note: "Only a rough ballpark transformation is available. It is blocked.",
          transformation: null,
        }}
      />,
    );
    const result = screen.getByRole("status", { name: "Measurement result" });
    expect(result).toHaveTextContent("911");
    expect(screen.getByTestId("lonlat-note")).toHaveTextContent("No longitude/latitude for these points: Only a rough ballpark");
  });
});
