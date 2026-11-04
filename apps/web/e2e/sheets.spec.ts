import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import path from "node:path";

import { expect, type Page, test } from "@playwright/test";

// Map sheets (OSM / ESM): intake, metadata, control points, layer order and alignment, against the real API, database,
// worker and map library. Every file is synthetic and made by the backend's test helpers; nothing here shows that real
// Open Series or Everest Series sheets are supported.
test.describe.configure({ mode: "serial" });

const PROFILE = "E2E Reviewer";
const API_DIR = path.resolve(process.cwd(), "../../services/api"); // tests run from apps/web

function python(expression: string): Buffer {
  const code =
    "import base64, sys; from rasterio.transform import from_origin; from tests import media; " +
    `sys.stdout.write(base64.b64encode(${expression}).decode())`;
  return Buffer.from(execFileSync("uv", ["run", "--no-sync", "python", "-c", code], { cwd: API_DIR }).toString(), "base64");
}

// EPSG:32643, origin (500000, 3000000), 10 m pixels. The imagery overlaps the sheet's south-east quarter.
const SHEET = python('media.geotiff_bytes(crs="EPSG:32643", bands=3, size=(200, 150))');
const IMAGERY = python(
  'media.geotiff_bytes(crs="EPSG:32643", bands=3, size=(100, 100), transform=from_origin(501000, 2999250, 10, 10))',
);
const SCAN = python('media.image_bytes("PNG", size=(400, 300))');

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

