import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";

import * as client from "../api/client";
import { renderPage } from "../test/render";
import type { MapCanvasProps } from "./MapCanvas";
import { PhotoLocationPanel } from "./PhotoLocationPanel";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, getPhotoLocation: vi.fn(), changePhotoLocation: vi.fn(), readPhotoExif: vi.fn(), getLayers: vi.fn() };
});

// jsdom has no WebGL: the stand-in records what the panel asks the map to draw and lets a test click the map.
const drawn: MapCanvasProps[] = [];
vi.mock("./MapCanvas", () => ({
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

const getPhotoLocation = vi.mocked(client.getPhotoLocation);
const changePhotoLocation = vi.mocked(client.changePhotoLocation);
const readPhotoExif = vi.mocked(client.readPhotoExif);
const getLayers = vi.mocked(client.getLayers);

const ASSET = {
  id: "a1",
  project_id: "p1",
  original_filename: "gate.jpg",
  modality: "ground_image",
  preview: { status: "ready", variants: ["view"], job_id: "j0", error_code: null, error_message: null },
} as unknown as client.Asset;

const NOTE = "A confirmed location records where this photograph was taken or placed.";
const NO_CURRENT: client.PhotoLocation["current"] = { state: "none", version_no: 0, source: null, latitude: null, longitude: null, crs_id: null, reason: null, actor_label: null, changed_at: null };
const NO_EXIF: client.PhotoLocation["exif"] = { state: "none", candidate_version_no: null, latitude: null, longitude: null, datum: null, problems: [] };

function location(overrides: Partial<client.PhotoLocation> = {}): client.PhotoLocation {
  return { asset_id: "a1", source_checksum: "ab".repeat(32), current: NO_CURRENT, exif: NO_EXIF, history: [], note: NOTE, ...overrides };
}

const CANDIDATE: client.PhotoLocation["exif"] = { state: "candidate", candidate_version_no: 1, latitude: 28.6, longitude: 77.2, datum: "not declared (EXIF default WGS 84)", problems: [] };
const CANDIDATE_ROW: client.PhotoLocation["history"][number] = {
  version_no: 1, status: "candidate", source: "exif", latitude: 28.6, longitude: 77.2, crs_id: "EPSG:4326",
  candidate_version_no: null, reason: null, actor_label: "Asha", created_at: "2026-10-28T10:00:00Z",
};

function confirmed(source: "exif" | "manual_entry" | "map_selection", lat: number, lon: number, version = 2): client.PhotoLocation {
  return location({
    current: { state: "confirmed", version_no: version, source, latitude: lat, longitude: lon, crs_id: "EPSG:4326", reason: null, actor_label: "Asha", changed_at: "2026-10-28T10:05:00Z" },
    exif: CANDIDATE,
    history: [CANDIDATE_ROW, { ...CANDIDATE_ROW, version_no: version, status: "confirmed", source, latitude: lat, longitude: lon }],
  });
}

const LAYERS = {
  rasters: [],
  excluded: [],
  aoi: null,
  observations: { with_location: 0, without_location: 0, note: "" },
  basemap: { configured: false, detail: "No offline basemap is configured." },
} as unknown as client.Layers;

const RASTER = {
  asset_id: "r1", title: "harbour_utm43n.tif", crs_id: "EPSG:32643", crs_name: "WGS 84 / UTM zone 43N", placement: "trusted", georef_version: 0,
  band_count: 3, dtypes: ["uint8"], bounds_4326: [75, 27.12, 75.02, 27.1225], footprint_4326: { type: "Polygon", coordinates: [] },
  tile_url: "/api/v1/assets/r1/tiles/{z}/{x}/{y}.png", display_unavailable_reason: null, min_zoom: 0, max_zoom: 17, native_zoom: 14, bands_shown: [1, 2, 3],
  display_stretch: "none", pixel_size: [10, 10], pixel_units: "metre", footprint_transformation: null,
} as unknown as client.RasterLayer;

const lastMap = () => drawn[drawn.length - 1]!;

beforeEach(() => {
  vi.clearAllMocks();
  drawn.length = 0;
  getLayers.mockResolvedValue(LAYERS);
});

function render() {
  return renderPage(<PhotoLocationPanel asset={ASSET} />);
}

describe("an unverified EXIF candidate", () => {
  it("is shown as unverified and is not drawn as a location", async () => {
    getPhotoLocation.mockResolvedValue(location({ exif: CANDIDATE, history: [CANDIDATE_ROW] }));
    render();
    expect(await screen.findByText(/Unverified candidate — not a location yet/)).toBeInTheDocument();
    const candidate = screen.getByRole("region", { name: "GPS in the photo file" });
    expect(within(candidate).getByText(/28\.600000° N, 77\.200000° E/)).toBeInTheDocument();
    expect(screen.getByText(/No confirmed location/)).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Confirmed location" })).not.toBeInTheDocument();
    // The map is given the candidate only as a preview ring: no photograph marker is drawn.
    await waitFor(() => expect(lastMap().photos).toEqual([]));
    await waitFor(() => expect(lastMap().preview).toEqual({ lon: 77.2, lat: 28.6 }));
  });

  it("is confirmed only when the person presses the button, and the request carries the reviewed version", async () => {
    getPhotoLocation.mockResolvedValue(location({ current: { ...NO_CURRENT, version_no: 1 }, exif: CANDIDATE, history: [CANDIDATE_ROW] }));
    changePhotoLocation.mockResolvedValue(confirmed("exif", 28.6, 77.2));
    render();
    await screen.findByText(/Unverified candidate/);
    expect(changePhotoLocation).not.toHaveBeenCalled(); // nothing is saved by looking at it
    await userEvent.click(screen.getByRole("button", { name: "Confirm this position" }));
    expect(changePhotoLocation).toHaveBeenCalledWith("a1", { action: "confirm_exif", reason: null, expected_version: 1, confirm: true });
    const region = await screen.findByRole("region", { name: "Confirmed location" });
    expect(within(region).getByText(/28\.600000° N, 77\.200000° E/)).toBeInTheDocument();
    expect(within(region).getByText(/GPS position from the photo file, confirmed · by Asha/)).toBeInTheDocument();
    await waitFor(() => expect(lastMap().photos).toEqual([{ id: "a1", lon: 77.2, lat: 28.6, label: "gate.jpg" }]));
  });

  it("does not offer to confirm a position the person has already confirmed", async () => {
    getPhotoLocation.mockResolvedValue(confirmed("exif", 28.6, 77.2));
    render();
    expect(await screen.findByText("This is the position you confirmed.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Confirm this position" })).not.toBeInTheDocument();
  });
});

describe("GPS that cannot be used or has not been read", () => {
  it("lists why unusable GPS tags are not a candidate", async () => {
    getPhotoLocation.mockResolvedValue(
      location({ exif: { ...NO_EXIF, state: "unusable", problems: [{ code: "GPS_VOID", message: "The device marked the reading as a void measurement (no valid fix)." }] } }),
    );
    render();
    expect(await screen.findByText("The file has GPS tags that cannot be used")).toBeInTheDocument();
    expect(screen.getByText(/void measurement/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Confirm this position" })).not.toBeInTheDocument();
    await waitFor(() => expect(lastMap().preview).toBeNull());
  });

  it("says when the file carries no GPS", async () => {
    getPhotoLocation.mockResolvedValue(location());
    render();
    expect(await screen.findByText("The file carries no GPS position.")).toBeInTheDocument();
  });

  it("reads the original on request and reports what it found", async () => {
    getPhotoLocation.mockResolvedValue(location({ exif: { ...NO_EXIF, state: "not_read" } }));
    readPhotoExif.mockResolvedValue({ outcome: "candidate_recorded", problems: [], location: location({ current: { ...NO_CURRENT, version_no: 1 }, exif: CANDIDATE, history: [CANDIDATE_ROW] }) });
    render();
    await screen.findByText(/have not been read/);
    await userEvent.click(screen.getByRole("button", { name: "Read the GPS position from the file" }));
    expect(readPhotoExif).toHaveBeenCalledWith("a1");
    expect(await screen.findByText(/A GPS position was read from the file\. It is unverified until you confirm it\./)).toBeInTheDocument();
    expect(await screen.findByText(/Unverified candidate/)).toBeInTheDocument();
  });

  it("shows a refusal when the file cannot be read again", async () => {
    getPhotoLocation.mockResolvedValue(location());
    readPhotoExif.mockRejectedValue(new client.ApiError(409, "ORIGINAL_MISSING", "The stored original is missing", "It cannot be read."));
    render();
    await userEvent.click(await screen.findByRole("button", { name: "Read the file's GPS again" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("The file could not be read again");
    expect(alert).toHaveTextContent("It cannot be read.");
    expect(alert).toHaveTextContent("ORIGINAL_MISSING");
  });
});

describe("manual entry and map selection", () => {
  it("validates the fields and keeps Save disabled until the position is valid and reviewed", async () => {
    getPhotoLocation.mockResolvedValue(location());
    render();
    const save = await screen.findByRole("button", { name: "Save location" });
    expect(save).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Latitude"), "91");
    await userEvent.type(screen.getByLabelText("Longitude"), "77.2");
    expect(screen.getByText("Latitude must be between -90 and 90.")).toBeInTheDocument();
    expect(lastMap().preview).toBeNull(); // an invalid position is not previewed
    expect(screen.getByRole("checkbox", { name: /I have checked this position/ })).toBeDisabled();
    await userEvent.clear(screen.getByLabelText("Latitude"));
    await userEvent.type(screen.getByLabelText("Latitude"), "28.6");
    expect(screen.queryByText(/Latitude must be/)).not.toBeInTheDocument();
    expect(lastMap().preview).toEqual({ lon: 77.2, lat: 28.6 }); // shown on the map before saving
    expect(save).toBeDisabled(); // not yet reviewed
    await userEvent.click(screen.getByRole("checkbox", { name: /I have checked this position/ }));
    expect(save).toBeEnabled();
    expect(changePhotoLocation).not.toHaveBeenCalled();
  });

  it("refuses text, an empty field and 0, 0", async () => {
    getPhotoLocation.mockResolvedValue(location());
    render();
    await userEvent.type(await screen.findByLabelText("Latitude"), "north");
    expect(screen.getByText(/Latitude must be a number in decimal degrees/)).toBeInTheDocument();
    await userEvent.clear(screen.getByLabelText("Latitude"));
    await userEvent.type(screen.getByLabelText("Latitude"), "0");
    await userEvent.type(screen.getByLabelText("Longitude"), "0");
    expect(screen.getByText(/0, 0 is almost always a missing value/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save location" })).toBeDisabled();
  });

  it("saves typed coordinates with the typed method, the note and the reviewed version", async () => {
    getPhotoLocation.mockResolvedValue(location());
    changePhotoLocation.mockResolvedValue(confirmed("manual_entry", 19.076, 72.8777, 1));
    render();
    await userEvent.type(await screen.findByLabelText("Latitude"), "19.076");
    await userEvent.type(screen.getByLabelText("Longitude"), "72.8777");
    await userEvent.type(screen.getByLabelText("Note (optional)"), "Site notes");
    await userEvent.click(screen.getByRole("checkbox", { name: /I have checked this position/ }));
    await userEvent.click(screen.getByRole("button", { name: "Save location" }));
    expect(changePhotoLocation).toHaveBeenCalledWith("a1", {
      action: "set_position", method: "manual_entry", latitude: 19.076, longitude: 72.8777, reason: "Site notes", expected_version: 0, confirm: true,
    });
    expect(await screen.findByRole("region", { name: "Confirmed location" })).toHaveTextContent("Coordinates typed in");
    expect(screen.getByText(/The location was saved/)).toBeInTheDocument();
  });

  it("fills the fields from a point chosen on the map and saves it as a map selection", async () => {
    getPhotoLocation.mockResolvedValue(location());
    changePhotoLocation.mockResolvedValue(confirmed("map_selection", 27.1222, 75.0005, 1));
    render();
    await userEvent.click(await screen.findByRole("button", { name: "click the map" }));
    expect(screen.getByLabelText("Latitude")).toHaveValue("27.122200");
    expect(screen.getByLabelText("Longitude")).toHaveValue("75.000500");
    await waitFor(() => expect(lastMap().preview).toEqual({ lon: 75.0005, lat: 27.1222 }));
    await userEvent.click(screen.getByRole("checkbox", { name: /I have checked this position/ }));
    await userEvent.click(screen.getByRole("button", { name: "Save location" }));
    expect(changePhotoLocation).toHaveBeenCalledWith("a1", expect.objectContaining({ action: "set_position", method: "map_selection", latitude: 27.1222, longitude: 75.0005 }));
    expect(await screen.findByRole("region", { name: "Confirmed location" })).toHaveTextContent("Point chosen on the map");
  });

  it("returns to the typed method when a picked value is edited by hand", async () => {
    getPhotoLocation.mockResolvedValue(location());
    changePhotoLocation.mockResolvedValue(confirmed("manual_entry", 27.1, 75.0005, 1));
    render();
    await userEvent.click(await screen.findByRole("button", { name: "click the map" }));
    await userEvent.clear(screen.getByLabelText("Latitude"));
    await userEvent.type(screen.getByLabelText("Latitude"), "27.1");
    await userEvent.click(screen.getByRole("checkbox", { name: /I have checked this position/ }));
    await userEvent.click(screen.getByRole("button", { name: "Save location" }));
    expect(changePhotoLocation).toHaveBeenCalledWith("a1", expect.objectContaining({ method: "manual_entry", latitude: 27.1 }));
  });

  it("shows the server's refusal and keeps what was typed", async () => {
    getPhotoLocation.mockResolvedValue(location());
    changePhotoLocation.mockRejectedValue(
      new client.ApiError(409, "LOCATION_VERSION_CONFLICT", "The location changed since you reviewed it", "You reviewed version 0; the current version is 2."),
    );
    render();
    await userEvent.type(await screen.findByLabelText("Latitude"), "10.5");
    await userEvent.type(screen.getByLabelText("Longitude"), "20.25");
    await userEvent.click(screen.getByRole("checkbox", { name: /I have checked this position/ }));
    await userEvent.click(screen.getByRole("button", { name: "Save location" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("The location was not saved");
    expect(alert).toHaveTextContent("The location changed since you reviewed it");
    expect(alert).toHaveTextContent("LOCATION_VERSION_CONFLICT");
    expect(screen.getByLabelText("Latitude")).toHaveValue("10.5");
    await waitFor(() => expect(getPhotoLocation).toHaveBeenCalledTimes(2)); // a conflict reloads the current state
  });
});

describe("removing a location and the history", () => {
  it("clears a confirmed location with the reviewed version", async () => {
    getPhotoLocation.mockResolvedValue(confirmed("exif", 28.6, 77.2));
    changePhotoLocation.mockResolvedValue(location({ current: { ...NO_CURRENT, version_no: 3 }, exif: CANDIDATE, history: [CANDIDATE_ROW] }));
    render();
    await userEvent.click(await screen.findByRole("button", { name: "Remove the location" }));
    expect(changePhotoLocation).toHaveBeenCalledWith("a1", { action: "clear", reason: null, expected_version: 2, confirm: true });
    expect(await screen.findByText(/The location was removed/)).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Confirmed location" })).not.toBeInTheDocument();
    await waitFor(() => expect(lastMap().photos).toEqual([]));
  });

  it("lists the history, newest first, naming who and how", async () => {
    getPhotoLocation.mockResolvedValue(confirmed("manual_entry", 10.5, 20.25));
    render();
    const history = within(await screen.findByRole("list", { name: "Location history", hidden: true }));
    const items = history.getAllByRole("listitem", { hidden: true });
    expect(items[0]).toHaveTextContent(/v2 · confirmed/);
    expect(items[0]).toHaveTextContent(/Coordinates typed in · Asha/);
    expect(items[1]).toHaveTextContent(/v1 · unverified EXIF candidate/);
  });
});

describe("states of the panel", () => {
  it("says so when the location cannot be loaded", async () => {
    getPhotoLocation.mockRejectedValue(new client.ApiError(0, "API_UNREACHABLE", "The local API is not reachable"));
    render();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("The photo location could not be loaded");
    expect(alert).toHaveTextContent("API_UNREACHABLE");
  });

  it("shows a loading state first", () => {
    getPhotoLocation.mockReturnValue(new Promise(() => {}));
    render();
    expect(screen.getByText("Loading the photo location…")).toBeInTheDocument();
  });

  it("states plainly that no imagery is available and that nothing is downloaded", async () => {
    getPhotoLocation.mockResolvedValue(location());
    render();
    const message = await screen.findByTestId("no-imagery");
    expect(message).toHaveTextContent("No georeferenced GeoTIFF imagery is available in this project");
    expect(message).toHaveTextContent("Nothing is downloaded from an online source");
    expect(screen.queryByRole("combobox", { name: "Context imagery" })).not.toBeInTheDocument();
  });

  it("offers uploaded GeoTIFFs as context and can show none", async () => {
    getLayers.mockResolvedValue({ ...LAYERS, rasters: [RASTER, { ...RASTER, asset_id: "r2", title: "second.tif" }] } as client.Layers);
    getPhotoLocation.mockResolvedValue(location());
    render();
    const select = await screen.findByRole("combobox", { name: "Context imagery" });
    expect(within(select).getAllByRole("option").map((o) => o.textContent)).toEqual(["None (coordinate grid only)", "harbour_utm43n.tif", "second.tif"]);
    expect(select).toHaveValue("r1"); // the first placed raster
    await waitFor(() => expect(lastMap().rasters.map((r) => r.layer.asset_id)).toEqual(["r1"]));
    await userEvent.selectOptions(select, "r2");
    await waitFor(() => expect(lastMap().rasters.map((r) => r.layer.asset_id)).toEqual(["r2"]));
    await userEvent.selectOptions(select, "");
    await waitFor(() => expect(lastMap().rasters).toEqual([]));
  });

  it("says that imagery is placed but still being prepared, then offers it when it is ready", async () => {
    const waiting = { ...RASTER, tile_url: null, display_unavailable_reason: "Band statistics are still being computed." };
    getLayers.mockResolvedValueOnce({ ...LAYERS, rasters: [waiting] } as client.Layers).mockResolvedValue({ ...LAYERS, rasters: [RASTER] } as client.Layers);
    getPhotoLocation.mockResolvedValue(location());
    render();
    const message = await screen.findByTestId("imagery-preparing");
    expect(message).toHaveTextContent("harbour_utm43n.tif is placed but not ready to display yet");
    expect(message).toHaveTextContent("Band statistics are still being computed.");
    expect(screen.queryByTestId("no-imagery")).not.toBeInTheDocument();
    // The panel asks again by itself; the person does not have to reload.
    expect(await screen.findByRole("combobox", { name: "Context imagery" }, { timeout: 8000 })).toHaveValue("r1");
  }, 15_000);

  it("says when the project's imagery cannot be listed, without hiding the rest of the panel", async () => {
    getLayers.mockRejectedValue(new client.ApiError(500, "INTERNAL", "Something failed"));
    getPhotoLocation.mockResolvedValue(location());
    render();
    expect(await screen.findByText("The project's imagery could not be loaded")).toBeInTheDocument();
    expect(screen.getByLabelText("Latitude")).toBeInTheDocument();
  });
});
