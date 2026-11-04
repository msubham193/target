import { act, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, vi } from "vitest";

import { PanZoomViewer } from "./PanZoomViewer";

// jsdom has no layout. The viewer measures its own box, so give every element an 800 x 400 box.
const sizes = vi.spyOn(HTMLElement.prototype, "clientWidth", "get");
const heights = vi.spyOn(HTMLElement.prototype, "clientHeight", "get");
beforeEach(() => {
  sizes.mockReturnValue(800);
  heights.mockReturnValue(400);
});
afterEach(() => {
  vi.unstubAllGlobals();
  Object.defineProperty(document, "fullscreenEnabled", { value: undefined, configurable: true });
});

// A 1600 x 1000 raster in an 800 x 400 viewer: "fit" is 0.4, so the picture is 640 x 400 and centred.
function viewer() {
  return render(<PanZoomViewer src="/r.png?x=1" alt="scene.tif" width={1600} height={1000} renderedSide={2048} header={<p>scene.tif</p>} />);
}
const port = () => screen.getByTestId("viewer-port");
const zoom = () => Number(port().getAttribute("data-zoom"));
const picture = () => screen.getByAltText("scene.tif") as HTMLImageElement;
/** The picture's top-left corner and size on screen, from the transform the viewer set. */
function placement() {
  const match = /translate\((-?[\d.]+)px, (-?[\d.]+)px\) scale\(([\d.]+)\)/.exec(picture().style.transform)!;
  return { x: Number(match[1]), y: Number(match[2]), scale: Number(match[3]) };
}
const loaded = () => fireEvent.load(picture());