test("map sheets are taken in, listed, georeferenced from control points, ordered and compared", async ({ page }) => {
  const external: string[] = [];
  const problems: string[] = [];
  page.on("request", (r) => {
    const url = new URL(r.url());
    if (url.protocol.startsWith("http") && url.hostname !== "127.0.0.1") external.push(r.url());
  });
  page.on("pageerror", (error) => problems.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error") problems.push(message.text());
  });

  await enterWorkspace(page);
  await page.getByRole("link", { name: "Create project", exact: true }).click();
  await page.getByLabel("Name").fill("Sheet survey");
  await page.getByRole("button", { name: "Create project" }).click();

  // --- intake: an OSM GeoTIFF and an ESM scan, each with its declaration ---
  await nav(page).getByRole("link", { name: "Upload & Ingestion" }).click();
  const ingestion = page.getByRole("navigation", { name: "Ingestion" });
  await ingestion.getByRole("link", { name: "Map sheets" }).click();
  await expect(page.getByText(/Everest-56, Polyconic and LCC are names, not definitions/)).toBeVisible();

  await page.getByLabel("Series").selectOption("osm");
  await page.getByLabel("Sheet number or name").fill("NF 43-01");
  await page.getByLabel("Printed scale: 1 :").fill("250000");
  await page.getByLabel("File").setInputFiles({ name: "sheet.tif", mimeType: "image/tiff", buffer: SHEET });
  await page.getByRole("button", { name: "Upload map sheet" }).click();
  const result = page.getByTestId("upload-result");
  await expect(result).toContainText("Stored", { timeout: 30_000 }); // identical content may already be in local storage
  await expect(result).toContainText("Georeferenced GeoTIFF · placed from its own metadata");

  await page.getByLabel("Series").selectOption("esm");
  await page.getByLabel("Sheet number or name").fill("NH 44-1");
  await page.getByLabel("File").setInputFiles({ name: "scan.png", mimeType: "image/png", buffer: SCAN });
  await page.getByRole("button", { name: "Upload map sheet" }).click();
  await expect(page.getByTestId("upload-result")).toContainText("Scanned picture (PNG / JPEG) · not placed on the map", { timeout: 30_000 });
  const sheets = page.getByRole("list", { name: "Map sheets" });
  await expect(sheets.getByLabel("sheet.tif")).toContainText("Placed");
  await expect(sheets.getByLabel("scan.png")).toContainText("Everest Series Map (ESM)");
  await expect(sheets.getByLabel("scan.png")).toContainText("Not placed");

  // The satellite image goes in through the ordinary raster upload.
  await ingestion.getByRole("link", { name: "Upload files" }).click();
  await page.getByRole("tab", { name: "Satellite / aerial raster (GeoTIFF)" }).click();
  await page.locator("#asset-files").setInputFiles({ name: "imagery.tif", mimeType: "image/tiff", buffer: IMAGERY });
  await expect(page.getByRole("list", { name: "Upload queue" }).getByRole("listitem", { name: "imagery.tif" })).toContainText("Stored", {
    timeout: 30_000,
  });

  // --- the unplaced scan says what it lacks, and assigns nothing ---
  await nav(page).getByRole("link", { name: "Map & Analysis", exact: true }).click();
  const excluded = page.getByRole("list", { name: "Files not placed on the map" });
  await expect(excluded).toContainText("scan.pngMap sheet · ESM");
  await expect(excluded).toContainText("No CRS in the file");
  await excluded.getByRole("link", { name: "Use control points…" }).click();

  const sheetPanel = page.locator("#map-sheet");
  await expect(sheetPanel).toContainText("Everest Series Map (ESM)");
  await expect(sheetPanel.getByTestId("fact-datum")).toContainText("Unknown");
  await expect(sheetPanel.getByRole("list", { name: "Unresolved items" })).toContainText("Missing");
  await expect(sheetPanel).toContainText("names, not definitions");
  // A note is recorded but never applied.
  await sheetPanel.getByLabel("What you found").fill("Everest 1956 as printed in the sheet margin");
  await sheetPanel.getByLabel("Where it comes from").fill("Sheet margin text, read by the reviewer");
  await sheetPanel.getByRole("button", { name: "Record note" }).click();
  await expect(sheetPanel.getByTestId("note")).toContainText("not applied");
  await expect(sheetPanel.getByTestId("fact-datum")).toContainText("Unknown");

  // --- control points: four corners of the picture and the printed grid coordinates they stand for ---
  const control = page.locator("#control-points");
  const points = [
    ["0", "0", "500000", "3000000"],
    ["400", "0", "504000", "3000000"],
    ["400", "300", "504000", "2997000"],
    ["0", "300", "500000", "2997000"],
  ];
  for (const [index, [column, row, x, y]] of points.entries()) {
    const n = index + 1;
    await control.getByLabel(`Point ${n} pixel column`).fill(column!);
    await control.getByLabel(`Point ${n} pixel row`).fill(row!);
    await control.getByLabel(`Point ${n} x coordinate`).fill(x!);
    await control.getByLabel(`Point ${n} y coordinate`).fill(y!);
  }
  await control.getByLabel("Where the coordinates come from").fill("Printed 1 km grid ticks");
  await control.getByLabel("Reason").fill("The scan carries no georeferencing.");
  await control.getByPlaceholder("e.g. EPSG:4326").fill("32643");
  await control.getByPlaceholder("e.g. EPSG:4326").press("Enter");
  await control.getByRole("list", { name: "CRS search results" }).getByRole("button", { name: /EPSG:32643/ }).click();
  await control.getByRole("button", { name: "Check the fit" }).click();
  const review = control.getByTestId("control-point-review");
  await expect(review).toContainText("Passes every check", { timeout: 15_000 });
  await expect(review.getByTestId("rms")).toContainText("0.00 px");
  const record = review.getByRole("button", { name: "Record georeferencing" });
  await expect(record).toBeDisabled();
  await review.getByRole("checkbox").check();
  await record.click();
  await expect(control).toContainText("Georeferencing recorded");
  const history = page.getByRole("list", { name: "Georeferencing history" });
  await expect(history).toContainText("CRS: none → EPSG:32643");
  await expect(history).toContainText("Placed: no → yes");

  // --- the scan is now a layer, positioned by control points and drawn from tiles of this version ---
  const tile = page.waitForResponse((r) => /\/assets\/[^/]+\/tiles\/\d+\/\d+\/\d+\.png\?v=1$/.test(r.url()) && r.status() === 200, {
    timeout: 30_000,
  });
  await nav(page).getByRole("link", { name: "Map & Analysis", exact: true }).click();
  const list = page.getByRole("list", { name: "Raster layers" });
  await expect(list).toContainText("scan.png");
  await expect(list).toContainText("Map sheet · ESM · NH 44-1 · Control points supplied by a person");
  await expect(list).toContainText("Map sheet · OSM · NF 43-01 · The file's own CRS and geotransform");
  await expect(list).toContainText("recorded georeferencing, version 1");
  await tile;

  // --- order, opacity and visibility are changed on the real map without errors ---
  const items = list.getByRole("listitem");
  await expect(items).toHaveCount(3);
  await expect(items.first()).toContainText("imagery.tif"); // the layer added last starts on top
  await list.getByRole("button", { name: "Move sheet.tif up" }).click();
  await expect(items.nth(1)).toContainText("sheet.tif");
  await list.getByRole("button", { name: "Move sheet.tif up" }).click();
  await expect(items.first()).toContainText("sheet.tif");
  await list.getByRole("checkbox", { name: /sheet\.tif/ }).uncheck();
  await list.getByRole("checkbox", { name: /sheet\.tif/ }).check();
  await page.waitForTimeout(500);

  // --- alignment inspection of a map sheet over a satellite image ---
  await page.getByLabel("First layer").selectOption({ label: "sheet.tif (map sheet)" });
  await page.getByLabel("Second layer").selectOption({ label: "imagery.tif" });
  const alignment = page.getByTestId("alignment-result");
  await expect(alignment).toBeVisible({ timeout: 15_000 });
  await expect(alignment.getByRole("list", { name: "Findings" })).toContainText("The footprints overlap over");
  await expect(alignment.getByRole("list", { name: "Cautions" })).toContainText("do not show that features coincide");
  await expect(alignment.getByLabel("First layer")).toContainText("Datum shift: exact");

  // --- the original scan is byte-for-byte what was uploaded ---
  const project = page.url().split("/")[4];
  const listed = await page.request.get(`/api/v1/projects/${project}/layers`);
  const rasters = ((await listed.json()) as { rasters: { asset_id: string; title: string }[] }).rasters;
  const scan = rasters.find((r) => r.title === "scan.png")!;
  const original = await page.request.get(`/api/v1/assets/${scan.asset_id}/content`);
  expect(createHash("sha256").update(await original.body()).digest("hex")).toBe(createHash("sha256").update(SCAN).digest("hex"));

  expect(problems.filter((p) => !/favicon|Failed to load resource.*(204|404)/.test(p))).toEqual([]);
  expect(external).toEqual([]);
});
