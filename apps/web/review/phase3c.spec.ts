import { execFileSync } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";

import { expect, type Page, test } from "@playwright/test";

// Sidecar metadata walkthrough on the scratch review stack (playwright.ui-review.config.ts):
//   $env:GEOPOC_REVIEW_SPEC = "phase3c.spec.ts"; pnpm exec playwright test --config playwright.ui-review.config.ts
// Inputs are synthetic and written here: unreferenced and georeferenced rasters, world files, .prj and .aux.xml.

const OUT = path.resolve(process.env.GEOPOC_REVIEW_OUT ?? "../../docs/ui-review/screenshots-sidecars");
const problems: string[] = [];
const failed: string[] = [];
const external: string[] = [];
let counter = 0;

function python(expression: string): Buffer {
  const code =
    "import base64, sys; from pyproj import CRS; from pyproj.enums import WktVersion; " +
    "from tests.test_raster_tools import tif, gradient; " +
    `sys.stdout.write(base64.b64encode(${expression}).decode())`;
  const api = path.resolve(process.cwd(), "../../services/api");
  return Buffer.from(execFileSync("uv", ["run", "--no-sync", "python", "-c", code], { cwd: api, maxBuffer: 64 * 1024 * 1024 }).toString(), "base64");
}

const UNPLACED = python("tif(gradient(bands=3, height=600, width=900, scale=3), crs=None, transform=None)");
const PLACED = python("tif(gradient(bands=3, height=600, width=900, scale=3))");
const PRJ = python("CRS('EPSG:32643').to_wkt(WktVersion.WKT1_ESRI).encode()");
const PRJ_LCC = Buffer.from(
  'PROJCS["Custom_LCC",GEOGCS["GCS_WGS_1984",DATUM["D_WGS_1984",SPHEROID["WGS_1984",6378137.0,298.257223563]],PRIMEM["Greenwich",0.0],' +
    'UNIT["Degree",0.0174532925199433]],PROJECTION["Lambert_Conformal_Conic"],PARAMETER["False_Easting",0.0],PARAMETER["False_Northing",0.0],' +
    'PARAMETER["Central_Meridian",80.0],PARAMETER["Standard_Parallel_1",20.0],PARAMETER["Standard_Parallel_2",30.0],PARAMETER["Latitude_Of_Origin",25.0],UNIT["Meter",1.0]]',
);
const WORLD = "10\n0\n0\n-10\n500005\n2999995\n";
const AUX = Buffer.from(
  "<PAMDataset><GeoTransform>600000, 10, 0, 3000000, 0, -10</GeoTransform>" +
    '<Metadata domain="RPC"><MDI key="LINE_OFF">1</MDI></Metadata><GCPList><GCP Id="1" Pixel="0" Line="0" X="1" Y="2"/></GCPList>' +
    '<PAMRasterBand band="1"><Description>red, as supplied</Description><NoDataValue>0</NoDataValue><UnitType>m</UnitType></PAMRasterBand>' +
    "</PAMDataset>",
);

async function shot(page: Page, name: string, fullPage = true) {
  counter += 1;
  if (fullPage) await page.evaluate(() => window.scrollTo(0, 0));
  await page.waitForTimeout(500);
  await page.screenshot({ path: path.join(OUT, `${String(counter).padStart(2, "0")}-${name}.png`), fullPage });
}

const nav = (page: Page) => page.getByRole("navigation", { name: "Main" });
const queueRow = (page: Page, name: string) => page.getByRole("list", { name: "Upload queue" }).getByRole("listitem", { name, exact: true });
const panel = (page: Page) => page.getByRole("region", { name: "Sidecar files", exact: true });

