import { fireEvent, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Outlet, Route, Routes } from "react-router-dom";
import { vi } from "vitest";

import * as client from "../api/client";
import type { MapCanvasProps } from "../components/MapCanvas";
import { renderPage } from "../test/render";
import { MapPage } from "./MapPage";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, getLayers: vi.fn(), transformPoint: vi.fn(), measurePoints: vi.fn(), getPixel: vi.fn(), getRasterTools: vi.fn() };
});

// jsdom has no WebGL. The stand-in records what the page asks the map to draw and lets a test "click" the map.
const drawn: MapCanvasProps[] = [];
vi.mock("../components/MapCanvas", () => ({
  MapCanvas: (props: MapCanvasProps) => {
    drawn.push(props);
    return (
      <div data-testid="map">
        <button type="button" onClick={() => props.onPick?.({ lon: 75.0005, lat: 27.1222 })}>
          click the map
        </button>
      </div>
    );
  },
}));

const PROJECT = { id: "p1", name: "Survey", archived_at: null } as client.Project;

const LAYER: client.RasterLayer = {
  asset_id: "r1",
  title: "harbour_utm43n.tif",
  crs_id: "EPSG:32643",
  crs_name: "WGS 84 / UTM zone 43N",
  placement: "trusted",
  georef_version: 0,
  band_count: 3,
  dtypes: ["uint8", "uint8", "uint8"],
  bounds_4326: [75, 27.12, 75.02, 27.1225],
  footprint_4326: { type: "Polygon", coordinates: [] },
  tile_url: "/api/v1/assets/r1/tiles/{z}/{x}/{y}.png",
  display_unavailable_reason: null,
  min_zoom: 0,
  max_zoom: 17,
  native_zoom: 14,
  bands_shown: [1, 2, 3],
  display_stretch: "none (8-bit values shown as stored)",
  pixel_size: [10, 10],
  pixel_units: "metre",
  footprint_transformation: { definition: "proj=pipeline step inv proj=utm zone=43", accuracy_m: 0, proj_version: "9.5.1" },
};

function layers(overrides: Partial<client.Layers> = {}): client.Layers {
  return {
    rasters: [LAYER],
    excluded: [
      { asset_id: "s1", title: "scan.tif", modality: "overhead_raster", reason: "CRS_MISSING", detail: "The file declares no coordinate reference system. None is assumed.", georeferencing_can_be_recorded: true },
      { asset_id: "p1", title: "photo.jpg", modality: "ground_image", reason: "NOT_GEOREFERENCED", detail: "Ground photographs carry no georeferencing.", georeferencing_can_be_recorded: false },
    ],
    aoi: null,
    observations: { with_location: 0, without_location: 3, note: "Only observations with an established geographic location can be drawn." },
    basemap: { configured: false, detail: "No offline basemap is configured (decision U-013 is open)." },
    ...overrides,
  };
}

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

beforeEach(() => {
  drawn.length = 0;
  vi.clearAllMocks();
});

const last = () => drawn[drawn.length - 1]!;

