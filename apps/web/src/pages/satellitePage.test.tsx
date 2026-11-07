import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Outlet, Route, Routes, useLocation } from "react-router-dom";
import { vi } from "vitest";

import * as client from "../api/client";
import type { MapCanvasProps } from "../components/MapCanvas";
import { renderPage } from "../test/render";
import { SatellitePage } from "./SatellitePage";

// The real map needs WebGL; a stand-in records what the page asks it to show.
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
    listAssets: vi.fn(),
    getAsset: vi.fn(),
    listTerrain: vi.fn(),
    getRasterTools: vi.fn(),
    getIngestionRules: vi.fn(),
    uploadAsset: vi.fn(),
    getLayers: vi.fn(),
    listPhotoLocations: vi.fn(),
    getPixel: vi.fn(),
  };
});

const listAssets = vi.mocked(client.listAssets);
const getAsset = vi.mocked(client.getAsset);
const listTerrain = vi.mocked(client.listTerrain);
const getRasterTools = vi.mocked(client.getRasterTools);
const getIngestionRules = vi.mocked(client.getIngestionRules);
const uploadAsset = vi.mocked(client.uploadAsset);
const getLayers = vi.mocked(client.getLayers);
const listPhotoLocations = vi.mocked(client.listPhotoLocations);
const getPixel = vi.mocked(client.getPixel);

const PROJECT = { id: "p1", name: "Survey", archived_at: null } as client.Project;

function image(id: string, name: string, georeferenced: boolean, over: Record<string, unknown> = {}): client.Asset {
  return {
    id,
    project_id: "p1",
    original_filename: name,
    modality: "overhead_raster",
    raster: { georeferenced, width: 1600, height: 1000, band_count: 3 },
    georeferencing: { placed: georeferenced },
    preview: { status: "ready", variants: ["thumb", "view"] },
    ...over,
  } as unknown as client.Asset;
}
const PLACED = image("a1", "scene.tif", true);
const PLAIN = image("a2", "plain.tif", false);
const HEIGHTS = image("a3", "dem.tif", true);

function Layout() {
  return (
    <>
      <output data-testid="location">{useLocation().pathname}</output>
      <output data-testid="search">{useLocation().search}</output>
      <Outlet context={PROJECT} />
    </>
  );
}

function render(path = "/projects/p1/satellite") {
  return renderPage(
    <Routes>
      <Route path="/projects/:projectId" element={<Layout />}>
        <Route path="satellite" element={<SatellitePage />} />
        <Route path="assets/:assetId/overhead" element={<SatellitePage />} />
      </Route>
    </Routes>,
    path,
  );
}

function rasters(...items: client.Asset[]) {
  listAssets.mockResolvedValue({ items, next_cursor: null } as unknown as Awaited<ReturnType<typeof client.listAssets>>);
  getAsset.mockImplementation(async (id: string) => items.find((item) => item.id === id) ?? PLACED);
}

beforeEach(() => {
  vi.clearAllMocks();
  drawn.length = 0;
  rasters();
  getLayers.mockResolvedValue({ rasters: [] } as unknown as Awaited<ReturnType<typeof client.getLayers>>);
  listPhotoLocations.mockResolvedValue({ items: [] } as unknown as Awaited<ReturnType<typeof client.listPhotoLocations>>);
  listTerrain.mockResolvedValue({ items: [] } as unknown as Awaited<ReturnType<typeof client.listTerrain>>);
  getRasterTools.mockResolvedValue({
    width: 1600,
    height: 1000,
    statistics: null,
    display_source: "original",
    statistics_job: null,
    display_copy_job: null,
  } as unknown as Awaited<ReturnType<typeof client.getRasterTools>>);
  getIngestionRules.mockResolvedValue({
    rules: [{ modality: "overhead_raster", label: "Satellite / aerial raster (GeoTIFF)", extensions: [".tif", ".tiff"], max_bytes: 1024 ** 3 }],
  } as unknown as Awaited<ReturnType<typeof client.getIngestionRules>>);
});

