import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";

import { expect, type Page, test } from "@playwright/test";

// Walks the real application and saves screenshots for a manual UI review. It is an aid for looking, not a
// test: a person still has to judge the pictures. Run it against a running stack (scripts\dev.ps1).
//
// Input files are read from GEOPOC_REVIEW_FILES (default D:\geopoc-cache\review) and are not in the repository.
// States that cannot be produced on demand (a queued job, a missing model) are shown by answering one API
// read with a fixed response; those screenshots are named "...-injected".

const OUT = path.resolve(process.env.GEOPOC_REVIEW_OUT ?? "../../docs/ui-review/screenshots");
const FILES = process.env.GEOPOC_REVIEW_FILES ?? "D:\\geopoc-cache\\review";
const PROFILE = "Reviewer";
const PROJECT = "UI review — harbour photographs";

const consoleProblems: string[] = [];
const failedRequests: string[] = [];
let counter = 0;

function file(name: string) {
  const full = path.join(FILES, name);
  if (!existsSync(full)) throw new Error(`review input file missing: ${full}`);
  return { name, mimeType: "application/octet-stream", buffer: readFileSync(full) };
}

async function shot(page: Page, name: string, fullPage = true) {
  counter += 1;
  await page.waitForTimeout(250); // let layout and images settle
  await page.screenshot({ path: path.join(OUT, `${String(counter).padStart(2, "0")}-${name}.png`), fullPage });
}

function watch(page: Page) {
  page.on("console", (message) => {
    if (message.type() === "error" || message.type() === "warning") {
      consoleProblems.push(`[${message.type()}] ${page.url()} :: ${message.text()}`);
    }
  });
  page.on("pageerror", (error) => consoleProblems.push(`[pageerror] ${page.url()} :: ${error.message}`));
  page.on("response", (response) => {
    if (response.status() >= 400) {
      failedRequests.push(`${response.status()} ${response.request().method()} ${new URL(response.url()).pathname}`);
    }
  });
  page.on("requestfailed", (request) => failedRequests.push(`FAILED ${request.method()} ${request.url()}`));
}

const queueRow = (page: Page, name: string) =>
  page.getByRole("list", { name: "Upload queue" }).getByRole("listitem", { name, exact: true });

async function upload(page: Page, tab: string, files: ReturnType<typeof file>[]) {
  await page.getByRole("tab", { name: tab }).click();
  await page.locator("#asset-files").setInputFiles(files);
}

async function openAsset(page: Page, name: string) {
  await page.getByRole("navigation", { name: "Project" }).getByRole("link", { name: "Assets", exact: true }).click();
  await page.getByRole("table", { name: "Assets in this project" }).getByRole("link", { name, exact: true }).first().click();
  await expect(page.getByRole("heading", { name, level: 1 })).toBeVisible();
}

