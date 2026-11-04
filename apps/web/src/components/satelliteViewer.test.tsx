import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { vi } from "vitest";

import * as client from "../api/client";
import { adjustFilter, bandsDeclaredRgb, DEFAULT_ADJUST, type DisplayAdjust, isDefaultAdjust } from "../lib/rasterDisplay";
import { renderPage } from "../test/render";
import { DisplayToolbar } from "./DisplayToolbar";
import { RasterWorkbench } from "./RasterWorkbench";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, getPixel: vi.fn(), measurePoints: vi.fn() };
});

describe("display adjustments", () => {
  it("make no filter at all when nothing is changed", () => {
    expect(adjustFilter(DEFAULT_ADJUST)).toBeUndefined();
    expect(isDefaultAdjust(DEFAULT_ADJUST)).toBe(true);
  });
  it("combine grey, brightness and contrast as a display filter only", () => {
    expect(adjustFilter({ mode: "grey", brightness: 100, contrast: 100 })).toBe("grayscale(1)");
    expect(adjustFilter({ mode: "colour", brightness: 120, contrast: 80 })).toBe("brightness(1.20) contrast(0.80)");
    expect(adjustFilter({ mode: "grey", brightness: 150, contrast: 110 })).toBe("grayscale(1) brightness(1.50) contrast(1.10)");
  });
  it("only call bands red, green and blue when the file declares them in that order", () => {
    const declared = [{ color_interpretation: "red" }, { color_interpretation: "green" }, { description: "Blue band" }];
    expect(bandsDeclaredRgb(declared)).toBe(true);
    expect(bandsDeclaredRgb([...declared].reverse())).toBe(false);
    expect(bandsDeclaredRgb([{ color_interpretation: "gray" }, { color_interpretation: "gray" }, { color_interpretation: "gray" }])).toBe(false);
    expect(bandsDeclaredRgb(declared.slice(0, 2))).toBe(false);
  });
});

function Harness({ bands, onReset }: { bands: number; onReset?: () => void }) {
  const [adjust, setAdjust] = useState<DisplayAdjust>(DEFAULT_ADJUST);
  return (
    <>
      <DisplayToolbar bandCount={bands} adjust={adjust} onAdjust={setAdjust} changed={false} onReset={() => (setAdjust(DEFAULT_ADJUST), onReset?.())} />
      <output data-testid="state">{JSON.stringify(adjust)}</output>
    </>
  );
}
const state = () => JSON.parse(screen.getByTestId("state").textContent!) as DisplayAdjust;

describe("DisplayToolbar", () => {
  it("starts in true colour for a colour image, and switches to grayscale and back", async () => {
    render(<Harness bands={3} />);
    expect(screen.getByRole("button", { name: "True colour" })).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(screen.getByRole("button", { name: "Grayscale" }));
    expect(state().mode).toBe("grey");
    expect(screen.getByRole("button", { name: "Grayscale" })).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(screen.getByRole("button", { name: "True colour" }));
    expect(state().mode).toBe("colour");
  });

  it("changes brightness and contrast, and Reset display puts everything back", async () => {
    const reset = vi.fn();
    render(<Harness bands={3} onReset={reset} />);
    expect(screen.getByRole("button", { name: "Reset display" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Brightness"), { target: { value: "130" } });
    fireEvent.change(screen.getByLabelText("Contrast"), { target: { value: "70" } });
    expect(state()).toMatchObject({ brightness: 130, contrast: 70 });
    await userEvent.click(screen.getByRole("button", { name: "Grayscale" }));
    await userEvent.click(screen.getByRole("button", { name: "Reset display" }));
    expect(state()).toEqual(DEFAULT_ADJUST);
    expect(reset).toHaveBeenCalled();
  });

  it("has no true colour for a one-band raster, which is already grey", () => {
    render(<Harness bands={1} />);
    expect(screen.getByRole("button", { name: "True colour" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Grayscale" })).toHaveAttribute("aria-pressed", "true");
  });
});

const PREVIEW = { status: "ready", variants: ["thumb", "view"], job_id: "j0", error_code: null, error_message: null };
const ASSET = { id: "r1", project_id: "p1", original_filename: "scene.tif", raster: { band_count: 3 }, preview: PREVIEW } as client.Asset;
const TOOLS = {
  asset_id: "r1",
  width: 200,
  height: 100,
  band_count: 3,
  dtypes: ["uint8", "uint8", "uint8"],
  bands: [],
  statistics: null,
  statistics_job: null,
  display_copy: null,
  display_copy_needed: false,
  display_copy_job: null,
  display_source: "original",
  derived_from: [],
  note: "",
} as unknown as client.RasterTools;

describe("RasterWorkbench in the satellite viewer", () => {
  it("offers no distance or area tools when measurements are off, and applies the display filter to the picture only", () => {
    renderPage(<RasterWorkbench asset={ASSET} tools={TOOLS} display={{}} measurements={false} showPixelPanel={false} imageFilter="grayscale(1) brightness(1.20)" />);
    expect(screen.queryByRole("button", { name: "Measure distance" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Measure area" })).not.toBeInTheDocument();
    expect(screen.getByRole("img", { name: /Rendering of scene.tif/ })).toHaveStyle({ filter: "grayscale(1) brightness(1.20)" });
    expect(screen.getByTestId("raster-click-layer")).not.toHaveStyle({ filter: "grayscale(1) brightness(1.20)" });
  });

  it("still reads the stored pixel values, and hands the reading to the page", async () => {
    const reading = {
      col: 50,
      row: 25,
      values: [{ band: 1, raw: 812, nodata: false, scaled: null, units: null, description: null }],
      dtypes: ["uint8"],
      native: null,
      lon_lat: null,
      georeferencing_source: null,
      note: "Values are as stored in the original.",
    };
    vi.mocked(client.getPixel).mockResolvedValue(reading as never);
    const onPixel = vi.fn();
    renderPage(<RasterWorkbench asset={ASSET} tools={TOOLS} display={{}} measurements={false} showPixelPanel={false} onPixel={onPixel} imageFilter="grayscale(1)" />);
    const layer = screen.getByTestId("raster-click-layer");
    layer.getBoundingClientRect = () => ({ left: 0, top: 0, width: 400, height: 200, right: 400, bottom: 200, x: 0, y: 0, toJSON: () => ({}) });
    fireEvent.click(layer, { clientX: 100, clientY: 50 });
    // the reading is the stored value, whatever the display filter does
    await waitFor(() => expect(onPixel).toHaveBeenCalledWith(reading));
    expect(client.getPixel).toHaveBeenCalledWith("r1", { col: 50, row: 25 });
    expect(screen.queryByRole("status", { name: "Pixel values" })).not.toBeInTheDocument();
  });

  it("keeps measurements and the pixel panel by default, for every other screen", () => {
    renderPage(<RasterWorkbench asset={ASSET} tools={TOOLS} display={{}} />);
    expect(screen.getByRole("button", { name: "Measure distance" })).toBeInTheDocument();
    expect(screen.getByText("No pixel chosen.")).toBeInTheDocument();
  });
});
