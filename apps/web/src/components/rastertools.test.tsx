import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";

import * as client from "../api/client";
import { renderPage } from "../test/render";
import { BandStatistics, DisplayControls, DisplayCopyPanel, MeasurementResult } from "./RasterTools";
import { ComparePanel, RasterWorkbench } from "./RasterWorkbench";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    getDisplay: vi.fn(),
    getPixel: vi.fn(),
    measurePoints: vi.fn(),
    requestStatistics: vi.fn(),
    requestDisplayCopy: vi.fn(),
    getAlignment: vi.fn(),
    requestComparison: vi.fn(),
    listComparisons: vi.fn(),
    listAssets: vi.fn(),
  };
});

function tools(overrides: Partial<client.RasterTools> = {}): client.RasterTools {
  return {
    asset_id: "r1",
    width: 200,
    height: 100,
    band_count: 3,
    dtypes: ["uint16", "uint16", "uint16"],
    bands: [
      { band: 1, dtype: "uint16", description: "near infrared", color_interpretation: "gray", units: null },
      { band: 2, dtype: "uint16", description: null, color_interpretation: "undefined", units: null },
      { band: 3, dtype: "uint16", description: null, color_interpretation: "red", units: "reflectance" },
    ],
    statistics: null,
    statistics_job: null,
    display_copy: null,
    display_copy_needed: false,
    display_copy_job: null,
    display_source: "original",
    derived_from: [],
    note: "",
    ...overrides,
  };
}

const PREVIEW = { status: "ready", variants: ["thumb", "view"], job_id: "j0", error_code: null, error_message: null };
const ASSET = { id: "r1", project_id: "p1", original_filename: "scene.tif", raster: { band_count: 3 }, preview: PREVIEW } as client.Asset;

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(client.getDisplay).mockResolvedValue({
    bands: [1, 2, 3],
    mode: "default",
    ranges: [[0, 100], [0, 100], [0, 100]],
    description: "stretched between the 2nd and 98th percentile of the preview sample",
    note: "Display only",
  });
});

