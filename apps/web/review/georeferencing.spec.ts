import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";

import { expect, type Page, test } from "@playwright/test";

// Walkthrough of recording georeferencing, on the scratch review stack (playwright.ui-review.config.ts):
//   $env:GEOPOC_REVIEW_SPEC = "georeferencing.spec.ts"; pnpm exec playwright test --config playwright.ui-review.config.ts
// Inputs: scan_without_crs.tif from the review folder (no CRS and no geotransform: shown, nothing recorded), and a
// synthetic GeoTIFF written by the backend's test helper with known coordinates and no CRS, on which a CRS is recorded.

function syntheticNoCrs(): Buffer {
  const code =
    "import base64, sys; from tests import media; " +
    "sys.stdout.write(base64.b64encode(media.geotiff_bytes(crs=None, bands=3, size=(300, 200))).decode())";
  const api = path.resolve(process.cwd(), "../../services/api");
  return Buffer.from(execFileSync("uv", ["run", "--no-sync", "python", "-c", code], { cwd: api }).toString(), "base64");
}

const OUT = path.resolve(process.env.GEOPOC_REVIEW_OUT ?? "../../docs/ui-review/screenshots-georeferencing");
const FILES = process.env.GEOPOC_REVIEW_FILES ?? "D:\\geopoc-cache\\review";
const DESKTOP = { width: 1440, height: 900 };
const NARROW = { width: 390, height: 844 };
const problems: string[] = [];
const failed: string[] = [];
const external: string[] = [];
const notes: Record<string, unknown>[] = [];
let counter = 0;

async function shot(page: Page, name: string, fullPage = true) {
  counter += 1;
  await page.waitForTimeout(400);
  await page.screenshot({ path: path.join(OUT, `${String(counter).padStart(2, "0")}-${name}.png`), fullPage });
}

const nav = (page: Page) => page.getByRole("navigation", { name: "Main" });

