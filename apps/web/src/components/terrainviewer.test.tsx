import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";

import type { TerrainMeta } from "../api/client";
import type { Hit } from "../lib/terrain";
import { TerrainViewer } from "./TerrainViewer";

// jsdom has no WebGL. A stand-in context accepts every call and records the ones that show what would be drawn.
function fakeGl() {
  const calls: { name: string; args: unknown[] }[] = [];
  const constants: Record<string, number> = { VERTEX_SHADER: 1, FRAGMENT_SHADER: 2, COMPILE_STATUS: 3, LINK_STATUS: 4, TRIANGLES: 5, LINES: 6, UNSIGNED_INT: 7 };
  const gl = new Proxy(
    {},
    {
      get(_target, name: string) {
        if (name in constants) return constants[name];
        if (name === "getShaderParameter" || name === "getProgramParameter") return () => true;
        return (...args: unknown[]) => {
          calls.push({ name, args });
          return {};
        };
      },
    },
  );
  return { gl, calls };
}

const COLS = 21;
const ROWS = 21;

function meta(over: Partial<TerrainMeta> = {}): TerrainMeta {
  return {
    asset_id: "a1", filename: "dem.tif", source_checksum: "ab".repeat(32), band: 1, source_size: [COLS, ROWS], grid: { width: COLS, height: ROWS, decimation: 1, method: "nearest" },
    crs_id: "EPSG:32643", crs_name: "UTM 43N", georeferencing_source: "file", horizontal_unit: "linear", cell_size_native: [10, 10], cell_size_m: [10, 10],
    extent_native: { x_min: 500000, y_min: 2999790, x_max: 500210, y_max: 3000000 }, extent_m: [210, 210], bbox_4326: [75, 27, 75.1, 27.1], centre_4326: [75, 27], positioning: { state: "positioned" },
    horizontal: { method: "exact", approximate: false },
    elevation: { unit: "metre", unit_label: "m", unit_source: "declared", to_metre: 1, role_source: "declared", declared_scale_offset_applied: false, nodata_value: null, valid_cells: COLS * ROWS, nodata_cells: 0, min: 100, max: 160, mean: 130 },
    vertical_datum: { state: "not_stated", name: null }, warnings: [], disclosures: [], grid_url: "/x", grid_format: "float32", ...over,
  } as TerrainMeta;
}

function values(): Float32Array {
  const v = new Float32Array(COLS * ROWS);
  for (let r = 0; r < ROWS; r++) for (let c = 0; c < COLS; c++) v[r * COLS + c] = 100 + c * 3; // rises to the east
  return v;
}

let gl: ReturnType<typeof fakeGl>;
beforeEach(() => {
  gl = fakeGl();
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockImplementation((() => gl.gl) as never);
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({ x: 0, y: 0, left: 0, top: 0, right: 800, bottom: 600, width: 800, height: 600, toJSON: () => ({}) });
  Element.prototype.setPointerCapture = vi.fn();
});
afterEach(() => vi.restoreAllMocks());

function show(over: Partial<React.ComponentProps<typeof TerrainViewer>> = {}) {
  const onHover = vi.fn();
  const onSelect = vi.fn();
  render(<TerrainViewer values={values()} meta={meta()} exaggeration={1} colour="elevation" onHover={onHover} onSelect={onSelect} {...over} />);
  return { onHover, onSelect, canvas: screen.getByTestId("terrain-canvas") as HTMLCanvasElement };
}

const attr = (el: HTMLElement, name: string) => Number(el.getAttribute(name));

