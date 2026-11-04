import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Outlet, Route, Routes } from "react-router-dom";
import { vi } from "vitest";

import * as client from "../api/client";
import type { MapCanvasProps } from "../components/MapCanvas";
import { renderPage } from "../test/render";
import { MapPage } from "./MapPage";
import { MapSheetsPage } from "./MapSheetsPage";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    getLayers: vi.fn(),
    listMapSheets: vi.fn(),
    uploadMapSheet: vi.fn(),
    getOverlayAlignment: vi.fn(),
    transformPoint: vi.fn(),
    getRasterTools: vi.fn(),
    listAnnotations: vi.fn(),
    listPhotoLocations: vi.fn(),
  };
});

const drawn: MapCanvasProps[] = [];
vi.mock("../components/MapCanvas", () => ({
  MapCanvas: (props: MapCanvasProps) => {
    drawn.push(props);
    return <div data-testid="map" />;
  },
}));

const PROJECT = { id: "p1", name: "Survey", archived_at: null } as client.Project;
const last = () => drawn[drawn.length - 1]!;

// --- the map-sheet intake page ---------------------------------------------------------------------------------------

function renderSheets(project: client.Project = PROJECT) {
  return renderPage(
    <Routes>
      <Route path="/projects/:projectId" element={<Outlet context={project} />}>
        <Route path="assets/map-sheets" element={<MapSheetsPage />} />
      </Route>
    </Routes>,
    "/projects/p1/assets/map-sheets",
  );
}

function sheetInfo(overrides: Record<string, unknown> = {}): client.MapSheetInfo {
  return {
    declaration: {
      series: "osm",
      series_label: "Open Series Map (OSM)",
      basis: "declared_by_uploader",
      sheet_label: "NF 43-01",
      scale_denominator: 250000,
      edition: null,
      declared_by: "Asha",
      note: "",
    },
    intake: "georeferenced_raster",
    format_family: "tiff",
    georeferencing_basis: "file_metadata",
    georeferencing_basis_label: "The file's own CRS and geotransform",
    placed: true,
    processing: [],
    ...overrides,
  } as client.MapSheetInfo;
}

