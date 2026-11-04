import { render, waitFor } from "@testing-library/react";
import { vi } from "vitest";

import { MapCanvas, type MapCanvasProps } from "./MapCanvas";

// A stand-in for MapLibre that records what MapCanvas asks of it. jsdom has no WebGL, so the real library cannot run here;
// this checks the layers, the data and the click handling that MapCanvas sets up for annotations, not how they look.
const { instances, FakeMap } = vi.hoisted(() => {
  type Handler = (event?: unknown) => void;
  const instances: InstanceType<typeof FakeMap>[] = [];
  class FakeMap {
    sources = new Map<string, { spec: Record<string, unknown>; setData: ReturnType<typeof vi.fn> }>();
    layers: Record<string, unknown>[] = [];
    handlers: Record<string, Handler[]> = {};
    layout: Record<string, Record<string, unknown>> = {};
    filters: Record<string, unknown> = {};
    rendered: unknown[] = [];
    touchZoomRotate = { disableRotation() {} };
    constructor() {
      instances.push(this);
    }
    addControl() {}
    on(event: string, first: unknown, second?: unknown) {
      const handler = (typeof first === "function" ? first : second) as Handler;
      (this.handlers[event] ??= []).push(handler);
      if (event === "load") queueMicrotask(() => handler());
      return this;
    }
    addSource(id: string, spec: Record<string, unknown>) {
      this.sources.set(id, { spec, setData: vi.fn() });
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
    setLayoutProperty(id: string, property: string, value: unknown) {
      (this.layout[id] ??= {})[property] = value;
    }
    setPaintProperty() {}
    setFilter(id: string, filter: unknown) {
      this.filters[id] = filter;
    }
    queryRenderedFeatures() {
      return this.rendered;
    }
    project() {
      return { x: 0, y: 0 };
    }
    getCanvas() {
      return { style: {} };
    }
    resize() {}
    fitBounds() {}
    remove() {}
    getZoom() {
      return 1;
    }
    fire(event: string, payload?: unknown) {
      for (const handler of this.handlers[event] ?? []) handler(payload);
    }
  }
  return { instances, FakeMap };
});

vi.mock("maplibre-gl", () => ({ default: { Map: FakeMap, NavigationControl: class {}, ScaleControl: class {} } }));

const LINE = { type: "LineString", coordinates: [[75, 27], [75.01, 27]] };
const GATE = { type: "Point", coordinates: [75.2, 27.3] };

function props(over: Partial<MapCanvasProps> = {}): MapCanvasProps {
  return { rasters: [], aoi: null, showAoi: false, showFootprints: false, selectedRasterId: null, fitTo: null, ...over };
}

const map = () => instances[instances.length - 1]!;

async function ready(over: Partial<MapCanvasProps> = {}) {
  const view = render(<MapCanvas {...props(over)} />);
  await waitFor(() => expect(map().getSource("annotations")?.setData).toHaveBeenCalled());
  return view;
}

const dataOf = (id: string) => {
  const calls = map().getSource(id)!.setData.mock.calls;
  return calls[calls.length - 1]![0] as { features: { properties: { id: string }; geometry: unknown }[] };
};
const paint = (id: string) => (map().getLayer(id) as { paint: Record<string, unknown> }).paint;

beforeEach(() => {
  instances.length = 0;
});

describe("annotation layers on the map", () => {
  it("adds their own source and layers, in a colour no other layer uses", async () => {
    await ready();
    const ids = map().layers.map((layer) => layer.id);
    for (const id of ["annotation-fill", "annotation-line", "annotation-point", "annotation-selected-line", "annotation-selected-point"]) {
      expect(ids).toContain(id);
    }
    const magenta = paint("annotation-line")["line-color"];
    const others = ["aoi-line", "footprints", "sketch-line"].map((id) => paint(id)["line-color"]).concat(paint("photos")["circle-color"] as string);
    expect(others).not.toContain(magenta);
    expect(paint("annotation-point")["circle-color"]).toBe(magenta);
    expect(paint("photos")["circle-color"]).not.toBe(paint("annotation-point")["circle-color"]);
  });

  it("gives the annotation source only the annotations, and the photograph source only the photographs", async () => {
    await ready({
      annotations: [
        { id: "n1", geometry: LINE },
        { id: "n2", geometry: GATE },
      ],
      photos: [{ id: "a1", lon: 77.2, lat: 28.6, label: "gate.jpg" }],
    });
    expect(dataOf("annotations").features.map((f) => f.properties.id)).toEqual(["n1", "n2"]);
    expect(dataOf("annotations").features[0]!.geometry).toEqual(LINE);
    await waitFor(() => expect(dataOf("photos").features.map((f) => f.properties.id)).toEqual(["a1"]));
  });

  it("hides and shows them as one group", async () => {
    const view = await ready({ annotations: [{ id: "n1", geometry: LINE }], showAnnotations: false });
    for (const id of ["annotation-fill", "annotation-line", "annotation-point", "annotation-selected-line", "annotation-selected-point"]) {
      expect(map().layout[id]!.visibility).toBe("none");
    }
    view.rerender(<MapCanvas {...props({ annotations: [{ id: "n1", geometry: LINE }], showAnnotations: true })} />);
    await waitFor(() => expect(map().layout["annotation-line"]!.visibility).toBe("visible"));
  });

  it("highlights the selected annotation by its id", async () => {
    await ready({ annotations: [{ id: "n1", geometry: LINE }], selectedAnnotationId: "n1" });
    expect(map().filters["annotation-selected-line"]).toEqual(["all", ["!=", ["geometry-type"], "Point"], ["==", ["get", "id"], "n1"]]);
    expect(map().filters["annotation-selected-point"]).toEqual(["all", ["==", ["geometry-type"], "Point"], ["==", ["get", "id"], "n1"]]);
  });
});

describe("clicking the map", () => {
  const click = () => map().fire("click", { point: { x: 10, y: 10 }, lngLat: { lng: 75.5, lat: 27.5 } });

  it("selects an annotation under the click and does not also pick a point", async () => {
    const onAnnotationSelect = vi.fn();
    const onPick = vi.fn();
    await ready({ annotations: [{ id: "n1", geometry: LINE }], onAnnotationSelect, onPick });
    map().rendered = [{ properties: { id: "n1" }, geometry: LINE }];
    click();
    expect(onAnnotationSelect).toHaveBeenCalledWith("n1");
    expect(onPick).not.toHaveBeenCalled();
  });

  it("picks the point when nothing is under the click", async () => {
    const onAnnotationSelect = vi.fn();
    const onPick = vi.fn();
    await ready({ annotations: [{ id: "n1", geometry: LINE }], onAnnotationSelect, onPick });
    map().rendered = [];
    click();
    expect(onAnnotationSelect).not.toHaveBeenCalled();
    expect(onPick).toHaveBeenCalledWith({ lon: 75.5, lat: 27.5 });
  });

  it("treats a click on an annotation as a position to draw when selecting is switched off", async () => {
    const onPick = vi.fn();
    await ready({ annotations: [{ id: "n1", geometry: LINE }], onPick }); // no onAnnotationSelect: a shape is being drawn
    map().rendered = [{ properties: { id: "n1" }, geometry: LINE }];
    click();
    expect(onPick).toHaveBeenCalledWith({ lon: 75.5, lat: 27.5 });
  });
});
