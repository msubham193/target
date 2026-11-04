import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { vi } from "vitest";

import { DEFAULT_LABELS, type Basemap, type BasemapStatus, type LabelState } from "../lib/mapbox";
import { BasemapPanel } from "./BasemapPanel";
import { MapCanvas, type MapCanvasProps } from "./MapCanvas";

const TOKEN = "pk.eyJ1IjoiZXhhbXBsZSIsImEiOiJmYWtlZmFrZWZha2UifQ.fakefakefakefakefake";

// Stand-ins for both map libraries that record what MapCanvas asks of them. jsdom has no WebGL, so neither real library can
// run here: this checks which engine is made, with what, and what is asked of it afterwards, not how anything looks.
const { made, FakeMap } = vi.hoisted(() => {
  type Handler = (event?: unknown) => void;
  const made: { kind: string; map: InstanceType<typeof FakeMap> }[] = [];
  class FakeMap {
    kind = "";
    options: Record<string, unknown>;
    handlers: Record<string, Handler[]> = {};
    layers: Record<string, unknown>[] = [];
    sources = new Map<string, { setData: ReturnType<typeof vi.fn> }>();
    config: [string, string, unknown][] = [];
    flights: Record<string, unknown>[] = [];
    removed = false;
    view = { lng: 0, lat: 20, zoom: 1, bearing: 0 };
    touchZoomRotate = { disableRotation() {} };
    constructor(options: Record<string, unknown>) {
      this.options = options;
      const center = options.center as [number, number];
      this.view = { lng: center[0], lat: center[1], zoom: options.zoom as number, bearing: (options.bearing as number) ?? 0 };
    }
    addControl() {}
    on(event: string, first: unknown, second?: unknown) {
      const handler = (typeof first === "function" ? first : second) as Handler;
      (this.handlers[event] ??= []).push(handler);
      if (event === "load") queueMicrotask(() => handler());
      return this;
    }
    fire(event: string, payload?: unknown) {
      for (const handler of this.handlers[event] ?? []) handler(payload);
    }
    addSource(id: string) {
      this.sources.set(id, { setData: vi.fn() });
    }
    getSource(id: string) {
      return this.sources.get(id);
    }
    addLayer(spec: Record<string, unknown>) {
      this.layers.push(spec);
    }
    getLayer(id: string) {
      return this.layers.find((layer) => layer.id === id);
    }
    getStyle() {
      return { layers: this.layers };
    }
    setLayoutProperty() {}
    setPaintProperty() {}
    setFilter() {}
    setConfigProperty(importId: string, name: string, value: unknown) {
      this.config.push([importId, name, value]);
    }
    getCenter() {
      return { lng: this.view.lng, lat: this.view.lat };
    }
    getZoom() {
      return this.view.zoom;
    }
    getBearing() {
      return this.view.bearing;
    }
    flyTo(options: Record<string, unknown>) {
      this.flights.push(options);
    }
    queryRenderedFeatures() {
      return [];
    }
    getCanvas() {
      return { style: {} };
    }
    resize() {}
    fitBounds() {}
    remove() {
      this.removed = true;
    }
  }
  return { made, FakeMap };
});

vi.mock("maplibre-gl", () => ({
  default: {
    Map: class extends FakeMap {
      constructor(o: Record<string, unknown>) {
        super(o);
        this.kind = "maplibre";
        made.push({ kind: "maplibre", map: this });
      }
    },
    NavigationControl: class {},
    ScaleControl: class {},
  },
}));
vi.mock("mapbox-gl", () => ({
  default: {
    accessToken: "",
    Map: class extends FakeMap {
      constructor(o: Record<string, unknown>) {
        super(o);
        this.kind = "mapbox";
        made.push({ kind: "mapbox", map: this });
      }
    },
    NavigationControl: class {},
    ScaleControl: class {},
  },
}));

const base: MapCanvasProps = { rasters: [], aoi: null, showAoi: false, showFootprints: false, selectedRasterId: null, fitTo: null };
const live = () => made.filter((entry) => !entry.map.removed);
const last = () => made[made.length - 1]!.map;

