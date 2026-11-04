import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";

import { expect, type Page, test } from "@playwright/test";

// Phase 4 walkthrough on the scratch review stack (playwright.ui-review.config.ts), with the real detector:
//   $env:GEOPOC_REVIEW_SPEC = "phase4.spec.ts"; pnpm exec playwright test --config playwright.ui-review.config.ts
// Saves screenshots and a log (console problems, failed requests, tile timings) for a person to look at.

const OUT = path.resolve(process.env.GEOPOC_REVIEW_OUT ?? "../../docs/ui-review/screenshots-phase4");
const FILES = process.env.GEOPOC_REVIEW_FILES ?? "D:\\geopoc-cache\\review";
const DESKTOP = { width: 1440, height: 900 };
const NARROW = { width: 390, height: 844 };

const consoleProblems: string[] = [];
const failedRequests: string[] = [];
const external: string[] = [];
const tiles: { url: string; status: number; ms: number }[] = [];
const notes: Record<string, unknown>[] = [];
let counter = 0;

function file(name: string) {
  const full = path.join(FILES, name);
  if (!existsSync(full)) throw new Error(`review input file missing: ${full}`);
  return { name, mimeType: "application/octet-stream", buffer: readFileSync(full) };
}

async function shot(page: Page, name: string, fullPage = true) {
  counter += 1;
  await page.waitForTimeout(400);
  await page.screenshot({ path: path.join(OUT, `${String(counter).padStart(2, "0")}-${name}.png`), fullPage });
}

function watch(page: Page) {
  const started = new Map<string, number>();
  page.on("console", (m) => {
    if (m.type() === "error" || m.type() === "warning") consoleProblems.push(`[${m.type()}] ${new URL(page.url()).pathname} :: ${m.text()}`);
  });
  page.on("pageerror", (e) => consoleProblems.push(`[pageerror] ${e.message}`));
  page.on("request", (r) => {
    started.set(r.url(), Date.now());
    const url = new URL(r.url());
    if (url.protocol.startsWith("http") && url.hostname !== "127.0.0.1") external.push(r.url());
  });
  page.on("response", (r) => {
    if (r.status() >= 400) failedRequests.push(`${r.status()} ${r.request().method()} ${new URL(r.url()).pathname}`);
    if (r.url().includes("/tiles/")) tiles.push({ url: new URL(r.url()).pathname, status: r.status(), ms: Date.now() - (started.get(r.url()) ?? Date.now()) });
  });
  page.on("requestfailed", (r) => failedRequests.push(`FAILED ${r.method()} ${r.url()}`));
}

const nav = (page: Page) => page.getByRole("navigation", { name: "Main" });
const queueRow = (page: Page, name: string) => page.getByRole("list", { name: "Upload queue" }).getByRole("listitem", { name, exact: true });

async function clickMapCentre(page: Page, dx = 0, dy = 0) {
  const box = (await page.getByRole("region", { name: "Map of project layers" }).locator("canvas").boundingBox())!;
  await page.mouse.move(box.x + box.width / 2 + dx - 3, box.y + box.height / 2 + dy);
  await page.mouse.click(box.x + box.width / 2 + dx, box.y + box.height / 2 + dy);
}

