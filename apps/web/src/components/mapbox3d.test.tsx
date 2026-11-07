import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";

import type { StartView } from "../lib/location3d";
import { Mapbox3DView } from "./Mapbox3DView";

const TOKEN = "pk.eyJ1IjoiZXhhbXBsZSIsImEiOiJmYWtlZmFrZWZha2UifQ.fakefakefakefakefake";

// A stand-in for Mapbox GL JS that records what the view asks of it. jsdom has no WebGL, so this checks the requests,
// not how the scene looks.
const { made, markers, FakeMap } = vi.hoisted(() => {
  type Handler = (event?: unknown) => void;
  const made: InstanceType<typeof FakeMap>[] = [];
  const markers: { lngLat: [number, number]; element: HTMLElement }[] = [];
  class FakeMap {
    options: Record<string, unknown>;
    handlers: Record<string, Handler[]> = {};
    sources: Record<string, unknown> = {};
    layers: Record<string, unknown>[] = [];
    terrain: Record<string, unknown> | null = null;
    config: [string, string, unknown][] = [];
    eases: Record<string, unknown>[] = [];
    layout: Record<string, Record<string, unknown>> = {};
    bearing = -20;
    removed = false;
    constructor(options: Record<string, unknown>) {
      this.options = options;
      made.push(this);
    }
    on(event: string, handler: Handler) {
      (this.handlers[event] ??= []).push(handler);
      if (event === "style.load") queueMicrotask(() => handler());
    }
    fire(event: string, payload?: unknown) {
      for (const h of this.handlers[event] ?? []) h(payload);
    }
    addControl() {}
    getSource(id: string) {
      return this.sources[id];
    }
    addSource(id: string, source: unknown) {
      this.sources[id] = source;
    }
    getLayer(id: string) {
      return this.layers.find((l) => l.id === id);
    }
    addLayer(layer: Record<string, unknown>) {
      this.layers.push(layer);
    }
    setLayoutProperty(id: string, name: string, value: unknown) {
      (this.layout[id] ??= {})[name] = value;
    }
    setTerrain(terrain: Record<string, unknown> | null) {
      this.terrain = terrain;
    }
    getTerrain() {
      return this.terrain;
    }
    setConfigProperty(importId: string, name: string, value: unknown) {
      this.config.push([importId, name, value]);
    }
    queryTerrainElevation() {
      return 41.7;
    }
    getCenter() {
      return { lng: 85.8624, lat: 20.2998 };
    }
    easeTo(options: Record<string, unknown>) {
      this.eases.push(options);
    }
    fitBounds() {}
    jumpTo() {}
    getBearing() {
      return this.bearing;
    }
    getPitch() {
      return 0;
    }
    resize() {}
    remove() {
      this.removed = true;
    }
  }
  return { made, markers, FakeMap };
});

vi.mock("mapbox-gl", () => ({
  default: {
    accessToken: "",
    Map: FakeMap,
    NavigationControl: class {},
    ScaleControl: class {},
    Marker: class {
      element: HTMLElement;
      constructor(options: { element: HTMLElement }) {
        this.element = options.element;
      }
      setLngLat(lngLat: [number, number]) {
        markers.push({ lngLat, element: this.element });
        return { addTo: () => ({ remove() {} }) };
      }
    },
  },
}));

const START: StartView = { source: "photographs", label: "the photograph a.jpg", bounds: [85.8624, 20.2998, 85.8624, 20.2998] };
const PHOTOS = [{ id: "p1", filename: "a.jpg", lon: 85.8624, lat: 20.2998 }];
const map = () => made[made.length - 1]!;

function show(start: StartView | null = START) {
  return render(
    <MemoryRouter>
      <Mapbox3DView projectId="proj" photos={PHOTOS} start={start} />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  made.length = 0;
  markers.length = 0;
  vi.stubEnv("VITE_MAPBOX_ACCESS_TOKEN", TOKEN);
});
afterEach(() => vi.unstubAllEnvs());