beforeEach(() => {
  made.length = 0;
  vi.unstubAllEnvs();
});
afterEach(() => vi.unstubAllEnvs());

describe("MapCanvas basemap", () => {
  it("draws the offline map by default, and never loads or contacts Mapbox", async () => {
    vi.stubEnv("VITE_MAPBOX_ACCESS_TOKEN", TOKEN);
    render(<MapCanvas {...base} />);
    await waitFor(() => expect(made).toHaveLength(1));
    expect(made[0]!.kind).toBe("maplibre");
    expect(last().options.style).toMatchObject({ version: 8 });
    expect(last().options.dragRotate).toBe(false);
  });

  it("draws Mapbox Standard Satellite when asked and a public token is set, with the project overlays on the same map", async () => {
    vi.stubEnv("VITE_MAPBOX_ACCESS_TOKEN", TOKEN);
    const statuses: BasemapStatus[] = [];
    render(<MapCanvas {...base} basemap="mapbox" onBasemapStatus={(s) => statuses.push(s)} />);
    await waitFor(() => expect(statuses.some((s) => s.state === "ready")).toBe(true));
    expect(live()).toHaveLength(1);
    const map = last();
    expect(map.kind).toBe("mapbox");
    expect(map.options).toMatchObject({
      style: "mapbox://styles/mapbox/standard-satellite",
      projection: "mercator",
      attributionControl: true, // Mapbox's attribution is never switched off
      config: { basemap: DEFAULT_LABELS },
    });
    expect(map.options.dragRotate).not.toBe(false); // rotation is allowed
    expect((await import("mapbox-gl")).default.accessToken).toBe(TOKEN);
    const ids = map.layers.map((layer) => layer.id);
    for (const id of ["aoi-fill", "footprints", "annotation-line", "photos", "sketch-line", "pinned"]) expect(ids).toContain(id);
    expect(ids).not.toContain("background"); // the offline graticule style is not part of the satellite map
  });

  it("falls back to the offline map, quietly and without breaking, when no token is configured", async () => {
    vi.stubEnv("VITE_MAPBOX_ACCESS_TOKEN", "");
    render(<MapCanvas {...base} basemap="mapbox" />);
    await waitFor(() => expect(made).toHaveLength(1));
    expect(made[0]!.kind).toBe("maplibre");
  });

  it("does not use a secret token", async () => {
    vi.stubEnv("VITE_MAPBOX_ACCESS_TOKEN", "sk.eyJ1IjoiZXhhbXBsZSIsImEiOiJmYWtlZmFrZWZha2UifQ.fakefakefakefakefake");
    render(<MapCanvas {...base} basemap="mapbox" />);
    await waitFor(() => expect(made).toHaveLength(1));
    expect(made[0]!.kind).toBe("maplibre");
  });

  it("replaces the map when the basemap changes, never keeps two, and carries the view over", async () => {
    vi.stubEnv("VITE_MAPBOX_ACCESS_TOKEN", TOKEN);
    const view = render(<MapCanvas {...base} basemap="offline" />);
    await waitFor(() => expect(made).toHaveLength(1));
    const first = last();
    first.view = { lng: 85.8245, lat: 20.2961, zoom: 14.5, bearing: 0 };
    first.fire("moveend");

    view.rerender(<MapCanvas {...base} basemap="mapbox" />);
    await waitFor(() => expect(made).toHaveLength(2));
    expect(first.removed).toBe(true);
    expect(live()).toHaveLength(1);
    expect(last().options).toMatchObject({ center: [85.8245, 20.2961], zoom: 14.5 });

    last().view = { lng: 77.209, lat: 28.6139, zoom: 12, bearing: 30 };
    last().fire("moveend");
    view.rerender(<MapCanvas {...base} basemap="offline" />);
    await waitFor(() => expect(made).toHaveLength(3));
    expect(live()).toHaveLength(1);
    expect(last().options).toMatchObject({ center: [77.209, 28.6139], zoom: 12 });
  });

  describe("camera across basemap changes", () => {
    // Mapbox is rotated to 45 degrees at a known place, then the person works on the offline map, which is always north-up.
    const MAPBOX_VIEW = { lng: 85.8245, lat: 20.2961, zoom: 15, bearing: 45 };

    async function rotatedMapbox() {
      vi.stubEnv("VITE_MAPBOX_ACCESS_TOKEN", TOKEN);
      const view = render(<MapCanvas {...base} basemap="mapbox" />);
      await waitFor(() => expect(made).toHaveLength(1));
      last().view = { ...MAPBOX_VIEW };
      last().fire("moveend");
      return view;
    }
    const show = async (view: ReturnType<typeof render>, basemap: Basemap, count: number) => {
      view.rerender(<MapCanvas {...base} basemap={basemap} />);
      await waitFor(() => expect(made).toHaveLength(count));
      expect(live()).toHaveLength(1);
    };

    it("saves the Mapbox bearing and gives it back when Mapbox is made again", async () => {
      const view = await rotatedMapbox();
      await show(view, "offline", 2);
      await show(view, "mapbox", 3);
      expect(last().kind).toBe("mapbox");
      expect(last().options).toMatchObject({ center: [85.8245, 20.2961], zoom: 15, bearing: 45 });
    });

    it("keeps the offline map north-up and rotation-free, whatever the Mapbox bearing was", async () => {
      const view = await rotatedMapbox();
      await show(view, "offline", 2);
      expect(last().kind).toBe("maplibre");
      expect(last().options.bearing).toBeUndefined();
      expect(last().options.dragRotate).toBe(false);
      expect(last().getBearing()).toBe(0);
    });

    it("does not let offline panning and zooming overwrite the Mapbox bearing", async () => {
      const view = await rotatedMapbox();
      await show(view, "offline", 2);
      const offline = last();
      // The offline map reports bearing 0 on every move; none of these may reach the saved Mapbox camera.
      offline.view = { lng: 86.1, lat: 20.5, zoom: 12.5, bearing: 0 };
      offline.fire("moveend");
      offline.view = { lng: 86.2, lat: 20.6, zoom: 9, bearing: 0 };
      offline.fire("moveend");
      await show(view, "mapbox", 3);
      // The place the person left off at is kept; only the bearing is Mapbox's own.
      expect(last().options).toMatchObject({ center: [86.2, 20.6], zoom: 9, bearing: 45 });
    });

    it("saves a new Mapbox bearing, and keeps the last one over repeated switching with offline movement each time", async () => {
      const view = await rotatedMapbox();
      let count = 1;
      for (const turn of [45, 120, -30, 0, 270]) {
        last().view = { lng: 85.8245, lat: 20.2961, zoom: 15, bearing: turn };
        last().fire("moveend");
        await show(view, "offline", ++count);
        last().view = { lng: 85.9 + count / 100, lat: 20.3, zoom: 11 + count / 10, bearing: 0 };
        last().fire("moveend");
        last().fire("moveend");
        await show(view, "mapbox", ++count);
        expect(last().options).toMatchObject({ bearing: turn, center: [85.9 + (count - 1) / 100, 20.3] });
        expect(made.filter((entry) => entry.map.removed)).toHaveLength(count - 1);
      }
    });

    it("still shows the offline map north-up when started offline, and leaves its own move handling alone", async () => {
      vi.stubEnv("VITE_MAPBOX_ACCESS_TOKEN", TOKEN);
      const view = render(<MapCanvas {...base} basemap="offline" />);
      await waitFor(() => expect(made).toHaveLength(1));
      last().view = { lng: 77.209, lat: 28.6139, zoom: 13, bearing: 0 };
      last().fire("moveend");
      await show(view, "mapbox", 2);
      expect(last().options).toMatchObject({ center: [77.209, 28.6139], zoom: 13, bearing: 0 });
    });
  });

  it("switches label groups without rebuilding the map", async () => {
    vi.stubEnv("VITE_MAPBOX_ACCESS_TOKEN", TOKEN);
    const view = render(<MapCanvas {...base} basemap="mapbox" mapboxLabels={DEFAULT_LABELS} />);
    await waitFor(() => expect(last().config.length).toBe(5));
    const map = last();
    const off: LabelState = { ...DEFAULT_LABELS, showRoadLabels: false, showPointOfInterestLabels: false };
    view.rerender(<MapCanvas {...base} basemap="mapbox" mapboxLabels={off} />);
    await waitFor(() => expect(map.config.length).toBe(10));
    expect(made).toHaveLength(1);
    const applied = Object.fromEntries(map.config.slice(5).map(([importId, name, value]) => [name, [importId, value]]));
    expect(applied.showRoadLabels).toEqual(["basemap", false]);
    expect(applied.showPointOfInterestLabels).toEqual(["basemap", false]);
    expect(applied.showPlaceLabels).toEqual(["basemap", true]);
  });

  it("does not ask the offline map for label settings", async () => {
    render(<MapCanvas {...base} mapboxLabels={{ ...DEFAULT_LABELS, showRoadLabels: false }} />);
    await waitFor(() => expect(made).toHaveLength(1));
    expect(last().config).toHaveLength(0);
  });

  it("goes to a typed position once, on either basemap", async () => {
    const target = { lon: 85.8245, lat: 20.2961 };
    const view = render(<MapCanvas {...base} goTo={target} />);
    await waitFor(() => expect(last().flights).toHaveLength(1));
    expect(last().flights[0]).toMatchObject({ center: [85.8245, 20.2961] });
    view.rerender(<MapCanvas {...base} goTo={target} showAoi />);
    expect(last().flights).toHaveLength(1); // the same request is not repeated
    view.rerender(<MapCanvas {...base} goTo={{ lon: 77.209, lat: 28.6139, zoom: 16 }} />);
    await waitFor(() => expect(last().flights).toHaveLength(2));
    expect(last().flights[1]).toMatchObject({ center: [77.209, 28.6139], zoom: 16 });
  });

  it("reports a rejected token without repeating it, and a missing basemap tile as a problem with the basemap only", async () => {
    vi.stubEnv("VITE_MAPBOX_ACCESS_TOKEN", TOKEN);
    const statuses: BasemapStatus[] = [];
    render(<MapCanvas {...base} basemap="mapbox" onBasemapStatus={(s) => statuses.push(s)} />);
    await waitFor(() => expect(statuses.some((s) => s.state === "ready")).toBe(true));
    last().fire("error", { error: { status: 401, message: "Unauthorized" } });
    const rejected = statuses.at(-1)!;
    expect(rejected.state).toBe("error");
    expect(rejected.message).toContain("rejected the access token (HTTP 401)");
    expect(JSON.stringify(statuses)).not.toContain(TOKEN);

    last().fire("error", { sourceId: "mapbox-satellite", error: { status: 404, message: "Not Found" } });
    expect(statuses.at(-1)!.message).toContain("basemap");

    // Whatever Mapbox puts in a message, a token in it never reaches the screen.
    last().fire("error", { sourceId: "mapbox-satellite", error: { status: 500, message: `failed https://api.mapbox.com/x?access_token=${TOKEN}` } });
    expect(statuses.at(-1)!.message).not.toContain(TOKEN);
    expect(statuses.at(-1)!.message).toContain("[token removed]");

    const before = statuses.length;
    last().fire("error", { sourceId: "raster-abc-v1", error: { status: 404, message: "tile missing" } });
    expect(statuses).toHaveLength(before); // the project's own raster tiles are reported by the API, not here
  });
});

