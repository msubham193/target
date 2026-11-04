import { execFileSync } from "node:child_process";
import path from "node:path";

import { expect, type Page, test } from "@playwright/test";

// Demo phase 1: photographs, their GPS candidates and person-confirmed locations, markers on the map, and the
// export, against the real API, database and worker (scratch database geopoc_e2e). Synthetic data only: JPEGs
// written here with EXIF GPS blocks, and a small GeoTIFF placed near the photographs' positions (UTM 43N).
test.describe.configure({ mode: "serial" });

const PROFILE = "E2E Reviewer";
const API_DIR = path.resolve(process.cwd(), "../../services/api");

function python(expression: string): Buffer {
  const code =
    "import base64, sys; from tests.media import image_bytes, gps_exif, exif_block; " +
    "from tests.test_raster_tools import tif, gradient; " +
    `sys.stdout.write(base64.b64encode(${expression}).decode())`;
  return Buffer.from(execFileSync("uv", ["run", "--no-sync", "python", "-c", code], { cwd: API_DIR }).toString(), "base64");
}

// 27 deg 6' 46.8" N = 27.113; 75 deg 0' 36" E = 75.01: inside the GeoTIFF below.
const GATE = python("image_bytes('JPEG', size=(400, 300), seed=11, exif=gps_exif(t2=(27.0, 6.0, 46.8), t4=(75.0, 0.0, 36.0)))");
const PLAIN = python("image_bytes('JPEG', size=(400, 300), seed=12, exif=exif_block())");
const VOID_GPS = python("image_bytes('JPEG', size=(400, 300), seed=13, exif=gps_exif(t9='V'))");
const UNPLACED = python("image_bytes('JPEG', size=(400, 300), seed=14)");
const RASTER = python("tif(gradient(bands=1, height=150, width=200))");

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
const locationPanel = (page: Page) => page.getByRole("region", { name: "Photo location", exact: true });

function watchExternalRequests(page: Page): string[] {
  const external: string[] = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.protocol.startsWith("http") && url.hostname !== "127.0.0.1") external.push(request.url());
  });
  return external;
}

async function openPhoto(page: Page, name: string) {
  await nav(page).getByRole("link", { name: "Ground Image Analysis" }).click();
  await page.getByRole("list", { name: "Photographs" }).getByRole("link", { name: new RegExp(name.replace(".", "\\.")) }).click();
  await expect(locationPanel(page)).toBeVisible();
}

/** A real mouse click at the centre of a map canvas (the map is fitted so that the centre is the place of interest). */
async function clickMapCentre(page: Page, within = page.getByRole("region", { name: "Map of project layers" })) {
  const canvas = within.locator("canvas");
  await expect(canvas).toBeVisible();
  await canvas.scrollIntoViewIfNeeded();
  const box = (await canvas.boundingBox())!;
  await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
}

