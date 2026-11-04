import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Outlet, Route, Routes, useLocation } from "react-router-dom";
import { vi } from "vitest";

import * as client from "../api/client";
import { renderPage } from "../test/render";
import { SatellitePage } from "./SatellitePage";

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
  };
});

const listAssets = vi.mocked(client.listAssets);
const getAsset = vi.mocked(client.getAsset);
const listTerrain = vi.mocked(client.listTerrain);
const getRasterTools = vi.mocked(client.getRasterTools);
const getIngestionRules = vi.mocked(client.getIngestionRules);
const uploadAsset = vi.mocked(client.uploadAsset);

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
  rasters();
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
  it("shows the empty state with the one action", async () => {
    render();
    expect(await screen.findByText("No satellite or aerial images")).toBeInTheDocument();
    expect(screen.getByText("Upload a GeoTIFF satellite or aerial image to view it here.")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("button", { name: "Upload image" })).toBeEnabled());
    expect(screen.queryByTestId("viewer-port")).not.toBeInTheDocument();
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
    expect(await screen.findByText("No satellite or aerial images")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /View them in 3D Terrain/ })).toHaveAttribute("href", "/projects/p1/3d");
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