describe("PanZoomViewer", () => {
  it("starts with the whole picture in view, centred, and nothing to zoom out to", () => {
    viewer();
    loaded();
    expect(zoom()).toBe(1);
    const { x, y, scale } = placement();
    expect(scale).toBeCloseTo(0.4);
    expect(x).toBeCloseTo(80); // (800 - 640) / 2
    expect(y).toBeCloseTo(0);
    expect(screen.getByRole("button", { name: "Zoom out" })).toBeDisabled();
    expect(screen.getByText("scene.tif")).toBeInTheDocument(); // the header is part of the viewer
  });

  it("zooms with the buttons and Fit to screen puts everything back", async () => {
    viewer();
    loaded();
    await userEvent.click(screen.getByRole("button", { name: "Zoom in" }));
    expect(zoom()).toBeCloseTo(1.5);
    await userEvent.click(screen.getByRole("button", { name: "Zoom in" }));
    expect(zoom()).toBeCloseTo(2.25);
    await userEvent.click(screen.getByRole("button", { name: "Zoom out" }));
    expect(zoom()).toBeCloseTo(1.5);
    await userEvent.click(screen.getByRole("button", { name: "Fit to screen" }));
    expect(zoom()).toBe(1);
    expect(placement().x).toBeCloseTo(80);
  });

  it("zooms with the mouse wheel, never out past fit, and stops zooming in at a useful limit", () => {
    viewer();
    loaded();
    fireEvent.wheel(port(), { deltaY: -400, clientX: 400, clientY: 200 });
    expect(zoom()).toBeGreaterThan(1.5);
    fireEvent.wheel(port(), { deltaY: 100000, clientX: 400, clientY: 200 });
    expect(zoom()).toBe(1);
    fireEvent.wheel(port(), { deltaY: -100000, clientX: 400, clientY: 200 });
    // At most 6 screen pixels per pixel of the rendered picture: the preview is 2048 px wide for a 1600 px raster, so it is
    // not smaller than the raster, and 6 / fit (0.4) = 15.
    expect(zoom()).toBeCloseTo(15);
    expect(screen.getByRole("button", { name: "Zoom in" })).toBeDisabled();
  });

  it("keeps the point under the pointer under the pointer when zooming with the wheel", () => {
    viewer();
    loaded();
    const under = (cx: number, cy: number) => {
      const { x, y, scale } = placement();
      return [(cx - x) / scale, (cy - y) / scale]; // the raster pixel at that screen point
    };
    const before = under(600, 150);
    fireEvent.wheel(port(), { deltaY: -500, clientX: 600, clientY: 150 });
    const after = under(600, 150);
    expect(after[0]).toBeCloseTo(before[0]!, 0);
    expect(after[1]).toBeCloseTo(before[1]!, 0);
  });

  it("pans by dragging, and the picture can never be dragged out of reach", () => {
    viewer();
    loaded();
    fireEvent.wheel(port(), { deltaY: -1000, clientX: 400, clientY: 200 });
    const start = placement();
    fireEvent.pointerDown(port(), { pointerId: 1, button: 0, clientX: 400, clientY: 200 });
    fireEvent.pointerMove(port(), { pointerId: 1, clientX: 430, clientY: 220 });
    const moved = placement();
    expect(moved.x - start.x).toBeCloseTo(30);
    expect(moved.y - start.y).toBeCloseTo(20);
    // far past the edge: the picture still covers the viewer
    fireEvent.pointerMove(port(), { pointerId: 1, clientX: 90000, clientY: 90000 });
    let p = placement();
    expect(p.x).toBeLessThanOrEqual(0.001);
    expect(p.y).toBeLessThanOrEqual(0.001);
    fireEvent.pointerMove(port(), { pointerId: 1, clientX: -90000, clientY: -90000 });
    p = placement();
    expect(p.x + 1600 * p.scale).toBeGreaterThanOrEqual(800 - 0.001);
    expect(p.y + 1000 * p.scale).toBeGreaterThanOrEqual(400 - 0.001);
    fireEvent.pointerUp(port(), { pointerId: 1 });
  });

  it("keeps the same part of the picture in view when the viewer changes size, as it does for full screen", () => {
    const observed: ResizeObserverCallback[] = [];
    vi.stubGlobal(
      "ResizeObserver",
      class {
        constructor(callback: ResizeObserverCallback) {
          observed.push(callback);
        }
        observe() {}
        disconnect() {}
      },
    );
    viewer();
    loaded();
    fireEvent.wheel(port(), { deltaY: -800, clientX: 600, clientY: 100 });
    const zoomBefore = zoom();
    // the raster pixel at the middle of a box of the given size
    const middle = (width: number, height: number) => {
      const { x, y, scale } = placement();
      return [(width / 2 - x) / scale, (height / 2 - y) / scale];
    };
    const before = middle(800, 400);

    sizes.mockReturnValue(1600);
    heights.mockReturnValue(900);
    act(() => observed.forEach((callback) => callback([], {} as ResizeObserver)));

    expect(zoom()).toBeCloseTo(zoomBefore); // the zoom is relative to fit, so it carries over
    const after = middle(1600, 900);
    expect(after[0]).toBeCloseTo(before[0]!, 0);
    expect(after[1]).toBeCloseTo(before[1]!, 0);
    expect(placement().scale).toBeGreaterThan(0.4 * zoomBefore); // the bigger box really is used
  });

  it("says so when the picture cannot be loaded, and tries again", async () => {
    viewer();
    fireEvent.error(picture());
    expect(screen.getByRole("alert")).toHaveTextContent("The image could not be loaded.");
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(picture().getAttribute("src")).toContain("retry=1");
  });

  it("offers full screen only where the browser supports it, and asks the viewer to go full screen", async () => {
    viewer();
    expect(screen.queryByRole("button", { name: "Full screen" })).not.toBeInTheDocument();
    Object.defineProperty(document, "fullscreenEnabled", { value: true, configurable: true });
    const request = vi.fn().mockResolvedValue(undefined);
    HTMLElement.prototype.requestFullscreen = request;
    viewer();
    await userEvent.click(screen.getAllByRole("button", { name: "Full screen" })[0]!);
    expect(request).toHaveBeenCalled();
  });
});