describe("Mapbox3DView", () => {
  it("opens tilted on the project's location with Standard Satellite, and keeps the terrain's relief at every zoom", async () => {
    show();
    await waitFor(() => expect(map().terrain).not.toBeNull());
    expect(map().options).toMatchObject({
      style: "mapbox://styles/mapbox/standard-satellite",
      center: [85.8624, 20.2998],
      pitch: 65,
      attributionControl: true,
    });
    expect(map().terrain).toEqual({ source: "mapbox-dem", exaggeration: 1.5 }); // a constant: not flattened at street zoom
  });

  it("shades the relief by default, offers height lines, and says when the ground in view is nearly flat", async () => {
    show();
    await waitFor(() => expect(map().getLayer("online-3d-shade")).toBeTruthy());
    expect(map().getLayer("online-3d-shade")).toMatchObject({ type: "hillshade" });
    expect(map().getLayer("online-3d-contours")).toMatchObject({ type: "line", "source-layer": "contour", layout: { visibility: "none" } });
    fireEvent.click(screen.getByRole("button", { name: "Height lines" }));
    await waitFor(() => expect(map().layout["online-3d-contours"]).toEqual({ visibility: "visible" }));
    fireEvent.click(screen.getByRole("button", { name: "Hill shading" }));
    await waitFor(() => expect(map().layout["online-3d-shade"]).toEqual({ visibility: "none" }));
    act(() => map().fire("idle"));
    expect(screen.getByTestId("online-3d-relief")).toHaveTextContent("Ground within about 2 km: 42–42 m. This area is nearly flat");
  });

  it("raises only buildings whose height Mapbox records", async () => {
    show();
    await waitFor(() => expect(map().getLayer("online-3d-buildings")).toBeTruthy());
    const layer = map().getLayer("online-3d-buildings") as { type: string; filter: unknown; paint: Record<string, unknown> };
    expect(layer.type).toBe("fill-extrusion");
    expect(JSON.stringify(layer.filter)).toContain('["has","height"]');
    expect(layer.paint["fill-extrusion-height"]).toEqual(["get", "height"]); // no assumed height
    fireEvent.click(screen.getByRole("button", { name: "Buildings" }));
    await waitFor(() => expect(map().layout["online-3d-buildings"]).toEqual({ visibility: "none" }));
  });

  it("marks each located photograph, and a click shows it with a link to Photo Analysis", async () => {
    show();
    await waitFor(() => expect(markers).toHaveLength(1));
    expect(markers[0]!.lngLat).toEqual([85.8624, 20.2998]);
    act(() => markers[0]!.element.click());
    const card = await screen.findByTestId("online-3d-photo");
    expect(card).toHaveTextContent("a.jpg");
    expect(screen.getByRole("link", { name: "Open in Photo Analysis" })).toHaveAttribute("href", "/projects/proj/assets/p1/ground");
  });

  it("changes the height exaggeration, looks straight down on request, and labels the ground height as Mapbox's", async () => {
    show();
    await waitFor(() => expect(map().terrain).not.toBeNull());
    fireEvent.change(screen.getByLabelText("Height exaggeration"), { target: { value: "2.5" } });
    await waitFor(() => expect(map().terrain).toEqual({ source: "mapbox-dem", exaggeration: 2.5 }));
    fireEvent.click(screen.getByRole("button", { name: "3D tilt" }));
    await waitFor(() => expect(map().eases.some((e) => e.pitch === 0)).toBe(true));
    act(() => map().fire("mousemove", { lngLat: { lng: 85.86, lat: 20.3 } }));
    expect(screen.getByTestId("online-3d-height")).toHaveTextContent("about 42 m (Mapbox terrain model, approximate; not measured by this app)");
  });

  it("says where it is centred, and asks for a place when the project has no location", async () => {
    show();
    expect(screen.getByTestId("online-3d-centre")).toHaveTextContent("Centred on the photograph a.jpg.");
    made.length = 0;
    show(null);
    expect(screen.getAllByTestId("online-3d-centre").at(-1)).toHaveTextContent("No location in this project yet");
  });

  it("draws nothing and explains itself without a Mapbox token", () => {
    vi.stubEnv("VITE_MAPBOX_ACCESS_TOKEN", "");
    show();
    expect(screen.getByTestId("online-3d-no-token")).toHaveTextContent("needs a Mapbox public token");
    expect(made).toHaveLength(0);
  });

  it("removes its map when it goes away", async () => {
    const view = show();
    await waitFor(() => expect(made).toHaveLength(1));
    view.unmount();
    expect(map().removed).toBe(true);
  });
});
