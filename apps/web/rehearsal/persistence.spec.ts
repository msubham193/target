import { expect, test } from "@playwright/test";

import { assertRehearsal, enter, nav, openProject, projectId } from "./helpers";

// Run after the rehearsal API, worker and web were stopped and started again: what the workflows created is still there,
// read from the database and the files, and nothing was run again.
test("everything created in the rehearsal survives a restart", async ({ page }) => {
  await assertRehearsal(page);
  await enter(page);
  const id = await projectId(page);

  const annotations = await (await page.request.get(`/api/v1/projects/${id}/annotations?include_deleted=true`)).json();
  expect([annotations.active, annotations.deleted]).toEqual([2, 1]);
  expect(annotations.items.map((i: { note: string }) => i.note).sort()).toEqual(["Rehearsal fence, east", "Rehearsal field", "Rehearsal gate"]);

  const jobs = await (await page.request.get(`/api/v1/projects/${id}/jobs?kind=analyze&limit=100`)).json();
  const video = (jobs.items as { payload: { pipeline?: string }; result: { run_id?: string } | null }[]).filter((j) => j.payload.pipeline === "video");
  expect(video).toHaveLength(1);
  const runId = video[0]!.result!.run_id!;
  const assetId = (jobs.items as { asset_id: string; payload: { pipeline?: string } }[]).find((j) => j.payload.pipeline === "video")!.asset_id;
  const frames = await (await page.request.get(`/api/v1/projects/${id}/assets/${assetId}/video-analysis/runs/${runId}/frames`)).json();
  expect(frames.frames.length).toBeGreaterThan(0);
  const image = await page.request.get(frames.frames[0].image_url);
  expect(image.status()).toBe(200);

  const locations = await (await page.request.get(`/api/v1/projects/${id}/photo-locations`)).json();
  expect(locations.confirmed).toBeGreaterThanOrEqual(2);

  await openProject(page);
  await nav(page).getByRole("link", { name: "3D View" }).click();
  await expect(page.getByRole("list", { name: "Rasters and whether they are elevation" })).toContainText("rehearsal_dem.tifElevation");
  await expect(page.getByTestId("terrain-canvas")).toBeVisible({ timeout: 30_000 });
});
