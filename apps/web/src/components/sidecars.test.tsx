import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";

import * as client from "../api/client";
import { checkSidecar, isSidecar, sidecarPairsWith } from "../lib/ingestion";
import { renderPage } from "../test/render";
import { SidecarPanel } from "./SidecarPanel";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, getSidecars: vi.fn(), validateSidecars: vi.fn(), applySidecars: vi.fn(), uploadSidecar: vi.fn() };
});

const GT = [500000, 10, 0, 3000000, 0, -10];

function item(overrides: Partial<client.SidecarItem> = {}): client.SidecarItem {
  return {
    id: "w1",
    kind: "world_file",
    kind_label: "World file",
    filename: "scan.tfw",
    sha256: "cd".repeat(32),
    size_bytes: 36,
    pairing: "filename_match",
    uploaded_by: "Asha",
    uploaded_at: "2026-10-21T10:00:00Z",
    current: true,
    status: "parsed",
    summary: "Six numbers: pixel size, rotation and the centre of the top-left pixel. No CRS.",
    values: { geotransform: GT },
    problems: [],
    ignored: [],
    comparisons: [
      { field: "geotransform", sidecar: GT.join(", "), file: null, in_use: null, relation_file: "none", relation_in_use: "none", applied: null },
    ],
    role: "Gives the geotransform in the proposed change.",
    ...overrides,
  };
}

const PRJ = item({
  id: "p1",
  kind: "prj",
  kind_label: ".prj CRS definition",
  filename: "scan.prj",
  summary: "A CRS definition matching EPSG:32643 (WGS 84 / UTM zone 43N) in the local registry.",
  values: { crs: { name: "WGS 84 / UTM zone 43N", identifier: "EPSG:32643" } },
  comparisons: [{ field: "CRS", sidecar: "EPSG:32643", file: null, in_use: null, relation_file: "none", relation_in_use: "none", applied: null }],
  role: "Gives the CRS in the proposed change.",
});

function review(overrides: Partial<client.SidecarReview> = {}): client.SidecarReview {
  return {
    asset_id: "r1",
    expected_version: 0,
    sidecar_ids: ["w1", "p1"],
    state: "proposal",
    title: "A change can be reviewed",
    explanation: ["The values below come from the sidecar files named. They are not applied until you check them, give a reason and confirm."],
    conflicts: [],
    choices: {},
    proposal: {
      operation: "set_geotransform",
      crs_id: "EPSG:32643",
      geotransform: GT,
      world_file: "10\n0\n0\n-10\n500005\n2999995\n",
      geotransform_source: "Sidecar scan.tfw",
      sources: { geotransform: { sidecar_id: "w1", filename: "scan.tfw" }, crs: { sidecar_id: "p1", filename: "scan.prj" } },
    },
    sidecars: [PRJ, item()],
    accepted_extensions: [".tfw", ".tifw", ".tiffw", ".wld", ".prj", ".aux.xml"],
    note: "Sidecar files are kept exactly as received and are never applied on their own.",
    ...overrides,
  };
}

const CHECKED: client.SidecarCheck = {
  review: review(),
  validation: {
    valid: true,
    checks: [
      { code: "SIDECAR_UNCHANGED", status: "ok", message: "scan.tfw matches the SHA-256 recorded when it was received." },
      { code: "COORDINATES_FIT_CRS", status: "ok", message: "Every point of the raster's outline transforms to valid longitude and latitude." },
    ],
    proposed: null,
    corners: [{ point: "top-left corner", pixel: [0, 0], native: [500000, 3000000], lon_lat: [75, 27.12247] }],
    method: "sidecar_geotransform",
    explanation: "The six numbers say where each pixel is. The values come from the sidecar files named.",
  },
};

const render = () => renderPage(<SidecarPanel assetId="r1" projectId="p1" />);

