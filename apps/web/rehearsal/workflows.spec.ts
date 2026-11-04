import { expect, test } from "@playwright/test";

import { assertRehearsal, elevationTiff, enter, nav, openProject, projectId, watchExternal } from "./helpers";

// The main workflows on the rehearsal copy (geopoc_rehearsal, migrated to 0009), with the data already in it plus small
// synthetic fixtures. Nothing here can reach the working database: every spec asks the app which database it is on first.
test.describe.configure({ mode: "serial" });

test.beforeAll(async ({ request }) => {
  const health = await (await request.get("/health")).json();
  expect(health.database.name).toBe("geopoc_rehearsal");
  expect(health.database.schema_revision).toBe("0009");
});

test("photo locations, workspace and comparison", async ({ page }, testInfo) => {
  const external = watchExternal(page);
  await assertRehearsal(page);
  await enter(page);
  await openProject(page);

  // Give two photographs a confirmed position (typed), through the existing panel.
  for (const [name, lat, lon] of [
    [/astronaut\.png/, "27.1135", "75.0105"],
    [/coffee\.png/, "27.1141", "75.0118"],
  ] as const) {
    await nav(page).getByRole("link", { name: "Ground Image Analysis" }).click();
    await page.getByRole("list", { name: "Photographs" }).getByRole("link", { name }).click();
    const panel = page.getByRole("region", { name: "Photo location", exact: true });
    await expect(panel).toBeVisible();
    if (await panel.getByRole("region", { name: "Confirmed location" }).count()) continue;
    await panel.getByLabel("Latitude").fill(lat);
    await panel.getByLabel("Longitude").fill(lon);
    await panel.getByRole("checkbox", { name: /I have checked this position/ }).check();
    await panel.getByRole("button", { name: "Save location" }).click();
    await expect(panel.getByRole("region", { name: "Confirmed location" })).toBeVisible();
  }

  // The workspace lists the photographs, and comparing two shows only stored facts.
  await nav(page).getByRole("link", { name: "Photo Workspace" }).click();
  await expect(page.getByRole("list", { name: "Project photographs" })).toBeVisible();
  await page.getByLabel("Compare astronaut.png").check();
  await page.getByLabel("Compare coffee.png").check();
  await page.getByRole("tab", { name: /Compare/ }).click();
  await expect(page.getByText("What this comparison does not say")).toBeVisible();
  await expect(page.getByTestId("distances")).toBeVisible(); // both have confirmed camera positions
  await page.screenshot({ path: testInfo.outputPath("compare.png") });
  await page.getByRole("tab", { name: /Map/ }).click();
  await expect(page.getByTestId("map").or(page.getByRole("region", { name: "Map of project layers" }))).toBeVisible();
  expect(external).toEqual([]);
});

test("findings report", async ({ page }, testInfo) => {
  await assertRehearsal(page);
  await enter(page);
  const id = await projectId(page);
  const profiles = await (await page.request.get("/api/v1/workspace/profiles")).json();
  const reviewer = (profiles.items as { id: string; display_name: string }[]).find((p) => p.display_name === "Reviewer")!;
  // generating a report is audited, so it needs a profile
  const report = await (await page.request.get(`/api/v1/projects/${id}/report`, { headers: { "X-Workspace-Profile": reviewer.id } })).json();
  expect(report.read_only).toBe(true);
  expect(report.summary.photos_total).toBeGreaterThanOrEqual(5);
  expect(report.limitations.length).toBeGreaterThan(0);
  await page.goto(`/projects/${id}/reports/findings`);
  await expect(page.getByRole("heading", { level: 1, name: "UI review — harbour photographs" })).toBeVisible();
  for (const section of ["1. Summary", "3. Limitations", "4. Source files", "6. Findings"]) {
    await expect(page.getByRole("heading", { name: section })).toBeVisible();
  }
  await page.screenshot({ path: testInfo.outputPath("report.png"), fullPage: false });
});