describe("Satellite Images with nothing uploaded", () => {
  it("opens the map to find a location, with the upload still one click away", async () => {
    render();
    expect(await screen.findByText("Find a location")).toBeInTheDocument();
    expect(screen.getByTestId("map")).toBeInTheDocument();
    expect(screen.getByText("No images uploaded to this project")).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Place name or coordinates" })).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("button", { name: "Upload image" })).toBeEnabled());
    expect(screen.queryByTestId("viewer-port")).not.toBeInTheDocument();
  });

  it("goes to a typed latitude and longitude without looking it up anywhere", async () => {
    const lookups = vi.spyOn(globalThis, "fetch");
    render();
    const box = await screen.findByRole("combobox", { name: "Place name or coordinates" });
    await userEvent.type(box, "20.2961, 85.8245{enter}");
    const found = await screen.findByRole("region", { name: "Found location" });
    expect(found).toHaveTextContent("20.296100° N, 85.824500° E");
    await waitFor(() => expect(drawn.at(-1)?.goTo).toEqual({ lon: 85.8245, lat: 20.2961, zoom: 16 }));
    expect(drawn.at(-1)?.pinned).toEqual({ lon: 85.8245, lat: 20.2961 });
    expect(screen.getByTestId("search")).toHaveTextContent("lat=20.296100&lon=85.824500");
    expect(lookups).not.toHaveBeenCalled(); // a position is read on this computer
    lookups.mockRestore();
  });

  it("says why a typed position cannot be used, and goes nowhere", async () => {
    render();
    const box = await screen.findByRole("combobox", { name: "Place name or coordinates" });
    await userEvent.type(box, "120.5, 20.3{enter}");
    expect(await screen.findByText("This position cannot be used")).toBeInTheDocument();
    expect(screen.getByText(/did you mean 20.3, 120.5\?/)).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Found location" })).toBeNull();
    expect(drawn.at(-1)?.goTo).toBeNull();
  });

  it("opens a position given in the address, so a view can be shared", async () => {
    render("/projects/p1/satellite?view=location&lat=19.8135&lon=85.8312&label=Puri");
    expect(await screen.findByRole("region", { name: "Found location" })).toHaveTextContent("Puri");
    expect(drawn.at(-1)?.goTo).toEqual({ lon: 85.8312, lat: 19.8135, zoom: 16 });
  });

  it("refuses a file type that is not accepted, says why, and uploads nothing", async () => {
    render();
    await waitFor(() => expect(screen.getByRole("button", { name: "Upload image" })).toBeEnabled());
    const input = screen.getByLabelText("Choose a satellite or aerial image file");
    expect(input).toHaveAttribute("accept", ".tif,.tiff");
    await userEvent.upload(input, new File(["x"], "photo.jpg", { type: "image/jpeg" }), { applyAccept: false });
    expect(await screen.findByRole("alert")).toHaveTextContent("This file type is not supported here");
    expect(uploadAsset).not.toHaveBeenCalled();
  });

  it("uploads with the existing call as a satellite raster, then opens the new image", async () => {
    const stored = image("a9", "new.tif", true);
    uploadAsset.mockImplementation(async () => {
      rasters(stored);
      return { asset: stored, duplicate: false } as unknown as Awaited<ReturnType<typeof client.uploadAsset>>;
    });
    render();
    await waitFor(() => expect(screen.getByRole("button", { name: "Upload image" })).toBeEnabled());
    const file = new File(["II*\0"], "new.tif", { type: "image/tiff" });
    await userEvent.upload(screen.getByLabelText("Choose a satellite or aerial image file"), file);
    await waitFor(() => expect(uploadAsset).toHaveBeenCalledWith("p1", "overhead_raster", file, expect.any(Function)));
    expect(await screen.findByTestId("viewer-port")).toBeInTheDocument();
    expect(screen.getByText("new.tif")).toBeInTheDocument();
  });

  it("shows the server's reason when it does not accept the file", async () => {
    uploadAsset.mockRejectedValue(new client.ApiError(415, "UNSUPPORTED_MEDIA", "The file is not a valid GeoTIFF", "It could not be read as a raster."));
    render();
    await waitFor(() => expect(screen.getByRole("button", { name: "Upload image" })).toBeEnabled());
    await userEvent.upload(screen.getByLabelText("Choose a satellite or aerial image file"), new File(["junk"], "bad.tif"));
    expect(await screen.findByRole("alert")).toHaveTextContent("The file is not a valid GeoTIFF");
  });

  it("explains when the only rasters are elevation files, and does not show them as images", async () => {
    rasters(HEIGHTS);
    listTerrain.mockResolvedValue({ items: [{ asset_id: "a3", status: "usable" }] } as unknown as Awaited<ReturnType<typeof client.listTerrain>>);
    render();
    expect(await screen.findByRole("link", { name: /Elevation files in 3D/ })).toHaveAttribute("href", "/projects/p1/3d");
    expect(screen.getByText("No images uploaded to this project")).toBeInTheDocument();
  });
});

