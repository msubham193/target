import { execFileSync } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";

import { expect, type Page, test } from "@playwright/test";

// Phase 3b walkthrough on the scratch review stack (playwright.ui-review.config.ts):
//   $env:GEOPOC_REVIEW_SPEC = "phase3b.spec.ts"; pnpm exec playwright test --config playwright.ui-review.config.ts
// Inputs: harbour_scene_utm43n.tif and scan_without_crs.tif from the review folder (synthetic test rasters made for
// the review), plus a synthetic 16-bit 4-band pair written by the backend's test helpers (900 added to one block of
// band 4, saturating at 65535 rather than wrapping). No real-world imagery.

const OUT = path.resolve(process.env.GEOPOC_REVIEW_OUT ?? "../../docs/ui-review/screenshots-phase3b");
const FILES = process.env.GEOPOC_REVIEW_FILES ?? "D:\\geopoc-cache\\review";
const NARROW = { width: 390, height: 844 };
const DESKTOP = { width: 1440, height: 900 };
const problems: string[] = [];
const failed: string[] = [];
const external: string[] = [];
const timings: { url: string; ms: number }[] = [];
let counter = 0;

function synthetic(expression: string): Buffer {
  const code =
    "import base64, sys; from tests.test_raster_tools import tif, gradient; " +
    `sys.stdout.write(base64.b64encode(${expression}).decode())`;
  const api = path.resolve(process.cwd(), "../../services/api");
  return Buffer.from(execFileSync("uv", ["run", "--no-sync", "python", "-c", code], { cwd: api, maxBuffer: 256 * 1024 * 1024 }).toString(), "base64");
}

function file(name: string) {
  const full = path.join(FILES, name);
  if (!existsSync(full)) throw new Error(`missing ${full}`);
  return { name, mimeType: "image/tiff", buffer: readFileSync(full) };
}

async function shot(page: Page, name: string, fullPage = true) {
  counter += 1;
  // A full-page capture of a scrolled page draws the sticky header and sidebar at the scroll position.
  if (fullPage) await page.evaluate(() => window.scrollTo(0, 0));
  await page.waitForTimeout(500);
  await page.screenshot({ path: path.join(OUT, `${String(counter).padStart(2, "0")}-${name}.png`), fullPage });
}

const nav = (page: Page) => page.getByRole("navigation", { name: "Main" });

async function openRaster(page: Page, name: string) {
  await nav(page).getByRole("link", { name: "Satellite Image Analysis" }).click();
  await page.getByRole("complementary", { name: "Rasters" }).getByRole("link", { name: new RegExp(`^${name.replace(".", "\\.")}`) }).click();
}

async function clickImage(page: Page, fx: number, fy: number) {
  await page.getByTestId("raster-click-layer").scrollIntoViewIfNeeded();
  const box = (await page.getByTestId("raster-click-layer").boundingBox())!;
  await page.mouse.click(box.x + box.width * fx, box.y + box.height * fy);
}