test("map annotations: create, edit, delete, history", async ({ page }, testInfo) => {
  const external = watchExternal(page);
  await assertRehearsal(page);
  await enter(page);
  await openProject(page);
  await nav(page).getByRole("link", { name: "Map & Analysis", exact: true }).click();
  const panel = page.getByRole("region", { name: "Annotations", exact: true });
  await expect(panel.getByRole("group", { name: "Draw an annotation" })).toBeVisible();
  const list = page.getByRole("list", { name: "Annotations in this project" });
  const click = async (dx: number, dy: number) => {
    const canvas = page.getByRole("region", { name: "Map of project layers" }).locator("canvas");
    await canvas.scrollIntoViewIfNeeded();
    const box = (await canvas.boundingBox())!;
    await page.mouse.click(box.x + box.width / 2 + dx, box.y + box.height / 2 + dy);
  };
  // A click sent within ~300 ms of pressing "Add ..." was ignored on this map (imagery loaded), so each click waits until the
  // drawing panel shows it registered (a person cannot click that fast).
  const place = async (dx: number, dy: number, count: number) => {
    for (let attempt = 0; attempt < 6; attempt++) {
      await click(dx, dy);
      await page.waitForTimeout(350);
      const hint = await page.getByRole("form", { name: "New annotation" }).innerText();
      if (count === 0 ? hint.includes("Click again") : hint.includes(`${count} position`)) return;
    }
    throw new Error("a map click was not registered after 6 tries");
  };

  await panel.getByRole("button", { name: "Add point" }).click();
  await place(0, 0, 0);
  await panel.getByLabel("Note").fill("Rehearsal gate");
  await panel.getByRole("button", { name: "Save annotation" }).click();
  await expect(list).toContainText("Rehearsal gate");

  await panel.getByRole("button", { name: "Add line" }).click();
  await place(-80, 30, 1);
  await place(80, 30, 2);
  await panel.getByLabel("Note").fill("Rehearsal fence");
  await panel.getByRole("button", { name: "Save annotation" }).click();
  await expect(list.locator("> li", { hasText: "Rehearsal fence" })).toContainText(/Length \(approximate\): [\d.,]+ (m|km)/);

  await panel.getByRole("button", { name: "Add polygon" }).click();
  await place(-60, -50, 1);
  await place(60, -50, 2);
  await place(0, -110, 3);
  await panel.getByLabel("Note").fill("Rehearsal field");
  await panel.getByRole("button", { name: "Save annotation" }).click();
  await expect(list.locator("> li", { hasText: "Rehearsal field" })).toContainText(/Area \(approximate\): [\d.,]+ (m²|ha|km²)/);
  await page.screenshot({ path: testInfo.outputPath("annotations.png") });

  // edit, history, delete (soft) with the history kept
  const fence = list.locator("> li", { hasText: "Rehearsal fence" });
  await fence.getByRole("button", { name: "Edit line" }).click();
  await panel.getByLabel("Note").fill("Rehearsal fence, east");
  await panel.getByRole("button", { name: "Save changes" }).click();
  await expect(panel).toContainText("saved as a new version");
  await list.locator("> li", { hasText: "Rehearsal fence, east" }).getByRole("button", { name: "Show history of line" }).click();
  await expect(panel.getByRole("region", { name: "Annotation history" })).toContainText("Version 2 · changed");
  const field = list.locator("> li", { hasText: "Rehearsal field" });
  await field.getByRole("button", { name: "Delete polygon" }).click();
  await field.getByRole("button", { name: "Delete annotation" }).click();
  await expect(list).not.toContainText("Rehearsal field");
  await expect(panel).toContainText("2 active · 1 deleted");
  await page.reload();
  await expect(page.getByRole("region", { name: "Annotations", exact: true })).toContainText("2 active · 1 deleted");
  expect(external).toEqual([]);
});

