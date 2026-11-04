import { fireEvent, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";

import * as client from "../api/client";
import { renderPage } from "../test/render";
import { buildRequest, ControlPointsPanel, type Row } from "./ControlPointsPanel";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, searchCrs: vi.fn(), validateControlPoints: vi.fn(), recordControlPoints: vi.fn() };
});

const UTM43N = {
  identifier: "EPSG:32643",
  name: "WGS 84 / UTM zone 43N",
  kind: "projected",
  units: ["metre"],
  axis_order: [],
  area_of_use: null,
  deprecated: false,
} as unknown as client.Crs;

const ASSET = {
  id: "a1",
  raster: { width: 400, height: 300, georeferenced: false },
  preview: { status: "ready", variants: ["view"] },
  georeferencing: { version: 0, placed: false },
} as unknown as client.Asset;

const row = (id: number, column: string, rowText: string, x: string, y: string): Row => ({ id, label: "", column, row: rowText, x, y });
const FILLED: Row[] = [
  row(1, "0", "0", "500000", "3000000"),
  row(2, "400", "0", "504000", "3000000"),
  row(3, "400", "300", "504000", "2997000"),
  row(4, "0", "300", "500000", "2997000"),
];

function validation(overrides: Record<string, unknown> = {}): client.ControlPointsValidation {
  return {
    valid: true,
    explanation: "fit",
    fit: {
      method: "affine_least_squares",
      point_count: 4,
      geotransform: [500000, 10, 0, 3000000, 0, -10],
      rms_pixels: 0,
      max_pixels: 0,
      rms_map_units: 0,
      map_units: "metre",
      pixel_size: [10, 10],
      rotation_degrees: 0,
      scale_ratio: 1,
      redundant: true,
      exact_fit: false,
      rms_m: 0,
      max_m: 0,
      rms_m_allowing_for_fit: 0,
      ground_pixel_m: [10, 10],
      ground_resolution_m: 10,
      shear_degrees: 0,
      corner_uncertainty_m: 14.1,
      tolerance: { ground_resolution_m: 10, warn_above_m: 28.3, fail_above_m: 70.7, reference_uncertainty_stated: false, source_uncertainty_stated: false },
      leave_one_out: { status: "complete" },
      residuals: FILLED.map((_row, i) => ({ label: `#${i + 1}`, column: 0, row: 0, x: 0, y: 0, fitted_x: 0, fitted_y: 0, residual_map_units: 0, residual_pixels: 0, residual_m: 0, leave_one_out_m: 0 })),
      note: "Least-squares affine fit.",
    },
    checks: [
      { code: "CONTROL_POINTS_RESIDUALS", status: "ok", message: "RMS residual 0.00 px: the points agree with a single affine map." },
      { code: "COORDINATES_FIT_CRS", status: "ok", message: "Every point of the outline transforms." },
    ],
    validation: null,
    ...overrides,
  } as unknown as client.ControlPointsValidation;
}

describe("buildRequest", () => {
  it("explains what is missing before a request can be made", () => {
    expect(buildRequest(FILLED, null, "ticks", "why", null).problem).toMatch(/Choose the CRS/);
    expect(buildRequest(FILLED.slice(0, 2), UTM43N, "ticks", "why", null).problem).toMatch(/at least 3/);
    expect(buildRequest([...FILLED.slice(0, 3), row(9, "", "", "", "")], UTM43N, "ticks", "why", null).request).not.toBeNull(); // a blank row is ignored
    expect(buildRequest([...FILLED.slice(0, 3), row(9, "12", "x", "1", "2")], UTM43N, "ticks", "why", null).problem).toMatch(/as numbers/);
    expect(buildRequest(FILLED, UTM43N, "  ", "why", null).problem).toMatch(/where the coordinates come from/);
    expect(buildRequest(FILLED, UTM43N, "ticks", " ", null).problem).toMatch(/reason/);
  });

  it("builds the request exactly from what was entered", () => {
    const { request } = buildRequest(FILLED, UTM43N, " grid ticks ", " reason ", "EPSG:1812");
    expect(request).toMatchObject({ crs_id: "EPSG:32643", source: "grid ticks", reason: "reason", transformation_operation: "EPSG:1812" });
    expect(request!.points).toHaveLength(4);
    expect(request!.points[2]).toEqual({ label: "", column: 400, row: 300, x: 504000, y: 2997000 });
  });
});