test("phase 4: map, review, conflicts and exports with real inputs", async ({ page }) => {
  mkdirSync(OUT, { recursive: true });
  watch(page);

  await page.goto("/");
  await expect(page.getByRole("list", { name: "Service health" })).toContainText("Background worker: running", { timeout: 90_000 });
  await page.getByLabel("New profile name").fill("Reviewer");
  await page.getByRole("button", { name: "Create profile and continue" }).click();

  await page.getByRole("link", { name: "Create project", exact: true }).click();
  await page.getByLabel("Name").fill("Phase 4 review");
  await page.getByLabel("Add an area of interest").check();
  await page.getByLabel("X (easting / longitude)").fill("500450");
  await page.getByLabel("Y (northing / latitude)").fill("2997000");
  const search = page.getByPlaceholder("e.g. EPSG:4326");
  await search.fill("32643");
  await search.press("Enter");
  await page.getByRole("list", { name: "CRS search results" }).getByRole("button", { name: /EPSG:32643/ }).click();
  await page.getByRole("button", { name: "Create project" }).click();
  await expect(page.getByRole("heading", { name: "Phase 4 review", level: 1 })).toBeVisible();

  await nav(page).getByRole("link", { name: "Upload & Ingestion" }).click();
  await page.getByRole("tab", { name: "Satellite / aerial raster (GeoTIFF)" }).click();
  await page.locator("#asset-files").setInputFiles([file("harbour_scene_utm43n.tif"), file("scan_without_crs.tif")]);
  await expect(queueRow(page, "scan_without_crs.tif")).toContainText("Stored", { timeout: 60_000 });
  await page.getByRole("tab", { name: "Ground photographs" }).click();
  const photo = file("astronaut.png");
  await page.locator("#asset-files").setInputFiles([photo, { ...photo, name: "astronaut copy.png" }]);
  await expect(queueRow(page, "astronaut copy.png")).toContainText("Duplicate", { timeout: 60_000 });

  // --- map -------------------------------------------------------------------------------------------------
  await page.waitForTimeout(3000); // previews (the raster's display stretch comes from its preview)
  await nav(page).getByRole("link", { name: "Map & Analysis", exact: true }).click();
  await expect(page.getByRole("list", { name: "Raster layers" })).toContainText("harbour_scene_utm43n.tif");
  await expect.poll(() => tiles.filter((t) => t.status === 200).length, { timeout: 30_000 }).toBeGreaterThan(0);
  await page.waitForTimeout(1500);
  await shot(page, "map-fitted-desktop", false);
  await clickMapCentre(page);
  await expect(page.getByRole("status", { name: "Pinned point" })).toContainText("metre", { timeout: 15_000 });
  notes.push({ pinned_point: await page.getByRole("status", { name: "Pinned point" }).textContent() });
  await page.getByRole("button", { name: "Details" }).click();
  await shot(page, "map-pinned-point-and-details");
  await page.getByRole("list", { name: "Raster layers" }).getByLabel("Opacity").fill("0.4");
  await page.getByRole("button", { name: "Zoom in" }).click();
  await page.getByRole("button", { name: "Zoom in" }).click();
  await page.waitForTimeout(2500);
  await shot(page, "map-zoomed-opacity-40", false);
  await page.setViewportSize(NARROW);
  await page.waitForTimeout(800);
  await shot(page, "map-narrow");
  await page.setViewportSize(DESKTOP);

  // --- ground analysis with the real model, twice: identical outputs become possible-duplicate links ------------
  await nav(page).getByRole("link", { name: "Ground Image Analysis" }).click();
  await page.getByRole("link", { name: /^astronaut\.png/ }).first().click();
  await expect(page.getByText("This photograph has not been analysed yet")).toBeVisible({ timeout: 30_000 });
  await page.getByRole("button", { name: "Run analysis" }).click();
  await expect(page.getByTestId("detection-box").first()).toBeVisible({ timeout: 180_000 });
  const findings = page.getByRole("table", { name: "Model findings in this photograph" });
  await findings.getByRole("row").nth(1).click();
  await expect(page.getByRole("group", { name: "Review actions" })).toBeVisible();
  await shot(page, "ground-finding-review-actions");
  await page.getByRole("group", { name: "Review actions" }).getByRole("button", { name: "Confirm" }).click();
  await page.getByLabel("Note (optional)").fill("Person clearly visible in the centre of the photograph.");
  await shot(page, "ground-confirm-form", false);
  await page.getByRole("button", { name: "Save: Confirm" }).click();
  await expect(page.getByText("Confirm saved as revision 1.")).toBeVisible();
  await shot(page, "ground-confirmed-with-history");

  await findings.getByRole("row").nth(2).click();
  await page.getByRole("group", { name: "Review actions" }).getByRole("button", { name: "Reject" }).click();
  await page.getByLabel("Reason (required)").fill("Second box is part of the same person, not a second one.");
  await page.getByRole("button", { name: "Save: Reject" }).click();
  await expect(page.getByText("Reject saved as revision 1.")).toBeVisible();

  // The copy of the photograph, analysed with the same model: same content, so candidate links, not new evidence.
  await page.getByRole("link", { name: /astronaut copy\.png/ }).first().click();
  await page.getByRole("button", { name: "Run analysis" }).click();
  await expect(page.getByTestId("detection-box").first()).toBeVisible({ timeout: 180_000 });

  // --- observations ------------------------------------------------------------------------------------------
  await nav(page).getByRole("link", { name: "Observations", exact: true }).click();
  const table = page.getByRole("table", { name: "Observations in this project" });
  await expect(table).toBeVisible();
  await shot(page, "observations-with-review-states");
  await table.getByRole("row").filter({ hasText: "astronaut copy.png" }).first().click();
  await expect(page.getByText(/Possible duplicate of/)).toBeVisible();
  await shot(page, "observations-possible-duplicate");
  await page.getByRole("group", { name: "Review actions" }).getByRole("button", { name: "Correct the category" }).click();
  await page.getByLabel("Correct category").fill("astronaut");
  await page.getByLabel("Reason (required)").fill("More specific than the model's class.");
  await page.getByRole("button", { name: "Save: Correct the category" }).click();
  await expect(page.getByText("Correct the category saved as revision 1.")).toBeVisible();
  await shot(page, "observations-relabelled");
  await page.setViewportSize(NARROW);
  await page.waitForTimeout(500);
  await shot(page, "observations-narrow");
  await page.setViewportSize(DESKTOP);

  // --- exports ----------------------------------------------------------------------------------------------
  await nav(page).getByRole("link", { name: "Reports", exact: true }).click();
  for (const format of ["CSV", "GEOJSON", "JSON"]) {
    const download = page.waitForEvent("download");
    await page.getByRole("button", { name: `Download ${format}` }).click();
    const saved = await download;
    const target = path.join(OUT, "..", `phase4-export.${format.toLowerCase()}`);
    await saved.saveAs(target);
    notes.push({ export: format, file: saved.suggestedFilename(), bytes: readFileSync(target).length });
  }
  await shot(page, "reports-exports");

  // --- satellite screen link ---------------------------------------------------------------------------------
  await nav(page).getByRole("link", { name: "Satellite Image Analysis" }).click();
  await page.getByRole("link", { name: /harbour_scene_utm43n\.tif/ }).first().click();
  await expect(page.getByText("Drawn on the Map & Analysis View")).toBeVisible();
  await shot(page, "satellite-on-the-map");

  const okTiles = tiles.filter((t) => t.status === 200).map((t) => t.ms).sort((a, b) => a - b);
  writeFileSync(
    path.join(OUT, "..", "phase4-browser-log.json"),
    JSON.stringify(
      {
        captured_at: new Date().toISOString(),
        screenshots: counter,
        console_problems: consoleProblems,
        failed_requests: failedRequests,
        external_requests: external,
        tiles: {
          requested: tiles.length,
          ok: okTiles.length,
          empty_204: tiles.filter((t) => t.status === 204).length,
          ms_median: okTiles[Math.floor(okTiles.length / 2)] ?? null,
          ms_max: okTiles[okTiles.length - 1] ?? null,
        },
        notes,
      },
      null,
      2,
    ),
  );
});