test("record georeferencing for a raster without a CRS", async ({ page }) => {
  mkdirSync(OUT, { recursive: true });
  page.on("console", (m) => {
    if (m.type() === "error" || m.type() === "warning") problems.push(`[${m.type()}] ${m.text()}`);
  });
  page.on("pageerror", (e) => problems.push(`[pageerror] ${e.message}`));
  page.on("response", (r) => {
    if (r.status() >= 400) failed.push(`${r.status()} ${r.request().method()} ${new URL(r.url()).pathname}`);
  });
  page.on("request", (r) => {
    const url = new URL(r.url());
    if (url.protocol.startsWith("http") && url.hostname !== "127.0.0.1") external.push(r.url());
  });

  const input = path.join(FILES, "scan_without_crs.tif");
  if (!existsSync(input)) throw new Error(`missing ${input}`);
  const bytes = readFileSync(input);

  await page.goto("/");
  await expect(page.getByRole("list", { name: "Service health" })).toContainText("Background worker: running", { timeout: 90_000 });
  await page.getByLabel("New profile name").fill("Reviewer");
  await page.getByRole("button", { name: "Create profile and continue" }).click();
  await page.getByRole("link", { name: "Create project", exact: true }).click();
  await page.getByLabel("Name").fill("Georeferencing review");
  await page.getByRole("button", { name: "Create project" }).click();
  await nav(page).getByRole("link", { name: "Upload & Ingestion" }).click();
  await page.getByRole("tab", { name: "Satellite / aerial raster (GeoTIFF)" }).click();
  const synthetic = syntheticNoCrs();
  await page.locator("#asset-files").setInputFiles([
    { name: "scan_without_crs.tif", mimeType: "image/tiff", buffer: bytes },
    { name: "synthetic_utm_coordinates_no_crs.tif", mimeType: "image/tiff", buffer: synthetic },
  ]);
  await expect(page.getByRole("list", { name: "Upload queue" }).getByRole("listitem", { name: "synthetic_utm_coordinates_no_crs.tif" })).toContainText("Stored", { timeout: 60_000 });
  await page.waitForTimeout(3000);

  await nav(page).getByRole("link", { name: "Map & Analysis", exact: true }).click();
  const notPlaced = page.getByRole("list", { name: "Files not placed on the map" });
  await expect(notPlaced).toContainText("scan_without_crs.tif");
  await shot(page, "map-before-not-placed", false);

  // A raster with neither a CRS nor a geotransform: a CRS alone is not offered, and nothing is invented for it.
  await notPlaced.getByRole("listitem").filter({ hasText: "scan_without_crs.tif" }).getByRole("link", { name: "Record georeferencing…" }).click();
  await expect(page.getByRole("button", { name: "Assign a CRS to the existing coordinates" })).toBeDisabled();
  await shot(page, "no-crs-no-geotransform-crs-alone-not-offered");

  await nav(page).getByRole("link", { name: "Map & Analysis", exact: true }).click();
  await notPlaced.getByRole("listitem").filter({ hasText: "synthetic_utm_coordinates_no_crs.tif" }).getByRole("link", { name: "Record georeferencing…" }).click();
  await expect(page.locator("#georeferencing")).toContainText("Why this raster is not on the map");
  await shot(page, "asset-unplaced-with-file-metadata");

  await page.getByRole("button", { name: "Assign a CRS to the existing coordinates" }).click();
  await page.getByPlaceholder("e.g. EPSG:4326").fill("32643");
  await page.getByPlaceholder("e.g. EPSG:4326").press("Enter");
  await page.getByRole("list", { name: "CRS search results" }).getByRole("button", { name: /EPSG:32643/ }).click();
  await page.getByLabel("Reason (required)").fill("Review walkthrough: CRS taken from the scene's delivery note.");
  await shot(page, "assign-crs-form");
  await page.getByRole("button", { name: "Check this change" }).click();
  const review = page.getByRole("region", { name: "Review the proposed change" });
  await expect(review).toContainText("Passed validation", { timeout: 20_000 });
  notes.push({ checks: await review.getByRole("list", { name: "Validation checks" }).innerText() });
  await review.getByRole("checkbox").check();
  await shot(page, "assign-crs-review-and-confirm");
  await review.getByRole("button", { name: "Record as version 1" }).click();
  await expect(page.getByText("Recorded as version 1")).toBeVisible();
  await shot(page, "assign-crs-recorded-with-history");

  const tile = page.waitForResponse((r) => /\/tiles\/\d+\/\d+\/\d+\.png\?v=1$/.test(r.url()) && r.status() === 200, { timeout: 30_000 });
  await nav(page).getByRole("link", { name: "Map & Analysis", exact: true }).click();
  await tile;
  await page.waitForTimeout(1500);
  await page.getByRole("list", { name: "Raster layers" }).getByRole("button", { name: "Details" }).click();
  await shot(page, "map-after-placed-from-recorded-georeferencing");
  await page.setViewportSize(NARROW);
  await page.waitForTimeout(800);
  await shot(page, "map-after-narrow");
  await page.setViewportSize(DESKTOP);

  // A refused proposal: metres given a geographic CRS.
  await page.getByRole("link", { name: "See the record" }).click();
  await page.getByRole("button", { name: "Assign a CRS to the existing coordinates" }).click();
  await page.getByPlaceholder("e.g. EPSG:4326").fill("EPSG:4326");
  await page.getByPlaceholder("e.g. EPSG:4326").press("Enter");
  await page.getByRole("list", { name: "CRS search results" }).getByRole("button", { name: /EPSG:4326 —/ }).click();
  await page.getByLabel("Reason (required)").fill("Trying a geographic CRS to show the refusal.");
  await page.getByRole("button", { name: "Check this change" }).click();
  await expect(page.getByRole("region", { name: "Review the proposed change" })).toContainText("Did not pass validation", { timeout: 20_000 });
  await shot(page, "refused-geographic-crs-for-metres");
  await page.setViewportSize(NARROW);
  await page.waitForTimeout(500);
  await shot(page, "refused-narrow");
  await page.setViewportSize(DESKTOP);

  const assetId = page.url().split("/assets/")[1]!.split(/[#?/]/)[0]!;
  const original = await page.request.get(`/api/v1/assets/${assetId}/content`);
  const body = await original.body();
  notes.push({
    asset_id: assetId,
    url_at_check: page.url(),
    content_status: original.status(),
    content_bytes: body.length,
    uploaded_bytes: synthetic.length,
    content_sha256: createHash("sha256").update(body).digest("hex"),
    uploaded_sha256: createHash("sha256").update(synthetic).digest("hex"),
  });
  notes.push({
    original_sha256_unchanged:
      createHash("sha256").update(await original.body()).digest("hex") === createHash("sha256").update(synthetic).digest("hex"),
  });
  writeFileSync(
    path.join(OUT, "..", "georeferencing-browser-log.json"),
    JSON.stringify({ captured_at: new Date().toISOString(), screenshots: counter, console_problems: problems, failed_requests: failed, external_requests: external, notes }, null, 2),
  );
});
