import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import path from "node:path";

import { expect, type Page, test } from "@playwright/test";

// Phase 3b non-AI raster tools against the real API, database and worker. Synthetic GeoTIFFs written by the backend's
// test helpers: two 3-band 16-bit rasters on the same EPSG:32643 grid (B = A + 500 in one block of band 2), and one
// raster with neither CRS nor geotransform. This is synthetic validation, not real-world imagery.
test.describe.configure({ mode: "serial" });

const PROFILE = "E2E Reviewer";
const API_DIR = path.resolve(process.cwd(), "../../services/api"); // tests run from apps/web

function raster(expression: string): Buffer {
  const code =
    "import base64, sys; from tests.test_raster_tools import tif, gradient; " +
    `sys.stdout.write(base64.b64encode(${expression}).decode())`;
  return Buffer.from(execFileSync("uv", ["run", "--no-sync", "python", "-c", code], { cwd: API_DIR }).toString(), "base64");
}

const A = raster("tif(gradient(bands=3, height=150, width=200))");
const B = raster("tif((lambda d: (d.__setitem__((1, slice(20, 30), slice(40, 50)), d[1, 20:30, 40:50] + 500), d)[1])(gradient(bands=3, height=150, width=200)))");
const UNPLACED = raster("tif(gradient(bands=1, height=60, width=80), crs=None, transform=None)");

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
  await page.getByRole("list", { name: "Project list" }).getByRole("link", { name: "Raster tools" }).click();
}

async function openRaster(page: Page, name: string) {
  await nav(page).getByRole("link", { name: "Satellite Image Analysis" }).click();
  await page.getByRole("complementary", { name: "Rasters" }).getByRole("link", { name: new RegExp(`^${name.replace(".", "\\.")}`) }).click();
  await expect(page.getByRole("heading", { name: "Satellite Image Analysis", level: 1 })).toBeVisible();
}

async function clickImage(page: Page, fx: number, fy: number) {
  // A mouse click lands where the pointer is in the viewport, so bring the image into view first.
  await page.getByTestId("raster-click-layer").scrollIntoViewIfNeeded();
  const box = (await page.getByTestId("raster-click-layer").boundingBox())!;
  await page.mouse.click(box.x + box.width * fx, box.y + box.height * fy);
}

test("bands, contrast, statistics and pixel values work on a stored raster without changing it", async ({ page }) => {
  const external: string[] = [];
  page.on("request", (r) => {
    const url = new URL(r.url());
    if (url.protocol.startsWith("http") && url.hostname !== "127.0.0.1") external.push(r.url());
  });
  await enterWorkspace(page);
  await page.getByRole("link", { name: "Create project", exact: true }).click();
  await page.getByLabel("Name").fill("Raster tools");
  await page.getByRole("button", { name: "Create project" }).click();
  await nav(page).getByRole("link", { name: "Upload & Ingestion" }).click();
  await page.getByRole("tab", { name: "Satellite / aerial raster (GeoTIFF)" }).click();
  await page.locator("#asset-files").setInputFiles([
    { name: "before.tif", mimeType: "image/tiff", buffer: A },
    { name: "after.tif", mimeType: "image/tiff", buffer: B },
    { name: "unplaced.tif", mimeType: "image/tiff", buffer: UNPLACED },
  ]);
  await expect(queueRow(page, "unplaced.tif")).toContainText("Stored", { timeout: 30_000 });

  await openRaster(page, "after.tif");
  // Statistics are computed in the background after upload.
  await expect(page.getByRole("list", { name: "Statistics per band" })).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText("Every pixel was read.")).toBeVisible();

  const controls = page.getByRole("region", { name: "Bands and contrast" });
  await controls.getByLabel("Shown as red").selectOption("3");
  await controls.getByLabel("Shown as blue").selectOption("1");
  await controls.getByLabel("Contrast stretch").selectOption("p2-98");
  const rendered = page.waitForResponse((r) => r.url().includes("/render.png") && r.url().includes("bands=3%2C2%2C1") && r.url().includes("stretch=p2-98"));
  await controls.getByRole("button", { name: "Apply" }).click();
  expect((await rendered).status()).toBe(200);
  await expect(page.getByRole("status", { name: "Display in use" })).toContainText("Showing bands 3, 2, 1: stretched between the 2 and 98 percentiles");

  // Pixel values: the centre of the block that changed in band 2 (column 45, row 25 of 200 × 150).
  await clickImage(page, 45.5 / 200, 25.5 / 150);
  const values = page.getByRole("status", { name: "Pixel values" });
  await expect(values).toContainText("Pixel column 45, row 25");
  const expected = 25 * 200 + 45; // gradient: row * width + col, plus 1000 per band index
  await expect(values).toContainText(String(expected + 1000 + 500));
  await expect(values).toContainText("° N");

  const download = await page.request.get(`/api/v1/assets/${page.url().split("/assets/")[1]!.split("/")[0]}/content`);
  expect(createHash("sha256").update(await download.body()).digest("hex")).toBe(createHash("sha256").update(B).digest("hex"));
  expect(external).toEqual([]);
});

