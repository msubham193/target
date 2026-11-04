import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import path from "node:path";

import { expect, type Page, test } from "@playwright/test";

// Phase 3b follow-up: sidecar metadata reviewed and confirmed in the browser, against the real API, database and
// worker. Synthetic data only: a raster with neither CRS nor geotransform, a world file and a .prj written here
// (EPSG:32643, 10 m pixels, top-left corner 500000 E, 3000000 N), and a raster whose own georeferencing is valid.
test.describe.configure({ mode: "serial" });

const PROFILE = "E2E Reviewer";
const API_DIR = path.resolve(process.cwd(), "../../services/api");
const WORLD = "10\n0\n0\n-10\n500005\n2999995\n"; // centre of the top-left pixel
const OTHER_WORLD = "10\n0\n0\n-10\n600005\n2999995\n";

function python(expression: string): Buffer {
  const code =
    "import base64, sys; from pyproj import CRS; from pyproj.enums import WktVersion; " +
    "from tests.test_raster_tools import tif, gradient; " +
    `sys.stdout.write(base64.b64encode(${expression}).decode())`;
  return Buffer.from(execFileSync("uv", ["run", "--no-sync", "python", "-c", code], { cwd: API_DIR }).toString(), "base64");
}

const UNPLACED = python("tif(gradient(bands=1, height=150, width=200), crs=None, transform=None)");
const PLACED = python("tif(gradient(bands=1, height=150, width=200))");
const PRJ = python("CRS('EPSG:32643').to_wkt(WktVersion.WKT1_ESRI).encode()");

async function enterWorkspace(page: Page) {
  await page.goto("/");
  const existing = page.getByRole("button", { name: `Continue as ${PROFILE}` });
  await expect(page.getByText("No profiles yet").or(existing)).toBeVisible();
  if (await existing.isVisible()) await existing.click();
  else {
    await page.getByLabel("New profile name").fill(PROFILE);
    await page.getByRole("button", { name: "Create profile and continue" }).click();
  }
  await expect(page).toHaveURL(/\/dashboard$/);
}

const nav = (page: Page) => page.getByRole("navigation", { name: "Main" });
const queueRow = (page: Page, name: string) =>
  page.getByRole("list", { name: "Upload queue" }).getByRole("listitem", { name, exact: true });

async function openProject(page: Page) {
  await enterWorkspace(page);
  await page.getByRole("list", { name: "Project list" }).getByRole("link", { name: "Sidecars" }).click();
}

async function uploadRasterTab(page: Page, files: { name: string; buffer: Buffer }[]) {
  await nav(page).getByRole("link", { name: "Upload & Ingestion" }).click();
  await page.getByRole("tab", { name: "Satellite / aerial raster (GeoTIFF)" }).click();
  await page.locator("#asset-files").setInputFiles(files.map((f) => ({ ...f, mimeType: "application/octet-stream" })));
}