function Harness({ initial = "mapbox" as Basemap, onGo = vi.fn() }) {
  const [basemap, setBasemap] = useState<Basemap>(initial);
  const [labels, setLabels] = useState<LabelState>(DEFAULT_LABELS);
  return <BasemapPanel basemap={basemap} onBasemap={setBasemap} labels={labels} onLabels={setLabels} status={null} onGo={onGo} />;
}

describe("BasemapPanel", () => {
  it("shows a clear configuration error when there is no token, and leaves the offline choice working", async () => {
    vi.stubEnv("VITE_MAPBOX_ACCESS_TOKEN", "");
    render(<Harness initial="offline" />);
    expect(screen.getByTestId("mapbox-config-error")).toHaveTextContent("VITE_MAPBOX_ACCESS_TOKEN");
    const satellite = screen.getByRole("radio", { name: /Satellite map/ });
    expect(satellite).toBeDisabled();
    expect(screen.getByRole("radio", { name: /Plain map/ })).toBeChecked();
    expect(screen.queryByRole("group", { name: "Show on the satellite map" })).toBeNull();
    // Going to a position still works.
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Latitude"), "20.2961");
    await user.type(screen.getByLabelText("Longitude"), "85.8245");
    expect(screen.getByRole("button", { name: "Go to this location" })).toBeEnabled();
  });

  it("goes to a valid position and refuses an invalid one, saying why", async () => {
    vi.stubEnv("VITE_MAPBOX_ACCESS_TOKEN", TOKEN);
    const onGo = vi.fn();
    const user = userEvent.setup();
    render(<Harness onGo={onGo} />);
    expect(screen.getByRole("button", { name: "Go to this location" })).toBeDisabled(); // nothing typed yet
    await user.type(screen.getByLabelText("Latitude"), "95");
    await user.type(screen.getByLabelText("Longitude"), "85.8245");
    await user.click(screen.getByRole("button", { name: "Go to this location" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Latitude must be between");
    expect(onGo).not.toHaveBeenCalled();
    await user.clear(screen.getByLabelText("Latitude"));
    await user.type(screen.getByLabelText("Latitude"), "20.2961 N");
    await user.click(screen.getByRole("button", { name: "Go to this location" }));
    expect(onGo).toHaveBeenCalledWith({ lat: 20.2961, lon: 85.8245 });
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("offers the label groups only on the satellite map, and switches them", async () => {
    vi.stubEnv("VITE_MAPBOX_ACCESS_TOKEN", TOKEN);
    const user = userEvent.setup();
    render(<Harness />);
    const group = screen.getByRole("group", { name: "Show on the satellite map" });
    for (const label of ["Road labels", "Place names", "Points of interest", "Transit labels", "Pedestrian roads and paths"]) {
      expect(within(group).getByRole("checkbox", { name: label })).toBeChecked();
    }
    await user.click(within(group).getByRole("checkbox", { name: "Points of interest" }));
    expect(within(group).getByRole("checkbox", { name: "Points of interest" })).not.toBeChecked();
    expect(within(group).getByRole("checkbox", { name: "Road labels" })).toBeChecked();

    await user.click(screen.getByRole("radio", { name: /Plain map/ }));
    expect(screen.queryByRole("group", { name: "Show on the satellite map" })).toBeNull();
    expect(screen.queryByTestId("mapbox-attribution-note")).toBeNull();
  });

  it("states attribution and that the imagery is not live, and warns when zoomed in far", () => {
    vi.stubEnv("VITE_MAPBOX_ACCESS_TOKEN", TOKEN);
    const { rerender } = render(
      <BasemapPanel basemap="mapbox" onBasemap={() => {}} labels={DEFAULT_LABELS} onLabels={() => {}} status={{ state: "ready", zoom: 12 }} onGo={() => {}} />,
    );
    expect(screen.getByTestId("mapbox-attribution-note")).toHaveTextContent("© Mapbox");
    expect(screen.getByTestId("mapbox-attribution-note")).toHaveTextContent("not live");
    expect(screen.queryByTestId("mapbox-zoom-note")).toBeNull();
    rerender(
      <BasemapPanel basemap="mapbox" onBasemap={() => {}} labels={DEFAULT_LABELS} onLabels={() => {}} status={{ state: "ready", zoom: 19.4 }} onGo={() => {}} />,
    );
    expect(screen.getByTestId("mapbox-zoom-note")).toHaveTextContent("may be enlarged beyond the resolution");
  });

  it("shows a provider problem and says the rest of the map still works", () => {
    vi.stubEnv("VITE_MAPBOX_ACCESS_TOKEN", TOKEN);
    render(
      <BasemapPanel
        basemap="mapbox"
        onBasemap={() => {}}
        labels={DEFAULT_LABELS}
        onLabels={() => {}}
        status={{ state: "error", message: "Mapbox rejected the access token (HTTP 401)." }}
        onGo={() => {}}
      />,
    );
    expect(screen.getByTestId("mapbox-status-error")).toHaveTextContent("rejected the access token");
    expect(screen.getByTestId("mapbox-status-error")).toHaveTextContent("still drawn and usable");
  });
});