test("video-frame analysis of the existing video with the installed detector", async ({ page }, testInfo) => {
  const external = watchExternal(page);
  await assertRehearsal(page);
  await enter(page);
  await openProject(page);
  await nav(page).getByRole("link", { name: "Video Analysis" }).click();
  await expect(page.getByText("This video has not been analysed yet")).toBeVisible();
  const form = page.getByRole("form", { name: "Analyse frames" });
  const model = form.getByLabel("Model");
  await expect(model).toBeVisible();
  const options = await model.locator("option").allTextContents();
  testInfo.annotations.push({ type: "models", description: options.join(" | ") });
  await form.getByLabel("Take a frame every (seconds)").fill("1");
  await form.getByLabel("At most this many frames").fill("10");
  await expect(form.getByText(/frames? would be analysed/)).toBeVisible();
  await expect(form.getByRole("button", { name: "Analyse these frames" })).toBeEnabled();
  await form.getByRole("button", { name: "Analyse these frames" }).click();
  const latest = page.getByRole("list", { name: "Latest analysis job" });
  await expect(latest).toContainText(/Needs review|Completed/, { timeout: 280_000 });
  testInfo.annotations.push({ type: "job", description: (await latest.textContent()) ?? "" });
  const strip = page.getByRole("list", { name: "Sampled frames" });
  await expect(strip.getByRole("listitem").first()).toBeVisible();
  const frames = await strip.getByRole("listitem").count();
  expect(frames).toBeGreaterThan(0);
  const timeline = page.getByRole("group", { name: "Sampled frames on the video timeline" });
  await expect(timeline.getByRole("button")).toHaveCount(frames);
  await timeline.getByRole("button").last().click();
  await expect(page.getByTestId("frame-viewer").getByRole("img")).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("video.png") });
  // reopening does not run anything again
  const id = await projectId(page);
  await page.reload();
  await expect(page.getByRole("list", { name: "Sampled frames" }).getByRole("listitem")).toHaveCount(frames);
  const jobs = await (await page.request.get(`/api/v1/projects/${id}/jobs?kind=analyze&limit=100`)).json();
  const videoJobs = (jobs.items as { payload: { pipeline?: string } }[]).filter((j) => j.payload.pipeline === "video");
  expect(videoJobs).toHaveLength(1);
  expect(external).toEqual([]);
});

test("3D terrain viewer with an uploaded synthetic elevation raster", async ({ page }, testInfo) => {
  const external = watchExternal(page);
  await assertRehearsal(page);
  await enter(page);
  await openProject(page);
  await nav(page).getByRole("link", { name: "Upload & Ingestion" }).click();
  await page.getByRole("tab", { name: "Satellite / aerial raster (GeoTIFF)" }).click();
  await page.locator("#asset-files").setInputFiles({ name: "rehearsal_dem.tif", mimeType: "image/tiff", buffer: elevationTiff() });
  await expect(page.getByRole("list", { name: "Upload queue" }).getByRole("listitem", { name: "rehearsal_dem.tif", exact: true })).toContainText("Stored", {
    timeout: 60_000,
  });
  await nav(page).getByRole("link", { name: "3D View" }).click();
  const list = page.getByRole("list", { name: "Rasters and whether they are elevation" });
  await expect(list).toContainText("rehearsal_dem.tifElevation");
  testInfo.annotations.push({ type: "raster states", description: ((await list.textContent()) ?? "").replace(/\s+/g, " ") });
  await list.getByRole("button", { name: /rehearsal_dem\.tif/ }).click();
  const canvas = page.getByTestId("terrain-canvas");
  await expect(canvas).toBeVisible({ timeout: 30_000 });
  const yaw = Number(await canvas.getAttribute("data-yaw"));
  const box = (await canvas.boundingBox())!;
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width / 2 + 100, box.y + box.height / 2 + 20, { steps: 5 });
  await page.mouse.up();
  expect(Number(await canvas.getAttribute("data-yaw"))).not.toBe(yaw);
  await page.mouse.move(box.x + box.width / 2, box.y + box.height * 0.6);
  await expect(page.getByTestId("hover-readout")).toContainText(/Elevation [\d,.]+ m/, { timeout: 10_000 });
  await canvas.screenshot({ path: testInfo.outputPath("terrain.png") });
  // the existing imagery rasters are not offered as terrain unless the file says so
  expect(external).toEqual([]);
});
