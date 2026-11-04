import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import path from "node:path";

import { expect, type Page, test } from "@playwright/test";

// Recording georeferencing for rasters whose own metadata is incomplete, against the real API, database and worker.
// The rasters are synthetic GeoTIFFs written by the backend's test helper with known coordinates (EPSG:32643,
// origin 500000, 3000000, 10 m pixels). This is synthetic validation, not real-world imagery.
test.describe.configure({ mode: "serial" });

const PROFILE = "E2E Reviewer";
const API_DIR = path.resolve(process.cwd(), "../../services/api"); // tests run from apps/web

function geotiff(args: string): Buffer {
  const code =
    "import base64, sys; from tests import media; " +
    `sys.stdout.write(base64.b64encode(media.geotiff_bytes(${args})).decode())`;
  return Buffer.from(execFileSync("uv", ["run", "--no-sync", "python", "-c", code], { cwd: API_DIR }).toString(), "base64");
}

const NO_CRS = geotiff("crs=None, bands=3, size=(200, 150)");
const NO_TRANSFORM = geotiff("transform=None, bands=3, size=(200, 150)");

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
  await page.getByRole("list", { name: "Project list" }).getByRole("link", { name: "Georeferencing" }).click();
}

test("a CRS is assigned to existing coordinates after review, and the raster appears on the map", async ({ page }) => {
  const external: string[] = [];
  page.on("request", (r) => {
    const url = new URL(r.url());
    if (url.protocol.startsWith("http") && url.hostname !== "127.0.0.1") external.push(r.url());
  });
  await enterWorkspace(page);
  await page.getByRole("link", { name: "Create project", exact: true }).click();
  await page.getByLabel("Name").fill("Georeferencing");
  await page.getByRole("button", { name: "Create project" }).click();
  await nav(page).getByRole("link", { name: "Upload & Ingestion" }).click();
  await page.getByRole("tab", { name: "Satellite / aerial raster (GeoTIFF)" }).click();
  await page.locator("#asset-files").setInputFiles([
    { name: "no_crs.tif", mimeType: "image/tiff", buffer: NO_CRS },
    { name: "no_transform.tif", mimeType: "image/tiff", buffer: NO_TRANSFORM },
  ]);
  await expect(queueRow(page, "no_transform.tif")).toContainText("Stored", { timeout: 30_000 });

  // Before: neither raster is placed, and each says why.
  await nav(page).getByRole("link", { name: "Map & Analysis", exact: true }).click();
  const excluded = page.getByRole("list", { name: "Files not placed on the map" });
  await expect(excluded).toContainText("no_crs.tifNo CRS in the file");
  await expect(excluded).toContainText("no_transform.tifNo geotransform");
  await expect(page.getByText("No georeferenced rasters in this project.")).toBeVisible();
  await excluded.getByRole("listitem").filter({ hasText: "no_crs.tif" }).getByRole("link", { name: "Record georeferencing…" }).click();

  const panel = page.locator("#georeferencing");
  await expect(panel).toContainText("Why this raster is not on the map");
  await expect(page.getByRole("region", { name: "What the file declares" })).toContainText("500000, 10, 0, 3000000, 0, -10");
  await page.getByRole("button", { name: "Assign a CRS to the existing coordinates" }).click();
  await expect(page.getByText(/are kept exactly; no pixel is moved/)).toBeVisible();
  await page.getByPlaceholder("e.g. EPSG:4326").fill("32643");
  await page.getByPlaceholder("e.g. EPSG:4326").press("Enter");
  await page.getByRole("list", { name: "CRS search results" }).getByRole("button", { name: /EPSG:32643/ }).click();
  await page.getByLabel("Reason (required)").fill("CRS stated in the supplier's delivery note.");
  await page.getByRole("button", { name: "Check this change" }).click();

  const review = page.getByRole("region", { name: "Review the proposed change" });
  await expect(review).toContainText("Passed validation", { timeout: 15_000 });
  await expect(review.getByRole("list", { name: "Validation checks" })).toContainText("A map tile at zoom");
  await expect(review).toContainText("500000.000, 3000000.000"); // the top-left corner, unchanged
  await expect(review).toContainText("75.000000, 27.122470"); // independently: EPSG:32643 (500000, 3000000)
  const record = review.getByRole("button", { name: "Record as version 1" });
  await expect(record).toBeDisabled();
  await review.getByRole("checkbox").check();
  await record.click();
  await expect(page.getByText("Recorded as version 1")).toBeVisible();
  const history = page.getByRole("list", { name: "Georeferencing history" });
  await expect(history).toContainText("CRS: none → EPSG:32643");
  await expect(history).toContainText("Placed: no → yes");
  await expect(history).toContainText(PROFILE);

  // After: placed, labelled as recorded georeferencing, drawn from tiles of this version.
  const tile = page.waitForResponse((r) => /\/tiles\/\d+\/\d+\/\d+\.png\?v=1$/.test(r.url()) && r.status() === 200, {
    timeout: 30_000,
  });
  await nav(page).getByRole("link", { name: "Map & Analysis", exact: true }).click();
  await expect(page.getByRole("list", { name: "Raster layers" })).toContainText("recorded georeferencing, version 1");
  await tile;

  // The original is byte-for-byte what was uploaded.
  const layer = await page.request.get(`${new URL(page.url()).origin}/api/v1/projects/${page.url().split("/")[4]}/layers`);
  const assetId = ((await layer.json()) as { rasters: { asset_id: string }[] }).rasters[0]!.asset_id;
  const original = await page.request.get(`/api/v1/assets/${assetId}/content`);
  expect(createHash("sha256").update(await original.body()).digest("hex")).toBe(createHash("sha256").update(NO_CRS).digest("hex"));
  expect(external).toEqual([]);
});