test("an EXIF position is only a candidate until confirmed; then the photograph is on the map with its analysis", async ({ page }) => {
  const external = watchExternalRequests(page);
  await enterWorkspace(page);
  await page.getByRole("link", { name: "Create project", exact: true }).click();
  await page.getByLabel("Name").fill("Photo locations");
  await page.getByRole("button", { name: "Create project" }).click();

  // Imagery first, then the photographs.
  await nav(page).getByRole("link", { name: "Upload & Ingestion" }).click();
  await page.getByRole("tab", { name: "Satellite / aerial raster (GeoTIFF)" }).click();
  await page.locator("#asset-files").setInputFiles({ name: "harbour.tif", mimeType: "image/tiff", buffer: RASTER });
  await expect(queueRow(page, "harbour.tif")).toContainText("Stored", { timeout: 30_000 });
  await page.getByRole("tab", { name: "Ground photographs" }).click();
  await page.locator("#asset-files").setInputFiles(
    [
      { name: "gate_gps.jpg", buffer: GATE },
      { name: "plain.jpg", buffer: PLAIN },
      { name: "void_gps.jpg", buffer: VOID_GPS },
      { name: "unplaced.jpg", buffer: UNPLACED },
    ].map((f) => ({ ...f, mimeType: "image/jpeg" })),
  );
  for (const name of ["gate_gps.jpg", "plain.jpg", "void_gps.jpg", "unplaced.jpg"]) {
    await expect(queueRow(page, name)).toContainText("Stored", { timeout: 30_000 });
  }

  // The GPS position is read, but it is unverified metadata, not a location.
  await openPhoto(page, "gate_gps.jpg");
  const panel = locationPanel(page);
  await expect(panel).toContainText("Unverified candidate — not a location yet");
  await expect(panel).toContainText("27.113000° N, 75.010000° E");
  await expect(panel).toContainText("No confirmed location");
  await expect(panel.getByRole("region", { name: "Confirmed location" })).toHaveCount(0);
  // The map offers the uploaded GeoTIFF as context.
  await expect(panel.getByLabel("Context imagery")).toContainText("harbour.tif");

  // Run the analysis (the test-only stub detector) so the map can show it later.
  await page.getByLabel("Model").selectOption({ label: "stub-detector test" });
  await page.getByRole("button", { name: "Run analysis" }).click();
  await expect(page.getByRole("listitem", { name: "Ground-image analysis job" })).toContainText("Needs review", { timeout: 30_000 });

  // On the map the photograph is not placed: it is only counted as having an unconfirmed position.
  await nav(page).getByRole("link", { name: "Map & Analysis", exact: true }).click();
  await expect(page.getByTestId("no-located-photos")).toBeVisible();
  await expect(page.getByText("1 photograph(s) have an unconfirmed GPS position")).toBeVisible();
  await expect(page.getByRole("list", { name: "Raster layers" }).getByRole("listitem")).toHaveCount(1);

  // The person confirms it.
  await openPhoto(page, "gate_gps.jpg");
  await locationPanel(page).getByRole("button", { name: "Confirm this position" }).click();
  const confirmed = locationPanel(page).getByRole("region", { name: "Confirmed location" });
  await expect(confirmed).toContainText("27.113000° N, 75.010000° E");
  await expect(confirmed).toContainText(`GPS position from the photo file, confirmed · by ${PROFILE}`);
  await expect(page.getByRole("list", { name: "Photographs" })).toContainText("Located");

  // It is now a marker; selecting it opens the photograph with the analysis boxes.
  await nav(page).getByRole("link", { name: "Map & Analysis", exact: true }).click();
  await expect(page.getByText("Located photographs (1)")).toBeVisible();
  await page.getByRole("list", { name: "Located photographs" }).getByRole("button", { name: /gate_gps\.jpg/ }).click();
  await expect(page.getByRole("img", { name: "Preview of gate_gps.jpg" })).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("detection-box")).toHaveCount(2);
  await expect(page.getByRole("list", { name: "Findings in this photograph" }).getByRole("listitem")).toHaveCount(2);
  await expect(page.getByText("the findings have no geographic location, only the photograph does")).toBeVisible();

  // A real click on the marker (the map is centred on it) opens the photograph again after it is closed.
  await page.getByRole("button", { name: "Close", exact: true }).click();
  await expect(page.getByRole("img", { name: "Preview of gate_gps.jpg" })).toHaveCount(0);
  await expect(async () => {
    await clickMapCentre(page);
    await expect(page.getByRole("img", { name: "Preview of gate_gps.jpg" })).toBeVisible({ timeout: 2_000 });
  }).toPass({ timeout: 20_000 });
  expect(external).toEqual([]);
});

