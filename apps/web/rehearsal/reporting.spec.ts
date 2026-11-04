import { expect, test } from "@playwright/test";

import { assertRehearsal, enter, projectId, watchExternal } from "./helpers";

// Annotations in the findings report and the JSON / GeoJSON / CSV exports, on the rehearsal copy. Read only: it creates
// nothing, so it can be run any number of times and does not disturb persistence.spec.ts. It relies on what
// workflows.spec.ts left in geopoc_rehearsal: a point and a line (active) and a polygon (deleted).

// The CSV header is pinned on purpose: annotations must never add a column or a row to it.
const CSV_HEADER = [
  "observation_id",
  "asset_id",
  "source_file",
  "source_sha256",
  "pipeline",
  "model",
  "model_version",
  "run_id",
  "model_category (as the model output it)",
  "current_category (after review)",
  "raw_score (model's own value, not a probability)",
  "score_calibrated",
  "review_status (current)",
  "revision",
  "region_type",
  "px_x_min (source image pixels, top-left origin)",
  "px_y_min (source image pixels, top-left origin)",
  "px_x_max (source image pixels, top-left origin)",
  "px_y_max (source image pixels, top-left origin)",
  "image_width_px",
  "image_height_px",
  "geometry_crs (empty: no geographic location)",
  "quality_flags",
  "created_at (UTC)",
  "frame_index (video: 0-based frame number; the px columns are in that frame)",
  "video_timestamp_ms (video: ms from the first frame)",
]
  .map((name) => (name.includes(",") ? `"${name}"` : name)) // the CSV writer quotes a name that contains a comma
  .join(",");

test("annotations appear in their own place in the report and the JSON and GeoJSON exports, and not in the CSV", async ({ page }, testInfo) => {
  const external = watchExternal(page);
  await assertRehearsal(page);
  await enter(page);
  const id = await projectId(page);
  const profiles = await (await page.request.get("/api/v1/workspace/profiles")).json();
  const reviewer = (profiles.items as { id: string; display_name: string }[]).find((p) => p.display_name === "Reviewer")!;
  const headers = { "X-Workspace-Profile": reviewer.id };

  // --- the report page ---
  await page.goto(`/projects/${id}/reports/findings`);
  const section = page.getByRole("region", { name: "9. Map annotations" });
  await expect(section).toBeVisible();
  await expect(section.getByText("Active annotations (2)")).toBeVisible();
  await expect(section.getByText("Deleted annotations (1)")).toBeVisible();
  const active = section.locator('[data-testid="annotation"][data-status="active"]');
  const deleted = section.locator('[data-testid="annotation"][data-status="deleted"]');
  await expect(active).toHaveCount(2);
  await expect(deleted).toHaveCount(1);
  await expect(deleted).toContainText("Rehearsal field");
  await expect(deleted).toContainText("Deleted: not an active mark");
  await expect(deleted).toContainText(/Deleted by/);
  await expect(active.filter({ hasText: "Rehearsal fence, east" })).toContainText(/Length \(approximate\): [\d.,]+ (m|km)/);
  await expect(active.filter({ hasText: "Rehearsal fence, east" }).getByRole("table", { name: "Version history of this annotation" })).toContainText("changed");
  await expect(active.filter({ hasText: "Rehearsal gate" })).toContainText("Point");
  await expect(page.getByTestId("count-Annotations: active")).toHaveText("2");
  await expect(page.getByTestId("count-Annotations: deleted")).toHaveText("1");
  // kept apart from the findings
  await expect(page.getByRole("region", { name: "6. Findings" })).not.toContainText("Rehearsal");
  await section.scrollIntoViewIfNeeded();
  await section.screenshot({ path: testInfo.outputPath("report-annotations.png") });

  // --- the report data ---
  const report = await (await page.request.get(`/api/v1/projects/${id}/report`, { headers })).json();
  expect(report.schema_version).toBe("1.1");
  expect([report.summary.annotations_active, report.summary.annotations_deleted]).toEqual([2, 1]);
  expect(JSON.stringify(report.observations)).not.toContain("Rehearsal");
  const reportIds = (report.annotations.items as { annotation_id: string; status: string }[]).map((i) => `${i.annotation_id}:${i.status}`).sort();

  // --- JSON export ---
  const json = await (await page.request.get(`/api/v1/projects/${id}/exports/observations.json`, { headers })).json();
  const items = json.annotations.items as { annotation_id: string; status: string; kind: string; note: string; created_by: string; versions: { version_no: number }[] }[];
  expect(items.map((i) => i.status).sort()).toEqual(["active", "active", "deleted"]);
  expect(items.map((i) => i.note).sort()).toEqual(["Rehearsal fence, east", "Rehearsal field", "Rehearsal gate"]);
  expect(items.every((i) => i.created_by === "Reviewer" && i.versions.length >= 1)).toBe(true);
  expect(items.find((i) => i.kind === "polygon")!.versions.length).toBe(2); // created, then deleted: both kept
  expect(items.map((i) => `${i.annotation_id}:${i.status}`).sort()).toEqual(reportIds);
  expect(JSON.stringify(json.observations)).not.toContain("Rehearsal");
  const observationCount = (json.observations as unknown[]).length;
  expect(observationCount).toBeGreaterThan(0);

  // --- GeoJSON export ---
  const geo = await (await page.request.get(`/api/v1/projects/${id}/exports/observations.geojson`, { headers })).json();
  const features = (geo.features as { id: string; properties: { feature_type?: string; status?: string; note?: string } }[]).filter(
    (f) => f.properties.feature_type === "user_annotation",
  );
  expect(features).toHaveLength(2);
  expect(features.every((f) => f.properties.status === "active")).toBe(true);
  expect(features.map((f) => f.properties.note).sort()).toEqual(["Rehearsal fence, east", "Rehearsal gate"]);
  expect(geo.annotations).toMatchObject({ available: true, exported: 2, deleted_not_exported: 1 });
  const deletedId = items.find((i) => i.status === "deleted")!.annotation_id;
  expect(JSON.stringify(geo.features)).not.toContain(deletedId);

  // --- CSV export: observations only, same columns ---
  const csv = (await (await page.request.get(`/api/v1/projects/${id}/exports/observations.csv`, { headers })).text()).replace(/^﻿/, "");
  expect(csv.split("\r\n")[0]).toBe(CSV_HEADER);
  expect(csv.match(/^[0-9a-f]{8}-[0-9a-f-]{27},/gm) ?? []).toHaveLength(observationCount);
  expect(csv.toLowerCase()).not.toContain("annotation");
  expect(csv).not.toContain("Rehearsal");
  for (const annotation of items) expect(csv).not.toContain(annotation.annotation_id);

  expect(external).toEqual([]);
});