describe("the control-points panel", () => {
  beforeEach(() => {
    vi.mocked(client.searchCrs).mockResolvedValue({ items: [UTM43N], registry: "PROJ (local)" } as never);
    vi.mocked(client.validateControlPoints).mockReset();
    vi.mocked(client.recordControlPoints).mockReset();
  });

  async function fill(user: ReturnType<typeof userEvent.setup>) {
    const rows = [
      ["0", "0", "500000", "3000000"],
      ["400", "0", "504000", "3000000"],
      ["400", "300", "504000", "2997000"],
      ["0", "300", "500000", "2997000"],
    ];
    for (const [index, values] of rows.entries()) {
      const n = index + 1;
      await user.type(screen.getByLabelText(`Point ${n} pixel column`), values[0]!);
      await user.type(screen.getByLabelText(`Point ${n} pixel row`), values[1]!);
      await user.type(screen.getByLabelText(`Point ${n} x coordinate`), values[2]!);
      await user.type(screen.getByLabelText(`Point ${n} y coordinate`), values[3]!);
    }
    await user.type(screen.getByLabelText("Where the coordinates come from"), "Printed 1 km grid ticks");
    await user.type(screen.getByLabelText("Reason"), "No georeferencing in the scan");
    await user.type(screen.getByPlaceholderText(/EPSG:4326/), "32643");
    await user.click(screen.getByRole("button", { name: "Search" }));
    await user.click(await screen.findByRole("button", { name: /EPSG:32643/ }));
  }

  it("will not check anything until points, a CRS, a source and a reason are given", async () => {
    renderPage(<ControlPointsPanel asset={ASSET} projectId="p1" />);
    expect(screen.getByRole("button", { name: "Check the fit" })).toBeDisabled();
    expect(screen.getByText(/What this does and does not do/)).toBeInTheDocument();
    expect(screen.getByText(/does not find them on the sheet/)).toBeInTheDocument();
    expect(screen.getByText(/Choose the CRS the coordinates are in/)).toBeInTheDocument();
  });

  it("picks a pixel position by clicking the picture", () => {
    renderPage(<ControlPointsPanel asset={ASSET} projectId="p1" />);
    const picker = screen.getByTestId("point-picker");
    picker.getBoundingClientRect = () => ({ left: 10, top: 20, width: 200, height: 150, right: 210, bottom: 170, x: 10, y: 20, toJSON: () => ({}) });
    fireEvent.click(picker, { clientX: 110, clientY: 95 }); // halfway across and down
    expect(screen.getByLabelText("Point 1 pixel column")).toHaveValue("200");
    expect(screen.getByLabelText("Point 1 pixel row")).toHaveValue("150");
  });

  it("shows the fit and its residuals, and records only after the person confirms", async () => {
    vi.mocked(client.validateControlPoints).mockResolvedValue(validation());
    vi.mocked(client.recordControlPoints).mockResolvedValue({} as never);
    const user = userEvent.setup();
    renderPage(<ControlPointsPanel asset={ASSET} projectId="p1" />);
    await fill(user);

    await user.click(screen.getByRole("button", { name: "Check the fit" }));
    const review = await screen.findByTestId("control-point-review");
    expect(client.validateControlPoints).toHaveBeenCalledWith(
      "a1",
      expect.objectContaining({ crs_id: "EPSG:32643", source: "Printed 1 km grid ticks", reason: "No georeferencing in the scan" }),
    );
    expect(within(review).getByText("Passes every check")).toBeInTheDocument();
    expect(within(review).getByTestId("rms")).toHaveTextContent("0.00 m");
    expect(within(review).getByTestId("corner-uncertainty")).toHaveTextContent("14.1 m");
    expect(within(review).getByTestId("tolerance")).toHaveTextContent("a warning above 28.3 m and a failure above 70.7 m");
    expect(within(review).getByTestId("tolerance")).toHaveTextContent("Uncertainty of the printed source: not stated");
    expect(within(review).getByRole("table", { name: "Residual of each control point" })).toBeInTheDocument();
    expect(within(review).getAllByTestId("check")).toHaveLength(2);

    const record = within(review).getByRole("button", { name: "Record georeferencing" });
    expect(record).toBeDisabled(); // nothing is recorded until the person confirms
    await user.click(within(review).getByRole("checkbox"));
    await user.click(record);
    expect(client.recordControlPoints).toHaveBeenCalledWith("a1", expect.objectContaining({ expected_version: 0, confirm: true, crs_id: "EPSG:32643" }));
    expect(await screen.findByText("Georeferencing recorded")).toBeInTheDocument();
  });

  it("says plainly that a three-point fit cannot be checked, and shows no claim of accuracy for it", async () => {
    const exact = validation();
    exact.fit = {
      ...exact.fit!,
      point_count: 3,
      redundant: false,
      exact_fit: true,
      rms_m_allowing_for_fit: null,
      leave_one_out: { status: "unavailable", reason: "With 3 points, leaving one out leaves 2." },
    };
    exact.checks = [{ code: "EXACT_FIT_NO_CHECK", status: "warning", message: "Exactly three points fit the six numbers with no residual." }];
    vi.mocked(client.validateControlPoints).mockResolvedValue(exact);
    const user = userEvent.setup();
    renderPage(<ControlPointsPanel asset={ASSET} projectId="p1" />);
    await fill(user);
    await user.click(screen.getByRole("button", { name: "Check the fit" }));
    const review = await screen.findByTestId("control-point-review");
    expect(within(review).getByTestId("rms")).toHaveTextContent("not available (exact fit)");
    expect(within(review).getByTestId("exact-fit-warning")).toHaveTextContent("A residual of zero means nothing here");
    expect(within(review).getByText("Passes every check")).toBeInTheDocument(); // allowed, with the permanent warning
  });

  it("labels the coordinate columns from the CRS and not from an assumption that x is east", async () => {
    const LO15 = {
      identifier: "EPSG:2046",
      name: "Hartebeesthoek94 / Lo15",
      kind: "projected",
      units: ["metre"],
      axis_order: [
        { abbreviation: "Y", direction: "west", unit: "metre" },
        { abbreviation: "X", direction: "south", unit: "metre" },
      ],
      area_of_use: null,
      deprecated: false,
    } as unknown as client.Crs;
    vi.mocked(client.searchCrs).mockResolvedValue({ items: [LO15], registry: "PROJ (local)" } as never);
    renderPage(<ControlPointsPanel asset={ASSET} projectId="p1" />);
    expect(screen.getByRole("columnheader", { name: "X (first coordinate)" })).toBeInTheDocument();
    const user = userEvent.setup();
    await user.type(screen.getByPlaceholderText(/EPSG:4326/), "2046");
    await user.click(screen.getByRole("button", { name: "Search" }));
    await user.click(await screen.findByRole("button", { name: /EPSG:2046/ }));
    expect(screen.getByRole("columnheader", { name: "X (Y, towards the west)" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Y (X, towards the south)" })).toBeInTheDocument();
  });

  it("asks for the acknowledgement of a datum shift worse than 10 m and sends it with the next check", async () => {
    const kalianpur = {
      id: "EPSG:1247",
      name: "Kalianpur 1962 to WGS 84 (1)",
      classification: "approximate",
      accuracy_m: 999,
      authority_code: "EPSG:1247",
      area_of_use: null,
      grids: [],
      summary: "",
      usable: true,
      recommended: false,
      selected: false,
      requires_acknowledgement: true,
      acknowledgement_reason: "accuracy_above_threshold",
      acknowledgement_threshold_m: 10,
    } as client.TransformationOperation;
    const refused = validation({
      valid: false,
      checks: [{ code: "TRANSFORMATION_ACKNOWLEDGEMENT_REQUIRED", status: "failed", message: "Acknowledge the transformation accuracy." }],
      validation: {
        checks: [{ code: "TRANSFORMATION_ACKNOWLEDGEMENT_REQUIRED", status: "failed", message: "Acknowledge." }],
        transformation_plan: {
          source_crs: "EPSG:32643",
          target_crs: "EPSG:4326",
          status: "approximate",
          status_message: "One registered datum shift applies.",
          recommended_id: null,
          selected_id: null,
          operations: [kalianpur],
          missing_grids: [],
          messages: [],
        },
      },
    });
    vi.mocked(client.validateControlPoints).mockResolvedValueOnce(refused).mockResolvedValueOnce(validation());
    const user = userEvent.setup();
    renderPage(<ControlPointsPanel asset={ASSET} projectId="p1" />);
    await fill(user);
    await user.click(screen.getByRole("button", { name: "Check the fit" }));
    const box = await screen.findByTestId("accuracy-acknowledgement");
    expect(box).toHaveTextContent("about 999 m, worse than the 10 m limit");
    const tick = within(box).getByRole("checkbox");
    expect((tick as HTMLInputElement).checked).toBe(false);
    expect(vi.mocked(client.validateControlPoints).mock.calls[0]![1]).toMatchObject({ acknowledge_transformation: false });

    await user.click(tick);
    await user.click(screen.getByRole("button", { name: "Check the fit" }));
    expect(vi.mocked(client.validateControlPoints).mock.calls[1]![1]).toMatchObject({ acknowledge_transformation: true });
  });

  it("does not allow recording a fit that failed, and shows why", async () => {
    vi.mocked(client.validateControlPoints).mockResolvedValue(
      validation({
        valid: false,
        checks: [{ code: "CONTROL_POINTS_RESIDUALS", status: "failed", message: "The fit does not agree with the points (RMS 40.0 px)." }],
      }),
    );
    const user = userEvent.setup();
    renderPage(<ControlPointsPanel asset={ASSET} projectId="p1" />);
    await fill(user);
    await user.click(screen.getByRole("button", { name: "Check the fit" }));
    const review = await screen.findByTestId("control-point-review");
    expect(within(review).getByText("Does not pass")).toBeInTheDocument();
    expect(within(review).getByText(/does not agree with the points/)).toBeInTheDocument();
    expect(within(review).getByRole("checkbox")).toBeDisabled();
    expect(within(review).getByRole("button", { name: "Record georeferencing" })).toBeDisabled();
  });

  it("asks for a datum transformation choice and never picks one", async () => {
    const operation = (id: string, accuracy: number, recommended: boolean) => ({
      id,
      name: id,
      classification: "approximate",
      accuracy_m: accuracy,
      authority_code: id,
      area_of_use: null,
      grids: [],
      summary: `about ${accuracy} m`,
      usable: true,
      recommended,
      selected: false,
    });
    vi.mocked(client.validateControlPoints).mockResolvedValue(
      validation({
        valid: false,
        checks: [{ code: "TRANSFORMATION_AVAILABLE", status: "failed", message: "Choose a datum transformation." }],
        validation: {
          valid: false,
          checks: [],
          proposed: null,
          corners: [],
          method: "geotransform_entered",
          explanation: "",
          transformation_plan: {
            source_crs: "EPSG:24047",
            target_crs: "EPSG:4326",
            status: "selection_required",
            status_message: "Several registered datum shifts apply.",
            recommended_id: "EPSG:1812",
            selected_id: null,
            operations: [operation("EPSG:1812", 3, true), operation("EPSG:1304", 5, false)],
            missing_grids: [],
            messages: [],
          },
        },
      }),
    );
    const user = userEvent.setup();
    renderPage(<ControlPointsPanel asset={ASSET} projectId="p1" />);
    await fill(user);
    await user.click(screen.getByRole("button", { name: "Check the fit" }));
    const review = await screen.findByTestId("control-point-review");
    expect(within(review).getByText("Several registered datum shifts apply.")).toBeInTheDocument();
    expect(within(review).getAllByTestId("operation")).toHaveLength(2);
    for (const radio of within(review).getAllByRole("radio")) expect(radio).not.toBeChecked();
    expect(within(review).getByText(/Nothing is chosen for you/)).toBeInTheDocument();
  });

  it("asks to check again when a point changes after the fit was reviewed", async () => {
    vi.mocked(client.validateControlPoints).mockResolvedValue(validation());
    const user = userEvent.setup();
    renderPage(<ControlPointsPanel asset={ASSET} projectId="p1" />);
    await fill(user);
    await user.click(screen.getByRole("button", { name: "Check the fit" }));
    await screen.findByTestId("control-point-review");
    await user.type(screen.getByLabelText("Point 1 pixel column"), "5");
    expect(screen.queryByTestId("control-point-review")).not.toBeInTheDocument();
    expect(screen.getByText(/changed since the last check/)).toBeInTheDocument();
  });

  it("explains that a picture without a ready preview cannot be clicked", () => {
    renderPage(<ControlPointsPanel asset={{ ...ASSET, preview: { status: "pending", variants: [] } } as unknown as client.Asset} projectId="p1" />);
    expect(screen.queryByTestId("point-picker")).not.toBeInTheDocument();
    expect(screen.getByText(/Enter pixel positions by hand/)).toBeInTheDocument();
  });
});
