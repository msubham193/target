import { execFileSync } from "node:child_process";
import { crc32, deflateSync } from "node:zlib";
import path from "node:path";

import { expect, type Page, test } from "@playwright/test";

// Runs against the real API, database and worker. The GeoTIFF is written by the backend's own test helper
// (rasterio) with a known CRS and geotransform; analysis uses the test-only stub detectors, so this covers the
// map, review and export integration, not any real model.
test.describe.configure({ mode: "serial" });

const PROFILE = "E2E Reviewer";
const API_DIR = path.resolve(process.cwd(), "../../services/api"); // tests run from apps/web

function geotiff(): Buffer {
  const code =
    "import base64, sys; from tests import media; " +
    "sys.stdout.write(base64.b64encode(media.geotiff_bytes(bands=3, size=(200, 150))).decode())";
  return Buffer.from(execFileSync("uv", ["run", "--no-sync", "python", "-c", code], { cwd: API_DIR }).toString(), "base64");
}

function png(width: number, height: number): Buffer {
  const chunk = (type: string, data: Buffer) => {
    const body = Buffer.concat([Buffer.from(type, "ascii"), data]);
    const length = Buffer.alloc(4);
    length.writeUInt32BE(data.length);
    const checksum = Buffer.alloc(4);
    checksum.writeUInt32BE(crc32(body) >>> 0);
    return Buffer.concat([length, body, checksum]);
  };
  const header = Buffer.alloc(13);
  header.writeUInt32BE(width, 0);
  header.writeUInt32BE(height, 4);
  header.set([8, 2, 0, 0, 0], 8);
  const row = Buffer.alloc(1 + width * 3);
  for (let x = 0; x < width; x++) row.set([(x * 5) % 256, 120, 80], 1 + x * 3);
  const pixels = deflateSync(Buffer.concat(Array.from({ length: height }, () => row)));
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk("IHDR", header),
    chunk("IDAT", pixels),
    chunk("IEND", Buffer.alloc(0)),
  ]);
}

async function enterWorkspace(page: Page) {
  await page.goto("/");
  const existing = page.getByRole("button", { name: `Continue as ${PROFILE}` });
  await expect(page.getByText("No profiles yet").or(existing)).toBeVisible();
  if (await existing.isVisible()) {
    await existing.click();
  } else {
    await page.getByLabel("New profile name").fill(PROFILE);
    await page.getByRole("button", { name: "Create profile and continue" }).click();
  }
  await expect(page).toHaveURL(/\/dashboard$/);
}

const queueRow = (page: Page, name: string) =>
  page.getByRole("list", { name: "Upload queue" }).getByRole("listitem", { name, exact: true });

test("a georeferenced raster is drawn on the map from its own CRS; other files are listed, not placed", async ({ page }) => {
  const external: string[] = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.protocol.startsWith("http") && url.hostname !== "127.0.0.1") external.push(request.url());
  });

  await enterWorkspace(page);
  await page.getByRole("link", { name: "Create project", exact: true }).click();
  await page.getByLabel("Name").fill("Map and review");
  await page.getByRole("button", { name: "Create project" }).click();
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Upload & Ingestion" }).click();

  await page.getByRole("tab", { name: "Satellite / aerial raster (GeoTIFF)" }).click();
  await page.locator("#asset-files").setInputFiles({ name: "scene_utm43n.tif", mimeType: "image/tiff", buffer: geotiff() });
  await expect(queueRow(page, "scene_utm43n.tif")).toContainText("Stored", { timeout: 30_000 });
  await page.getByRole("tab", { name: "Ground photographs" }).click();
  await page.locator("#asset-files").setInputFiles({ name: "street.png", mimeType: "image/png", buffer: png(400, 300) });
  await expect(queueRow(page, "street.png")).toContainText("Stored and validated.");

  const tile = page.waitForResponse((r) => /\/api\/v1\/assets\/[^/]+\/tiles\/\d+\/\d+\/\d+\.png\?v=\d+$/.test(r.url()) && r.status() === 200, {
    timeout: 30_000,
  });
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Map & Analysis", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Map & Analysis View", level: 1 })).toBeVisible();

  const layers = page.getByRole("list", { name: "Raster layers" });
  await expect(layers).toContainText("scene_utm43n.tif");
  await expect(layers).toContainText("EPSG:32643");
  const excluded = page.getByRole("list", { name: "Files not placed on the map" });
  await expect(excluded).toContainText("street.png");
  await expect(excluded).toContainText("No georeferencing");

  // A real tile, rendered by the API from the stored original, reached the map.
  const response = await tile;
  expect(response.headers()["content-type"]).toBe("image/png");
  const canvas = page.getByRole("region", { name: "Map of project layers" }).locator("canvas");
  await expect(canvas).toBeVisible();
  // The canvas fills the map frame (a regression check: MapLibre's own CSS once collapsed the container).
  const frame = await page.getByRole("region", { name: "Map of project layers" }).boundingBox();
  expect(frame!.height).toBeGreaterThan(300);
  expect((await canvas.boundingBox())!.height).toBeCloseTo(frame!.height, 0);

  // Clicking the middle of the map (fitted to the raster) pins a point inside the raster's footprint.
  // A real mouse click at the canvas centre (the map is fitted to the raster, so the centre is inside it).
  const box = await page.getByRole("region", { name: "Map of project layers" }).locator("canvas").boundingBox();
  expect(box).not.toBeNull();
  await page.mouse.move(box!.x + box!.width / 2 - 5, box!.y + box!.height / 2);
  await page.mouse.click(box!.x + box!.width / 2, box!.y + box!.height / 2);
  const pinned = page.getByRole("status", { name: "Pinned point" });
  await expect(pinned).toContainText("° N");
  await expect(pinned).toContainText("° E");
  await expect(pinned).toContainText("metre", { timeout: 10_000 }); // the same point in EPSG:32643
  const easting = Number((await pinned.textContent())?.match(/(\d{6}\.\d{3}) metre/)?.[1]);
  expect(easting).toBeGreaterThan(500_000);
  expect(easting).toBeLessThan(502_000); // the raster spans 500 000 to 502 000 m

  expect(external).toEqual([]);
});