describe("MapSheetsPage", () => {
  beforeEach(() => {
    vi.mocked(client.listMapSheets).mockReset();
    vi.mocked(client.uploadMapSheet).mockReset();
    vi.mocked(client.listMapSheets).mockResolvedValue({ items: [], note: "" });
  });

  it("states that the series is a label and that a PDF cannot be georeferenced here", async () => {
    renderSheets();
    expect(await screen.findByText("No map sheets yet.")).toBeInTheDocument();
    expect(screen.getByText(/It does not give the sheet a CRS, datum or projection/)).toBeInTheDocument();
    expect(screen.getByText(/Everest-56, Polyconic and LCC are names, not definitions/)).toBeInTheDocument();
    expect(screen.getByText(/A PDF is stored and its structure scanned/)).toBeInTheDocument();
    expect(screen.getByText(/Defence Series Maps are not supported/)).toBeInTheDocument();
    const series = screen.getByLabelText("Series");
    expect(within(series).getAllByRole("option").map((o) => o.textContent)).toEqual([
      "Open Series Map (OSM)",
      "Everest Series Map (ESM)",
      "Series not stated",
    ]);
  });

  it("uploads one sheet with the declaration and links to what is missing", async () => {
    vi.mocked(client.uploadMapSheet).mockResolvedValue({
      asset: { id: "a9" } as client.Asset,
      map_sheet: sheetInfo({ intake: "scanned_image", georeferencing_basis: "none", georeferencing_basis_label: "Not georeferenced", placed: false }),
      duplicate: false,
      duplicate_of_asset_id: null,
      stored_original_reused: false,
      message: "Stored and validated.",
    } as client.MapSheetUpload);
    const user = userEvent.setup();
    renderSheets();
    await screen.findByText("No map sheets yet.");

    const submit = screen.getByRole("button", { name: "Upload map sheet" });
    expect(submit).toBeDisabled();
    await user.selectOptions(screen.getByLabelText("Series"), "esm");
    await user.type(screen.getByLabelText("Sheet number or name"), "NH 44-1");
    await user.type(screen.getByLabelText("Printed scale: 1 :"), "250000");
    await user.type(screen.getByLabelText("Edition"), "2nd");
    const file = new File([new Uint8Array([1, 2, 3])], "scan.png", { type: "image/png" });
    await user.upload(screen.getByLabelText("File"), file);
    expect(submit).toBeEnabled();
    await user.click(submit);

    await waitFor(() => expect(client.uploadMapSheet).toHaveBeenCalled());
    const [projectId, sent, declaration] = vi.mocked(client.uploadMapSheet).mock.calls[0]!;
    expect(projectId).toBe("p1");
    expect(sent).toBe(file);
    expect(declaration).toEqual({ series: "esm", sheetLabel: "NH 44-1", scaleDenominator: 250000, edition: "2nd" });
    const result = await screen.findByTestId("upload-result");
    expect(result).toHaveTextContent("Stored and validated.");
    expect(result).toHaveTextContent("Scanned picture (PNG / JPEG) · not placed on the map");
    expect(within(result).getByRole("link", { name: "Review what is missing and georeference it" })).toHaveAttribute(
      "href",
      "/projects/p1/assets/a9#map-sheet",
    );
  });

  it("refuses an unsupported file or a bad scale before uploading", async () => {
    const user = userEvent.setup({ applyAccept: false });
    renderSheets();
    await screen.findByText("No map sheets yet.");
    await user.upload(screen.getByLabelText("File"), new File(["x"], "sheet.gif", { type: "image/gif" }));
    expect(screen.getByRole("alert")).toHaveTextContent("this file ends in .gif");
    expect(screen.getByRole("button", { name: "Upload map sheet" })).toBeDisabled();
    await user.upload(screen.getByLabelText("File"), new File(["x"], "sheet.tif"));
    await user.type(screen.getByLabelText("Printed scale: 1 :"), "2.5");
    expect(screen.getByText("Enter the N of a 1 : N scale as a whole number.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Upload map sheet" })).toBeDisabled();
    expect(client.uploadMapSheet).not.toHaveBeenCalled();
  });

  it("shows the server's reason when a sheet is not accepted", async () => {
    vi.mocked(client.uploadMapSheet).mockRejectedValue(new client.ApiError(422, "ASSET_UNREADABLE", "The file could not be read", "The PDF is truncated."));
    const user = userEvent.setup();
    renderSheets();
    await screen.findByText("No map sheets yet.");
    await user.upload(screen.getByLabelText("File"), new File(["%PDF-1.4"], "cut.pdf"));
    await user.click(screen.getByRole("button", { name: "Upload map sheet" }));
    expect(await screen.findByText("The file was not accepted")).toBeInTheDocument();
    expect(screen.getByText(/ASSET_UNREADABLE/)).toBeInTheDocument();
  });

  it("lists the sheets with how each is positioned and what it needs", async () => {
    vi.mocked(client.listMapSheets).mockResolvedValue({
      note: "",
      items: [
        { asset_id: "a1", filename: "placed.tif", info: sheetInfo(), crs_id: "EPSG:32643", crs_name: "WGS 84 / UTM zone 43N", crs_status: "valid", width: 10, height: 10, quality_flags: [], next_step: "Placed on the map.", ingested_at: "2026-10-31T00:00:00Z" },
        {
          asset_id: "a2",
          filename: "scan.png",
          info: sheetInfo({ declaration: { ...sheetInfo().declaration, series: "esm", series_label: "Everest Series Map (ESM)" }, intake: "scanned_image", georeferencing_basis: "none", georeferencing_basis_label: "Not georeferenced", placed: false }),
          crs_id: null, crs_name: null, crs_status: "missing", width: 10, height: 10, quality_flags: [], next_step: "Not georeferenced. Confirm a world file or .prj, or enter control points.", ingested_at: "2026-10-31T00:00:00Z",
        },
      ],
    } as client.MapSheetList);
    renderSheets();
    const list = await screen.findByRole("list", { name: "Map sheets" });
    const placed = within(list).getByLabelText("placed.tif");
    expect(placed).toHaveTextContent("Placed");
    expect(placed).toHaveTextContent("EPSG:32643");
    const scan = within(list).getByLabelText("scan.png");
    expect(scan).toHaveTextContent("Everest Series Map (ESM)");
    expect(scan).toHaveTextContent("Not placed");
    expect(scan).toHaveTextContent("enter control points");
  });

  it("does not offer upload in an archived project", async () => {
    renderSheets({ ...PROJECT, archived_at: "2026-10-01T00:00:00Z" } as client.Project);
    expect(await screen.findByText("This project is archived")).toBeInTheDocument();
    expect(screen.getByLabelText("File")).toBeDisabled();
  });
});

// --- the map workspace ------------------------------------------------------------------------------------------------