test("a position can be typed or chosen on the map, refusals are visible, and located photographs are exported", async ({ page }) => {
  const external = watchExternalRequests(page);
  await enterWorkspace(page);
  await page.getByRole("list", { name: "Project list" }).getByRole("link", { name: "Photo locations" }).click();

  // Typed coordinates are validated before anything is sent.
  await openPhoto(page, "plain.jpg");
  let panel = locationPanel(page);
  await expect(panel).toContainText("The file carries no GPS position.");
  await panel.getByLabel("Latitude").fill("95");
  await panel.getByLabel("Longitude").fill("75.0105");
  await expect(panel).toContainText("Latitude must be between -90 and 90.");
  await expect(panel.getByRole("button", { name: "Save location" })).toBeDisabled();
  await panel.getByLabel("Latitude").fill("27.1135");
  await expect(panel).not.toContainText("Latitude must be");
  await panel.getByRole("checkbox", { name: /I have checked this position/ }).check();
  await panel.getByRole("button", { name: "Save location" }).click();
  await expect(panel.getByRole("region", { name: "Confirmed location" })).toContainText("27.113500° N, 75.010500° E");
  await expect(panel.getByRole("region", { name: "Confirmed location" })).toContainText("Coordinates typed in");

  // Unusable GPS is explained and not used; a point is chosen on the map instead.
  await openPhoto(page, "void_gps.jpg");
  panel = locationPanel(page);
  await expect(panel).toContainText("The file has GPS tags that cannot be used");
  await expect(panel).toContainText("void measurement");
  await expect(panel.getByRole("button", { name: "Confirm this position" })).toHaveCount(0);
  await expect(async () => {
    await clickMapCentre(page, panel.getByRole("region", { name: "Map of project layers" }));
    await expect(panel.getByLabel("Latitude")).toHaveValue(/^27\.1\d{5}$/, { timeout: 2_000 });
  }).toPass({ timeout: 20_000 });
  await expect(panel.getByLabel("Longitude")).toHaveValue(/^75\.0\d{5}$/);
  await panel.getByRole("checkbox", { name: /I have checked this position/ }).check();
  await panel.getByRole("button", { name: "Save location" }).click();
  await expect(panel.getByRole("region", { name: "Confirmed location" })).toContainText("Point chosen on the map");

  // A failed request is shown to the person, and nothing is recorded.
  await openPhoto(page, "unplaced.jpg");
  panel = locationPanel(page);
  await page.route("**/api/v1/assets/*/location", async (route) => {
    if (route.request().method() !== "POST") return route.fallback();
    return route.fulfill({
      status: 500,
      contentType: "application/problem+json",
      body: JSON.stringify({ title: "The server could not record the location", status: 500, code: "INTERNAL_ERROR", errors: [] }),
    });
  });
  await panel.getByLabel("Latitude").fill("27.1");
  await panel.getByLabel("Longitude").fill("75.005");
  await panel.getByRole("checkbox", { name: /I have checked this position/ }).check();
  await panel.getByRole("button", { name: "Save location" }).click();
  await expect(panel.getByRole("alert")).toContainText("The location was not saved");
  await expect(panel.getByRole("alert")).toContainText("INTERNAL_ERROR");
  await expect(panel.getByRole("region", { name: "Confirmed location" })).toHaveCount(0);
  await page.unroute("**/api/v1/assets/*/location");

  // The export carries the three located photographs, with how each location was recorded, and counts the rest.
  await nav(page).getByRole("link", { name: "Reports", exact: true }).click();
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download GEOJSON" }).click();
  const geo = JSON.parse((await (await (await download).createReadStream()).toArray()).join("")) as {
    features: { geometry: { type: string; coordinates: number[] }; properties: Record<string, unknown> }[];
    photographs: { located: number; not_located: number };
  };
  const photos = geo.features.filter((f) => f.properties.feature_type === "photo_location");
  expect(photos.map((f) => f.properties.source_file).sort()).toEqual(["gate_gps.jpg", "plain.jpg", "void_gps.jpg"]);
  const sources = Object.fromEntries(photos.map((f) => [f.properties.source_file, f.properties.location_source]));
  expect(sources).toEqual({ "gate_gps.jpg": "exif", "plain.jpg": "manual_entry", "void_gps.jpg": "map_selection" });
  for (const feature of photos) {
    expect(feature.geometry.type).toBe("Point");
    const [lon, lat] = feature.geometry.coordinates as [number, number];
    expect(lon).toBeGreaterThanOrEqual(-180);
    expect(lon).toBeLessThanOrEqual(180);
    expect(lat).toBeGreaterThanOrEqual(-90);
    expect(lat).toBeLessThanOrEqual(90);
  }
  const gate = photos.find((f) => f.properties.source_file === "gate_gps.jpg")!;
  expect(gate.geometry.coordinates[0]).toBeCloseTo(75.01, 6);
  expect(gate.geometry.coordinates[1]).toBeCloseTo(27.113, 6);
  expect(geo.photographs).toMatchObject({ located: 3, not_located: 1 });
  expect(JSON.stringify(geo)).not.toContain("unplaced.jpg"); // no coordinates are invented for it

  // Removing a location takes the photograph off the map; the history keeps the steps.
  await openPhoto(page, "plain.jpg");
  await locationPanel(page).getByRole("button", { name: "Remove the location" }).click();
  await expect(locationPanel(page).getByRole("region", { name: "Confirmed location" })).toHaveCount(0);
  await locationPanel(page).getByText(/^History \(/).click();
  await expect(locationPanel(page).getByRole("list", { name: "Location history" })).toContainText("location removed");
  await nav(page).getByRole("link", { name: "Map & Analysis", exact: true }).click();
  await expect(page.getByText("Located photographs (2)")).toBeVisible();
  expect(external).toEqual([]);
});

test("photographs a few metres apart: a click asks which one, and every choice opens the right photograph", async ({ page }) => {
  const external = watchExternalRequests(page);
  await enterWorkspace(page);
  await page.getByRole("list", { name: "Project list" }).getByRole("link", { name: "Photo locations" }).click();

  // A second photograph about 15 m from gate_gps.jpg (27.113, 75.010): their markers overlap on the screen.
  await openPhoto(page, "unplaced.jpg");
  const panel = locationPanel(page);
  await panel.getByLabel("Latitude").fill("27.1131");
  await panel.getByLabel("Longitude").fill("75.0101");
  await panel.getByRole("checkbox", { name: /I have checked this position/ }).check();
  await panel.getByRole("button", { name: "Save location" }).click();
  await expect(panel.getByRole("region", { name: "Confirmed location" })).toContainText("27.113100° N, 75.010100° E");

  await nav(page).getByRole("link", { name: "Map & Analysis", exact: true }).click();
  await expect(page.getByText("Located photographs (3)")).toBeVisible(); // gate_gps, void_gps and unplaced
  const located = page.getByRole("list", { name: "Located photographs" });
  const GATE = "27.113000° N, 75.010000° E";
  const NEAR = "27.113100° N, 75.010100° E";

  // The list is exact: each entry opens its own photograph and its own coordinates, never its neighbour's.
  await located.getByRole("button", { name: /unplaced\.jpg/ }).click();
  await expect(page.getByRole("img", { name: "Preview of unplaced.jpg" })).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText(NEAR)).toBeVisible();
  await expect(page.getByText(GATE)).toHaveCount(0);
  await located.getByRole("button", { name: /gate_gps\.jpg/ }).click();
  await expect(page.getByRole("img", { name: "Preview of gate_gps.jpg" })).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText(GATE)).toBeVisible();
  await expect(page.getByText(NEAR)).toHaveCount(0);

  // A real click on the overlapping markers (the map is centred on gate_gps.jpg) asks instead of guessing.
  const asking = page.getByRole("list", { name: "Photographs near the click" });
  for (const [choice, shown, other] of [
    ["unplaced.jpg", NEAR, GATE],
    ["gate_gps.jpg", GATE, NEAR],
  ] as const) {
    await page.getByRole("button", { name: "Close", exact: true }).click();
    await expect(page.getByRole("img", { name: /^Preview of / })).toHaveCount(0);
    await expect(async () => {
      await clickMapCentre(page);
      await expect(asking).toBeVisible({ timeout: 2_000 });
    }).toPass({ timeout: 20_000 });
    await expect(asking.getByRole("button", { name: "gate_gps.jpg" })).toBeVisible();
    await expect(asking.getByRole("button", { name: "unplaced.jpg" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Close", exact: true })).toHaveCount(0); // nothing opened without a choice
    await asking.getByRole("button", { name: choice }).click();
    await expect(page.getByRole("img", { name: `Preview of ${choice}` })).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText(shown)).toBeVisible();
    await expect(page.getByText(other)).toHaveCount(0);
    await expect(asking).toHaveCount(0);
  }
  expect(external).toEqual([]);
});
