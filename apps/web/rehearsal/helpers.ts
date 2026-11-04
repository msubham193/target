import { execFileSync } from "node:child_process";
import path from "node:path";

import { expect, type Page } from "@playwright/test";

export const PROFILE = "Reviewer"; // an existing profile in the restored copy
export const PROJECT = "UI review — harbour photographs";
const API_DIR = path.resolve(process.cwd(), "../../services/api");

/** Refuse to do anything unless the app is connected to the rehearsal copy (never the working database). */
export async function assertRehearsal(page: Page) {
  const health = await (await page.request.get("/health")).json();
  expect(health.database.name, "the app must be connected to the rehearsal database").toBe("geopoc_rehearsal");
  expect(health.storage.data_root).toMatch(/_rehearsal$/);
  return health;
}

export async function enter(page: Page) {
  await page.goto("/");
  const existing = page.getByRole("button", { name: `Continue as ${PROFILE}` });
  await expect(existing).toBeVisible();
  await existing.click();
  await expect(page).toHaveURL(/\/dashboard$/);
}

export async function projectId(page: Page, name = PROJECT): Promise<string> {
  const listing = await (await page.request.get("/api/v1/projects?limit=100")).json();
  const found = (listing.items as { id: string; name: string }[]).find((p) => p.name === name);
  expect(found, `project ${name}`).toBeTruthy();
  return found!.id;
}

export async function openProject(page: Page) {
  await page.getByRole("list", { name: "Project list" }).getByRole("link", { name: /UI review/ }).click();
  await expect(page).toHaveURL(/\/projects\/[0-9a-f-]{36}/);
}

export const nav = (page: Page) => page.getByRole("navigation", { name: "Main" });

export function watchExternal(page: Page): string[] {
  const external: string[] = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.protocol.startsWith("http") && url.hostname !== "127.0.0.1") external.push(request.url());
  });
  return external;
}

const PRELUDE = [
  "import base64, sys, numpy as np",
  "from rasterio.io import MemoryFile",
  "from rasterio.transform import from_origin",
  "r, c = np.mgrid[0:120, 0:160]",
  "z = (300 + 80 * np.sin(r / 15.0) + 60 * np.cos(c / 20.0) + (r + c) * 0.3).astype('float32')",
  "z[40:55, 60:80] = -9999",
  "profile = dict(driver='GTiff', width=160, height=120, count=1, dtype='float32', crs='EPSG:32643', nodata=-9999, transform=from_origin(500000, 3000000, 25, 25))",
  "with MemoryFile() as m:",
  "    with m.open(**profile) as d:",
  "        d.write(z[None]); d.units = ['metre']; d.set_band_description(1, 'elevation')",
  "    data = m.read()",
  "sys.stdout.write(base64.b64encode(data).decode())",
].join("\n");

/** A small synthetic elevation GeoTIFF (declared metres, with a no-data hole). */
export function elevationTiff(): Buffer {
  return Buffer.from(execFileSync("uv", ["run", "--no-sync", "python", "-c", PRELUDE], { cwd: API_DIR }).toString(), "base64");
}
