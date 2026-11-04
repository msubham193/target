import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";

import * as client from "../api/client";
import { renderPage } from "../test/render";
import { GeoreferencingPanel } from "./GeoreferencingPanel";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    getGeoreferencing: vi.fn(),
    validateGeoreferencing: vi.fn(),
    recordGeoreferencing: vi.fn(),
    searchCrs: vi.fn(),
  };
});

const NORTH_UP = [500000, 10, 0, 3000000, 0, -10];
const UTM43N: client.Crs = {
  identifier: "EPSG:32643",
  name: "WGS 84 / UTM zone 43N",
  kind: "projected",
  units: ["metre"],
  axis_order: [
    { abbreviation: "E", direction: "east", unit: "metre" },
    { abbreviation: "N", direction: "north", unit: "metre" },
  ],
  area_of_use: { name: "Between 72°E and 78°E, northern hemisphere", west: 72, south: 0, east: 78, north: 84 },
  deprecated: false,
};

function georef(overrides: Partial<client.Georeferencing> = {}): client.Georeferencing {
  return {
    asset_id: "r1",
    source_checksum: "ab".repeat(32),
    width: 200,
    height: 150,
    file: {
      crs_status: "missing",
      crs_identifier: null,
      crs_name: null,
      geotransform: NORTH_UP,
      geotransform_problem: null,
      georeferenced: false,
      ground_control_points: 0,
      unknowns: { crs: "The file declares no coordinate reference system. None is assumed." },
    },
    current: {
      version: 0,
      source: "file",
      crs_id: null,
      crs_name: null,
      crs_usable: false,
      geotransform: NORTH_UP,
      geotransform_usable: true,
      georeferenced: false,
      footprint_4326: null,
      bounds_native: null,
      pixel_size: [10, 10],
      pixel_units: null,
    },
    unplaced_reason:
      "Its pixels have coordinates (a geotransform), but the file declares no CRS, so it is not known what those coordinates mean.",
    options: {
      assign_crs: true,
      assign_crs_reason: "States which CRS the raster's existing coordinates are in.",
      set_geotransform: true,
      set_geotransform_reason: "Enters the six affine parameters.",
      crs_required_with_geotransform: true,
      revert_to_file: false,
    },
    history: [],
    note: "Recording a CRS or geotransform does not change the file.",
    ...overrides,
  };
}

const VALID: client.GeoreferenceValidation = {
  valid: true,
  method: "registry_crs_selected",
  explanation: "The raster's existing coordinates are kept exactly.",
  checks: [
    { code: "REASON_RECORDED", status: "ok", message: "A reason is recorded." },
    { code: "COORDINATES_FIT_CRS", status: "ok", message: "Every point of the raster's outline transforms to valid longitude and latitude." },
    { code: "WITHIN_AREA_OF_USE", status: "warning", message: "Check the CRS: a wrong CRS can still give valid-looking coordinates." },
  ],
  proposed: {
    version: 1,
    source: "recorded",
    crs_id: "EPSG:32643",
    crs_name: "WGS 84 / UTM zone 43N",
    crs_usable: true,
    geotransform: NORTH_UP,
    geotransform_usable: true,
    georeferenced: true,
    footprint_4326: { type: "Polygon", coordinates: [] },
    bounds_native: { left: 500000, bottom: 2998500, right: 502000, top: 3000000 },
    pixel_size: [10, 10],
    pixel_units: "metre",
  },
  corners: [{ point: "top-left corner", pixel: [0, 0], native: [500000, 3000000], lon_lat: [75, 27.12247] }],
};

function render() {
  return renderPage(<GeoreferencingPanel assetId="r1" projectId="p1" />, "/projects/p1/assets/r1");
}

async function chooseCrs() {
  vi.mocked(client.searchCrs).mockResolvedValue({ items: [UTM43N], registry: "PROJ (local)" });
  await userEvent.type(screen.getByPlaceholderText(/e.g. EPSG:4326/), "32643{Enter}");
  await userEvent.click(await screen.findByRole("button", { name: /EPSG:32643/ }));
}

beforeEach(() => vi.clearAllMocks());

