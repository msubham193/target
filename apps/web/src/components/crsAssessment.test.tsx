import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";

import * as client from "../api/client";
import { renderPage } from "../test/render";
import { CrsAssessmentPanel } from "./CrsAssessmentPanel";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, getCrsAssessment: vi.fn() };
});

type Op = client.TransformationOperation;
type Plan = client.CrsAssessment["transformations"][number];

const STATUS_CODES = [
  "georeferenced",
  "crs_identified",
  "crs_missing",
  "crs_ambiguous",
  "transformation_available",
  "transformation_unavailable",
  "georeferencing_required",
  "validation_failed",
] as const;

function statuses(active: string[]): client.CrsAssessment["statuses"] {
  return STATUS_CODES.map((code) => ({
    code,
    label: code.replaceAll("_", " ").replace(/^./, (c) => c.toUpperCase()),
    active: active.includes(code),
    message: `${code} message`,
  }));
}

const EXACT: Op = {
  id: "datum-unchanged",
  name: "Inverse of UTM zone 43N",
  classification: "exact",
  accuracy_m: 0,
  authority_code: null,
  area_of_use: null,
  grids: [],
  summary: "No datum change; exact.",
  usable: true,
  recommended: false,
  selected: false,
  requires_acknowledgement: false,
  acknowledgement_threshold_m: 10,
};
const FINE: Op = { ...EXACT, id: "EPSG:1812", name: "Indian 1975 to WGS 84 (4)", classification: "approximate", accuracy_m: 3, summary: "about 3 m", recommended: true };
const COARSE: Op = { ...FINE, id: "EPSG:1304", name: "Indian 1975 to WGS 84 (2)", accuracy_m: 5, summary: "about 5 m", recommended: false };
const BALLPARK: Op = { ...EXACT, id: "ballpark", name: "Ballpark geographic offset from Kalianpur 1880 to WGS 84", classification: "ballpark", accuracy_m: null, summary: "blocked", usable: false };
const MISSING: Op = {
  ...EXACT,
  id: "EPSG:7709",
  name: "OSGB36 to ETRS89 (2)",
  classification: "unavailable",
  accuracy_m: 0.03,
  grids: [{ name: "uk_os_OSTN15_NTv2_OSGBtoETRS.tif", available: false }],
  summary: "Needs the grid",
  usable: false,
};

function plan(purpose: "coordinates" | "tiles", overrides: Partial<Plan>): Plan {
  return {
    source_crs: "EPSG:32643",
    target_crs: purpose === "coordinates" ? "EPSG:4326" : "EPSG:3857",
    status: "exact",
    status_message: "No datum change is involved, so the conversion is exact.",
    recommended_id: null,
    selected_id: null,
    operations: [EXACT],
    missing_grids: [],
    messages: [],
    purpose,
    purpose_label: purpose === "coordinates" ? "Footprints, coordinate readouts and exports" : "Map display tiles",
    resolution: { operation_id: "datum-unchanged", classification: "exact", accuracy_m: 0, chosen_by: "only_option", requires_acknowledgement: false, acknowledged: false },
    blocked: null,
    ...overrides,
  };
}

const UTM_CRS: NonNullable<client.CrsAssessment["crs"]> = {
  identifier: "EPSG:32643",
  name: "WGS 84 / UTM zone 43N",
  kind: "projected",
  registry_matches: ["EPSG:32643"],
  datum: { name: "World Geodetic System 1984 ensemble", is_ensemble: true, ensemble_members: ["WGS 84 (Transit)", "WGS 84 (G730)"] },
  ellipsoid: { name: "WGS 84", semi_major_m: 6378137, inverse_flattening: 298.257223563, semi_minor_m: 6356752.314245179 },
  prime_meridian: { name: "Greenwich", longitude_degrees: 0 },
  projection: {
    method: "Transverse Mercator",
    name: "UTM zone 43N",
    parameters: [
      { name: "Latitude of natural origin", value: 0, unit: "degree" },
      { name: "Longitude of natural origin", value: 75, unit: "degree" },
      { name: "Scale factor at natural origin", value: 0.9996, unit: "unity" },
      { name: "False easting", value: 500000, unit: "metre" },
    ],
  },
  axes: [
    { name: "Easting", abbreviation: "E", direction: "east", unit: "metre", unit_to_si: 1 },
    { name: "Northing", abbreviation: "N", direction: "north", unit: "metre", unit_to_si: 1 },
  ],
  coordinate_units: ["metre"],
  area_of_use: { name: "Between 72°E and 78°E", west: 72, south: 0, east: 78, north: 84 },
  deprecated: false,
  towgs84: [],
};

