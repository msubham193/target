import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";

import * as client from "../api/client";
import { factValue } from "../lib/mapsheet";
import { renderPage } from "../test/render";
import { MapSheetPanel } from "./MapSheetPanel";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, getMapSheetAssessment: vi.fn(), recordMapSheetNote: vi.fn() };
});

const DECLARATION = {
  series: "esm",
  series_label: "Everest Series Map (ESM)",
  basis: "declared_by_uploader",
  sheet_label: "NH 44-1",
  scale_denominator: 250000,
  edition: null,
  declared_by: "Reviewer",
  note: "Declared by the person who uploaded the file.",
};

function info(overrides: Record<string, unknown> = {}) {
  return {
    declaration: DECLARATION,
    intake: "scanned_image",
    format_family: "png",
    georeferencing_basis: "none",
    georeferencing_basis_label: "Not georeferenced",
    placed: false,
    processing: [
      { step: "stored_original", note: "Stored unmodified." },
      { step: "inspected", tool: "rasterio / GDAL 3.9.3", note: "Metadata read as declared." },
      { step: "georeferencing", status: "none: the sheet is not georeferenced" },
    ],
    ...overrides,
  };
}

const UNKNOWN = (field: string, label: string) => ({
  field,
  label,
  state: "unknown",
  value: null,
  detail: "The sheet has no usable CRS, so this is unknown.",
});

function unplaced(overrides: Record<string, unknown> = {}): client.MapSheetAssessment {
  return {
    asset_id: "a1",
    filename: "esm.png",
    info: info(),
    facts: [
      UNKNOWN("datum", "Datum"),
      UNKNOWN("ellipsoid", "Ellipsoid"),
      UNKNOWN("projection", "Projection"),
      { field: "extent", label: "Geographic extent", state: "unknown", value: { pixels: [300, 200] }, detail: "Not placed." },
    ],
    unresolved: [
      { field: "datum", label: "Datum", status: "missing", detail: "The sheet has no usable CRS.", required: "A CRS stated by the file or a confirmed .prj." },
      { field: "projection_parameters", label: "Projection parameters", status: "declared_unverified", detail: "Declared ESM; not assumed alike.", required: "Per-sheet confirmation." },
    ],
    required_information: ["Datum: A CRS stated by the file or a confirmed .prj.", "An authoritative written definition of the sheet's datum."],
    family_hints: [],
    sidecars: [],
    notes: [],
    pdf: null,
    crs_assessment: null,
    series_note: "Everest-56, Polyconic and LCC are names, not definitions.",
    limitations: ["A note recorded about a sheet is never applied."],
    ...overrides,
  } as unknown as client.MapSheetAssessment;
}

beforeEach(() => {
  vi.mocked(client.getMapSheetAssessment).mockReset();
  vi.mocked(client.recordMapSheetNote).mockReset();
});