test("distances are measured in ground units only when the georeferencing supports it", async ({ page }) => {
  await openProject(page);
  await openRaster(page, "after.tif");
  await page.getByRole("button", { name: "Measure distance" }).click();
  await clickImage(page, 0.1, 0.5);
  await clickImage(page, 0.6, 0.5); // 100 pixels of 10 m along a row
  await page.getByRole("button", { name: "Measure", exact: true }).click();
  const result = page.getByRole("status", { name: "Measurement result" });
  await expect(result).toContainText("valid");
  await expect(result).toContainText("geodesic on the WGS 84 ellipsoid");
  await expect(result).toContainText("Planar: 1000.0 m");

  await openRaster(page, "unplaced.tif");
  await page.getByRole("button", { name: "Measure distance" }).click();
  await clickImage(page, 0.1, 0.5);
  await clickImage(page, 0.9, 0.5);
  await page.getByRole("button", { name: "Measure", exact: true }).click();
  await expect(page.getByRole("status", { name: "Measurement result" })).toContainText("No ground measurement");
  await expect(page.getByRole("status", { name: "Measurement result" })).toContainText("not ground units");
});

test("a pixel comparison of aligned rasters becomes a derived raster with its lineage; misaligned ones are refused", async ({ page }) => {
  await openProject(page);
  await openRaster(page, "after.tif");
  const panel = page.getByRole("region", { name: "Pixel comparison" });
  await panel.getByLabel(/Raster A/).selectOption({ label: "unplaced.tif" });
  await expect(panel).toContainText("These rasters are not on the same grid");
  await expect(panel.getByRole("button", { name: "Compute B − A" })).toHaveCount(0);

  await panel.getByLabel(/Raster A/).selectOption({ label: "before.tif" });
  await expect(panel).toContainText("Same grid: same CRS, geotransform and size");
  await panel.getByLabel("Band of A").selectOption("2");
  await panel.getByLabel("Band of B (this raster)").selectOption("2");
  await panel.getByRole("button", { name: "Compute B − A" }).click();
  await panel.getByRole("link", { name: "Open the result" }).click({ timeout: 30_000 });

  await expect(page.getByRole("region", { name: "Derived raster" })).toContainText("B - A (pixel difference, not change interpretation)");
  await expect(page.getByRole("list", { name: "Statistics per band" })).toBeVisible({ timeout: 30_000 });
  await clickImage(page, 45.5 / 200, 25.5 / 150);
  await expect(page.getByRole("status", { name: "Pixel values" })).toContainText("500");
  await clickImage(page, 5.5 / 200, 5.5 / 150);
  await expect(page.getByRole("status", { name: "Pixel values" })).toContainText("column 5, row 5");
});

test("a display copy is built and verified on request, and the map measures geodesically", async ({ page }) => {
  await openProject(page);
  await openRaster(page, "before.tif");
  const copy = page.getByRole("region", { name: "Display copy" });
  await copy.getByRole("button", { name: "Build display copy" }).click();
  await expect(copy).toContainText("full-resolution windows identical to the original", { timeout: 30_000 });

  await nav(page).getByRole("link", { name: "Map & Analysis", exact: true }).click();
  await expect(page.getByRole("list", { name: "Raster layers" }).getByRole("listitem")).toHaveCount(3); // A, B, B − A
  await page.getByRole("button", { name: "Measure distance" }).click();
  await expect(page.getByText("Loading the map…")).toHaveCount(0, { timeout: 15_000 }); // clicks before then are not taken
  await page.getByRole("region", { name: "Map of project layers" }).scrollIntoViewIfNeeded();
  const box = (await page.getByRole("region", { name: "Map of project layers" }).locator("canvas").boundingBox())!;
  await page.mouse.click(box.x + box.width * 0.4, box.y + box.height * 0.5);
  await page.mouse.click(box.x + box.width * 0.6, box.y + box.height * 0.5);
  await page.getByRole("button", { name: "Measure", exact: true }).click();
  const result = page.getByRole("status", { name: "Measurement result" });
  await expect(result).toContainText("valid");
  await expect(result).toContainText("WGS 84 ellipsoid");
  await expect(result).toContainText(" m");
});