test("a world file and .prj uploaded with a raster are reviewed, confirmed and place it on the map", async ({ page }) => {
  const external: string[] = [];
  page.on("request", (r) => {
    const url = new URL(r.url());
    if (url.protocol.startsWith("http") && url.hostname !== "127.0.0.1") external.push(r.url());
  });
  await enterWorkspace(page);
  await page.getByRole("link", { name: "Create project", exact: true }).click();
  await page.getByLabel("Name").fill("Sidecars");
  await page.getByRole("button", { name: "Create project" }).click();
  // Sidecars first in the selection: they still wait for the raster and pair with it.
  await uploadRasterTab(page, [
    { name: "scan.tfw", buffer: Buffer.from(WORLD) },
    { name: "scan.prj", buffer: PRJ },
    { name: "scan.tif", buffer: UNPLACED },
  ]);
  await expect(queueRow(page, "scan.tif")).toContainText("Stored", { timeout: 30_000 });
  await expect(queueRow(page, "scan.tfw")).toContainText("Kept for review", { timeout: 30_000 });
  await expect(queueRow(page, "scan.prj")).toContainText("Kept for review");
  await expect(queueRow(page, "scan.tfw")).toContainText("Kept for review with scan.tif (matched by name). Nothing has been applied.");

  await queueRow(page, "scan.tfw").getByRole("link", { name: "Review with scan.tif" }).click();
  const panel = page.getByRole("region", { name: "Sidecar files", exact: true });
  await expect(panel).toBeVisible();
  // Nothing is applied by uploading.
  await expect(page.getByRole("region", { name: "Spatial placement" })).toContainText("Not placed");
  const files = panel.getByRole("list", { name: "Sidecar files" });
  await expect(files.getByRole("listitem", { name: "scan.tfw" })).toContainText("No CRS");
  await expect(files.getByRole("listitem", { name: "scan.prj" })).toContainText("→ EPSG:32643");

  const reviewRegion = panel.getByRole("region", { name: "Review the change from sidecar files" });
  await expect(reviewRegion).toContainText("500000, 10, 0, 3000000, 0, -10");
  await expect(reviewRegion).toContainText("scan.tfw and scan.prj");
  await expect(reviewRegion.getByRole("button", { name: "Check this change" })).toBeDisabled();
  await reviewRegion.getByLabel("Reason (required)").fill("World file and .prj delivered with the scan.");
  await reviewRegion.getByRole("button", { name: "Check this change" }).click();
  await expect(reviewRegion).toContainText("Passed validation");
  await expect(reviewRegion.getByRole("list", { name: "Validation checks" })).toContainText("matches the SHA-256 recorded when it was received");
  await expect(reviewRegion).toContainText("500000.000, 3000000.000"); // the top-left corner, from the pixel-centre convention
  const record = reviewRegion.getByRole("button", { name: "Record as version 1" });
  await expect(record).toBeDisabled();
  await reviewRegion.getByRole("checkbox", { name: /does not show that the raster's location is correct/ }).check();
  await record.click();
  await expect(panel).toContainText("Recorded as version 1");
  await expect(panel).toContainText("Already in use");

  const placement = page.getByRole("region", { name: "Spatial placement" });
  await expect(placement).toContainText("Placed from recorded georeferencing");
  const history = placement.getByRole("list", { name: "Georeferencing history" });
  await expect(history).toContainText("Geotransform from a sidecar file, reviewed and confirmed");
  await expect(history).toContainText("Sidecar files used: scan.tfw");
  await expect(history).toContainText("scan.prj");

  // The original is unchanged; the map now has the raster, labelled as placed from recorded georeferencing.
  const assetId = page.url().split("/assets/")[1]!.split(/[/#]/)[0]!;
  const content = await page.request.get(`/api/v1/assets/${assetId}/content`);
  expect(createHash("sha256").update(await content.body()).digest("hex")).toBe(createHash("sha256").update(UNPLACED).digest("hex"));
  const tile = page.waitForResponse((r) => r.url().includes(`/assets/${assetId}/tiles/`) && r.url().includes("v=1") && r.status() === 200);
  await nav(page).getByRole("link", { name: "Map & Analysis", exact: true }).click();
  await expect(page.getByRole("list", { name: "Raster layers" }).getByRole("listitem")).toHaveCount(1);
  await tile;
  expect(external).toEqual([]);
});

test("a world file alone does not place a raster; valid raster metadata is never overridden", async ({ page }) => {
  await openProject(page);
  await uploadRasterTab(page, [
    { name: "alone.tif", buffer: UNPLACED },
    { name: "alone.tfw", buffer: Buffer.from(WORLD) },
    { name: "valid.tif", buffer: PLACED },
    { name: "valid.tfw", buffer: Buffer.from(OTHER_WORLD) },
    { name: "orphan.prj", buffer: PRJ },
  ]);
  await expect(queueRow(page, "orphan.prj")).toContainText("Not accepted", { timeout: 30_000 });
  await expect(queueRow(page, "orphan.prj")).toContainText("No raster in this project matches this sidecar's name");

  await expect(queueRow(page, "valid.tfw")).toContainText("Kept for review");
  // The queue is page state, so read both review links before leaving the page.
  const aloneUrl = await queueRow(page, "alone.tfw").getByRole("link", { name: "Review with alone.tif" }).getAttribute("href");
  const validUrl = await queueRow(page, "valid.tfw").getByRole("link", { name: "Review with valid.tif" }).getAttribute("href");
  await page.goto(aloneUrl!);
  const panel = page.getByRole("region", { name: "Sidecar files", exact: true });
  await expect(panel).toContainText("Not enough to place the raster");
  await expect(panel).toContainText("not which coordinate system they are in");
  await expect(panel.getByRole("button", { name: "Check this change" })).toHaveCount(0);
  await expect(page.getByRole("region", { name: "Spatial placement" })).toContainText("Not placed");

  await page.goto(validUrl!);
  await expect(panel).toContainText("The raster's own georeferencing is valid");
  await expect(panel).toContainText("Disagreements with the raster file");
  await expect(panel.getByRole("list", { name: "Sidecar files" }).getByRole("listitem", { name: "valid.tfw" })).toContainText("differs");
  await expect(panel.getByRole("button", { name: "Check this change" })).toHaveCount(0);
  await expect(page.getByRole("region", { name: "Spatial placement" })).toContainText("Placed from the file's metadata");
});