function layer(id: string, title: string, overrides: Partial<client.RasterLayer> = {}): client.RasterLayer {
  return {
    asset_id: id,
    title,
    crs_id: "EPSG:32643",
    crs_name: "WGS 84 / UTM zone 43N",
    placement: "trusted",
    georef_version: 0,
    band_count: 3,
    dtypes: ["uint8"],
    bounds_4326: [75, 27.12, 75.02, 27.1225],
    footprint_4326: { type: "Polygon", coordinates: [] },
    tile_url: `/api/v1/assets/${id}/tiles/{z}/{x}/{y}.png`,
    display_unavailable_reason: null,
    min_zoom: 0,
    max_zoom: 17,
    native_zoom: 14,
    bands_shown: [1, 2, 3],
    display_stretch: "none",
    pixel_size: [10, 10],
    pixel_units: "metre",
    footprint_transformation: null,
    ...overrides,
  } as client.RasterLayer;
}

const SHEET = layer("sheet", "sheet.tif", {
  map_sheet: {
    series: "osm",
    series_label: "Open Series Map (OSM)",
    sheet_label: "NF 43-01",
    scale_denominator: 250000,
    edition: null,
    intake: "georeferenced_raster",
    georeferencing_basis: "control_points",
    georeferencing_basis_label: "Control points supplied by a person",
  },
  placement: "user_asserted",
  georef_version: 1,
});
const SATELLITE = layer("sat", "sat.tif");

function layersData(overrides: Partial<client.Layers> = {}): client.Layers {
  return {
    rasters: [SHEET, SATELLITE],
    excluded: [],
    aoi: null,
    observations: { with_location: 0, without_location: 0, note: "" },
    basemap: { configured: false, detail: "No offline basemap." },
    ...overrides,
  } as client.Layers;
}

function renderMap() {
  return renderPage(
    <Routes>
      <Route path="/projects/:projectId" element={<Outlet context={PROJECT} />}>
        <Route path="map" element={<MapPage />} />
      </Route>
    </Routes>,
    "/projects/p1/map",
  );
}