test("findings are reviewed with a recorded reason; disagreeing runs create a conflict a reviewer resolves", async ({ page }) => {
  await enterWorkspace(page);
  await page.getByRole("list", { name: "Project list" }).getByRole("link", { name: "Map and review" }).click();
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Ground Image Analysis" }).click();
  await expect(page.getByText("This photograph has not been analysed yet")).toBeVisible({ timeout: 30_000 });

  await page.getByLabel("Model", { exact: true }).selectOption({ label: "stub-detector test" });
  await page.getByRole("button", { name: "Run analysis" }).click();
  const findings = page.getByRole("table", { name: "Model findings in this photograph" });
  await expect(findings.getByRole("row")).toHaveCount(3, { timeout: 30_000 });

  // Reject "thing b" with a reason; the review is recorded and shown.
  await findings.getByRole("row").filter({ hasText: "thing b" }).click();
  const actions = page.getByRole("group", { name: "Review actions" });
  await actions.getByRole("button", { name: "Reject" }).click();
  const save = page.getByRole("button", { name: "Save: Reject" });
  await expect(save).toBeDisabled();
  await page.getByLabel("Reason (required)").fill("Shadow, not an object.");
  await save.click();
  await expect(page.getByText("Reject saved as revision 1.")).toBeVisible();
  await expect(page.getByRole("list", { name: "Review history" })).toContainText("Needs review → Rejected");
  await expect(page.getByRole("list", { name: "Review history" })).toContainText(PROFILE);
  await expect(findings.getByRole("row").filter({ hasText: "thing b" })).toContainText("Rejected");

  // A second model calls "thing a" something else: both findings are kept and disputed.
  await page.getByLabel("Model", { exact: true }).selectOption({ label: "stub-detector-alt test" });
  await page.getByRole("button", { name: "Run analysis" }).click();
  await expect(page.getByLabel("Analysis run")).toBeVisible({ timeout: 30_000 });

  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Observations", exact: true }).click();
  await expect(page.getByText("Open conflicts 1")).toBeVisible();
  await page.getByLabel("Conflicts").selectOption("open");
  const table = page.getByRole("table", { name: "Observations in this project" });
  await expect(table.getByRole("row")).toHaveCount(3);
  await expect(table).toContainText("thing a");
  await expect(table).toContainText("thing z");

  await table.getByRole("row").filter({ hasText: "thing z" }).click();
  await expect(page.getByText("Open conflict: category")).toBeVisible();
  await page.getByRole("group", { name: "Review actions" }).getByRole("button", { name: "Confirm" }).click();
  await page.getByLabel("Reason (required)").fill("The second label matches the object.");
  await page.getByRole("button", { name: "Save: Confirm" }).click();
  await expect(page.getByText("Confirm saved as revision 2.")).toBeVisible();

  await table.getByRole("row").filter({ hasText: "thing a" }).click();
  await page.getByRole("group", { name: "Review actions" }).getByRole("button", { name: "Reject" }).click();
  await page.getByLabel("Reason (required)").fill("Wrong label.");
  await page.getByRole("button", { name: "Save: Reject" }).click();
  await expect(page.getByText("Reject saved as revision 2.")).toBeVisible();
  await expect(page.getByText("No observations match these filters")).toBeVisible();
  await page.getByLabel("Conflicts").selectOption("");
  await expect(table.getByRole("row")).toHaveCount(5); // nothing was merged or deleted
});

test("exports come from the server, label pixel columns, and place nothing without a location", async ({ page }) => {
  await enterWorkspace(page);
  await page.getByRole("list", { name: "Project list" }).getByRole("link", { name: "Map and review" }).click();
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Reports", exact: true }).click();

  const csvDownload = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download CSV" }).click();
  const csv = await csvDownload;
  expect(csv.suggestedFilename()).toMatch(/^observations-.+\.csv$/);
  const csvText = (await (await csv.createReadStream()).toArray()).join("");
  expect(csvText).toContain("px_x_min (source image pixels, top-left origin)");
  expect(csvText.trim().split("\n")).toHaveLength(5); // header + 4 findings

  const geoDownload = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download GEOJSON" }).click();
  const geo = JSON.parse((await (await (await geoDownload).createReadStream()).toArray()).join("")) as {
    features: unknown[];
    excluded: { count: number };
  };
  expect(geo.features).toEqual([]);
  expect(geo.excluded.count).toBe(4);
  await expect(page.getByRole("status")).toContainText("Saved observations-");
});