describe("Where a georeferenced image is", () => {
  const FOOTPRINT = {
    type: "Polygon",
    coordinates: [[[85.8, 20.2], [85.9, 20.2], [85.9, 20.4], [85.8, 20.4], [85.8, 20.2]]],
  };
  const LOCATED = image("a1", "scene.tif", true, {
    raster: { georeferenced: true, width: 1600, height: 1000, band_count: 3, footprint_4326: FOOTPRINT },
  });

  it("shows the image's centre and links to it on the satellite map", async () => {
    rasters(LOCATED);
    render("/projects/p1/assets/a1/overhead");
    expect(await screen.findByLabelText("Image centre")).toHaveTextContent("Centre 20.30000° N, 85.85000° E");
    expect(screen.getByRole("link", { name: /Show on satellite map/ })).toHaveAttribute(
      "href",
      "/projects/p1/satellite?view=location&image=a1",
    );
    expect(screen.getByRole("link", { name: /Find a location/ })).toHaveAttribute("href", "/projects/p1/satellite?view=location");
  });

  it("reads the latitude and longitude of a clicked point from the server", async () => {
    rasters(LOCATED);
    getPixel.mockResolvedValue({ col: 800, row: 500, lon_lat: [85.85, 20.3], values: [], dtypes: [], native: null, georeferencing_source: "file", note: "" } as unknown as client.PixelValues);
    render("/projects/p1/assets/a1/overhead");
    const picture = await screen.findByAltText("Satellite or aerial image scene.tif");
    fireEvent.load(picture);
    expect(await screen.findByText("Click the image to read the latitude and longitude of a point.")).toBeInTheDocument();
    const port = screen.getByTestId("viewer-port");
    fireEvent.pointerDown(port, { button: 0, clientX: 0, clientY: 0 });
    fireEvent.pointerUp(port, { button: 0, clientX: 0, clientY: 0 });
    const point = await screen.findByRole("region", { name: "Clicked point" });
    expect(point).toHaveTextContent("20.300000° N, 85.850000° E");
    expect(getPixel).toHaveBeenCalledWith("a1", { col: expect.any(Number), row: expect.any(Number) });
  });

  it("says why no position is given when the conversion is not exact enough", async () => {
    rasters(LOCATED);
    getPixel.mockResolvedValue({ col: 800, row: 500, lon_lat: null, lon_lat_note: "Several datum shifts exist; choose one.", values: [], dtypes: [], native: null, georeferencing_source: "file", note: "" } as unknown as client.PixelValues);
    render("/projects/p1/assets/a1/overhead");
    fireEvent.load(await screen.findByAltText("Satellite or aerial image scene.tif"));
    const port = screen.getByTestId("viewer-port");
    fireEvent.pointerDown(port, { button: 0, clientX: 0, clientY: 0 });
    fireEvent.pointerUp(port, { button: 0, clientX: 0, clientY: 0 });
    expect(await screen.findByText("Several datum shifts exist; choose one.")).toBeInTheDocument();
  });

  it("does not offer positions for an image that is not georeferenced", async () => {
    rasters(PLAIN);
    render("/projects/p1/assets/a2/overhead");
    fireEvent.load(await screen.findByAltText("Satellite or aerial image plain.tif"));
    expect(screen.queryByText(/Click the image to read/)).toBeNull();
    expect(screen.queryByRole("link", { name: /Show on satellite map/ })).toBeNull();
  });

  it("draws the project's georeferenced images on the satellite map and frames the chosen one", async () => {
    rasters(LOCATED);
    getLayers.mockResolvedValue({
      rasters: [{ asset_id: "a1", title: "scene.tif", tile_url: "/tiles/a1/{z}/{x}/{y}.png", bounds_4326: [85.8, 20.2, 85.9, 20.4] }],
    } as unknown as Awaited<ReturnType<typeof client.getLayers>>);
    render("/projects/p1/satellite?view=location&image=a1");
    expect(await screen.findByRole("region", { name: "Your image" })).toHaveTextContent("scene.tif");
    await waitFor(() => expect(drawn.at(-1)?.rasters).toHaveLength(1));
    expect(drawn.at(-1)?.fitTo).toEqual([85.8, 20.2, 85.9, 20.4]);
    await userEvent.click(screen.getByLabelText(/Show this project's georeferenced images/));
    expect(drawn.at(-1)?.rasters).toHaveLength(0);
  });
});

describe("Satellite Images with images", () => {
  it("opens the first image straight into the viewer, drawn from the bounded server preview", async () => {
    rasters(PLACED, PLAIN);
    render();
    const picture = await screen.findByAltText("Satellite or aerial image scene.tif");
    expect(picture.getAttribute("src")).toMatch(/^\/api\/v1\/assets\/a1\/render\.png\?max_side=2048&ready=n-original$/);
    expect(screen.getByText("Georeferenced")).toBeInTheDocument();
    expect(screen.getByLabelText("Image")).toHaveValue("a1");
    for (const name of ["Zoom in", "Zoom out", "Fit to screen"]) expect(screen.getByRole("button", { name })).toBeInTheDocument();
  });

  it("states that an image is not georeferenced, and assumes nothing about it", async () => {
    rasters(PLAIN);
    render("/projects/p1/assets/a2/overhead");
    expect(await screen.findByText("Not georeferenced")).toBeInTheDocument();
    expect(screen.queryByText("Georeferenced")).not.toBeInTheDocument();
  });

  it("switches between uploaded images, and leaves elevation files out of the list", async () => {
    rasters(PLACED, PLAIN, HEIGHTS);
    listTerrain.mockResolvedValue({ items: [{ asset_id: "a3", status: "usable" }] } as unknown as Awaited<ReturnType<typeof client.listTerrain>>);
    render("/projects/p1/assets/a1/overhead");
    await screen.findByTestId("viewer-port"); // the bar is drawn again once the raster's size is known
    await waitFor(() => expect(screen.getByLabelText("Image").querySelectorAll("option")).toHaveLength(2));
    const select = screen.getByLabelText("Image");
    await userEvent.selectOptions(select, "a2");
    await waitFor(() => expect(screen.getByTestId("location")).toHaveTextContent("/projects/p1/assets/a2/overhead"));
    expect(await screen.findByAltText("Satellite or aerial image plain.tif")).toBeInTheDocument();
    expect(screen.getByText("Not georeferenced")).toBeInTheDocument();
  });

  it("carries none of the removed analysis controls", async () => {
    rasters(PLACED);
    render("/projects/p1/assets/a1/overhead");
    await screen.findByTestId("viewer-port");
    for (const text of [/Advanced inspection/, /Brightness/, /Contrast/, /Grayscale/, /True colour/, /Pixel/, /Histogram/, /Statistics/, /Image information/, /Band/]) {
      expect(screen.queryByText(text)).not.toBeInTheDocument();
    }
    expect(getRasterTools).toHaveBeenCalledTimes(1); // one look at the raster, no repeated requests
  });

  it("warns, but still shows the file, when it is elevation data", async () => {
    rasters(PLACED, HEIGHTS);
    listTerrain.mockResolvedValue({ items: [{ asset_id: "a3", status: "usable" }] } as unknown as Awaited<ReturnType<typeof client.listTerrain>>);
    render("/projects/p1/assets/a3/overhead");
    expect(await screen.findByText("This file is elevation data, not a satellite image")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open it in 3D Terrain" })).toHaveAttribute("href", "/projects/p1/3d");
  });
});