describe("drawing", () => {
  it("draws the surface and a frame locally, with the vertical exaggeration and colour mode it was given", async () => {
    show({ exaggeration: 3, colour: "grey" });
    await waitFor(() => expect(gl.calls.some((c) => c.name === "drawElements")).toBe(true));
    expect(gl.calls.some((c) => c.name === "drawArrays")).toBe(true); // the extent's frame
    const uniform = (name: string) => gl.calls.filter((c) => c.name === name).map((c) => c.args);
    expect(uniform("uniform1f").some((a) => a[1] === 3)).toBe(true); // exaggeration
    expect(uniform("uniform1i").some((a) => a[1] === 1)).toBe(true); // grey mode
    const drawn = gl.calls.find((c) => c.name === "drawElements")!;
    expect(drawn.args[1]).toBe((COLS - 1) * (ROWS - 1) * 6); // every cell has data: two triangles each
  });

  it("leaves holes: no triangle is drawn where the grid has no data", async () => {
    const v = values();
    v[10 * COLS + 10] = Number.NaN;
    show({ values: v });
    await waitFor(() => expect(gl.calls.some((c) => c.name === "drawElements")).toBe(true));
    expect(gl.calls.find((c) => c.name === "drawElements")!.args[1]).toBe(((COLS - 1) * (ROWS - 1) - 4) * 6);
  });

  it("redraws when the exaggeration or colour changes, without rebuilding the surface", async () => {
    const view = render(<TerrainViewer values={values()} meta={meta()} exaggeration={1} colour="elevation" onHover={vi.fn()} onSelect={vi.fn()} />);
    await waitFor(() => expect(gl.calls.some((c) => c.name === "drawElements")).toBe(true));
    const buffers = gl.calls.filter((c) => c.name === "bufferData").length;
    const draws = gl.calls.filter((c) => c.name === "drawElements").length;
    view.rerender(<TerrainViewer values={values()} meta={meta()} exaggeration={5} colour="elevation" onHover={vi.fn()} onSelect={vi.fn()} />);
    await waitFor(() => expect(gl.calls.filter((c) => c.name === "drawElements").length).toBeGreaterThan(draws));
    expect(screen.getByTestId("terrain-canvas")).toHaveAttribute("data-exaggeration", "5");
    expect(gl.calls.filter((c) => c.name === "bufferData").length).toBeGreaterThanOrEqual(buffers); // values/meta objects were recreated by the test
  });

  it("states the exaggeration on the picture itself, and the scale of the surface", () => {
    show();
    expect(screen.getByTestId("exaggeration-badge")).toHaveTextContent("Vertical exaggeration ×1 (true scale)");
    expect(screen.getByText(/Surface 200 m × 200 m · one cell 10 m × 10 m/)).toBeInTheDocument();
  });

  it("warns on the picture when heights are exaggerated", () => {
    show({ exaggeration: 4 });
    expect(screen.getByTestId("exaggeration-badge")).toHaveTextContent("Vertical exaggeration ×4: heights are drawn 4 times taller than true scale");
  });

  it("shows the colour scale with the data's own unit, and none for the grey mode", () => {
    const { rerender } = render(<TerrainViewer values={values()} meta={meta()} exaggeration={1} colour="elevation" onHover={vi.fn()} onSelect={vi.fn()} />);
    expect(screen.getByLabelText("Elevation colour scale")).toHaveTextContent("100 m");
    expect(screen.getByLabelText("Elevation colour scale")).toHaveTextContent("160 m");
    rerender(<TerrainViewer values={values()} meta={meta()} exaggeration={1} colour="grey" onHover={vi.fn()} onSelect={vi.fn()} />);
    expect(screen.queryByLabelText("Elevation colour scale")).not.toBeInTheDocument();
  });

  it("says plainly when the browser cannot draw WebGL 2, and still names the surface", () => {
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
    show();
    expect(screen.getByText("The 3D view cannot be drawn")).toBeInTheDocument();
    expect(screen.getByText(/cannot draw WebGL 2/)).toBeInTheDocument();
    expect(screen.queryByTestId("north-arrow")).not.toBeInTheDocument();
  });

  it("reports a graphics failure instead of leaving a blank view", () => {
    const broken = new Proxy({}, { get: (_t, name: string) => (name === "getShaderParameter" ? () => false : name === "getShaderInfoLog" ? () => "bad shader" : () => ({})) });
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(broken as never);
    show();
    expect(screen.getByText(/Shader error: bad shader/)).toBeInTheDocument();
  });
});

