import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Outlet, Route, Routes } from "react-router-dom";
import { vi } from "vitest";

import * as client from "../api/client";
import type { Hit } from "../lib/terrain";
import { renderPage } from "../test/render";
import { ThreeDPage } from "./ThreeDPage";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, listTerrain: vi.fn(), getTerrain: vi.fn(), fetchTerrainGrid: vi.fn(), transformPoint: vi.fn() };
});

// jsdom has no WebGL. The stand-in records what the page hands the viewer and lets a test move and click the pointer.
interface ViewerProps {
  values: Float32Array;
  meta: client.TerrainMeta;
  exaggeration: number;
  colour: string;
  onHover: (hit: Hit | null) => void;
  onSelect: (hit: Hit) => void;
}
const shown: ViewerProps[] = [];
const HIT: Hit = { col: 2, row: 1, value: 123.456, x: 20, y: 10, z: 5 };
vi.mock("../components/TerrainViewer", () => ({
  TerrainViewer: (props: ViewerProps) => {
    shown.push(props);
    return (
      <div data-testid="viewer" data-exaggeration={props.exaggeration} data-colour={props.colour} data-cells={props.values.length}>
        <button type="button" onClick={() => props.onHover(HIT)}>hover a cell</button>
        <button type="button" onClick={() => props.onHover(null)}>leave the surface</button>
        <button type="button" onClick={() => props.onSelect(HIT)}>click a cell</button>
      </div>
    );
  },
}));

const listTerrain = vi.mocked(client.listTerrain);
const getTerrain = vi.mocked(client.getTerrain);
const fetchTerrainGrid = vi.mocked(client.fetchTerrainGrid);
const transformPoint = vi.mocked(client.transformPoint);

const PROJECT = { id: "p1", name: "Survey", archived_at: null } as client.Project;
const last = () => shown[shown.length - 1]!;

function band(over: Partial<client.TerrainRaster["bands"][number]> = {}): client.TerrainRaster["bands"][number] {
  return { band: 1, dtype: "float32", description: "elevation", colour_interpretation: "gray", declared_unit: "metre", role: "elevation_declared", reasons: ["The file declares this band as elevation in m."], ...over } as client.TerrainRaster["bands"][number];
}

function raster(over: Partial<client.TerrainRaster> = {}): client.TerrainRaster {
  return { asset_id: "a1", filename: "dem.tif", width: 600, height: 400, crs_id: "EPSG:32643", status: "usable", reasons: ["A band is declared as elevation with a stated unit."], bands: [band()], ...over } as client.TerrainRaster;
}

const IMAGERY = raster({
  asset_id: "a2", filename: "imagery.tif", status: "not_elevation", reasons: ["No band of this raster is elevation data.", "The file marks this band as red: it is image colour, not elevation."],
  bands: [band({ band: 1, dtype: "uint8", colour_interpretation: "red", declared_unit: null, role: "not_elevation", reasons: ["The file marks this band as red: it is image colour, not elevation."] })],
});
const UNPLACED = raster({ asset_id: "a3", filename: "unplaced.tif", status: "unusable", crs_id: null, reasons: ["It has neither a usable CRS nor a usable geotransform, so its pixels have no map position."] });
const UNDECLARED = raster({
  asset_id: "a4", filename: "undeclared.tif", status: "needs_confirmation",
  reasons: ["A band may be elevation but the file does not declare it, or does not state the unit. A person must state this for the view."],
  bands: [band({ description: null, declared_unit: null, role: "possible", reasons: ["A single numeric band."] })],
});

function listing(items: client.TerrainRaster[]): client.TerrainList {
  return {
    items, usable: items.filter((i) => i.status === "usable").length, needs_confirmation: items.filter((i) => i.status === "needs_confirmation").length,
    not_available: items.filter((i) => ["not_elevation", "unusable"].includes(i.status)).length, max_grid_side: 512, default_grid_side: 256,
    note: "Only rasters that are georeferenced and have a band declared as elevation can be drawn as terrain.",
  } as client.TerrainList;
}