function assessment(overrides: Partial<client.CrsAssessment> = {}): client.CrsAssessment {
  return {
    asset_id: "r1",
    georeferencing_source: "file",
    georeferencing_version: 0,
    crs: UTM_CRS,
    raster: { width: 200, height: 150, band_count: 3, pixel_size: [10, 10], pixel_units: "metre", geotransform: [500000, 10, 0, 3000000, 0, -10], bounds_native: null, nodata: [null, null, null] },
    statuses: statuses(["georeferenced", "crs_identified", "transformation_available"]),
    transformations: [plan("coordinates", {}), plan("tiles", {})],
    planning_area: { west: 74.9, south: 27, east: 75.1, north: 27.2 },
    selected_operation_id: null,
    summary: "Georeferenced, CRS identified, Transformation available",
    accuracy_notes: ["Accuracy is the horizontal figure published with the operation in the local PROJ/EPSG registry."],
    class_explanations: { exact: "No datum change.", approximate: "A registered datum shift.", ballpark: "A rough fallback.", unavailable: "Needs a grid." },
    limitations: ["Nothing here shows that a position is correct."],
    versions: { proj: "9.5.1", pyproj: "3.7.1", network: "disabled" },
    ...overrides,
  };
}

function render() {
  return renderPage(<CrsAssessmentPanel assetId="r1" />, "/projects/p1/assets/r1");
}

beforeEach(() => vi.clearAllMocks());