test("a raster with a CRS but no geotransform needs entered coordinates; a CRS alone is not offered", async ({ page }) => {
  await openProject(page);
  await nav(page).getByRole("link", { name: "Map & Analysis", exact: true }).click();
  await page
    .getByRole("list", { name: "Files not placed on the map" })
    .getByRole("listitem")
    .filter({ hasText: "no_transform.tif" })
    .getByRole("link", { name: "Record georeferencing…" })
    .click();
  await expect(page.getByRole("button", { name: "Assign a CRS to the existing coordinates" })).toBeDisabled();
  await expect(page.getByText(/a CRS alone cannot place it/)).toBeVisible();
  await page.getByRole("button", { name: "Enter a geotransform" }).click();
  await page.getByLabel("World file (.tfw, .wld)").check();
  // A world file gives the centre of the top-left pixel.
  await page.getByLabel(/World file contents/).fill("10\n0\n0\n-10\n500005\n2999995\n");
  await page.getByLabel("Where these numbers come from (required)").fill("no_transform.tfw supplied with the scene");
  await page.getByLabel("Reason (required)").fill("The world file was delivered separately.");
  await page.getByRole("button", { name: "Check this change" }).click();
  const review = page.getByRole("region", { name: "Review the proposed change" });
  await expect(review).toContainText("Passed validation", { timeout: 15_000 });
  await expect(review).toContainText("500000, 10, 0, 3000000, 0, -10"); // converted to the corner
  await review.getByRole("checkbox").check();
  await review.getByRole("button", { name: "Record as version 1" }).click();
  await expect(page.getByText("Recorded as version 1")).toBeVisible();
  await expect(page.getByRole("list", { name: "Georeferencing history" })).toContainText("Geotransform read from a world file");

  await nav(page).getByRole("link", { name: "Map & Analysis", exact: true }).click();
  await expect(page.getByRole("list", { name: "Raster layers" }).getByRole("listitem")).toHaveCount(2);
});

test("invalid coordinates are refused, and reverting restores the file's state", async ({ page }) => {
  await openProject(page);
  await nav(page).getByRole("link", { name: "Map & Analysis", exact: true }).click();
  await page.getByRole("list", { name: "Raster layers" }).getByRole("listitem").filter({ hasText: "no_crs.tif" }).getByRole("button", { name: "Details" }).click();
  await page.getByRole("link", { name: "See the record" }).click();

  // Metres given a geographic CRS: refused, nothing recorded.
  await page.getByRole("button", { name: "Assign a CRS to the existing coordinates" }).click();
  await page.getByPlaceholder("e.g. EPSG:4326").fill("EPSG:4326");
  await page.getByPlaceholder("e.g. EPSG:4326").press("Enter");
  await page.getByRole("list", { name: "CRS search results" }).getByRole("button", { name: /EPSG:4326 —/ }).click();
  await page.getByLabel("Reason (required)").fill("Trying the geographic CRS.");
  await page.getByRole("button", { name: "Check this change" }).click();
  const review = page.getByRole("region", { name: "Review the proposed change" });
  await expect(review).toContainText("Did not pass validation", { timeout: 15_000 });
  await expect(review).toContainText("fall outside the valid range");
  await expect(review.getByRole("button", { name: /Record as version/ })).toHaveCount(0);

  await page.getByRole("button", { name: "Use the file's own metadata again" }).click();
  await page.getByLabel("Reason (required)").fill("The delivery note was for another scene.");
  await page.getByRole("button", { name: "Check this change" }).click();
  await page.getByRole("region", { name: "Review the proposed change" }).getByRole("checkbox").check();
  await page.getByRole("button", { name: "Record as version 2" }).click();
  await expect(page.getByText("Recorded as version 2")).toBeVisible();
  await expect(page.getByText("Why this raster is not on the map")).toBeVisible();
  await expect(page.getByRole("list", { name: "Georeferencing history" }).getByRole("listitem")).toHaveCount(2);

  await nav(page).getByRole("link", { name: "Map & Analysis", exact: true }).click();
  await expect(page.getByRole("list", { name: "Files not placed on the map" })).toContainText("no_crs.tif");
  await expect(page.getByRole("list", { name: "Raster layers" }).getByRole("listitem")).toHaveCount(1);
});