function meta(over: Record<string, unknown> = {}, elevation: Record<string, unknown> = {}): client.TerrainMeta {
  return {
    asset_id: "a1", filename: "dem.tif", source_checksum: "ab".repeat(32), band: 1, source_size: [600, 400], grid: { width: 60, height: 40, decimation: 10, method: "nearest source pixel" },
    crs_id: "EPSG:32643", crs_name: "WGS 84 / UTM zone 43N", georeferencing_source: "file", horizontal_unit: "linear", cell_size_native: [100, 100], cell_size_m: [100, 100],
    extent_native: { x_min: 500000, y_min: 2996000, x_max: 506000, y_max: 3000000 }, extent_m: [6000, 4000], bbox_4326: [75, 27, 75.1, 27.04], centre_4326: [75.05, 27.02], positioning: { state: "positioned" },
    horizontal: { method: "exact: the CRS unit is 1 m", approximate: false },
    elevation: { unit: "metre", unit_label: "m", unit_source: "declared", to_metre: 1, role_source: "declared", declared_scale_offset_applied: false, nodata_value: -9999, valid_cells: 2300, nodata_cells: 100, min: 100, max: 300, mean: 200, ...elevation },
    vertical_datum: { state: "not_stated", name: null },
    warnings: [], disclosures: ["The surface is a 60 x 40 grid made by taking every 10th source pixel (nearest pixel).", "No-data cells are left as holes.", "The vertical datum (what zero height means) is not stated by the file."],
    grid_url: "/api/v1/projects/p1/assets/a1/terrain/grid?band=1&max_side=256", grid_format: "float32", ...over,
  } as client.TerrainMeta;
}

function render(path = "/projects/p1/3d") {
  return renderPage(
    <Routes>
      <Route path="/projects/:projectId" element={<Outlet context={PROJECT} />}>
        <Route path="3d" element={<ThreeDPage />} />
      </Route>
    </Routes>,
    path,
  );
}

let fetchSpy: ReturnType<typeof vi.spyOn>;
beforeEach(() => {
  vi.clearAllMocks();
  shown.length = 0;
  fetchSpy = vi.spyOn(globalThis, "fetch");
  getTerrain.mockResolvedValue(meta());
  fetchTerrainGrid.mockImplementation(async (_url, cells) => new Float32Array(cells).fill(150));
  transformPoint.mockResolvedValue({ x: 75.0123456, y: 27.0123456 } as client.CrsTransform);
});
afterEach(() => fetchSpy.mockRestore());