describe("CrsAssessmentPanel", () => {
  it("shows all eight statuses, saying which hold", async () => {
    vi.mocked(client.getCrsAssessment).mockResolvedValue(assessment());
    render();
    const list = await screen.findByRole("list", { name: "CRS assessment statuses" });
    expect(within(list).getAllByRole("listitem")).toHaveLength(8);
    for (const code of ["georeferenced", "crs_identified", "transformation_available"]) {
      expect(screen.getByTestId(`status-${code}`)).toHaveAttribute("data-active", "true");
    }
    for (const code of ["crs_missing", "crs_ambiguous", "transformation_unavailable", "georeferencing_required", "validation_failed"]) {
      expect(screen.getByTestId(`status-${code}`)).toHaveAttribute("data-active", "false");
    }
  });

  it("shows datum, ellipsoid, projection, parameters and units as the CRS definition says them", async () => {
    vi.mocked(client.getCrsAssessment).mockResolvedValue(assessment());
    render();
    await screen.findByText("What the CRS definition says");
    expect(screen.getByText(/EPSG:32643 — WGS 84 \/ UTM zone 43N/)).toBeInTheDocument();
    expect(screen.getByText(/World Geodetic System 1984 ensemble/)).toBeInTheDocument();
    expect(screen.getByText(/semi-major axis 6378137 m, inverse flattening 298.257223563/)).toBeInTheDocument();
    expect(screen.getByText(/Greenwich \(0°\)/)).toBeInTheDocument();
    const parameters = screen.getByRole("list", { name: "Projection parameters" });
    expect(parameters).toHaveTextContent("Longitude of natural origin: 75 degree");
    expect(parameters).toHaveTextContent("Scale factor at natural origin: 0.9996 unity");
    expect(screen.getByText("Transverse Mercator")).toBeInTheDocument();
    expect(screen.getByText("metre", { selector: "dd" })).toBeInTheDocument();
  });

  it("calls an exact conversion exact and offers no choice", async () => {
    vi.mocked(client.getCrsAssessment).mockResolvedValue(assessment());
    render();
    const coordinates = await screen.findByTestId("plan-coordinates");
    expect(within(coordinates).getByTestId("operation")).toHaveTextContent("Exactexact (no datum change)");
    expect(within(coordinates).queryAllByRole("radio")).toHaveLength(0);
    expect(screen.getByTestId("resolution-coordinates")).toHaveTextContent("datum-unchanged (exact)");
  });

  it("shows an approximate shift with its documented accuracy", async () => {
    vi.mocked(client.getCrsAssessment).mockResolvedValue(
      assessment({
        transformations: [
          plan("coordinates", {
            status: "approximate",
            operations: [
              {
                ...FINE,
                recommended: false,
                accuracy_m: 999,
                name: "Kalianpur 1962 to WGS 84 (1)",
                id: "EPSG:1247",
                requires_acknowledgement: true,
                acknowledgement_reason: "accuracy_above_threshold",
              },
            ],
            // As for a raster whose recorded georeferencing carries the acknowledgement.
            resolution: { operation_id: "EPSG:1247", classification: "approximate", accuracy_m: 999, chosen_by: "only_option", requires_acknowledgement: true, acknowledged: true },
          }),
        ],
      }),
    );
    render();
    const coordinates = await screen.findByTestId("plan-coordinates");
    const row = within(coordinates).getByTestId("operation");
    expect(row).toHaveTextContent("Kalianpur 1962 to WGS 84 (1)");
    expect(row).toHaveTextContent("Approximate");
    expect(row).toHaveTextContent("about 999 m");
    expect(screen.getByTestId("resolution-coordinates")).toHaveTextContent("EPSG:1247 (documented accuracy about 999 m)");
  });

  it("blocks a ballpark transformation and says why, with no way to choose it", async () => {
    vi.mocked(client.getCrsAssessment).mockResolvedValue(
      assessment({
        statuses: statuses(["crs_identified", "transformation_unavailable", "georeferencing_required", "validation_failed"]),
        transformations: [
          plan("coordinates", {
            status: "unavailable",
            status_message: "No usable transformation exists.",
            operations: [BALLPARK],
            resolution: null,
            blocked: { code: "TRANSFORMATION_BALLPARK_BLOCKED", title: "Only a rough ballpark transformation is available", detail: "It is blocked. The error can be tens to hundreds of metres." },
            messages: ["A ballpark fallback exists for this pair. It has no documented accuracy and is blocked unless you explicitly accept it."],
          }),
        ],
        summary: "CRS identified, Transformation unavailable, Georeferencing required, Validation failed",
      }),
    );
    render();
    const coordinates = await screen.findByTestId("plan-coordinates");
    expect(within(coordinates).getByText("Only a rough ballpark transformation is available")).toBeInTheDocument();
    expect(within(coordinates).getByText(/tens to hundreds of metres/)).toBeInTheDocument();
    const row = within(coordinates).getByTestId("operation");
    expect(row).toHaveTextContent("Ballpark — blocked");
    expect(row).toHaveTextContent("not stated");
    expect(row).toHaveTextContent("Blocked: no documented accuracy.");
    expect(screen.getByTestId("status-transformation_unavailable")).toHaveAttribute("data-active", "true");
    expect(screen.getByTestId("status-transformation_available")).toHaveAttribute("data-active", "false");
  });

  it("lists a missing grid by name and never offers that operation", async () => {
    vi.mocked(client.getCrsAssessment).mockResolvedValue(
      assessment({
        transformations: [
          plan("coordinates", {
            status: "unavailable",
            operations: [BALLPARK, MISSING],
            missing_grids: ["uk_os_OSTN15_NTv2_OSGBtoETRS.tif"],
            resolution: null,
            blocked: { code: "TRANSFORMATION_GRID_MISSING", title: "A required datum-shift grid is not installed", detail: "Nothing was downloaded and no substitute was used." },
          }),
        ],
      }),
    );
    render();
    const coordinates = await screen.findByTestId("plan-coordinates");
    expect(within(coordinates).getByText("A required datum-shift grid is not installed")).toBeInTheDocument();
    const rows = within(coordinates).getAllByTestId("operation");
    expect(rows[1]).toHaveTextContent("Grid missing");
    expect(rows[1]).toHaveTextContent("Needs uk_os_OSTN15_NTv2_OSGBtoETRS.tif (not installed)");
    expect(rows[1]).toHaveTextContent("about 0.03 m, if its grid were installed");
  });

  it("requires a person to choose between several operations, suggesting but not selecting one", async () => {
    const choose = plan("coordinates", {
      status: "selection_required",
      recommended_id: "EPSG:1812",
      operations: [FINE, COARSE],
      resolution: null,
      blocked: { code: "TRANSFORMATION_SELECTION_REQUIRED", title: "Choose a transformation", detail: "2 registered operations apply and they give different positions." },
      messages: ["2 registered operations apply."],
    });
    vi.mocked(client.getCrsAssessment).mockImplementation(async (_id, operation) =>
      operation
        ? assessment({
            selected_operation_id: operation,
            transformations: [
              {
                ...choose,
                selected_id: operation,
                operations: choose.operations.map((o) => ({ ...o, selected: o.id === operation })),
                resolution: { operation_id: operation, classification: "approximate", accuracy_m: operation === "EPSG:1812" ? 3 : 5, chosen_by: "selection", requires_acknowledgement: false, acknowledged: false },
                blocked: null,
              },
            ],
          })
        : assessment({ transformations: [choose] }),
    );
    render();
    const coordinates = await screen.findByTestId("plan-coordinates");
    expect(within(coordinates).getByText("Choice required")).toBeInTheDocument();
    expect(within(coordinates).getByText("Choose a transformation")).toBeInTheDocument();
    const radios = within(coordinates).getAllByRole("radio");
    expect(radios).toHaveLength(2);
    expect(radios.every((radio) => !(radio as HTMLInputElement).checked)).toBe(true);
    expect(within(coordinates).getByText("Smallest documented accuracy (a suggestion only)")).toBeInTheDocument();

    await userEvent.click(within(coordinates).getByRole("radio", { name: /EPSG:1304/ }));
    expect(client.getCrsAssessment).toHaveBeenLastCalledWith("r1", "EPSG:1304");
    expect(await screen.findByTestId("resolution-coordinates")).toHaveTextContent("With your choice, EPSG:1304 (documented accuracy about 5 m)");
    expect(screen.getByText(/This is a preview only; nothing was stored/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Clear the preview" }));
    expect(client.getCrsAssessment).toHaveBeenLastCalledWith("r1", null);
  });

  it("says plainly when a raster has no CRS, and shows none", async () => {
    vi.mocked(client.getCrsAssessment).mockResolvedValue(
      assessment({
        crs: null,
        transformations: [],
        statuses: statuses(["crs_missing", "georeferencing_required"]),
        summary: "CRS missing, Georeferencing required",
        raster: { width: 200, height: 150, band_count: 1, pixel_size: null, pixel_units: null, geotransform: null, bounds_native: null, nodata: [null] },
      }),
    );
    render();
    expect(await screen.findByText("No usable CRS")).toBeInTheDocument();
    expect(screen.getByText(/None is assumed or taken from the file name/)).toBeInTheDocument();
    expect(screen.queryByText("What the CRS definition says")).not.toBeInTheDocument();
    expect(screen.queryByTestId("plan-coordinates")).not.toBeInTheDocument();
    expect(screen.getByTestId("status-crs_missing")).toHaveAttribute("data-active", "true");
    expect(screen.getByTestId("status-georeferencing_required")).toHaveAttribute("data-active", "true");
  });

  it("explains the classes, the accuracy figures and what is not covered", async () => {
    vi.mocked(client.getCrsAssessment).mockResolvedValue(assessment());
    render();
    await screen.findByRole("list", { name: "CRS assessment statuses" });
    expect(screen.getByText("What the classes and accuracy figures mean")).toBeInTheDocument();
    expect(screen.getByText(/Accuracy is the horizontal figure published/)).toBeInTheDocument();
    expect(screen.getByRole("list", { name: "Limitations" })).toHaveTextContent("Nothing here shows that a position is correct.");
    expect(screen.getByText(/network access disabled/)).toBeInTheDocument();
  });

  it("shows an error when the assessment cannot be loaded", async () => {
    vi.mocked(client.getCrsAssessment).mockRejectedValue(new client.ApiError(409, "NOT_A_RASTER", "Only rasters have a CRS assessment"));
    render();
    expect(await screen.findByText("Only rasters have a CRS assessment")).toBeInTheDocument();
  });
});