describe("GeoreferencingPanel", () => {
  it("says why the raster is unplaced and shows what the file declares", async () => {
    vi.mocked(client.getGeoreferencing).mockResolvedValue(georef());
    render();
    expect(await screen.findByText("Why this raster is not on the map")).toBeInTheDocument();
    expect(screen.getByText(/the file declares no CRS/)).toBeInTheDocument();
    const file = screen.getByRole("region", { name: "What the file declares" });
    expect(file).toHaveTextContent("CRSmissing");
    expect(file).toHaveTextContent("500000, 10, 0, 3000000, 0, -10");
    expect(screen.getByText("Not placed")).toBeInTheDocument();
    expect(screen.getByText(/No georeferencing has been recorded/)).toBeInTheDocument();
  });

  it("assigns a CRS through a reviewed, confirmed change", async () => {
    vi.mocked(client.getGeoreferencing).mockResolvedValue(georef());
    vi.mocked(client.validateGeoreferencing).mockResolvedValue(VALID);
    render();
    await userEvent.click(await screen.findByRole("button", { name: "Assign a CRS to the existing coordinates" }));
    expect(screen.getByText(/The coordinates \(500000, 10, 0, 3000000, 0, -10\) are kept exactly/)).toBeInTheDocument();
    const check = screen.getByRole("button", { name: "Check this change" });
    expect(check).toBeDisabled();
    await chooseCrs();
    expect(check).toBeDisabled(); // a reason is still missing
    await userEvent.type(screen.getByLabelText("Reason (required)"), "Stated in the delivery note.");
    await userEvent.click(check);
    expect(client.validateGeoreferencing).toHaveBeenCalledWith("r1", {
      operation: "assign_crs",
      reason: "Stated in the delivery note.",
      acknowledge_transformation: false,
      crs_id: "EPSG:32643",
    });

    const review = await screen.findByRole("region", { name: "Review the proposed change" });
    expect(review).toHaveTextContent("Passed validation");
    const checks = within(review).getByRole("list", { name: "Validation checks" });
    expect(checks).toHaveTextContent("Warning · Check the CRS: a wrong CRS can still give valid-looking coordinates.");
    expect(review).toHaveTextContent("500000.000, 3000000.000");
    expect(review).toHaveTextContent("75.000000, 27.122470");
    const save = within(review).getByRole("button", { name: "Record as version 1" });
    expect(save).toBeDisabled(); // not before the confirmation
    await userEvent.click(within(review).getByRole("checkbox", { name: /does not show that the raster's location is correct/ }));

    const placed = georef({
      current: { ...georef().current, version: 1, source: "recorded", crs_id: "EPSG:32643", crs_usable: true, georeferenced: true },
      unplaced_reason: null,
      history: [
        {
          version_no: 1,
          operation: "assign_crs",
          method: "registry_crs_selected",
          crs_id: "EPSG:32643",
          crs_name: "WGS 84 / UTM zone 43N",
          geotransform: NORTH_UP,
          geotransform_source: null,
          world_file: null,
          reason: "Stated in the delivery note.",
          previous: { crs_id: null, geotransform: NORTH_UP, georeferenced: false },
          result: { crs_id: "EPSG:32643", geotransform: NORTH_UP, georeferenced: true },
          validation: { checks: VALID.checks },
          georeferenced: true,
          source_checksum: "ab".repeat(32),
          actor_label: "Asha",
          created_at: "2026-10-21T10:00:00Z",
        },
      ],
    });
    vi.mocked(client.recordGeoreferencing).mockResolvedValue(placed);
    await userEvent.click(save);
    expect(client.recordGeoreferencing).toHaveBeenCalledWith("r1", {
      operation: "assign_crs",
      reason: "Stated in the delivery note.",
      acknowledge_transformation: false,
      crs_id: "EPSG:32643",
      expected_version: 0,
      confirm: true,
    });
    expect(await screen.findByText("Recorded as version 1")).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: "Open the map" })[0]).toHaveAttribute("href", "/projects/p1/map");
    const history = screen.getByRole("list", { name: "Georeferencing history" });
    expect(history).toHaveTextContent("Version 1 · Assign a CRS to the existing coordinates");
    expect(history).toHaveTextContent("CRS: none → EPSG:32643");
    expect(history).toHaveTextContent("Placed: no → yes");
    expect(history).toHaveTextContent("“Stated in the delivery note.”");
    expect(history).toHaveTextContent("Warning at the time");
    expect(screen.getByText("Placed from recorded georeferencing")).toBeInTheDocument();
  });

  it("asks for a datum transformation when several apply, and records the one that was chosen", async () => {
    const op = (id: string, name: string, accuracy: number): client.TransformationOperation => ({
      id,
      name,
      classification: "approximate",
      accuracy_m: accuracy,
      authority_code: id,
      area_of_use: null,
      grids: [],
      summary: `about ${accuracy} m`,
      usable: true,
      recommended: accuracy === 3,
      selected: false,
      requires_acknowledgement: false,
      acknowledgement_threshold_m: 10,
    });
    const plan: client.TransformationPlan = {
      source_crs: "EPSG:24047",
      target_crs: "EPSG:4326",
      status: "selection_required",
      status_message: "Several registered datum shifts apply and they give different positions. Choose one.",
      recommended_id: "EPSG:1812",
      selected_id: null,
      operations: [op("EPSG:1812", "Indian 1975 to WGS 84 (4)", 3), op("EPSG:1304", "Indian 1975 to WGS 84 (2)", 5)],
      missing_grids: [],
      messages: [],
    };
    const refused: client.GeoreferenceValidation = {
      ...VALID,
      valid: false,
      proposed: null,
      corners: [],
      checks: [{ code: "TRANSFORMATION_AVAILABLE", status: "failed", message: "Choose a transformation. 2 registered operations apply." }],
      transformation_plan: plan,
    };
    vi.mocked(client.getGeoreferencing).mockResolvedValue(georef());
    vi.mocked(client.validateGeoreferencing).mockResolvedValueOnce(refused).mockResolvedValueOnce({ ...VALID, transformation_plan: null });
    render();
    await userEvent.click(await screen.findByRole("button", { name: "Assign a CRS to the existing coordinates" }));
    await chooseCrs();
    await userEvent.type(screen.getByLabelText("Reason (required)"), "Stated in the delivery note.");
    await userEvent.click(screen.getByRole("button", { name: "Check this change" }));

    const review = await screen.findByRole("region", { name: "Review the proposed change" });
    expect(review).toHaveTextContent("Did not pass validation");
    expect(review).toHaveTextContent("Choose a transformation");
    const choice = within(review).getByTestId("georeference-transformation");
    const radios = within(choice).getAllByRole("radio");
    expect(radios).toHaveLength(2);
    expect(radios.every((radio) => !(radio as HTMLInputElement).checked)).toBe(true); // the suggestion is not pre-selected
    const again = within(choice).getByRole("button", { name: "Check again with this choice" });
    expect(again).toBeDisabled();

    await userEvent.click(within(choice).getByRole("radio", { name: /EPSG:1304/ }));
    await userEvent.click(again);
    expect(client.validateGeoreferencing).toHaveBeenLastCalledWith("r1", {
      operation: "assign_crs",
      reason: "Stated in the delivery note.",
      acknowledge_transformation: false,
      crs_id: "EPSG:32643",
      transformation_operation: "EPSG:1304",
    });
  });

  it("asks for an acknowledgement of a datum shift worse than 10 m, and records it only when the person gives it", async () => {
    const kalianpur: client.TransformationOperation = {
      id: "EPSG:1247",
      name: "Kalianpur 1962 to WGS 84 (1)",
      classification: "approximate",
      accuracy_m: 999,
      authority_code: "EPSG:1247",
      area_of_use: null,
      grids: [],
      summary: "about 999 m; needs acknowledgement before use",
      usable: true,
      recommended: false,
      selected: false,
      requires_acknowledgement: true,
      acknowledgement_reason: "accuracy_above_threshold",
      acknowledgement_threshold_m: 10,
    };
    const refused: client.GeoreferenceValidation = {
      ...VALID,
      valid: false,
      proposed: null,
      corners: [],
      checks: [{ code: "TRANSFORMATION_ACKNOWLEDGEMENT_REQUIRED", status: "failed", message: "Acknowledge the transformation accuracy." }],
      transformation_plan: {
        source_crs: "EPSG:24313",
        target_crs: "EPSG:4326",
        status: "approximate",
        status_message: "One registered datum shift applies.",
        recommended_id: null,
        selected_id: null,
        operations: [kalianpur],
        missing_grids: [],
        messages: [],
      },
    };
    vi.mocked(client.getGeoreferencing).mockResolvedValue(georef());
    vi.mocked(client.validateGeoreferencing).mockResolvedValueOnce(refused).mockResolvedValueOnce({ ...VALID, transformation_plan: null });
    vi.mocked(client.recordGeoreferencing).mockResolvedValue(georef({ current: { ...georef().current, version: 1, georeferenced: true } }));
    render();
    await userEvent.click(await screen.findByRole("button", { name: "Assign a CRS to the existing coordinates" }));
    await chooseCrs();
    await userEvent.type(screen.getByLabelText("Reason (required)"), "Stated in the delivery note.");
    await userEvent.click(screen.getByRole("button", { name: "Check this change" }));

    const review = await screen.findByRole("region", { name: "Review the proposed change" });
    expect(review).toHaveTextContent("Did not pass validation");
    const box = within(review).getByTestId("accuracy-acknowledgement");
    expect(box).toHaveTextContent("Kalianpur 1962 to WGS 84 (1) (EPSG:1247)");
    expect(box).toHaveTextContent("about 999 m, worse than the 10 m limit");
    expect(box).toHaveTextContent("recorded with this georeferencing version, with your name and the time");
    const checkbox = within(box).getByRole("checkbox");
    expect((checkbox as HTMLInputElement).checked).toBe(false); // never pre-ticked
    const again = within(review).getByRole("button", { name: "Check again with this acknowledgement" });
    expect(again).toBeDisabled();
    expect(within(review).getAllByTestId("operation")[0]).toHaveTextContent("Needs your acknowledgement: worse than 10 m.");

    await userEvent.click(checkbox);
    await userEvent.click(again);
    expect(client.validateGeoreferencing).toHaveBeenLastCalledWith("r1", {
      operation: "assign_crs",
      reason: "Stated in the delivery note.",
      acknowledge_transformation: true,
      crs_id: "EPSG:32643",
    });

    // The reviewed change is then recorded with the acknowledgement; changing the values clears it.
    const passed = await screen.findByText("Passed validation");
    expect(passed).toBeInTheDocument();
    await userEvent.click(screen.getByRole("checkbox", { name: /I have checked these values/ }));
    await userEvent.click(screen.getByRole("button", { name: "Record as version 1" }));
    expect(client.recordGeoreferencing).toHaveBeenCalledWith(
      "r1",
      expect.objectContaining({ acknowledge_transformation: true, expected_version: 0, confirm: true }),
    );
  });

  it("shows why a ballpark-only transformation cannot be used and offers no choice", async () => {
    const refused: client.GeoreferenceValidation = {
      ...VALID,
      valid: false,
      proposed: null,
      corners: [],
      checks: [{ code: "TRANSFORMATION_AVAILABLE", status: "failed", message: "Only a rough ballpark transformation is available." }],
      transformation_plan: {
        source_crs: "EPSG:24371",
        target_crs: "EPSG:4326",
        status: "unavailable",
        status_message: "No usable transformation exists.",
        recommended_id: null,
        selected_id: null,
        operations: [
          {
            id: "ballpark",
            name: "Ballpark geographic offset from Kalianpur 1880 to WGS 84",
            classification: "ballpark",
            accuracy_m: null,
            authority_code: null,
            area_of_use: null,
            grids: [],
            summary: "blocked",
            usable: false,
            recommended: false,
            selected: false,
            requires_acknowledgement: false,
            acknowledgement_threshold_m: 10,
          },
        ],
        missing_grids: [],
        messages: ["A ballpark fallback exists for this pair. It is blocked unless you explicitly accept it."],
      },
    };
    vi.mocked(client.getGeoreferencing).mockResolvedValue(georef());
    vi.mocked(client.validateGeoreferencing).mockResolvedValue(refused);
    render();
    await userEvent.click(await screen.findByRole("button", { name: "Assign a CRS to the existing coordinates" }));
    await chooseCrs();
    await userEvent.type(screen.getByLabelText("Reason (required)"), "Stated in the delivery note.");
    await userEvent.click(screen.getByRole("button", { name: "Check this change" }));
    const choice = await screen.findByTestId("georeference-transformation");
    expect(choice).toHaveTextContent("Ballpark — blocked");
    expect(choice).toHaveTextContent("Blocked: no documented accuracy.");
    expect(within(choice).queryAllByRole("radio")).toHaveLength(0);
    expect(within(choice).queryByRole("button", { name: /Check again/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Record as version/ })).not.toBeInTheDocument();
  });

  it("does not offer a CRS alone when there is no geotransform, and asks for the six numbers and their source", async () => {
    vi.mocked(client.getGeoreferencing).mockResolvedValue(
      georef({
        file: { ...georef().file, crs_status: "valid", crs_identifier: "EPSG:32643", geotransform: null, geotransform_problem: "There is no geotransform." },
        current: { ...georef().current, crs_id: "EPSG:32643", crs_usable: true, geotransform: null, geotransform_usable: false },
        unplaced_reason: "It has a CRS, but no usable geotransform, so its pixels have no coordinates in that CRS.",
        options: {
          ...georef().options,
          assign_crs: false,
          assign_crs_reason: "Not available: the raster has no usable geotransform, so a CRS alone cannot place it.",
          crs_required_with_geotransform: false,
        },
      }),
    );
    vi.mocked(client.validateGeoreferencing).mockResolvedValue(VALID);
    render();
    expect(await screen.findByRole("button", { name: "Assign a CRS to the existing coordinates" })).toBeDisabled();
    expect(screen.getByText(/a CRS alone cannot place it/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Enter a geotransform" }));
    expect(screen.getByText(/leave empty to keep EPSG:32643/)).toBeInTheDocument();
    for (const [index, value] of NORTH_UP.entries()) await userEvent.type(screen.getByLabelText(new RegExp(`^${index + 1}\\. `)), String(value));
    await userEvent.type(screen.getByLabelText("Reason (required)"), "From the supplier.");
    expect(screen.getByRole("button", { name: "Check this change" })).toBeDisabled(); // the source is required
    await userEvent.type(screen.getByLabelText("Where these numbers come from (required)"), "scene.tfw");
    await userEvent.click(screen.getByRole("button", { name: "Check this change" }));
    expect(client.validateGeoreferencing).toHaveBeenCalledWith("r1", {
      operation: "set_geotransform",
      reason: "From the supplier.",
      acknowledge_transformation: false,
      crs_id: null,
      geotransform: NORTH_UP,
      world_file: null,
      geotransform_source: "scene.tfw",
    });
  });

  it("refuses to record a change that failed validation", async () => {
    vi.mocked(client.getGeoreferencing).mockResolvedValue(georef());
    vi.mocked(client.validateGeoreferencing).mockResolvedValue({
      ...VALID,
      valid: false,
      proposed: null,
      corners: [],
      checks: [{ code: "COORDINATES_FIT_CRS", status: "failed", message: "The coordinates fall outside the valid range of WGS 84." }],
    });
    render();
    await userEvent.click(await screen.findByRole("button", { name: "Assign a CRS to the existing coordinates" }));
    await chooseCrs();
    await userEvent.type(screen.getByLabelText("Reason (required)"), "Trying 4326.");
    await userEvent.click(screen.getByRole("button", { name: "Check this change" }));
    const review = await screen.findByRole("region", { name: "Review the proposed change" });
    expect(review).toHaveTextContent("Did not pass validation");
    expect(review).toHaveTextContent("Failed · The coordinates fall outside the valid range of WGS 84.");
    expect(within(review).queryByRole("button", { name: /Record as version/ })).not.toBeInTheDocument();
    expect(review).toHaveTextContent("The raster keeps its current state.");
  });

  it("explains a concurrent change instead of overwriting it", async () => {
    vi.mocked(client.getGeoreferencing).mockResolvedValue(georef());
    vi.mocked(client.validateGeoreferencing).mockResolvedValue(VALID);
    vi.mocked(client.recordGeoreferencing).mockRejectedValue(
      new client.ApiError(409, "GEOREFERENCE_VERSION_CONFLICT", "changed", "You reviewed version 0; it is now at version 1."),
    );
    render();
    await userEvent.click(await screen.findByRole("button", { name: "Assign a CRS to the existing coordinates" }));
    await chooseCrs();
    await userEvent.type(screen.getByLabelText("Reason (required)"), "Delivery note.");
    await userEvent.click(screen.getByRole("button", { name: "Check this change" }));
    await userEvent.click(await screen.findByRole("checkbox", { name: /does not show/ }));
    await userEvent.click(screen.getByRole("button", { name: "Record as version 1" }));
    expect(await screen.findByText("Someone else changed this raster's georeferencing")).toBeInTheDocument();
    expect(client.getGeoreferencing).toHaveBeenCalledTimes(2); // the latest version is reloaded
  });

  it("offers nothing for a raster whose own georeferencing is valid", async () => {
    vi.mocked(client.getGeoreferencing).mockResolvedValue(
      georef({
        file: { ...georef().file, crs_status: "valid", crs_identifier: "EPSG:32643", georeferenced: true },
        current: { ...georef().current, crs_id: "EPSG:32643", crs_usable: true, georeferenced: true },
        unplaced_reason: null,
        options: {
          assign_crs: false,
          assign_crs_reason: "The file's own CRS and geotransform are valid. They are not overridden.",
          set_geotransform: false,
          set_geotransform_reason: "The file's own CRS and geotransform are valid. They are not overridden.",
          crs_required_with_geotransform: false,
          revert_to_file: false,
        },
      }),
    );
    render();
    expect(await screen.findByText("Placed from the file's metadata")).toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "Georeferencing operations" })).not.toBeInTheDocument();
    expect(screen.getByText(/They are not overridden/)).toBeInTheDocument();
  });
});