describe("DisplayControls", () => {
  it("names bands only as the file declares them and offers only usable stretches", async () => {
    const onChange = vi.fn();
    renderPage(<DisplayControls assetId="r1" tools={tools()} value={{}} onChange={onChange} />);
    const red = screen.getByLabelText("Shown as red");
    const options = within(red).getAllByRole("option").map((o) => o.textContent);
    expect(options).toEqual([
      "Band 1 — near infrared (as the file declares)",
      "Band 2 — no description in the file",
      "Band 3 — colour: red (as the file declares)",
    ]);
    const stretch = screen.getByLabelText("Contrast stretch");
    expect(within(stretch).getByRole("option", { name: /None \(values as stored/ })).toBeDisabled(); // 16-bit
    expect(within(stretch).getByRole("option", { name: /2–98 percentile \(needs band statistics\)/ })).toBeDisabled();
    expect(await screen.findByRole("status", { name: "Display in use" })).toHaveTextContent("2nd and 98th percentile");

    await userEvent.selectOptions(red, "3");
    await userEvent.selectOptions(screen.getByLabelText("Shown as blue"), "1");
    await userEvent.selectOptions(stretch, "manual");
    for (const [i, [low, high]] of [["0", "500"], ["0", "600"], ["10", "700"]].entries()) {
      await userEvent.type(screen.getByLabelText(`Band ${[3, 2, 1][i]} low`), low!);
      await userEvent.type(screen.getByLabelText(`Band ${[3, 2, 1][i]} high`), high!);
    }
    await userEvent.click(screen.getByRole("button", { name: "Apply" }));
    expect(onChange).toHaveBeenCalledWith({ bands: "3,2,1", stretch: "manual", low: "0,0,10", high: "500,600,700" });
  });

  it("explains a setting the API refuses", async () => {
    vi.mocked(client.getDisplay).mockRejectedValue(new client.ApiError(422, "DISPLAY_INVALID", "These display settings cannot be used", "Each low value must be smaller than its high value."));
    renderPage(<DisplayControls assetId="r1" tools={tools()} value={{ stretch: "manual", low: "5", high: "1" }} onChange={vi.fn()} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Code: DISPLAY_INVALID");
  });
});

describe("BandStatistics and DisplayCopyPanel", () => {
  it("shows how statistics were computed and lets them be recomputed", async () => {
    vi.mocked(client.requestStatistics).mockResolvedValue({ id: "j", status: "queued", error_code: null, error_message: null, created_at: "2026-10-21T00:00:00Z" });
    const stats = {
      method: "sample",
      method_note: "An evenly spaced sample of 1000 × 800 real pixel values was read; the statistics are estimates.",
      bands: [
        { band: 1, dtype: "uint16", description: "near infrared", min: 3, max: 4000, mean: 812.5, std: 120.25, percentiles: { "2": 40, "98": 3600 }, valid: 800000, pixels_read: 800000, nodata_or_masked: 0, units: null, histogram: { counts: [1, 5, 2] } },
      ],
    };
    renderPage(<BandStatistics assetId="r1" tools={tools({ statistics: stats })} />);
    expect(screen.getByText(/statistics are estimates/)).toBeInTheDocument();
    const list = screen.getByRole("list", { name: "Statistics per band" });
    expect(list).toHaveTextContent("Minimum / maximum3 / 4000");
    expect(list).toHaveTextContent("2nd / 98th percentile40 / 3600");
    expect(screen.getByRole("img", { name: "Histogram of band 1" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Recompute statistics" }));
    expect(client.requestStatistics).toHaveBeenCalledWith("r1");
  });

  it("says whether a display copy is needed, in use, or failed", () => {
    const { unmount } = renderPage(<DisplayCopyPanel assetId="r1" tools={tools({ display_copy_needed: true })} />);
    expect(screen.getByText(/Recommended: this raster is large/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Build display copy" })).toBeEnabled();
    unmount();
    renderPage(
      <DisplayCopyPanel
        assetId="r1"
        tools={tools({
          display_copy: { size_bytes: 290 * 1024 ** 2, build_seconds: 23.9, created_at: "2026-10-21T10:00:00Z", verification: { windows_compared: 25, overview_levels: [2, 4, 8] } },
          display_source: "display_copy",
        })}
      />,
    );
    expect(screen.getByText("In use")).toBeInTheDocument();
    expect(screen.getByText(/25 full-resolution windows identical to the original/)).toBeInTheDocument();
    expect(screen.getByText(/always read the original/)).toBeInTheDocument();
  });
});

describe("RasterWorkbench", () => {
  function clickAt(fractionX: number, fractionY: number) {
    const layer = screen.getByTestId("raster-click-layer");
    layer.getBoundingClientRect = () => ({ left: 0, top: 0, width: 400, height: 200, right: 400, bottom: 200, x: 0, y: 0, toJSON: () => ({}) });
    fireEvent.click(layer, { clientX: 400 * fractionX, clientY: 200 * fractionY });
  }

  it("reads the stored values of the clicked pixel", async () => {
    vi.mocked(client.getPixel).mockResolvedValue({
      col: 50,
      row: 25,
      values: [
        { band: 1, raw: 812, nodata: false, scaled: null, units: null, description: "near infrared" },
        { band: 2, raw: null, nodata: true, scaled: null, units: null, description: null },
      ],
      dtypes: ["uint16", "uint16"],
      native: null,
      lon_lat: null,
      georeferencing_source: null,
      note: "Values are as stored in the original.",
    });
    renderPage(<RasterWorkbench asset={ASSET} tools={tools()} display={{}} />);
    clickAt(0.25, 0.25);
    await waitFor(() => expect(client.getPixel).toHaveBeenCalledWith("r1", { col: 50, row: 25 }));
    const panel = await screen.findByRole("status", { name: "Pixel values" });
    expect(panel).toHaveTextContent("no map position (not georeferenced)");
    expect(panel).toHaveTextContent("812");
    expect(panel).toHaveTextContent("nodata");
  });

  it("measures in the pixel grid and shows when no ground value is possible", async () => {
    vi.mocked(client.measurePoints).mockResolvedValue({
      kind: "distance",
      validity: "unavailable",
      value: null,
      unit: null,
      method: "none: the raster has no valid georeferencing",
      perimeter_m: null,
      planar: null,
      pixel_value: 100,
      pixel_unit: "pixels",
      flags: ["NOT_GEOREFERENCED"],
      note: "No ground measurement is possible. The pixel value is in image pixels, not ground units.",
      points_lonlat: null,
    });
    renderPage(<RasterWorkbench asset={ASSET} tools={tools()} display={{}} />);
    await userEvent.click(screen.getByRole("button", { name: "Measure distance" }));
    clickAt(0, 0.5);
    clickAt(0.5, 0.5);
    await userEvent.click(screen.getByRole("button", { name: "Measure" }));
    expect(client.measurePoints).toHaveBeenCalledWith({
      kind: "distance",
      space: "pixel",
      asset_id: "r1",
      points: [
        [0, 50],
        [100, 50],
      ],
    });
    const result = await screen.findByRole("status", { name: "Measurement result" });
    expect(result).toHaveTextContent("No ground measurement");
    expect(result).toHaveTextContent("unavailable");
    expect(result).toHaveTextContent("In the image: 100.0 pixels");
  });
});

describe("MeasurementResult", () => {
  it("shows the value with its method, the planar value, and the recorded-georeferencing flag", () => {
    renderPage(
      <MeasurementResult
        result={{
          kind: "area",
          validity: "valid",
          value: 1_000_800,
          unit: "m²",
          method: "pixel grid → WGS 84 / UTM zone 43N via the affine geotransform → geodesic on the WGS 84 ellipsoid",
          perimeter_m: 4001.6,
          planar: { value: 1_000_000, unit: "m²", method: "planar in WGS 84 / UTM zone 43N (metre); includes map-projection distortion" },
          pixel_value: 10000,
          pixel_unit: "pixels²",
          flags: ["CRS_USER_ASSERTED"],
          note: "Computed from georeferencing recorded by a person.",
          points_lonlat: null,
        }}
      />,
    );
    const result = screen.getByRole("status", { name: "Measurement result" });
    expect(result).toHaveTextContent("100.08 ha");
    expect(result).toHaveTextContent("recorded georeferencing");
    expect(result).toHaveTextContent("Planar: 100.00 ha — planar in WGS 84 / UTM zone 43N");
    expect(result).toHaveTextContent("Perimeter: 4001.6 m");
  });
});

describe("ComparePanel", () => {
  beforeEach(() => {
    vi.mocked(client.listAssets).mockResolvedValue({
      items: [ASSET, { id: "r0", project_id: "p1", original_filename: "before.tif", raster: { band_count: 1 }, preview: PREVIEW } as client.Asset],
      next_cursor: null,
    });
    vi.mocked(client.listComparisons).mockResolvedValue([]);
  });

  it("explains why rasters on different grids are not compared", async () => {
    vi.mocked(client.getAlignment).mockResolvedValue({ aligned: false, reasons: ["The CRSs differ (EPSG:32643 and EPSG:32644)."] });
    renderPage(<ComparePanel asset={ASSET} tools={tools()} projectId="p1" />);
    await screen.findByRole("option", { name: "before.tif" });
    await userEvent.selectOptions(screen.getByLabelText(/Raster A/), "r0");
    expect(await screen.findByText("These rasters are not on the same grid")).toBeInTheDocument();
    expect(screen.getByText(/The CRSs differ/)).toBeInTheDocument();
    expect(screen.getByText(/Nothing is resampled or shifted/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Compute B − A" })).not.toBeInTheDocument();
  });

  it("requests B − A for aligned rasters with the chosen bands", async () => {
    vi.mocked(client.getAlignment).mockResolvedValue({ aligned: true, reasons: [] });
    vi.mocked(client.requestComparison).mockResolvedValue({} as client.Comparison);
    renderPage(<ComparePanel asset={ASSET} tools={tools()} projectId="p1" />);
    await screen.findByRole("option", { name: "before.tif" });
    await userEvent.selectOptions(screen.getByLabelText(/Raster A/), "r0");
    await userEvent.selectOptions(await screen.findByLabelText("Band of B (this raster)"), "3");
    await userEvent.type(screen.getByLabelText(/Purpose/), "Seasonal check");
    await userEvent.click(screen.getByRole("button", { name: "Compute B − A" }));
    expect(client.requestComparison).toHaveBeenCalledWith("p1", { asset_a: "r0", asset_b: "r1", band_a: 1, band_b: 3, reason: "Seasonal check" });
  });
});