describe("orbit, zoom and pan", () => {
  it("starts looking north from the south, with north up", () => {
    show();
    expect(attr(screen.getByTestId("terrain-canvas"), "data-yaw")).toBeLessThan(0.1 + 20); // a slight turn off due south
    expect(screen.getByTestId("north-arrow")).toHaveAttribute("aria-label", expect.stringContaining("North is"));
  });

  it("orbits when dragged, and the north arrow turns with the view", () => {
    const { canvas } = show();
    const yaw = attr(canvas, "data-yaw");
    const pitch = attr(canvas, "data-pitch");
    fireEvent.pointerDown(canvas, { pointerId: 1, clientX: 400, clientY: 300, button: 0 });
    fireEvent.pointerMove(canvas, { pointerId: 1, clientX: 450, clientY: 320 });
    fireEvent.pointerUp(canvas, { pointerId: 1, clientX: 450, clientY: 320 });
    expect(attr(canvas, "data-yaw")).not.toBe(yaw);
    expect(attr(canvas, "data-pitch")).toBeGreaterThan(pitch); // dragged down: the camera rises
    const turn = (screen.getByTestId("north-arrow").querySelector("span") as HTMLElement).style.transform;
    expect(Number(/rotate\((-?[\d.]+)deg\)/.exec(turn)![1])).toBeCloseTo(attr(canvas, "data-yaw"), 0); // the arrow turns by the view's yaw
  });

  it("zooms with the wheel, within limits", () => {
    const { canvas } = show();
    const distance = attr(canvas, "data-distance");
    fireEvent.wheel(canvas, { deltaY: -200 });
    expect(attr(canvas, "data-distance")).toBeLessThan(distance);
    fireEvent.wheel(canvas, { deltaY: 1e6 });
    const far = attr(canvas, "data-distance");
    fireEvent.wheel(canvas, { deltaY: 1e6 });
    expect(attr(canvas, "data-distance")).toBe(far); // the limit holds
    fireEvent.wheel(canvas, { deltaY: -1e6 });
    expect(attr(canvas, "data-distance")).toBeGreaterThan(0);
  });

  it("pans with shift-drag or a right-button drag without turning the view", () => {
    const { canvas } = show();
    const before = canvas.getAttribute("data-eye")!.split(",").map(Number);
    const yaw = attr(canvas, "data-yaw");
    fireEvent.pointerDown(canvas, { pointerId: 1, clientX: 400, clientY: 300, button: 0, shiftKey: true });
    fireEvent.pointerMove(canvas, { pointerId: 1, clientX: 500, clientY: 300 });
    fireEvent.pointerUp(canvas, { pointerId: 1, clientX: 500, clientY: 300 });
    expect(attr(canvas, "data-yaw")).toBe(yaw);
    expect(canvas.getAttribute("data-eye")!.split(",").map(Number)[0]).toBeLessThan(before[0]!); // dragged right: the scene follows, the view moves west
    fireEvent.pointerDown(canvas, { pointerId: 2, clientX: 400, clientY: 300, button: 2 });
    fireEvent.pointerMove(canvas, { pointerId: 2, clientX: 400, clientY: 340 });
    fireEvent.pointerUp(canvas, { pointerId: 2, clientX: 400, clientY: 340 });
    expect(attr(canvas, "data-yaw")).toBe(yaw);
  });

  it("can be used from the keyboard: arrows orbit, shift-arrows pan, plus and minus zoom, Home resets", async () => {
    const { canvas } = show();
    canvas.focus();
    const start = { yaw: attr(canvas, "data-yaw"), distance: attr(canvas, "data-distance"), eye: canvas.getAttribute("data-eye") };
    await userEvent.keyboard("{ArrowRight}");
    expect(attr(canvas, "data-yaw")).not.toBe(start.yaw);
    await userEvent.keyboard("+");
    expect(attr(canvas, "data-distance")).toBeLessThan(start.distance);
    await userEvent.keyboard("-");
    await userEvent.keyboard("-");
    expect(attr(canvas, "data-distance")).toBeGreaterThan(start.distance * 0.99);
    await userEvent.keyboard("{Shift>}{ArrowLeft}{/Shift}");
    expect(canvas.getAttribute("data-eye")).not.toBe(start.eye);
    await userEvent.keyboard("{Home}");
    expect(attr(canvas, "data-yaw")).toBe(start.yaw);
    expect(attr(canvas, "data-distance")).toBe(start.distance);
  });

  it("offers the same controls as buttons, and view presets", async () => {
    const { canvas } = show();
    const distance = attr(canvas, "data-distance");
    await userEvent.click(screen.getByRole("button", { name: "Zoom in" }));
    expect(attr(canvas, "data-distance")).toBeLessThan(distance);
    await userEvent.click(screen.getByRole("button", { name: "Top view" }));
    expect(attr(canvas, "data-pitch")).toBeGreaterThan(85);
    await userEvent.click(screen.getByRole("button", { name: "From the north" }));
    expect(attr(canvas, "data-yaw")).toBeCloseTo(180, 0);
    await userEvent.click(screen.getByRole("button", { name: "Reset view" }));
    expect(attr(canvas, "data-pitch")).toBeCloseTo(40, 0);
  });

  it("describes how to use it for people who cannot see it", () => {
    const { canvas } = show();
    expect(canvas).toHaveAttribute("aria-label", "3D terrain of dem.tif");
    expect(canvas).toHaveAttribute("tabindex", "0");
    expect(document.getElementById(canvas.getAttribute("aria-describedby")!)).toHaveTextContent(/arrow keys orbit/);
  });
});

