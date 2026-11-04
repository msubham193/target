import { execFileSync } from "node:child_process";
import path from "node:path";

import { expect, type Page, test } from "@playwright/test";

// The 3D terrain view in a real browser (WebGL), against the real API and database (scratch database geopoc_e2e). Synthetic
// GeoTIFFs only: an elevation raster with a declared unit and a no-data hole, one that declares nothing, and a colour image.
// Named to sort after the other specs: they share one scratch database and expect it fresh.
test.describe.configure({ mode: "serial" });

const PROFILE = "E2E Reviewer";
const API_DIR = path.resolve(process.cwd(), "../../services/api");

const PRELUDE = [
  "import base64, sys, numpy as np",
  "from rasterio.io import MemoryFile",
  "from rasterio.transform import from_origin",
  "from rasterio.enums import ColorInterp",
  "r, c = np.mgrid[0:120, 0:160]",
  "z = (300 + 80 * np.sin(r / 15.0) + 60 * np.cos(c / 20.0) + (r + c) * 0.3).astype('float32')",
  "def make(array, **extra):",
  "    profile = dict(driver='GTiff', width=160, height=120, count=array.shape[0], dtype=str(array.dtype), crs='EPSG:32643', transform=from_origin(500000, 3000000, 25, 25))",
  "    profile.update(extra.pop('profile', {}))",
  "    with MemoryFile() as m:",
  "        with m.open(**profile) as d:",
  "            d.write(array)",
  "            for k, v in extra.items(): setattr(d, k, v)",
  "        return m.read()",
].join("\n");

function tiff(body: string): Buffer {
  const code = `${PRELUDE}\n${body}\nsys.stdout.write(base64.b64encode(data).decode())\n`;
  return Buffer.from(execFileSync("uv", ["run", "--no-sync", "python", "-c", code], { cwd: API_DIR }).toString(), "base64");
}

const DEM = tiff("z[40:55, 60:80] = -9999\ndata = make(z[None], units=['metre'], profile={'nodata': -9999})");
const UNDECLARED = tiff("data = make(z[None])");
const RGB = tiff("data = make(np.stack([(z % 255).astype('uint8')] * 3), colorinterp=[ColorInterp.red, ColorInterp.green, ColorInterp.blue])");

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