test("phase 3b raster tools", async ({ page }) => {
  mkdirSync(OUT, { recursive: true });
  const started = new Map<string, number>();
  page.on("console", (m) => {
    if (m.type() === "error" || m.type() === "warning") problems.push(`[${m.type()}] ${m.text()}`);
  });
  page.on("pageerror", (e) => problems.push(`[pageerror] ${e.message}`));
  page.on("requestfailed", (r) => failed.push(`${r.failure()?.errorText ?? "failed"} ${r.method()} ${r.url()}`));
  page.on("request", (r) => {
    started.set(r.url(), Date.now());
    const url = new URL(r.url());
    if (url.protocol.startsWith("http") && url.hostname !== "127.0.0.1") external.push(r.url());
  });
  page.on("response", (r) => {
    if (r.status() >= 400) failed.push(`${r.status()} ${r.request().method()} ${new URL(r.url()).pathname}`);
    if (r.url().includes("/render.png") || r.url().includes("/tiles/")) timings.push({ url: new URL(r.url()).pathname, ms: Date.now() - (started.get(r.url()) ?? Date.now()) });
  });

  await page.goto("/");
  await expect(page.getByRole("list", { name: "Service health" })).toContainText("Background worker: running", { timeout: 90_000 });
  await page.getByLabel("New profile name").fill("Reviewer");
  await page.getByRole("button", { name: "Create profile and continue" }).click();
  await page.getByRole("link", { name: "Create project", exact: true }).click();
  await page.getByLabel("Name").fill("Phase 3b review");
  await page.getByRole("button", { name: "Create project" }).click();
  await nav(page).getByRole("link", { name: "Upload & Ingestion" }).click();
  await page.getByRole("tab", { name: "Satellite / aerial raster (GeoTIFF)" }).click();
  const before = synthetic("tif(gradient(bands=4, height=600, width=900, scale=3), descriptions=['', '', '', 'declared: band 4'])");
  const after = synthetic(
    "tif((lambda d: (d.__setitem__((3, slice(200, 320), slice(300, 450)), (d[3, 200:320, 300:450].astype('int64') + 900).clip(0, 65535).astype('uint16')), d)[1])(gradient(bands=4, height=600, width=900, scale=3)), descriptions=['', '', '', 'declared: band 4'])",
  );
  await page.locator("#asset-files").setInputFiles([
    file("harbour_scene_utm43n.tif"),
    file("scan_without_crs.tif"),
    { name: "synthetic_16bit_before.tif", mimeType: "image/tiff", buffer: before },
    { name: "synthetic_16bit_after.tif", mimeType: "image/tiff", buffer: after },
  ]);
  await expect(page.getByRole("list", { name: "Upload queue" }).getByRole("listitem", { name: "synthetic_16bit_after.tif" })).toContainText("Stored", { timeout: 60_000 });
  await page.waitForTimeout(4000);

  // 1. bands and contrast on a 16-bit, 4-band raster
  await openRaster(page, "synthetic_16bit_after.tif");
  await expect(page.getByRole("list", { name: "Statistics per band" })).toBeVisible({ timeout: 30_000 });
  await shot(page, "satellite-16bit-default-display");
  const controls = page.getByRole("region", { name: "Bands and contrast" });
  await controls.getByLabel("Shown as red").selectOption("4");
  await controls.getByLabel("Contrast stretch").selectOption("p2-98");
  await controls.getByRole("button", { name: "Apply" }).click();
  await expect(page.getByRole("status", { name: "Display in use" })).toContainText("Showing bands 4, 2, 3");
  await shot(page, "satellite-bands-4-2-3-percentile-stretch");

  // 2. pixel values and statistics
  await clickImage(page, 0.42, 0.43);
  await expect(page.getByRole("status", { name: "Pixel values" })).toBeVisible();
  await shot(page, "satellite-pixel-values");

  // 3. measurement: valid on a georeferenced raster, unavailable without georeferencing
  await openRaster(page, "harbour_scene_utm43n.tif");
  await page.getByRole("button", { name: "Measure area" }).click();
  for (const [x, y] of [[0.2, 0.2], [0.6, 0.2], [0.6, 0.7], [0.2, 0.7]] as const) await clickImage(page, x, y);
  await page.getByRole("button", { name: "Measure", exact: true }).click();
  await expect(page.getByRole("status", { name: "Measurement result" })).toContainText("valid");
  await shot(page, "satellite-area-measurement-valid");
  await openRaster(page, "scan_without_crs.tif");
  await page.getByRole("button", { name: "Measure distance" }).click();
  await clickImage(page, 0.2, 0.5);
  await clickImage(page, 0.8, 0.5);
  await page.getByRole("button", { name: "Measure", exact: true }).click();
  await expect(page.getByRole("status", { name: "Measurement result" })).toContainText("No ground measurement");
  await shot(page, "satellite-measurement-unavailable-no-georeferencing");

  // 4. display copy on request
  await openRaster(page, "harbour_scene_utm43n.tif");
  await page.getByRole("region", { name: "Display copy" }).getByRole("button", { name: "Build display copy" }).click();
  await expect(page.getByRole("region", { name: "Display copy" })).toContainText("identical to the original", { timeout: 60_000 });
  await page.getByRole("region", { name: "Display copy" }).scrollIntoViewIfNeeded();
  await shot(page, "satellite-display-copy-verified", false);

  // 5. pixel comparison: refused without georeferencing, computed for aligned ones (harbour_scene shares the synthetic grid)
  await openRaster(page, "synthetic_16bit_after.tif");
  const compare = page.getByRole("region", { name: "Pixel comparison" });
  await compare.getByLabel(/Raster A/).selectOption({ label: "scan_without_crs.tif" });
  await expect(compare).toContainText("These rasters are not on the same grid");
  await compare.scrollIntoViewIfNeeded();
  await shot(page, "comparison-refused-no-georeferencing", false);
  await compare.getByLabel(/Raster A/).selectOption({ label: "synthetic_16bit_before.tif" });
  await compare.getByLabel("Band of A").selectOption("4");
  await compare.getByLabel("Band of B (this raster)").selectOption("4");
  await compare.getByLabel(/Purpose/).fill("Walkthrough: synthetic block added to band 4.");
  await compare.getByRole("button", { name: "Compute B − A" }).click();
  await compare.getByRole("link", { name: "Open the result" }).click({ timeout: 60_000 });
  await expect(page.getByRole("list", { name: "Statistics per band" })).toBeVisible({ timeout: 30_000 });
  await page.waitForTimeout(1500);
  await shot(page, "comparison-result-derived-raster");

  // 6. map: layers with display settings, geodesic measurement, values at a point
  await nav(page).getByRole("link", { name: "Map & Analysis", exact: true }).click();
  await expect(page.getByText("Loading the map…")).toHaveCount(0, { timeout: 30_000 });
  await page.waitForTimeout(2000);
  await page.getByRole("button", { name: "Measure distance" }).click();
  const box = (await page.getByRole("region", { name: "Map of project layers" }).locator("canvas").boundingBox())!;
  await page.mouse.click(box.x + box.width * 0.35, box.y + box.height * 0.5);
  await page.mouse.click(box.x + box.width * 0.65, box.y + box.height * 0.5);
  await page.getByRole("button", { name: "Measure", exact: true }).click();
  await expect(page.getByRole("status", { name: "Measurement result" })).toContainText("valid");
  await shot(page, "map-geodesic-distance", false);
  await page.getByRole("button", { name: "Pin a point" }).click();
  await page.mouse.click(box.x + box.width * 0.5, box.y + box.height * 0.5);
  await expect(page.getByRole("list", { name: "Raster values at the pinned point" })).toBeVisible();
  await shot(page, "map-pinned-point-raster-values");

  await openRaster(page, "synthetic_16bit_after.tif");
  const rasterUrl = page.url(); // at narrow widths the main navigation is behind a menu button, so go by URL
  await page.setViewportSize(NARROW);
  await page.goto(rasterUrl);
  await expect(page.getByRole("list", { name: "Statistics per band" })).toBeVisible({ timeout: 30_000 });
  await page.waitForTimeout(1500);
  await shot(page, "narrow-satellite-tools");
  await page.setViewportSize(DESKTOP);

  writeFileSync(
    path.join(OUT, "..", "phase3b-browser-log.json"),
    JSON.stringify({ captured_at: new Date().toISOString(), screenshots: counter, console_problems: problems, failed_requests: failed, external_requests: external, render_and_tile_ms: timings }, null, 2),
  );
});