describe("reading the surface under the pointer", () => {
  it("reports the cell the pointer is over (hover) and the cell clicked, with the stored value", async () => {
    const { canvas, onHover, onSelect } = show();
    // look straight down at the centre so the pointer in the middle of the view is over the middle of the surface
    await userEvent.click(screen.getByRole("button", { name: "Top view" }));
    fireEvent.pointerMove(canvas, { pointerId: 1, clientX: 400, clientY: 300 });
    await waitFor(() => expect(onHover).toHaveBeenCalled());
    const hit = onHover.mock.calls.at(-1)![0] as Hit;
    expect(hit).not.toBeNull();
    expect(Math.abs(hit.col - 10)).toBeLessThanOrEqual(2);
    expect(hit.value).toBe(100 + hit.col * 3); // the stored value of that very cell, nothing interpolated
    fireEvent.pointerDown(canvas, { pointerId: 1, clientX: 400, clientY: 300, button: 0 });
    fireEvent.pointerUp(canvas, { pointerId: 1, clientX: 400, clientY: 300 });
    expect(onSelect).toHaveBeenCalledTimes(1);
    expect((onSelect.mock.calls[0]![0] as Hit).value).toBe(100 + (onSelect.mock.calls[0]![0] as Hit).col * 3);
  });

  it("does not select when the pointer was dragged", async () => {
    const { canvas, onSelect } = show();
    fireEvent.pointerDown(canvas, { pointerId: 1, clientX: 400, clientY: 300, button: 0 });
    fireEvent.pointerMove(canvas, { pointerId: 1, clientX: 440, clientY: 300 });
    fireEvent.pointerUp(canvas, { pointerId: 1, clientX: 440, clientY: 300 });
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("reports nothing over empty space and over a no-data hole, and clears when the pointer leaves", async () => {
    const v = values();
    for (let r = 6; r <= 14; r++) for (let c = 6; c <= 14; c++) v[r * COLS + c] = Number.NaN;
    const { canvas, onHover } = show({ values: v });
    await userEvent.click(screen.getByRole("button", { name: "Top view" }));
    fireEvent.pointerMove(canvas, { pointerId: 1, clientX: 400, clientY: 300 });
    await waitFor(() => expect(onHover).toHaveBeenCalled());
    expect(onHover.mock.calls.at(-1)![0]).toBeNull(); // the middle of the surface is a hole: there is no elevation there
    fireEvent.pointerLeave(canvas);
    expect(onHover.mock.calls.at(-1)![0]).toBeNull();
    fireEvent.pointerMove(canvas, { pointerId: 1, clientX: 2, clientY: 2 });
    await waitFor(() => expect(onHover.mock.calls.length).toBeGreaterThan(1));
    expect(onHover.mock.calls.at(-1)![0]).toBeNull();
  });
});