test("only elevation is drawn: the surface can be orbited, zoomed and read, and everything else is refused with a reason", async ({
  page,
}, testInfo) => {
  const external: string[] = [];
  const grids: string[] = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.protocol.startsWith("http") && url.hostname !== "127.0.0.1") external.push(request.url());
    if (url.pathname.endsWith("/terrain/grid")) grids.push(url.pathname + url.search);
  });
  await enterWorkspace(page);
  await page.getByRole("link", { name: "Create project", exact: true }).click();
  await page.getByLabel("Name").fill("Terrain");
  await page.getByRole("button", { name: "Create project" }).click();

  // A project with no rasters says so and draws nothing.
  await nav(page).getByRole("link", { name: "3D View" }).click();
  await expect(page.getByRole("status").filter({ hasText: "This project has no rasters" })).toBeVisible();
  await expect(page.getByTestId("terrain-canvas")).toHaveCount(0);

  await nav(page).getByRole("link", { name: "Upload & Ingestion" }).click();
  await page.getByRole("tab", { name: "Satellite / aerial raster (GeoTIFF)" }).click();
  await page.locator("#asset-files").setInputFiles(
    [
      { name: "dem.tif", buffer: DEM },
      { name: "undeclared.tif", buffer: UNDECLARED },
      { name: "colour.tif", buffer: RGB },
    ].map((f) => ({ ...f, mimeType: "image/tiff" })),
  );
  for (const name of ["dem.tif", "undeclared.tif", "colour.tif"]) {
    await expect(page.getByRole("list", { name: "Upload queue" }).getByRole("listitem", { name, exact: true })).toContainText("Stored", {
      timeout: 30_000,
    });
  }

  await nav(page).getByRole("link", { name: "3D View" }).click();
  const list = page.getByRole("list", { name: "Rasters and whether they are elevation" });
  await expect(list).toContainText("dem.tifElevation");
  await expect(list).toContainText("undeclared.tifNeeds confirmation");
  await expect(list).toContainText("colour.tifNot elevation");

  // The declared elevation raster is drawn at once.
  const canvas = page.getByTestId("terrain-canvas");
  await expect(canvas).toBeVisible({ timeout: 30_000 });
  const data = page.getByRole("heading", { name: "Elevation data" }).locator("xpath=ancestor::section[1]");
  await expect(data).toContainText("Elevation unitm · declared by the file");
  await expect(data).toContainText("Vertical datumnot stated by the file");
  await expect(data).toContainText(/Cells with data[\s\S]*with no data/);
  await expect(page.getByRole("list", { name: "Disclosures" })).toContainText("No-data cells are left as holes");
  expect(grids.length).toBeGreaterThan(0);

  // Something real is on the canvas: many colours, and not just the background.
  await page.waitForTimeout(500);
  const picture = (await canvas.screenshot()).toString("base64");
  const stats = await page.evaluate(async (b64) => {
    const img = new Image();
    img.src = `data:image/png;base64,${b64}`;
    await img.decode();
    const c = document.createElement("canvas");
    c.width = img.width;
    c.height = img.height;
    const ctx = c.getContext("2d")!;
    ctx.drawImage(img, 0, 0);
    const px = ctx.getImageData(0, 0, c.width, c.height).data;
    const colours = new Set<number>();
    let background = 0;
    for (let i = 0; i < px.length; i += 4) {
      if (Math.abs(px[i]! - 17) < 6 && Math.abs(px[i + 1]! - 20) < 6 && Math.abs(px[i + 2]! - 26) < 6) background++;
      colours.add(((px[i]! >> 3) << 10) | ((px[i + 1]! >> 3) << 5) | (px[i + 2]! >> 3));
    }
    return { colours: colours.size, background: background / (px.length / 4) };
  }, picture);
  expect(stats.colours).toBeGreaterThan(40);
  expect(stats.background).toBeLessThan(0.85);
  await canvas.screenshot({ path: testInfo.outputPath("terrain-oblique.png") });

  // Orbit, zoom and keyboard change the real camera.
  const yaw0 = Number(await canvas.getAttribute("data-yaw"));
  const box = (await canvas.boundingBox())!;
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width / 2 + 120, box.y + box.height / 2 + 30, { steps: 6 });
  await page.mouse.up();
  expect(Number(await canvas.getAttribute("data-yaw"))).not.toBe(yaw0);
  const distance0 = Number(await canvas.getAttribute("data-distance"));
  await page.mouse.wheel(0, -400);
  await expect(async () => expect(Number(await canvas.getAttribute("data-distance"))).toBeLessThan(distance0)).toPass({ timeout: 5000 });
  await canvas.focus();
  await page.keyboard.press("ArrowLeft");
  await page.getByRole("button", { name: "Top view" }).click();
  expect(Number(await canvas.getAttribute("data-pitch"))).toBeGreaterThan(85);
  await canvas.screenshot({ path: testInfo.outputPath("terrain-top.png") });

  // The pointer reads the stored cell value; a click selects it and gives its longitude and latitude.
  await page.getByRole("button", { name: "Reset view" }).click();
  await page.mouse.move(box.x + box.width / 2, box.y + box.height * 0.6);
  await expect(page.getByTestId("hover-readout")).toContainText(/Elevation [\d,.]+ m/, { timeout: 10_000 });
  await expect(page.getByTestId("hover-readout")).toContainText("EPSG:32643");
  await page.mouse.click(box.x + box.width / 2, box.y + box.height * 0.6);
  await expect(page.getByTestId("pinned-readout")).toContainText(/lat 27\.\d+°, lon 75\.\d+° \(WGS 84\)/, { timeout: 10_000 });

  // Vertical exaggeration is shown on the picture and in the controls.
  await expect(page.getByTestId("exaggeration-badge")).toContainText("×1 (true scale)");
  await page.getByLabel(/Vertical exaggeration/).fill("4");
  await expect(page.getByTestId("exaggeration-badge")).toContainText("heights are drawn 4 times taller than true scale");
  await expect(canvas).toHaveAttribute("data-exaggeration", "4");

  // A raster that declares nothing needs a person's statement, for this view only.
  await list.getByRole("button", { name: /undeclared\.tif/ }).click();
  const form = page.getByRole("form", { name: "State what this raster holds" });
  await expect(form).toBeVisible();
  await expect(page.getByTestId("terrain-canvas")).toHaveCount(0);
  await expect(form.getByRole("button", { name: "Show in 3D" })).toBeDisabled();
  await form.getByRole("checkbox").check();
  await form.getByLabel("Elevation unit").selectOption("foot");
  await form.getByRole("button", { name: "Show in 3D" }).click();
  await expect(page.getByTestId("terrain-canvas")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByRole("list", { name: "Disclosures" })).toContainText("stated by a person for this view only");

  // Colour imagery is never elevation.
  await list.getByRole("button", { name: /colour\.tif/ }).click();
  await expect(page.getByText("colour.tif cannot be shown in 3D: not elevation")).toBeVisible();
  await expect(page.getByText(/image colour, not elevation/).first()).toBeVisible();
  await expect(page.getByTestId("terrain-canvas")).toHaveCount(0);
  expect(external).toEqual([]);
});