describe("MapPage", () => {
  it("draws only placeable rasters and lists the rest with the reason", async () => {
    vi.mocked(client.getLayers).mockResolvedValue(layers());
    render();
    await screen.findByTestId("map");
    expect(last().rasters.map((r) => r.layer.asset_id)).toEqual(["r1"]);
    expect(last().fitTo).toEqual([75, 27.12, 75.02, 27.1225]);
    const excluded = screen.getByRole("list", { name: "Files not placed on the map" });
    expect(excluded).toHaveTextContent("scan.tifNo CRS in the file");
    expect(excluded).toHaveTextContent("photo.jpgNo georeferencing");
    // Only the raster can have georeferencing recorded; the photograph cannot.
    const links = within(excluded).getAllByRole("link", { name: "Record georeferencing…" });
    expect(links).toHaveLength(1);
    expect(links[0]).toHaveAttribute("href", "/projects/p1/assets/s1#georeferencing");
    expect(screen.getByText(/No offline basemap is configured/)).toBeInTheDocument();
    expect(screen.getByText(/0 finding\(s\) have a known location and 3 do not/)).toBeInTheDocument();
  });

  it("changes visibility and opacity of a layer", async () => {
    vi.mocked(client.getLayers).mockResolvedValue(layers());
    render();
    const list = await screen.findByRole("list", { name: "Raster layers" });
    await userEvent.click(within(list).getByRole("checkbox", { name: /harbour_utm43n.tif/ }));
    expect(last().rasters[0]!.visible).toBe(false);
    const slider = within(list).getByLabelText("Opacity");
    // jsdom does not move range inputs with the keyboard; set the value as the browser would.
    fireEvent.change(slider, { target: { value: "0.9" } });
    expect(last().rasters[0]!.opacity).toBeCloseTo(0.9);
    expect(list).toHaveTextContent("90%");
  });

  it("pins a clicked point and shows it in decimal degrees, DMS and the layer's CRS", async () => {
    vi.mocked(client.getLayers).mockResolvedValue(layers());
    vi.mocked(client.transformPoint).mockResolvedValue({
      source_crs_id: "EPSG:4326",
      target_crs_id: "EPSG:32643",
      x: 500049.6,
      y: 2999976.1,
      target_axes: [
        { abbreviation: "E", direction: "east", unit: "metre" },
        { abbreviation: "N", direction: "north", unit: "metre" },
      ],
      xy_axes: [
        { abbreviation: "E", direction: "east", unit: "metre" },
        { abbreviation: "N", direction: "north", unit: "metre" },
      ],
      order_note: "x is easting or longitude and y is northing or latitude, whatever the authority's axis order.",
      transformation: { definition: "...", accuracy_m: 0, proj_version: "9.5.1", network: "disabled" },
      warnings: [],
    });
    render();
    await userEvent.click(await screen.findByRole("button", { name: "click the map" }));
    const pinned = screen.getByRole("status", { name: "Pinned point" });
    expect(pinned).toHaveTextContent("27.122200° N · 27°07′19.92″N");
    expect(pinned).toHaveTextContent("75.000500° E · 75°00′01.80″E");
    expect(await within(pinned).findByText("500049.600 metre")).toBeInTheDocument();
    expect(client.transformPoint).toHaveBeenCalledWith({ source: "EPSG:4326", target: "EPSG:32643", x: 75.0005, y: 27.1222 });
  });

  it("asks before reading a point out through a datum shift worse than 10 m, for this readout only", async () => {
    vi.mocked(client.getLayers).mockResolvedValue(layers());
    const ok = {
      source_crs_id: "EPSG:4326",
      target_crs_id: "EPSG:32643",
      x: 500049.6,
      y: 2999976.1,
      target_axes: [],
      xy_axes: [],
      order_note: "",
      transformation: { definition: "...", accuracy_m: 999, proj_version: "9.5.1", network: "disabled" },
      warnings: ["ACKNOWLEDGED_BY_REQUEST"],
    };
    vi.mocked(client.transformPoint)
      .mockRejectedValueOnce(
        new client.ApiError(409, "TRANSFORMATION_ACKNOWLEDGEMENT_REQUIRED", "Acknowledge", "Needs it.", null, [], {
          operation_id: "EPSG:1247",
          operation_name: "Kalianpur 1962 to WGS 84 (1)",
          accuracy_m: 999,
          threshold_m: 10,
          reason: "accuracy_above_threshold",
          stale_acknowledgement: false,
        }),
      )
      .mockResolvedValueOnce(ok);
    render();
    await userEvent.click(await screen.findByRole("button", { name: "click the map" }));
    const pinned = screen.getByRole("status", { name: "Pinned point" });
    const box = await within(pinned).findByTestId("accuracy-acknowledgement");
    expect(box).toHaveTextContent("For this readout only. Nothing is stored");
    const show = within(pinned).getByRole("button", { name: "Show the readout with this shift" });
    expect(show).toBeDisabled();
    await userEvent.click(within(box).getByRole("checkbox"));
    await userEvent.click(show);
    expect(await within(pinned).findByText("500049.600")).toBeInTheDocument();
    expect(client.transformPoint).toHaveBeenLastCalledWith(
      expect.objectContaining({ acknowledge_operation: "EPSG:1247", acknowledge_accuracy_m: 999 }),
    );
  });

  it("labels a layer placed from recorded georeferencing", async () => {
    vi.mocked(client.getLayers).mockResolvedValue(layers({ rasters: [{ ...LAYER, placement: "user_asserted", georef_version: 2 }] }));
    render();
    const list = await screen.findByRole("list", { name: "Raster layers" });
    expect(list).toHaveTextContent("recorded georeferencing, version 2");
    await userEvent.click(screen.getByRole("button", { name: "Details" }));
    expect(screen.getByText("Placed from recorded georeferencing")).toBeInTheDocument();
    expect(screen.getByText(/not read from the file/)).toBeInTheDocument();
  });

  it("shows layer details including how its outline was transformed", async () => {
    vi.mocked(client.getLayers).mockResolvedValue(layers());
    render();
    await userEvent.click(await screen.findByRole("button", { name: "Details" }));
    expect(last().selectedRasterId).toBe("r1");
    expect(screen.getByText("proj=pipeline step inv proj=utm zone=43")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open in Satellite Images" })).toHaveAttribute("href", "/projects/p1/assets/r1/overhead");
  });

  it("asks for a datum transformation before placing a layer where several apply, and applies only the one chosen", async () => {
    const candidate = (id: string, accuracy: number): client.LayerTransformation["candidates"][number] => ({
      id,
      name: `Indian 1975 to WGS 84 (${id.slice(-1)})`,
      classification: "approximate",
      accuracy_m: accuracy,
      authority_code: id,
      area_of_use: null,
      grids: [],
      summary: `about ${accuracy} m`,
      usable: true,
      recommended: false,
      selected: false,
      requires_acknowledgement: false,
      acknowledgement_threshold_m: 10,
    });
    const open: client.RasterLayer = {
      ...LAYER,
      tile_url: null,
      display_unavailable_reason: "Choose a transformation: 2 registered operations apply.",
      transformation: {
        status: "selection_required",
        classification: null,
        operation_id: null,
        accuracy_m: null,
        chosen_by: null,
        selection_required: true,
        message: "Choose a transformation: 2 registered operations apply.",
        candidates: [candidate("EPSG:1812", 3), candidate("EPSG:1304", 5)],
        missing_grids: [],
        requires_acknowledgement: false,
        acknowledged: false,
      },
    };
    const placed: client.RasterLayer = {
      ...LAYER,
      transformation: { ...open.transformation!, classification: "approximate", operation_id: "EPSG:1304", accuracy_m: 5, chosen_by: "selection", selection_required: false },
    };
    vi.mocked(client.getLayers).mockImplementation(async (_project, operations = []) => layers({ rasters: [operations.length > 0 ? placed : open] }));
    render();
    const choice = await screen.findByTestId("transformation-choice");
    expect(choice).toHaveTextContent("Choose a datum transformation to place this layer");
    expect(last().rasters[0]!.layer.tile_url).toBeNull(); // nothing is drawn through a default
    const list = screen.getByRole("list", { name: "Raster layers" });
    expect(within(list).getByRole("checkbox")).toBeDisabled();
    expect(within(list).queryByLabelText("Opacity")).not.toBeInTheDocument();
    const select = within(choice).getByRole("combobox", { name: /Datum transformation for harbour_utm43n.tif/ });
    expect(select).toHaveValue(""); // the smaller figure is not pre-selected
    expect(within(select).getAllByRole("option")).toHaveLength(3);

    await userEvent.selectOptions(select, "EPSG:1304");
    await vi.waitFor(() => expect(client.getLayers).toHaveBeenLastCalledWith("p1", ["r1:EPSG:1304"]));
    expect(await screen.findByTestId("approximate-note")).toHaveTextContent("Approximate: EPSG:1304, accuracy about 5 m");
    expect(screen.queryByTestId("transformation-choice")).not.toBeInTheDocument();
  });

  it("labels an approximate layer with its datum shift and documented accuracy", async () => {
    const approximate: client.RasterLayer = {
      ...LAYER,
      transformation: {
        status: "approximate",
        classification: "approximate",
        operation_id: "EPSG:1247",
        accuracy_m: 999,
        chosen_by: "only_option",
        selection_required: false,
        message: "One registered datum shift applies.",
        candidates: [],
        missing_grids: [],
        // As for a raster whose recorded georeferencing carries the acknowledgement.
        requires_acknowledgement: true,
        acknowledged: true,
      },
    };
    vi.mocked(client.getLayers).mockResolvedValue(layers({ rasters: [approximate] }));
    render();
    expect(await screen.findByTestId("approximate-note")).toHaveTextContent("Approximate: EPSG:1247, accuracy about 999 m");
    await userEvent.click(screen.getByRole("button", { name: "Details" }));
    expect(screen.getByText("Approximate: EPSG:1247, accuracy about 999 m", { selector: "dd" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /CRS assessment: datum, projection and transformation options/ })).toHaveAttribute(
      "href",
      "/projects/p1/assets/r1#crs-assessment",
    );
  });

  it("lists a layer whose only transformation is a blocked ballpark as not placed, with the reason", async () => {
    vi.mocked(client.getLayers).mockResolvedValue(
      layers({
        rasters: [],
        excluded: [
          {
            asset_id: "k1",
            title: "kalianpur_zone1.tif",
            modality: "overhead_raster",
            reason: "TRANSFORMATION_BLOCKED",
            detail: "Only a rough ballpark transformation is available. It is blocked.",
            georeferencing_can_be_recorded: true,
          },
        ],
      }),
    );
    render();
    const excluded = await screen.findByRole("list", { name: "Files not placed on the map" });
    expect(excluded).toHaveTextContent("kalianpur_zone1.tifDatum transformation blocked");
    expect(excluded).toHaveTextContent("Only a rough ballpark transformation is available. It is blocked.");
  });

  it("says so when nothing can be placed and never draws an area of interest that was not entered", async () => {
    vi.mocked(client.getLayers).mockResolvedValue(layers({ rasters: [] }));
    render();
    expect(await screen.findByText("Nothing in this project can be placed on a map")).toBeInTheDocument();
    expect(last().aoi).toBeNull();
    expect(screen.getByRole("checkbox", { name: /Project area of interest/ })).toBeDisabled();
  });

  it("draws the entered area of interest as its own layer", async () => {
    vi.mocked(client.getLayers).mockResolvedValue(
      layers({
        rasters: [],
        aoi: { geometry_4326: { type: "Point", coordinates: [75, 27.1] }, entered_crs_id: "EPSG:32643", note: "Entered by a person." },
      }),
    );
    render();
    await screen.findByTestId("map");
    expect(last().aoi).toEqual({ type: "Point", coordinates: [75, 27.1] });
    const [west, south, east, north] = last().fitTo!;
    expect([west, south, east, north].map((v) => Number(v.toFixed(6)))).toEqual([74.99, 27.09, 75.01, 27.11]);
  });

  it("shows a layer that cannot be drawn yet with the reason and no toggle", async () => {
    vi.mocked(client.getLayers).mockResolvedValue(
      layers({ rasters: [{ ...LAYER, tile_url: null, display_unavailable_reason: "The preview is not ready yet." }] }),
    );
    render();
    const list = await screen.findByRole("list", { name: "Raster layers" });
    expect(list).toHaveTextContent("The preview is not ready yet.");
    expect(within(list).getByRole("checkbox")).toBeDisabled();
  });

  it("measures on the map geodesically, from the points placed", async () => {
    vi.mocked(client.getLayers).mockResolvedValue(layers());
    vi.mocked(client.measurePoints).mockResolvedValue({
      kind: "distance",
      validity: "valid",
      value: 1234.5,
      unit: "m",
      method: "geodesic on the WGS 84 ellipsoid (PROJ geodesic, Karney)",
      perimeter_m: null,
      planar: null,
      pixel_value: null,
      pixel_unit: null,
      flags: [],
      note: "Measured between the points placed on the map; it is not a measurement of any feature.",
      points_lonlat: null,
    });
    render();
    await userEvent.click(await screen.findByRole("button", { name: "Measure distance" }));
    await userEvent.click(screen.getByRole("button", { name: "click the map" }));
    await userEvent.click(screen.getByRole("button", { name: "click the map" }));
    expect(last().sketch).toEqual({ kind: "distance", points: [[75.0005, 27.1222], [75.0005, 27.1222]] });
    expect(last().pinned).toBeNull(); // measuring does not pin points
    await userEvent.click(screen.getByRole("button", { name: "Measure" }));
    expect(client.measurePoints).toHaveBeenCalledWith({ kind: "distance", space: "lonlat", points: [[75.0005, 27.1222], [75.0005, 27.1222]] });
    const result = await screen.findByRole("status", { name: "Measurement result" });
    expect(result).toHaveTextContent("1234.5 m");
    expect(result).toHaveTextContent("not a measurement of any feature");
  });

  it("shows the stored values of the rasters under a pinned point", async () => {
    vi.mocked(client.getLayers).mockResolvedValue(layers({ rasters: [{ ...LAYER, bounds_4326: [75, 27.12, 75.02, 27.13] }] }));
    vi.mocked(client.transformPoint).mockResolvedValue({
      source_crs_id: "EPSG:4326", target_crs_id: "EPSG:32643", x: 1, y: 2, target_axes: [], xy_axes: [], order_note: "", transformation: {}, warnings: [],
    });
    vi.mocked(client.getPixel).mockResolvedValue({
      col: 12, row: 40, values: [{ band: 1, raw: 87, nodata: false, scaled: null, units: null, description: null }],
      dtypes: ["uint8"], native: null, lon_lat: [75.0005, 27.1222], georeferencing_source: "file", note: "",
    });
    render();
    await userEvent.click(await screen.findByRole("button", { name: "click the map" }));
    const values = await screen.findByRole("list", { name: "Raster values at the pinned point" });
    expect(await within(values).findByText(/pixel 12, 40/)).toBeInTheDocument();
    expect(values).toHaveTextContent("87");
    expect(client.getPixel).toHaveBeenCalledWith("r1", { lon: 75.0005, lat: 27.1222 });
  });
});