describe("when there is nothing to draw", () => {
  it("says a project with no rasters has none, offers the upload, and asks the server for nothing else", async () => {
    listTerrain.mockResolvedValue(listing([]));
    render();
    expect(await screen.findByText("This project has no rasters")).toBeInTheDocument();
    expect(screen.getByText(/Photographs, video and colour imagery are not elevation/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Upload a raster" })).toHaveAttribute("href", "/projects/p1/assets/new");
    expect(getTerrain).not.toHaveBeenCalled();
    expect(screen.queryByTestId("viewer")).not.toBeInTheDocument();
  });

  it("says colour imagery is not terrain and lists why, without drawing anything or inventing elevation", async () => {
    listTerrain.mockResolvedValue(listing([IMAGERY, UNPLACED]));
    render();
    expect(await screen.findByText("No suitable elevation data in this project")).toBeInTheDocument();
    expect(screen.getByText(/colour imagery is never treated as elevation/)).toBeInTheDocument();
    // the first raster's reasons are in the main area; every raster is in the list with its status
    expect(screen.getByText("imagery.tif cannot be shown in 3D: not elevation")).toBeInTheDocument();
    expect(screen.getAllByText(/image colour, not elevation/).length).toBeGreaterThan(0);
    const list = screen.getByRole("list", { name: "Rasters and whether they are elevation" });
    expect(list).toHaveTextContent("imagery.tifNot elevation");
    expect(list).toHaveTextContent("unplaced.tifCannot be drawn");
    expect(getTerrain).not.toHaveBeenCalled();
    expect(fetchTerrainGrid).not.toHaveBeenCalled();
    expect(screen.queryByTestId("viewer")).not.toBeInTheDocument();
  });

  it("explains why a raster that cannot be placed is not drawn when it is selected", async () => {
    listTerrain.mockResolvedValue(listing([IMAGERY, UNPLACED]));
    render();
    await userEvent.click(await screen.findByRole("button", { name: /unplaced\.tif/ }));
    expect(await screen.findByText("unplaced.tif cannot be shown in 3D: cannot be drawn")).toBeInTheDocument();
    expect(screen.getByText(/no map position/)).toBeInTheDocument();
    expect(getTerrain).not.toHaveBeenCalled();
  });

  it("says no terrain is generated where elevation is absent", async () => {
    listTerrain.mockResolvedValue(listing([IMAGERY]));
    render();
    expect(await screen.findByText(/No terrain is generated and no elevation is guessed/)).toBeInTheDocument();
  });
});

describe("a raster the file declares as elevation", () => {
  it("is read and drawn at once, from the grid the server sent, with everything stated", async () => {
    listTerrain.mockResolvedValue(listing([raster(), IMAGERY]));
    render();
    const viewer = await screen.findByTestId("viewer");
    expect(getTerrain).toHaveBeenCalledWith("p1", "a1", { max_side: 256 });
    await waitFor(() => expect(fetchTerrainGrid).toHaveBeenCalledWith("/api/v1/projects/p1/assets/a1/terrain/grid?band=1&max_side=256", 2400));
    expect(viewer).toHaveAttribute("data-cells", "2400");
    expect(last().meta.filename).toBe("dem.tif");
    const data = screen.getByRole("heading", { name: "Elevation data" }).closest("section")!;
    expect(data).toHaveTextContent("Elevation unitm · declared by the file");
    expect(data).toHaveTextContent("Range100 m to 300 m");
    expect(data).toHaveTextContent("Cells with data2,300 (100 with no data)");
    expect(data).toHaveTextContent("Grid60 × 40 cells (every 10th source pixel)");
    expect(data).toHaveTextContent("Vertical datumnot stated by the file");
    expect(data).toHaveTextContent("CRSEPSG:32643 · from the file");
    const disclosures = screen.getByRole("list", { name: "Disclosures" });
    expect(disclosures).toHaveTextContent("every 10th source pixel");
    expect(disclosures).toHaveTextContent("No-data cells are left as holes");
    expect(disclosures).toHaveTextContent("vertical datum");
    expect(screen.getByText("Only elevation is drawn")).toBeInTheDocument();
  });

  it("requests the page's own grid size choice and offers no more than the server allows", async () => {
    listTerrain.mockResolvedValue({ ...listing([raster()]), max_grid_side: 256 });
    render();
    await screen.findByTestId("viewer");
    const detail = screen.getByLabelText("Detail (cells per side, at most)");
    expect(within(detail).getAllByRole("option").map((o) => o.textContent)).toEqual(["128", "256"]);
    await userEvent.selectOptions(detail, "128");
    await waitFor(() => expect(getTerrain).toHaveBeenLastCalledWith("p1", "a1", { max_side: 128 }));
  });

  it("shows vertical exaggeration plainly, at true scale by default, and warns when it is changed", async () => {
    listTerrain.mockResolvedValue(listing([raster()]));
    render();
    const viewer = await screen.findByTestId("viewer");
    expect(viewer).toHaveAttribute("data-exaggeration", "1");
    expect(screen.getByText("Vertical exaggeration: ×1")).toBeInTheDocument();
    expect(screen.getByText(/×1 draws heights at true scale/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/Vertical exaggeration/), { target: { value: "3" } });
    expect(viewer).toHaveAttribute("data-exaggeration", "3");
    expect(screen.getByText(/Heights are drawn 3 times taller than they are/)).toBeInTheDocument();
    expect(screen.getByText("The view is drawn with the heights exaggerated ×3.")).toBeInTheDocument();
    expect(getTerrain).toHaveBeenCalledTimes(1); // the picture changed, not the data
  });

  it("changes the colouring without reading anything again", async () => {
    listTerrain.mockResolvedValue(listing([raster()]));
    render();
    const viewer = await screen.findByTestId("viewer");
    await userEvent.selectOptions(screen.getByLabelText("Colour"), "grey");
    expect(viewer).toHaveAttribute("data-colour", "grey");
    expect(fetchTerrainGrid).toHaveBeenCalledTimes(1);
  });

  it("shows warnings from the data, such as an undeclared no-data marker", async () => {
    getTerrain.mockResolvedValue(meta({ warnings: ["3 cell(s) hold the value -9999, which is often a no-data marker, but the file declares no no-data value."] }));
    listTerrain.mockResolvedValue(listing([raster()]));
    render();
    expect(await screen.findByRole("list", { name: "Warnings" })).toHaveTextContent("often a no-data marker");
  });
});

describe("a raster the file does not fully describe", () => {
  it("waits for the person to confirm the band and state the unit, and says it is not saved", async () => {
    listTerrain.mockResolvedValue(listing([UNDECLARED]));
    render();
    const form = await screen.findByRole("form", { name: "State what this raster holds" });
    expect(form).toHaveTextContent("The file does not say everything the 3D view needs");
    expect(form).toHaveTextContent("used for this view only. It is not saved");
    expect(getTerrain).not.toHaveBeenCalled(); // nothing is read until it is stated
    const show = within(form).getByRole("button", { name: "Show in 3D" });
    expect(show).toBeDisabled();
    await userEvent.click(within(form).getByRole("checkbox"));
    expect(show).toBeDisabled(); // the unit is still missing
    await userEvent.selectOptions(within(form).getByLabelText("Elevation unit"), "foot");
    expect(show).toBeEnabled();
    await userEvent.click(show);
    await waitFor(() => expect(getTerrain).toHaveBeenCalledWith("p1", "a4", { band: 1, confirm_elevation: true, unit: "foot", max_side: 256 }));
    expect(await screen.findByTestId("viewer")).toBeInTheDocument();
    expect(screen.queryByRole("form", { name: "State what this raster holds" })).not.toBeInTheDocument();
  });

  it("credits the unit and the band to the person, not to the file, once drawn", async () => {
    getTerrain.mockResolvedValue(
      meta(
        { filename: "undeclared.tif", disclosures: ["The file declares no elevation unit. ft was stated by a person for this view only; the software cannot check it."] },
        { unit: "foot", unit_label: "ft", unit_source: "stated_for_this_view", role_source: "confirmed_for_this_view", to_metre: 0.3048 },
      ),
    );
    listTerrain.mockResolvedValue(listing([UNDECLARED]));
    render();
    const form = await screen.findByRole("form", { name: "State what this raster holds" });
    await userEvent.click(within(form).getByRole("checkbox"));
    await userEvent.selectOptions(within(form).getByLabelText("Elevation unit"), "foot");
    await userEvent.click(within(form).getByRole("button", { name: "Show in 3D" }));
    const data = (await screen.findByRole("heading", { name: "Elevation data" })).closest("section")!;
    expect(data).toHaveTextContent("Elevation unitft · stated by you");
    expect(data).toHaveTextContent("1 (confirmed by you for this view)");
    expect(screen.getByRole("list", { name: "Disclosures" })).toHaveTextContent("stated by a person for this view only");
  });

  it("asks only for the unit when the file declares the band as elevation but states none", async () => {
    const declaredNoUnit = raster({ asset_id: "a5", filename: "dem2.tif", status: "needs_confirmation", bands: [band({ declared_unit: null })] });
    listTerrain.mockResolvedValue(listing([declaredNoUnit]));
    render();
    const form = await screen.findByRole("form", { name: "State what this raster holds" });
    expect(within(form).queryByRole("checkbox")).not.toBeInTheDocument(); // the file already says it is elevation
    await userEvent.selectOptions(within(form).getByLabelText("Elevation unit"), "metre");
    await userEvent.click(within(form).getByRole("button", { name: "Show in 3D" }));
    await waitFor(() => expect(getTerrain).toHaveBeenCalledWith("p1", "a5", { band: 1, unit: "metre", max_side: 256 }));
  });

  it("lets the person choose between candidate bands", async () => {
    const multi = raster({
      asset_id: "a6", filename: "multi.tif", status: "needs_confirmation",
      bands: [band({ band: 1, declared_unit: null }), band({ band: 2, declared_unit: null, description: "height" })],
    });
    listTerrain.mockResolvedValue(listing([multi]));
    render();
    const form = await screen.findByRole("form", { name: "State what this raster holds" });
    await userEvent.selectOptions(within(form).getByLabelText("Band"), "2");
    await userEvent.selectOptions(within(form).getByLabelText("Elevation unit"), "metre");
    await userEvent.click(within(form).getByRole("button", { name: "Show in 3D" }));
    await waitFor(() => expect(getTerrain).toHaveBeenCalledWith("p1", "a6", { band: 2, unit: "metre", max_side: 256 }));
  });
});

describe("the position and elevation readout", () => {
  it("shows the stored value under the pointer with its unit, and nothing when the pointer leaves", async () => {
    listTerrain.mockResolvedValue(listing([raster()]));
    render();
    await screen.findByTestId("viewer");
    const readout = screen.getByTestId("hover-readout");
    expect(readout).toHaveTextContent("Move the pointer over the surface");
    await userEvent.click(screen.getByRole("button", { name: "hover a cell" }));
    // cell (2,1) of a grid whose cells are 100 m, with the extent's north-west corner at (500000, 3000000)
    expect(readout).toHaveTextContent("cell 2, 1 · EPSG:32643 x 500250.0, y 2999850.0");
    expect(readout).toHaveTextContent("Elevation 123.5 m");
    await userEvent.click(screen.getByRole("button", { name: "leave the surface" }));
    expect(readout).toHaveTextContent("Move the pointer over the surface");
    expect(readout).not.toHaveTextContent("Elevation");
  });

  it("pins a cell on click and works out its longitude and latitude with the existing transform", async () => {
    listTerrain.mockResolvedValue(listing([raster()]));
    render();
    await screen.findByTestId("viewer");
    expect(screen.getByTestId("pinned-readout")).toHaveTextContent("Nothing selected.");
    await userEvent.click(screen.getByRole("button", { name: "click a cell" }));
    const pinned = screen.getByTestId("pinned-readout");
    expect(pinned).toHaveTextContent("Elevation 123.5 m");
    expect(await within(pinned).findByText("lat 27.012346°, lon 75.012346° (WGS 84)")).toBeInTheDocument();
    expect(transformPoint).toHaveBeenCalledWith({ source: "EPSG:32643", target: "EPSG:4326", x: 500250, y: 2999850 });
  });

  it("says what zero height means only if the file does", async () => {
    listTerrain.mockResolvedValue(listing([raster()]));
    render();
    expect(await screen.findByText(/does not say what zero height means/)).toBeInTheDocument();
  });

  it("marks an elevation whose unit the person stated", async () => {
    getTerrain.mockResolvedValue(meta({}, { unit: "foot", unit_label: "ft", unit_source: "stated_for_this_view", role_source: "confirmed_for_this_view" }));
    listTerrain.mockResolvedValue(listing([raster()]));
    render();
    await screen.findByTestId("viewer");
    await userEvent.click(screen.getByRole("button", { name: "hover a cell" }));
    expect(screen.getByTestId("hover-readout")).toHaveTextContent("Elevation 123.5 ft (unit stated by you)");
  });
});

describe("failures", () => {
  it("shows the server's refusal with its code, and draws nothing", async () => {
    getTerrain.mockRejectedValue(new client.ApiError(422, "TERRAIN_NO_VALID_DATA", "This raster cannot be shown as terrain", "Every cell of this band is no-data, so there is no terrain to show."));
    listTerrain.mockResolvedValue(listing([raster()]));
    render();
    expect(await screen.findByText("This raster cannot be shown in 3D")).toBeInTheDocument();
    expect(screen.getByText(/Every cell of this band is no-data/)).toBeInTheDocument();
    expect(screen.getByText(/TERRAIN_NO_VALID_DATA/)).toBeInTheDocument();
    expect(screen.queryByTestId("viewer")).not.toBeInTheDocument();
  });

  it("shows a grid that could not be loaded or is the wrong size", async () => {
    fetchTerrainGrid.mockRejectedValue(new client.ApiError(500, "TERRAIN_GRID_SIZE_MISMATCH", "The height grid is not the size its description gave"));
    listTerrain.mockResolvedValue(listing([raster()]));
    render();
    expect(await screen.findByText("The height grid could not be loaded")).toBeInTheDocument();
    expect(screen.getByText(/TERRAIN_GRID_SIZE_MISMATCH/)).toBeInTheDocument();
    expect(screen.queryByTestId("viewer")).not.toBeInTheDocument();
  });

  it("shows a failure to list the project's rasters", async () => {
    listTerrain.mockRejectedValue(new client.ApiError(500, "INTERNAL_ERROR", "Something failed"));
    render();
    expect(await screen.findByText("Something failed")).toBeInTheDocument();
  });
});

describe("choosing a raster", () => {
  it("opens the first drawable raster, lists every raster with its state, and switching starts afresh", async () => {
    getTerrain.mockImplementation(async (_p, assetId) => meta({ asset_id: assetId, filename: assetId === "a1" ? "dem.tif" : "second.tif" }));
    const second = raster({ asset_id: "a7", filename: "second.tif" });
    listTerrain.mockResolvedValue(listing([IMAGERY, raster(), second]));
    render();
    await screen.findByTestId("viewer");
    expect(getTerrain).toHaveBeenCalledWith("p1", "a1", { max_side: 256 }); // skipped the imagery, opened the first elevation raster
    fireEvent.change(screen.getByLabelText(/Vertical exaggeration/), { target: { value: "4" } });
    await userEvent.click(screen.getByRole("button", { name: "hover a cell" }));
    await userEvent.click(screen.getByRole("button", { name: /second\.tif/ }));
    await waitFor(() => expect(getTerrain).toHaveBeenCalledWith("p1", "a7", { max_side: 256 }));
    await waitFor(() => expect(screen.getByTestId("viewer")).toHaveAttribute("data-exaggeration", "1")); // a new surface: a fresh view
    expect(screen.getByTestId("hover-readout")).toHaveTextContent("Move the pointer");
  });
});

// Regression: the working project's colour rasters (three 8-bit bands, none elevation) made the API send the same reason text
// once per band, and the lists keyed their items by that text, so React logged "two children with the same key". These tests
// repeat the text on purpose and require that React logs nothing and that every repeated line is still shown.
describe("lines of text that repeat", () => {
  const BRIGHTNESS = "An 8-bit band holds picture brightness, not elevation.";
  const THREE_BAND = raster({
    asset_id: "a5", filename: "colour.tif", status: "not_elevation",
    reasons: ["No band of this raster is elevation data.", BRIGHTNESS, BRIGHTNESS, BRIGHTNESS],
    bands: [1, 2, 3].map((n) => band({ band: n, dtype: "uint8", colour_interpretation: null, declared_unit: null, role: "not_elevation", reasons: [BRIGHTNESS] })),
  });
  let errors: ReturnType<typeof vi.spyOn>;
  beforeEach(() => {
    errors = vi.spyOn(console, "error").mockImplementation(() => undefined);
  });
  afterEach(() => errors.mockRestore());
  const keyWarnings = () => errors.mock.calls.filter((call: unknown[]) => String(call[0]).includes("same key"));

  it("shows every repeated reason of a raster that is not elevation, with no duplicate-key warning", async () => {
    listTerrain.mockResolvedValue(listing([THREE_BAND]));
    render();
    const notice = (await screen.findByText("colour.tif cannot be shown in 3D: not elevation")).closest("div")!.parentElement as HTMLElement;
    expect(within(notice).getAllByText(BRIGHTNESS, { selector: "li" }).length).toBeGreaterThanOrEqual(3); // none dropped or merged
    expect(within(notice).getByRole("list", { name: "Bands" }).querySelectorAll("li")).toHaveLength(3);
    expect(keyWarnings()).toHaveLength(0);
  });

  it("shows every repeated reason in the form that asks the person to state the band and unit", async () => {
    const REPEATED = "The file does not say this band is elevation.";
    listTerrain.mockResolvedValue(listing([raster({ ...UNDECLARED, reasons: [REPEATED, REPEATED] })]));
    render();
    const form = await screen.findByRole("form", { name: "State what this raster holds" });
    expect(within(form).getAllByText(REPEATED)).toHaveLength(2);
    expect(keyWarnings()).toHaveLength(0);
  });

  it("shows repeated disclosures and warnings of a drawn surface", async () => {
    getTerrain.mockResolvedValue(meta({ disclosures: ["Stated twice.", "Stated twice."], warnings: ["Careful.", "Careful."] }));
    listTerrain.mockResolvedValue(listing([raster()]));
    render();
    await screen.findByTestId("viewer");
    expect(within(await screen.findByRole("list", { name: "Disclosures" })).getAllByText("Stated twice.")).toHaveLength(2);
    expect(within(screen.getByRole("list", { name: "Warnings" })).getAllByText("Careful.")).toHaveLength(2);
    expect(keyWarnings()).toHaveLength(0);
  });
});

describe("what the page does not do", () => {
  it("states that photographs are not reconstructed in 3D here and requests nothing from any other address", async () => {
    listTerrain.mockResolvedValue(listing([raster()]));
    const { container } = render();
    await screen.findByTestId("viewer");
    expect(screen.getByText("Photographs to 3D")).toBeInTheDocument();
    expect(screen.getByText(/Nothing here is reconstructed from photographs/)).toBeInTheDocument();
    expect(screen.getByText(/No online terrain, tiles or basemap is requested/)).toBeInTheDocument();
    expect(fetchSpy).not.toHaveBeenCalled(); // every request went through the mocked client
    expect([...container.querySelectorAll("a, img")].every((el) => !/^https?:/.test(el.getAttribute("href") ?? el.getAttribute("src") ?? ""))).toBe(true);
  });
});