describe("the map workspace with map sheets", () => {
  beforeEach(() => {
    drawn.length = 0;
    vi.clearAllMocks();
    vi.mocked(client.listAnnotations).mockResolvedValue({ items: [] } as never);
    vi.mocked(client.listPhotoLocations).mockResolvedValue({ items: [] } as never);
    vi.mocked(client.getLayers).mockResolvedValue(layersData());
  });

  it("tells a map sheet from a raster and says how the sheet was positioned", async () => {
    renderMap();
    const list = await screen.findByRole("list", { name: "Raster layers" });
    const tag = within(list).getByTestId("map-sheet-tag");
    expect(tag).toHaveTextContent("Map sheet · OSM · NF 43-01 · Control points supplied by a person");
    expect(within(list).getByText("Satellite image")).toBeInTheDocument();
    expect(within(list).getByText(/Location entered by a person/)).toBeInTheDocument();
  });

  it("draws the newest layer on top and lets a person reorder the stack", async () => {
    const user = userEvent.setup();
    renderMap();
    const list = await screen.findByRole("list", { name: "Raster layers" });
    // Listed top first: the satellite image (added later) is above the sheet. The map gets them bottom first.
    expect(within(list).getAllByRole("listitem").map((li) => li.textContent?.includes("sat.tif"))).toEqual([true, false]);
    expect(last().rasters.map((r) => r.layer.asset_id)).toEqual(["sheet", "sat"]);
    expect(within(list).getByRole("button", { name: "Move sat.tif up" })).toBeDisabled();
    expect(within(list).getByRole("button", { name: "Move sheet.tif down" })).toBeDisabled();

    await user.click(within(list).getByRole("button", { name: "Move sheet.tif up" }));
    expect(last().rasters.map((r) => r.layer.asset_id)).toEqual(["sat", "sheet"]); // the sheet is now on top
    expect(within(list).getAllByRole("listitem").map((li) => li.textContent?.includes("sheet.tif"))).toEqual([true, false]);

    await user.click(within(list).getByRole("button", { name: "Move sheet.tif down" }));
    expect(last().rasters.map((r) => r.layer.asset_id)).toEqual(["sheet", "sat"]);
  });

  it("keeps visibility and opacity with the layer when it is moved", async () => {
    const user = userEvent.setup();
    renderMap();
    const list = await screen.findByRole("list", { name: "Raster layers" });
    await user.click(within(list).getByRole("checkbox", { name: /sheet\.tif/ }));
    await user.click(within(list).getByRole("button", { name: "Move sheet.tif up" }));
    const sheet = last().rasters.find((r) => r.layer.asset_id === "sheet")!;
    expect(sheet.visible).toBe(false);
  });

  it("lists a sheet that is not placed with a way to georeference it", async () => {
    vi.mocked(client.getLayers).mockResolvedValue(
      layersData({
        rasters: [SATELLITE],
        excluded: [
          {
            asset_id: "scan",
            title: "scan.png",
            modality: "overhead_raster",
            reason: "CRS_MISSING",
            detail: "The file declares no coordinate reference system. None is assumed.",
            georeferencing_can_be_recorded: true,
            map_sheet: { ...SHEET.map_sheet!, series: "esm", series_label: "Everest Series Map (ESM)", georeferencing_basis: "none", georeferencing_basis_label: "Not georeferenced" },
          },
          {
            asset_id: "pdf",
            title: "sheet.pdf",
            modality: "overhead_raster",
            reason: "NOT_GEOREFERENCED",
            detail: "A PDF cannot be georeferenced here.",
            georeferencing_can_be_recorded: false,
            map_sheet: { ...SHEET.map_sheet!, intake: "pdf_document" },
          },
        ],
      }),
    );
    renderMap();
    const excluded = await screen.findByRole("list", { name: "Files not placed on the map" });
    expect(excluded).toHaveTextContent("scan.pngMap sheet · ESM");
    expect(within(excluded).getByRole("link", { name: "Place using reference points…" })).toHaveAttribute("href", "/projects/p1/assets/scan#control-points");
    expect(within(excluded).getByRole("link", { name: "Review the sheet…" })).toHaveAttribute("href", "/projects/p1/assets/pdf#map-sheet");
  });

  it("inspects the alignment of two layers and states what it cannot show", async () => {
    vi.mocked(client.getOverlayAlignment).mockResolvedValue({
      first: { asset_id: "sheet", title: "sheet.tif", kind: "map_sheet", series: "osm", crs_id: "EPSG:32643", crs_name: "x", placement: "recorded_by_person", georeferencing_basis: "control_points", transformation_class: "exact", datum_shift_accuracy_m: 0, selection_required: false, control_point_rms_m: 4.2, ground_pixel_m: 10, bounds_4326: null },
      second: { asset_id: "sat", title: "sat.tif", kind: "raster", series: null, crs_id: "EPSG:4326", crs_name: "x", placement: "from_file", georeferencing_basis: "file_metadata", transformation_class: "approximate", datum_shift_accuracy_m: 3, selection_required: false, control_point_rms_m: null, ground_pixel_m: 50, bounds_4326: null },
      overlap: true,
      overlap_bounds_4326: null,
      overlap_area_km2: 1,
      overlap_fraction_of_first: 1,
      overlap_fraction_of_second: 0.11,
      resolution_ratio: 5,
      findings: ["The footprints overlap over 1.00 km², 100% of the first layer and 11% of the second."],
      cautions: ["Overlapping footprints show that both layers are placed in the same area. They do not show that features coincide."],
    } as client.OverlayAlignment);
    const user = userEvent.setup();
    renderMap();
    await screen.findByRole("list", { name: "Raster layers" });
    await user.selectOptions(screen.getByLabelText("First layer"), "sheet");
    expect(client.getOverlayAlignment).not.toHaveBeenCalled(); // both layers are needed
    await user.selectOptions(screen.getByLabelText("Second layer"), "sat");
    const result = await screen.findByTestId("alignment-result");
    expect(client.getOverlayAlignment).toHaveBeenCalledWith("p1", "sheet", "sat");
    expect(within(result).getByLabelText("First layer")).toHaveTextContent("Control points: 4.2 m RMS");
    expect(within(result).getByLabelText("Second layer")).toHaveTextContent("Datum shift: about 3 m");
    expect(within(result).getByRole("list", { name: "Findings" })).toHaveTextContent("overlap over 1.00 km²");
    expect(within(result).getByRole("list", { name: "Cautions" })).toHaveTextContent("do not show that features coincide");
  });

  it("does not offer an alignment inspection with fewer than two layers", async () => {
    vi.mocked(client.getLayers).mockResolvedValue(layersData({ rasters: [SHEET] }));
    renderMap();
    await screen.findByRole("list", { name: "Raster layers" });
    expect(screen.queryByText("Overlay alignment")).not.toBeInTheDocument();
  });

  it("links a selected sheet to its metadata", async () => {
    const user = userEvent.setup();
    renderMap();
    const list = await screen.findByRole("list", { name: "Raster layers" });
    await user.click(within(list).getAllByRole("button", { name: "Details" })[1]!); // the sheet is second from the top
    expect(await screen.findByRole("link", { name: /Map sheet details/ })).toHaveAttribute("href", "/projects/p1/assets/sheet#map-sheet");
    expect(screen.getByText("Control points supplied by a person", { selector: "dd" })).toBeInTheDocument();
  });
});