describe("the map-sheet panel", () => {
  it("shows an unplaced ESM scan as unknown, with what is unresolved and what is required, and assigns nothing", async () => {
    vi.mocked(client.getMapSheetAssessment).mockResolvedValue(unplaced());
    renderPage(<MapSheetPanel assetId="a1" />);

    expect(await screen.findByText("Everest Series Map (ESM)")).toBeInTheDocument();
    expect(screen.getByText("Scanned picture (PNG / JPEG)")).toBeInTheDocument();
    expect(screen.getByText("Not placed")).toBeInTheDocument();
    expect(screen.getByText("NH 44-1")).toBeInTheDocument();
    expect(screen.getByText("1 : 250,000")).toBeInTheDocument();
    expect(screen.getByText(/names, not definitions/)).toBeInTheDocument();

    const datum = screen.getByTestId("fact-datum");
    expect(within(datum).getByText("Unknown")).toBeInTheDocument();
    expect(screen.getByTestId("fact-extent")).toHaveTextContent("300 × 200 pixels, not placed");

    const items = screen.getAllByTestId("unresolved");
    expect(items).toHaveLength(2);
    expect(within(items[0]!).getByText("Missing")).toBeInTheDocument();
    expect(within(items[1]!).getByText("Declared, not verified")).toBeInTheDocument();
    const required = screen.getByRole("list", { name: "Information still required" });
    expect(within(required).getAllByRole("listitem")).toHaveLength(2);
  });

  it("states what a placed OSM sheet's file says, with its source", async () => {
    vi.mocked(client.getMapSheetAssessment).mockResolvedValue(
      unplaced({
        info: info({
          declaration: { ...DECLARATION, series: "osm", series_label: "Open Series Map (OSM)" },
          intake: "georeferenced_raster",
          georeferencing_basis: "file_metadata",
          georeferencing_basis_label: "The file's own CRS and geotransform",
          placed: true,
        }),
        facts: [
          { field: "datum", label: "Datum", state: "stated_by_file", value: { name: "World Geodetic System 1984 ensemble", is_ensemble: true, ensemble_members: [] }, detail: "ok" },
          { field: "ellipsoid", label: "Ellipsoid", state: "stated_by_file", value: { name: "WGS 84", semi_major_m: 6378137, inverse_flattening: 298.257223563 }, detail: "ok" },
          { field: "units", label: "Units", state: "stated_by_file", value: ["metre"], detail: "ok" },
          { field: "transformation_to_wgs84", label: "Transformation to WGS 84", state: "from_registry", value: { status: "exact", classification: "exact", accuracy_m: 0, operations: [] }, detail: "No datum change is involved." },
        ],
        unresolved: [],
        required_information: [],
      }),
    );
    renderPage(<MapSheetPanel assetId="a1" />);
    expect(await screen.findByText("Open Series Map (OSM)")).toBeInTheDocument();
    expect(screen.getByText("Placed on the map")).toBeInTheDocument();
    expect(within(screen.getByTestId("fact-datum")).getByText("Stated by the file")).toBeInTheDocument();
    expect(screen.getByTestId("fact-ellipsoid")).toHaveTextContent("WGS 84 · a = 6378137 m · 1/f = 298.257223563");
    expect(within(screen.getByTestId("fact-transformation_to_wgs84")).getByText("From the PROJ registry")).toBeInTheDocument();
    expect(screen.queryAllByTestId("unresolved")).toHaveLength(0);
    expect(screen.getByText(/Nothing is unresolved in what the file states/)).toBeInTheDocument();
  });

  it("warns that names such as Everest or Polyconic are not definitions", async () => {
    vi.mocked(client.getMapSheetAssessment).mockResolvedValue(
      unplaced({
        family_hints: [
          { family: "Lambert Conic Conformal", matched: "projection: Lambert Conic Conformal (1SP)", meaning: "A definition also needs standard parallels." },
        ],
      }),
    );
    renderPage(<MapSheetPanel assetId="a1" />);
    expect(await screen.findByText("Names that are not definitions")).toBeInTheDocument();
    expect(screen.getByText(/A definition also needs standard parallels/)).toBeInTheDocument();
  });

  it("reports a PDF's facts and its geospatial metadata as unverified", async () => {
    vi.mocked(client.getMapSheetAssessment).mockResolvedValue(
      unplaced({
        info: info({ intake: "pdf_document", format_family: "pdf" }),
        pdf: {
          pdf_version: "1.4",
          page_count: 1,
          first_page_size: { width_mm: 297, height_mm: 210 },
          embedded_images_found: 2,
          encrypted: false,
          geo_markers: ["ISO 32000 geospatial measure (/GEO)"],
          declared_corner_points: [{ latitude: 28, longitude: 77 }],
          declared_epsg: 32643,
          scan_note: "Read from the file's text structure without rendering it.",
        },
      }),
    );
    renderPage(<MapSheetPanel assetId="a1" />);
    expect(await screen.findByText("PDF document")).toBeInTheDocument();
    expect(screen.getByText("297 × 210 mm")).toBeInTheDocument();
    expect(screen.getByText(/The PDF declares geospatial metadata\. It is not applied\./)).toBeInTheDocument();
    expect(screen.getByText(/declared EPSG:32643/)).toBeInTheDocument();
  });

  it("records a note only with a value and a source, and says it is never applied", async () => {
    vi.mocked(client.getMapSheetAssessment).mockResolvedValue(unplaced());
    vi.mocked(client.recordMapSheetNote).mockResolvedValue({} as client.MapSheetNote);
    const user = userEvent.setup();
    renderPage(<MapSheetPanel assetId="a1" />);
    await screen.findByText("Everest Series Map (ESM)");
    expect(screen.getByText(/never applied: it does not define a CRS/)).toBeInTheDocument();

    const record = screen.getByRole("button", { name: "Record note" });
    expect(record).toBeDisabled();
    await user.type(screen.getByLabelText("What you found"), "Everest 1956 as printed in the margin");
    expect(record).toBeDisabled(); // a source is required
    await user.type(screen.getByLabelText("Where it comes from"), "Sheet margin, photographed");
    await user.selectOptions(screen.getByLabelText("Status"), "needs_authority");
    expect(record).toBeEnabled();
    await user.click(record);
    expect(client.recordMapSheetNote).toHaveBeenCalledWith("a1", {
      field: "datum",
      value: "Everest 1956 as printed in the margin",
      source: "Sheet margin, photographed",
      status: "needs_authority",
    });
  });

  it("lists recorded notes as not applied", async () => {
    vi.mocked(client.getMapSheetAssessment).mockResolvedValue(
      unplaced({
        notes: [{ id: "n1", field: "datum", value: "Everest 1956", source: "Margin", status: "needs_authority", recorded_by: "Reviewer", recorded_at: "2026-10-31T10:00:00Z", applied: false }],
      }),
    );
    renderPage(<MapSheetPanel assetId="a1" />);
    const note = await screen.findByTestId("note");
    expect(note).toHaveTextContent("Datum: Everest 1956");
    expect(note).toHaveTextContent("needs an authoritative source");
    expect(note).toHaveTextContent("not applied");
  });

  it("shows an error when the assessment cannot be loaded", async () => {
    vi.mocked(client.getMapSheetAssessment).mockRejectedValue(new client.ApiError(409, "NOT_A_MAP_SHEET", "This asset was not taken in as a map sheet"));
    renderPage(<MapSheetPanel assetId="a1" />);
    expect(await screen.findByText("The map-sheet assessment could not be loaded")).toBeInTheDocument();
  });
});

describe("factValue", () => {
  it("renders only what is there", () => {
    expect(factValue("datum", null)).toBeNull();
    expect(factValue("projection", { method: null, name: null, geographic: true })).toBe("None (geographic CRS)");
    expect(factValue("projection_parameters", [{ name: "Scale factor", value: 0.9996, unit: "unity" }])).toBe("Scale factor = 0.9996 unity");
    expect(factValue("extent", { bounds_4326: [75, 27, 75.1, 27.1], area_km2: 0.8 })).toContain("0.8 km²");
    expect(factValue("transformation_to_wgs84", { status: "approximate", classification: "approximate", accuracy_m: 999 })).toBe(
      "approximate · approximate · about 999 m",
    );
  });
});