test("sidecar metadata review", async ({ page }) => {
  mkdirSync(OUT, { recursive: true });
  page.on("console", (m) => {
    if (m.type() === "error" || m.type() === "warning") problems.push(`[${m.type()}] ${m.text()}`);
  });
  page.on("pageerror", (e) => problems.push(`[pageerror] ${e.message}`));
  page.on("requestfailed", (r) => failed.push(`${r.failure()?.errorText ?? "failed"} ${r.method()} ${r.url()}`));
  page.on("request", (r) => {
    const url = new URL(r.url());
    if (url.protocol.startsWith("http") && url.hostname !== "127.0.0.1") external.push(r.url());
  });
  page.on("response", (r) => {
    if (r.status() >= 400) failed.push(`${r.status()} ${r.request().method()} ${new URL(r.url()).pathname}`);
  });

  await page.goto("/");
  await expect(page.getByRole("list", { name: "Service health" })).toContainText("Background worker: running", { timeout: 90_000 });
  await page.getByLabel("New profile name").fill("Reviewer");
  await page.getByRole("button", { name: "Create profile and continue" }).click();
  await page.getByRole("link", { name: "Create project", exact: true }).click();
  await page.getByLabel("Name").fill("Sidecar review");
  await page.getByRole("button", { name: "Create project" }).click();
  await nav(page).getByRole("link", { name: "Upload & Ingestion" }).click();
  await page.getByRole("tab", { name: "Satellite / aerial raster (GeoTIFF)" }).click();
  const file = (name: string, buffer: Buffer) => ({ name, mimeType: "application/octet-stream", buffer });
  await page.locator("#asset-files").setInputFiles([
    file("scan.tif", UNPLACED),
    file("scan.tfw", Buffer.from(WORLD)),
    file("scan.prj", PRJ),
    file("lcc.tif", UNPLACED),
    file("lcc.tfw", Buffer.from(WORLD)),
    file("lcc.prj", PRJ_LCC),
    file("ortho.tif", PLACED),
    file("ortho.tif.aux.xml", AUX),
    file("stray.prj", PRJ),
  ]);
  await expect(queueRow(page, "stray.prj")).toContainText("Not accepted", { timeout: 60_000 });
  await shot(page, "upload-queue-with-sidecars");
  const scanUrl = await queueRow(page, "scan.tfw").getByRole("link", { name: "Review with scan.tif" }).getAttribute("href");
  const lccUrl = await queueRow(page, "lcc.prj").getByRole("link", { name: "Review with lcc.tif" }).getAttribute("href");
  const orthoUrl = await queueRow(page, "ortho.tif.aux.xml").getByRole("link", { name: "Review with ortho.tif" }).getAttribute("href");

  // 1. proposal from a world file and .prj; check; confirm; record
  await page.goto(scanUrl!);
  await expect(panel(page)).toContainText("A change can be reviewed");
  await panel(page).scrollIntoViewIfNeeded();
  await shot(page, "scan-sidecars-proposal");
  const reviewRegion = panel(page).getByRole("region", { name: "Review the change from sidecar files" });
  await reviewRegion.getByLabel("Reason (required)").fill("World file and .prj delivered with the scan (walkthrough, synthetic).");
  await reviewRegion.getByRole("button", { name: "Check this change" }).click();
  await expect(reviewRegion).toContainText("Passed validation");
  await reviewRegion.getByRole("checkbox").check();
  await reviewRegion.scrollIntoViewIfNeeded();
  await shot(page, "scan-sidecars-checked", false);
  await reviewRegion.getByRole("button", { name: "Record as version 1" }).click();
  await expect(panel(page)).toContainText("Recorded as version 1");
  await shot(page, "scan-recorded-history");

  // 2. a CRS definition the registry cannot resolve
  await page.goto(lccUrl!);
  await expect(panel(page)).toContainText("Not enough to place the raster");
  await shot(page, "lcc-unresolved-crs");

  // 3. valid raster metadata with a disagreeing .aux.xml: conflict shown, nothing offered; unknown content listed
  await page.goto(orthoUrl!);
  await expect(panel(page)).toContainText("The raster's own georeferencing is valid");
  await panel(page).getByText(/Present in the file, not used/).click();
  await shot(page, "ortho-file-valid-conflict-aux");

  // 4. the map: scan.tif placed from recorded georeferencing
  await nav(page).getByRole("link", { name: "Map & Analysis", exact: true }).click();
  await expect(page.getByText("Loading the map…")).toHaveCount(0, { timeout: 30_000 });
  await page.waitForTimeout(2000);
  await shot(page, "map-scan-placed", false);

  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(lccUrl!);
  await expect(panel(page)).toBeVisible();
  await shot(page, "narrow-sidecar-panel");

  writeFileSync(
    path.join(OUT, "..", "sidecars-browser-log.json"),
    JSON.stringify({ captured_at: new Date().toISOString(), screenshots: counter, console_problems: problems, failed_requests: failed, external_requests: external }, null, 2),
  );
});