test("capture the Phase 1-3 screens", async ({ page }) => {
  mkdirSync(OUT, { recursive: true });
  watch(page);

  // --- workspace -------------------------------------------------------------------------------------
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Choose a profile" })).toBeVisible();
  await expect(page.getByRole("list", { name: "Service health" })).toContainText("Background worker");
  await shot(page, "login");
  const existing = page.getByRole("button", { name: `Continue as ${PROFILE}` });
  if (await existing.isVisible()) {
    await existing.click();
  } else {
    await page.getByLabel("New profile name").fill(PROFILE);
    await page.getByRole("button", { name: "Create profile and continue" }).click();
  }
  await expect(page).toHaveURL(/\/dashboard$/);
  await expect(page.getByRole("heading", { name: "Projects" })).toBeVisible();
  await page.waitForTimeout(500);
  await shot(page, "dashboard");

  // --- new project ------------------------------------------------------------------------------------
  await page.getByRole("link", { name: "Create project", exact: true }).click();
  await shot(page, "new-project-blank");
  await page.getByRole("button", { name: "Create project" }).click();
  await page.getByLabel("Name").fill(PROJECT);
  await page.getByLabel("Description (optional)").fill("Ground photographs, one overhead scene and a short clip, for the Phase 3.5 interface review.");
  await page.getByLabel("Tags (optional)").fill("review, coast");
  await page.getByLabel("Add an area of interest").check();
  await page.getByRole("button", { name: "Create project" }).click();
  await shot(page, "new-project-location-without-crs-error");
  const search = page.getByPlaceholder("e.g. EPSG:4326");
  await search.fill("WGS84 + LCC");
  await search.press("Enter");
  await expect(page.getByText("No matching CRS in the local registry")).toBeVisible();
  await shot(page, "new-project-crs-no-match");
  await search.fill("utm zone 43n");
  await search.press("Enter");
  await expect(page.getByRole("list", { name: "CRS search results" })).toBeVisible();
  await shot(page, "new-project-crs-results");
  await page.getByRole("list", { name: "CRS search results" }).getByRole("button", { name: /EPSG:32643/ }).click();
  await page.getByLabel("X (easting / longitude)").fill("500000");
  await page.getByLabel("Y (northing / latitude)").fill("3000000");
  await shot(page, "new-project-filled");
  await page.getByRole("button", { name: "Create project" }).click();
  await expect(page.getByRole("heading", { name: PROJECT, level: 1 })).toBeVisible();
  await shot(page, "project-overview");
  const projectUrl = page.url();

  // --- upload -----------------------------------------------------------------------------------------
  await page.getByRole("navigation", { name: "Project" }).getByRole("link", { name: "Upload", exact: true }).click();
  await expect(page.getByText("No files added yet.")).toBeVisible();
  await shot(page, "upload-empty");

  const photo = file("astronaut.png");
  await upload(page, "Ground photographs", [photo, file("coffee.png")]);
  await expect(queueRow(page, "coffee.png")).toContainText("Stored and validated.", { timeout: 60_000 });
  await upload(page, "Ground photographs", [{ ...photo, name: "astronaut copy.png" }]);
  await expect(queueRow(page, "astronaut copy.png")).toContainText("Duplicate");
  await upload(page, "Ground photographs", [{ ...photo, name: "renamed_png.jpg" }]);
  await expect(queueRow(page, "renamed_png.jpg")).toContainText("SIGNATURE_MISMATCH");
  await upload(page, "Ground photographs", [{ ...photo, name: "truncated.png", buffer: photo.buffer.subarray(0, 4000) }]);
  await expect(queueRow(page, "truncated.png")).toContainText("ASSET_UNREADABLE");
  await upload(page, "Ground photographs", [{ name: "notes.gif", mimeType: "image/gif", buffer: Buffer.from("GIF89a") }]);
  await expect(queueRow(page, "notes.gif")).toContainText("UNSUPPORTED_FILE_TYPE");
  await upload(page, "Ground photographs", [
    { ...photo, name: "a_very_long_file_name_from_a_camera_export_folder_IMG_20261012_093015_0001_final_final_edited_v2.png", buffer: file("chelsea.png").buffer },
  ]);
  await expect(page.getByRole("list", { name: "Upload queue" }).getByRole("listitem").last()).toContainText("Stored", { timeout: 60_000 });
  await shot(page, "upload-ground-states");

  await upload(page, "Satellite / aerial raster (GeoTIFF)", [file("harbour_scene_utm43n.tif"), file("scan_without_crs.tif"), file("complex_values.tif")]);
  await expect(queueRow(page, "complex_values.tif")).toContainText("Stored", { timeout: 60_000 });
  await upload(page, "Video", [file("walkthrough.mp4")]);
  await expect(queueRow(page, "walkthrough.mp4")).toContainText("Stored", { timeout: 60_000 });
  await shot(page, "upload-raster-and-video");

  // --- asset list -------------------------------------------------------------------------------------
  await page.getByRole("link", { name: "View all assets" }).click();
  const table = page.getByRole("table", { name: "Assets in this project" });
  await expect(table.getByRole("row")).toHaveCount(9);
  await shot(page, "assets-just-after-upload");
  await expect(table.locator("img")).toHaveCount(7, { timeout: 90_000 }); // all but the complex raster
  await shot(page, "assets-list");
  await page.getByLabel("Kind of input").selectOption("video");
  await expect(table.getByRole("row")).toHaveCount(2);
  await shot(page, "assets-filtered", false);
  await page.getByLabel("Kind of input").selectOption("");

  // --- photograph: preview, analysis, findings -----------------------------------------------------------
  await openAsset(page, "astronaut.png");
  await expect(page.getByRole("img", { name: "Preview of astronaut.png" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Run analysis" })).toBeEnabled();
  await shot(page, "asset-photo-before-analysis");

  await page.getByRole("button", { name: "Run analysis" }).click();
  const analysis = page.getByRole("listitem", { name: "Ground-image analysis job" }).first();
  await expect(analysis).toBeVisible();
  await shot(page, "asset-photo-job-running", false);
  await expect(analysis).toContainText("Needs review", { timeout: 120_000 });
  await analysis.scrollIntoViewIfNeeded();
  await shot(page, "asset-photo-job-finished");
  await analysis.getByRole("link", { name: "View results" }).click();
  await expect(page.getByTestId("detection-box").first()).toBeVisible();
  await shot(page, "ground-results");
  await page.getByRole("table", { name: "Model findings in this photograph" }).getByRole("row").nth(1).hover();
  await shot(page, "ground-results-row-highlighted", false);

  // a second run, to see the run selector
  await page.getByRole("link", { name: /Back to astronaut.png/ }).click();
  await page.getByRole("button", { name: "Run analysis" }).click();
  await expect(page.getByRole("listitem", { name: "Ground-image analysis job" })).toHaveCount(2);
  await expect(page.getByRole("listitem", { name: "Ground-image analysis job" }).first()).toContainText("Needs review", { timeout: 120_000 });
  await page.getByRole("listitem", { name: "Ground-image analysis job" }).first().getByRole("link", { name: "View results" }).click();
  await expect(page.getByLabel("Analysis run")).toBeVisible();
  await shot(page, "ground-results-two-runs", false);

  // a photograph with no findings yet
  await openAsset(page, "coffee.png");
  await page.goto(`${page.url()}/ground`);
  await expect(page.getByText("This photograph has not been analysed yet")).toBeVisible();
  await shot(page, "ground-results-not-analysed", false);

  // --- other assets -----------------------------------------------------------------------------------
  await openAsset(page, "astronaut copy.png");
  await shot(page, "asset-duplicate");
  await openAsset(page, "harbour_scene_utm43n.tif");
  await shot(page, "asset-raster-georeferenced");
  await openAsset(page, "scan_without_crs.tif");
  await shot(page, "asset-raster-without-crs");
  await openAsset(page, "complex_values.tif");
  await expect(page.getByText("No preview is available for this file")).toBeVisible();
  await shot(page, "asset-preview-unsupported-job-failed");
  // Retrying an unsupported preview cannot succeed, so the control must not be offered.
  await expect(page.getByRole("listitem", { name: "Preview job" }).getByRole("button", { name: "Retry" })).toHaveCount(0);
  await openAsset(page, "walkthrough.mp4");
  await shot(page, "asset-video");
  await openAsset(page, "a_very_long_file_name_from_a_camera_export_folder_IMG_20261012_093015_0001_final_final_edited_v2.png");
  await shot(page, "asset-long-file-name", false);

  // --- job states that cannot be produced on demand: one API read is answered with fixed data ------------
  await openAsset(page, "coffee.png");
  const assetId = page.url().split("/").pop()!;
  const projectId = projectUrl.split("/").pop()!;
  const base = { project_id: projectId, asset_id: assetId, kind: "analyze", stage: null, progress: null, cancel_requested: false, attempt: 1, max_attempts: 2, payload: { pipeline: "ground_image", model: { name: "fasterrcnn_resnet50_fpn_v2", version: "coco_v1" } }, result: null, error: null, has_partial_outputs: false, retry_of_job_id: null, created_at: new Date().toISOString(), started_at: null, finished_at: null };
  const injected = [
    { ...base, id: "00000000-0000-4000-8000-000000000001", status: "queued", attempt: 0 },
    { ...base, id: "00000000-0000-4000-8000-000000000002", status: "processing", stage: "running model", progress: 0.5 },
    { ...base, id: "00000000-0000-4000-8000-000000000003", status: "validating", stage: "checking inputs" },
    { ...base, id: "00000000-0000-4000-8000-000000000004", status: "failed", attempt: 2, error: { code: "GPU_OUT_OF_MEMORY", message: "The GPU ran out of memory during inference." } },
    { ...base, id: "00000000-0000-4000-8000-000000000005", status: "cancelled", cancel_requested: true },
    { ...base, id: "00000000-0000-4000-8000-000000000006", status: "completed_with_warnings", progress: 1, result: { run_id: "00000000-0000-4000-8000-0000000000aa", observations: 3, below_min_score: 120, rejected_outputs: 1 } },
    { ...base, id: "00000000-0000-4000-8000-000000000007", status: "completed", progress: 1, result: { run_id: "00000000-0000-4000-8000-0000000000ab", observations: 0, below_min_score: 87, rejected_outputs: 0 } },
  ];
  await page.route("**/api/v1/projects/*/jobs?*", (route) => route.fulfill({ json: { items: injected, next_cursor: null } }));
  await page.reload();
  await expect(page.getByRole("listitem", { name: "Ground-image analysis job" })).toHaveCount(7);
  await page.getByRole("heading", { name: "Analysis and background jobs" }).scrollIntoViewIfNeeded();
  await shot(page, "job-states-injected");
  await page.unroute("**/api/v1/projects/*/jobs?*");

  const health = await (await page.request.get("/health/models")).json();
  await page.route("**/health/models", (route) =>
    route.fulfill({ json: { ...health, models: health.models.map((m: object) => ({ ...m, available: false, reason_unavailable: "The model weights are not installed on this machine." })) } }),
  );
  await page.reload();
  await expect(page.getByText("This model cannot be run")).toBeVisible();
  await page.getByRole("heading", { name: "Analysis and background jobs" }).scrollIntoViewIfNeeded();
  await shot(page, "model-unavailable-injected", false);
  await page.unroute("**/health/models");
  await page.route("**/health/models", (route) => route.fulfill({ json: { ...health, worker: { running: false, last_seen_at: null, capabilities: null } } }));
  await page.reload();
  await expect(page.getByText("No worker is running")).toBeVisible();
  await page.getByRole("heading", { name: "Analysis and background jobs" }).scrollIntoViewIfNeeded();
  await shot(page, "worker-not-running-injected", false);
  await page.unroute("**/health/models");

  // --- settings ---------------------------------------------------------------------------------------
  for (const tab of ["diagnostics", "models", "crs", "sources", "storage"]) {
    await page.goto(`/settings/${tab}`);
    await page.waitForTimeout(600);
    await shot(page, `settings-${tab}`);
  }

  // --- placeholders and edge pages ------------------------------------------------------------------------
  await page.goto(`${projectUrl}/map`);
  await shot(page, "placeholder-map", false);
  await page.goto("/projects/00000000-0000-4000-8000-000000000000");
  await expect(page.getByText("This project does not exist")).toBeVisible();
  await shot(page, "project-not-found", false);
  await page.goto("/no/such/page");
  await shot(page, "page-not-found", false);

  // --- dashboard with content, search, and the API being unreachable ----------------------------------------
  await page.goto("/dashboard");
  await expect(page.getByRole("list", { name: "Project list" })).toBeVisible();
  await shot(page, "dashboard-with-project");
  await page.getByLabel("Search projects").fill("nothing matches this");
  await page.getByRole("button", { name: "Apply" }).click();
  await expect(page.getByText("No projects match these filters")).toBeVisible();
  await shot(page, "dashboard-no-match", false);
  await page.route("**/api/v1/**", (route) => route.abort());
  await page.route("**/health", (route) => route.abort());
  await page.reload();
  await expect(page.getByRole("alert").first()).toBeVisible();
  await shot(page, "api-unreachable-injected", false);
  await page.unroute("**/api/v1/**");
  await page.unroute("**/health");

  // --- narrow screen ----------------------------------------------------------------------------------
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/dashboard");
  await expect(page.getByRole("list", { name: "Project list" })).toBeVisible();
  await shot(page, "narrow-dashboard");
  await page.goto(projectUrl);
  await shot(page, "narrow-project-overview");
  await page.goto(`${projectUrl}/assets/new`);
  await shot(page, "narrow-upload");
  await page.goto(`${projectUrl}/assets`);
  await expect(page.getByRole("table", { name: "Assets in this project" })).toBeVisible();
  await shot(page, "narrow-assets");
  await openAsset(page, "astronaut.png");
  await shot(page, "narrow-asset-detail");
  await page.goto(`${page.url()}/ground`);
  await expect(page.getByTestId("detection-box").first()).toBeVisible();
  await shot(page, "narrow-ground-results");
  await page.goto("/settings/models");
  await page.waitForTimeout(600);
  await shot(page, "narrow-settings-models");
  await page.goto("/projects/new");
  await shot(page, "narrow-new-project");

  writeFileSync(
    path.join(OUT, "..", "browser-log.json"),
    JSON.stringify({ captured_at: new Date().toISOString(), screenshots: counter, console_problems: consoleProblems, failed_requests: failedRequests }, null, 2),
  );
});