describe("SidecarPanel", () => {
  it("shows each sidecar, what it says and how it compares, before anything is applied", async () => {
    vi.mocked(client.getSidecars).mockResolvedValue(review());
    render();
    const list = await screen.findByRole("list", { name: "Sidecar files" });
    const world = within(list).getByRole("listitem", { name: "scan.tfw" });
    expect(world).toHaveTextContent("No CRS");
    expect(world).toHaveTextContent("kept unmodified");
    expect(within(world).getByRole("table", { name: "How scan.tfw compares with the raster" })).toHaveTextContent("none to compare");
    expect(within(list).getByRole("listitem", { name: "scan.prj" })).toHaveTextContent("→ EPSG:32643");
    const proposal = screen.getByRole("region", { name: "Review the change from sidecar files" });
    expect(proposal).toHaveTextContent("Give the raster's pixels coordinates");
    expect(proposal).toHaveTextContent("500000, 10, 0, 3000000, 0, -10");
    expect(proposal).toHaveTextContent("scan.tfw and scan.prj");
  });

  it("needs a reason, a check and a confirmation before recording", async () => {
    vi.mocked(client.getSidecars).mockResolvedValue(review());
    vi.mocked(client.validateSidecars).mockResolvedValue(CHECKED);
    render();
    const check = await screen.findByRole("button", { name: "Check this change" });
    expect(check).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Reason (required)"), "Delivered with the scan.");
    await userEvent.click(check);
    expect(client.validateSidecars).toHaveBeenCalledWith("r1", { reason: "Delivered with the scan.", geotransform_from: null, crs_from: null });
    expect(await screen.findByText("Passed validation")).toBeInTheDocument();
    expect(screen.getByRole("list", { name: "Validation checks" })).toHaveTextContent("matches the SHA-256");
    const save = screen.getByRole("button", { name: "Record as version 1" });
    expect(save).toBeDisabled();
    await userEvent.click(screen.getByRole("checkbox", { name: /does not show that the raster's location is correct/ }));
    vi.mocked(client.applySidecars).mockResolvedValue({ current: { version: 1 } } as client.Georeferencing);
    vi.mocked(client.getSidecars).mockResolvedValue(review({ state: "in_use", title: "Already in use", proposal: null }));
    await userEvent.click(save);
    expect(client.applySidecars).toHaveBeenCalledWith("r1", {
      reason: "Delivered with the scan.",
      geotransform_from: null,
      crs_from: null,
      expected_version: 0,
      sidecar_ids: ["w1", "p1"],
      confirm: true,
    });
    expect(await screen.findByText("Recorded as version 1")).toBeInTheDocument();
  });

  it("explains why a world file alone cannot place the raster and offers nothing to record", async () => {
    vi.mocked(client.getSidecars).mockResolvedValue(
      review({
        state: "insufficient",
        title: "Not enough to place the raster",
        explanation: ["No sidecar gives a usable CRS. A world file gives pixel coordinates, but not which coordinate system they are in."],
        proposal: null,
        sidecars: [item({ role: "Not used for the change shown." })],
        sidecar_ids: ["w1"],
      }),
    );
    render();
    expect(await screen.findByText("Not enough to place the raster")).toBeInTheDocument();
    expect(screen.getByText(/not which coordinate system/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Check this change" })).not.toBeInTheDocument();
  });

  it("asks which file to use when two sidecars disagree", async () => {
    vi.mocked(client.getSidecars).mockResolvedValue(
      review({
        state: "conflict",
        title: "The sidecar files disagree",
        proposal: null,
        choices: {
          geotransform: [
            { sidecar_id: "w1", filename: "scan.tfw", value: "500000, 10, 0, 3000000, 0, -10" },
            { sidecar_id: "a1", filename: "scan.tif.aux.xml", value: "600000, 10, 0, 3000000, 0, -10" },
          ],
        },
      }),
    );
    vi.mocked(client.validateSidecars).mockResolvedValue(CHECKED);
    render();
    const group = await screen.findByRole("radiogroup", { name: "Geotransform from" });
    await userEvent.type(screen.getByLabelText("Reason (required)"), "The aux file is the supplier's latest.");
    const check = screen.getByRole("button", { name: "Check this change" });
    expect(check).toBeDisabled(); // a choice is needed first
    await userEvent.click(within(group).getByRole("radio", { name: /scan.tif.aux.xml/ }));
    await userEvent.click(check);
    expect(client.validateSidecars).toHaveBeenCalledWith("r1", {
      reason: "The aux file is the supplier's latest.",
      geotransform_from: "a1",
      crs_from: null,
    });
  });

  it("shows disagreements with valid raster metadata and keeps the file's values", async () => {
    vi.mocked(client.getSidecars).mockResolvedValue(
      review({
        state: "file_valid",
        title: "The raster's own georeferencing is valid",
        conflicts: ["scan.tfw: its geotransform (600000, 10, 0, 3000000, 0, -10) differs from the one in the raster file (500000, 10, 0, 3000000, 0, -10)."],
        proposal: null,
        sidecars: [
          item({
            role: "Shown for comparison only: the raster file's own georeferencing is valid and is used.",
            comparisons: [
              { field: "geotransform", sidecar: "600000, 10, 0, 3000000, 0, -10", file: "500000, 10, 0, 3000000, 0, -10", in_use: "500000, 10, 0, 3000000, 0, -10", relation_file: "differs", relation_in_use: "differs", applied: null },
            ],
          }),
        ],
      }),
    );
    render();
    expect(await screen.findByText("Disagreements with the raster file")).toBeInTheDocument();
    expect(screen.getByText(/The raster file's value is kept/)).toBeInTheDocument();
    expect(screen.getAllByText("differs")).toHaveLength(2);
    expect(screen.queryByRole("region", { name: "Review the change from sidecar files" })).not.toBeInTheDocument();
  });

  it("lists problems and content that is present but not used", async () => {
    vi.mocked(client.getSidecars).mockResolvedValue(
      review({
        state: "insufficient",
        title: "Not enough to place the raster",
        proposal: null,
        sidecars: [
          item({
            id: "a1",
            kind: "aux_xml",
            kind_label: "GDAL .aux.xml",
            filename: "scan.aux.xml",
            status: "parsed",
            problems: ["Band 1: nodata -9999 cannot occur in a uint8 band."],
            ignored: ["An RPC sensor model (1 items): not used; there is no orthorectification."],
            comparisons: [{ field: "band 1 description", sidecar: "red", file: null, in_use: null, relation_file: "none", relation_in_use: "none", applied: false }],
          }),
        ],
      }),
    );
    render();
    const card = await screen.findByRole("listitem", { name: "scan.aux.xml" });
    expect(within(card).getByRole("list", { name: "Problems in scan.aux.xml" })).toHaveTextContent("-9999 cannot occur");
    expect(card).toHaveTextContent("Present in the file, not used (1)");
    expect(card).toHaveTextContent("shown only, not applied");
  });
});

describe("sidecar names", () => {
  const rule = {
    modality: "overhead_raster",
    label: "Satellite / aerial raster (GeoTIFF)",
    extensions: [".tif", ".tiff"],
    max_bytes: 1024,
    sidecar_extensions: [".tfw", ".tifw", ".tiffw", ".wld", ".prj", ".aux.xml"],
    sidecar_max_bytes: 100,
  } as const satisfies client.IngestionRule;

  it("recognises sidecars and pairs them as the server does", () => {
    expect(isSidecar("Scan.TFW", rule)).toBe(true);
    expect(isSidecar("scan.tif.aux.xml", rule)).toBe(true);
    expect(isSidecar("scan.xml", rule)).toBe(false);
    expect(sidecarPairsWith("SCAN.tfw", "scan.tif")).toBe(true);
    expect(sidecarPairsWith("scan.aux.xml", "scan.tiff")).toBe(true);
    expect(sidecarPairsWith("scan.tif.aux.xml", "scan.tif")).toBe(true);
    expect(sidecarPairsWith("scan.tif.aux.xml", "scan.tiff")).toBe(false);
    expect(sidecarPairsWith("other.prj", "scan.tif")).toBe(false);
    expect(sidecarPairsWith("scan.prj", "scan.png")).toBe(false);
  });

  it("checks size before uploading", () => {
    expect(checkSidecar({ name: "a.prj", size: 0 }, rule)?.code).toBe("EMPTY_FILE");
    expect(checkSidecar({ name: "a.prj", size: 101 }, rule)?.code).toBe("FILE_TOO_LARGE");
    expect(checkSidecar({ name: "a.prj", size: 50 }, rule)).toBeNull();
  });
});
