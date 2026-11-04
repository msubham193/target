import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";

import * as client from "../api/client";
import { searchPlaces } from "../lib/mapbox";
import { renderPage } from "../test/render";
import { NewProjectPage } from "./NewProjectPage";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, createProject: vi.fn(), searchCrs: vi.fn() };
});

const TOKEN = "pk.eyJ1IjoiZXhhbXBsZSIsImEiOiJmYWtlZmFrZWZha2UifQ.fakefakefakefakefake";

const GEOCODE = {
  features: [
    {
      id: "place.1",
      geometry: { coordinates: [85.8245, 20.2961] },
      properties: { name: "Bhubaneswar", place_formatted: "Odisha, India" },
    },
    { id: "place.2", geometry: { coordinates: [85.9, 20.1] }, properties: { name: "Khordha", place_formatted: "Odisha, India" } },
    { id: "bad", geometry: {}, properties: { name: "No position" } },
  ],
};

// EPSG:4326 as the registry lists it: latitude first, then longitude.
const EPSG4326 = {
  identifier: "EPSG:4326",
  name: "WGS 84",
  kind: "geographic 2D",
  units: ["degree"],
  deprecated: false,
  area_of_use: { name: "World" },
  axis_order: [
    { abbreviation: "Lat", direction: "north", unit: "degree" },
    { abbreviation: "Lon", direction: "east", unit: "degree" },
  ],
} as unknown as client.Crs;

function answerWith(body: unknown, status = 200) {
  const fetchMock = vi.fn().mockResolvedValue({ ok: status >= 200 && status < 300, status, json: async () => body });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(client.searchCrs).mockResolvedValue({ items: [EPSG4326], next_cursor: null } as never);
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("searchPlaces", () => {
  it("returns the matches Mapbox gives, with their positions, and skips ones without a position", async () => {
    const fetchMock = answerWith(GEOCODE);
    const matches = await searchPlaces("Bhubaneswar", TOKEN);
    expect(matches).toEqual([
      { id: "place.1", label: "Bhubaneswar, Odisha, India", lon: 85.8245, lat: 20.2961 },
      { id: "place.2", label: "Khordha, Odisha, India", lon: 85.9, lat: 20.1 },
    ]);
    const url = String(fetchMock.mock.calls[0]![0]);
    expect(url).toContain("https://api.mapbox.com/search/geocode/v6/forward?q=Bhubaneswar");
    expect(url).toContain("limit=5");
  });

  it("explains a rejected token without repeating it", async () => {
    answerWith({}, 401);
    const failure = await searchPlaces("x", TOKEN).catch((error: Error) => error);
    expect(failure).toBeInstanceOf(Error);
    expect((failure as Error).message).not.toContain(TOKEN);
    expect((failure as Error).message).toContain("token");
  });

  it("reports any other failure with its status", async () => {
    answerWith({}, 500);
    await expect(searchPlaces("x", TOKEN)).rejects.toThrow("HTTP 500");
  });
});

describe("Place search on the New Project page", () => {
  it("stays off, and says why, when no Mapbox token is configured", async () => {
    vi.stubEnv("VITE_MAPBOX_ACCESS_TOKEN", "");
    const fetchMock = answerWith(GEOCODE);
    renderPage(<NewProjectPage />);
    expect(screen.getByLabelText("Place search")).toBeDisabled();
    expect(screen.getByText("Needs a Mapbox token")).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("suggests places as you type, and fills in a single EPSG:4326 point with longitude as x when one is clicked", async () => {
    vi.stubEnv("VITE_MAPBOX_ACCESS_TOKEN", TOKEN);
    const fetchMock = answerWith(GEOCODE);
    const user = userEvent.setup();
    renderPage(<NewProjectPage />);
    await user.type(screen.getByLabelText("Place search"), "Bhubaneswar");
    // no button pressed: the suggestions come by themselves, after a pause in typing
    const results = await screen.findByRole("listbox", { name: "Place search results" });
    expect(results).toHaveTextContent("Bhubaneswar, Odisha, India");
    expect(fetchMock).toHaveBeenCalledTimes(1); // one request for the finished word, not one per letter
    await user.click(screen.getByRole("option", { name: /Bhubaneswar, Odisha, India/ }));

    // The location section opens by itself, with the CRS and point filled in. Nothing is created yet.
    expect(await screen.findByText(/EPSG:4326 — WGS 84/)).toBeInTheDocument();
    expect(screen.getByLabelText(/^X/)).toHaveValue("85.8245"); // longitude is the east-west coordinate
    expect(screen.getByLabelText(/^Y/)).toHaveValue("20.2961");
    expect(screen.getByTestId("place-filled")).toHaveTextContent("Bhubaneswar, Odisha, India");
    expect(screen.getByLabelText("Place search")).toHaveValue("Bhubaneswar, Odisha, India");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    await new Promise((resolve) => setTimeout(resolve, 500));
    expect(fetchMock).toHaveBeenCalledTimes(1); // choosing a result does not start another search
    expect(client.createProject).not.toHaveBeenCalled();
  });

  it("does not ask Mapbox until there are three letters", async () => {
    vi.stubEnv("VITE_MAPBOX_ACCESS_TOKEN", TOKEN);
    const fetchMock = answerWith(GEOCODE);
    const user = userEvent.setup();
    renderPage(<NewProjectPage />);
    await user.type(screen.getByLabelText("Place search"), "Bh");
    await new Promise((resolve) => setTimeout(resolve, 500));
    expect(fetchMock).not.toHaveBeenCalled();
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });

  it("works from the keyboard: arrows to move, Enter to choose, Escape to close", async () => {
    vi.stubEnv("VITE_MAPBOX_ACCESS_TOKEN", TOKEN);
    answerWith(GEOCODE);
    const user = userEvent.setup();
    renderPage(<NewProjectPage />);
    const box = screen.getByLabelText("Place search");
    await user.type(box, "Bhubaneswar");
    await screen.findByRole("listbox");
    await user.keyboard("{ArrowDown}{ArrowDown}");
    expect(screen.getByRole("option", { name: /Khordha/ })).toHaveAttribute("aria-selected", "true");
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    await user.keyboard("{ArrowDown}"); // Escape cleared the highlight: this reopens the list on the first match
    await screen.findByRole("listbox");
    await user.keyboard("{ArrowDown}{Enter}");
    expect(await screen.findByTestId("place-filled")).toHaveTextContent("Khordha, Odisha, India");
    expect(screen.getByLabelText(/^X/)).toHaveValue("85.9");
  });

  it("says plainly when nothing matches, and when the search fails", async () => {
    vi.stubEnv("VITE_MAPBOX_ACCESS_TOKEN", TOKEN);
    const user = userEvent.setup();
    answerWith({ features: [] });
    renderPage(<NewProjectPage />);
    await user.type(screen.getByLabelText("Place search"), "zzzz");
    expect(await screen.findByText(/No place found for/)).toBeInTheDocument();

    answerWith({}, 401);
    await user.type(screen.getByLabelText("Place search"), "q");
    await waitFor(() => expect(screen.getByTestId("place-search-error")).toHaveTextContent("did not accept the access token"));
    expect(screen.getByTestId("place-search-error")).not.toHaveTextContent(TOKEN);
  });
});
