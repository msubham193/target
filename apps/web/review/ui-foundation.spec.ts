import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";

import { expect, type Page, test } from "@playwright/test";

// Walks all twelve screens of the UI/UX foundation phase on a scratch stack and saves screenshots at a desktop
// and a phone width, plus a log of console problems, failed requests and simple accessibility checks. It is an
// aid for looking, not a test: a person still has to judge the pictures.
//
// Input files come from GEOPOC_REVIEW_FILES (default D:\geopoc-cache\review) and are not in the repository.

const OUT = path.resolve(process.env.GEOPOC_REVIEW_OUT ?? "../../docs/ui-review/screenshots-ui-foundation");
const FILES = process.env.GEOPOC_REVIEW_FILES ?? "D:\\geopoc-cache\\review";
const PROFILE = "Reviewer";
const PROJECT = "Harbour survey";
const DESKTOP = { width: 1440, height: 900 };
const NARROW = { width: 390, height: 844 };

const consoleProblems: string[] = [];
const failedRequests: string[] = [];
const checks: Record<string, unknown>[] = [];
const external: string[] = [];
let counter = 0;

function file(name: string) {
  const full = path.join(FILES, name);
  if (!existsSync(full)) throw new Error(`review input file missing: ${full}`);
  return { name, mimeType: "application/octet-stream", buffer: readFileSync(full) };
}

/** Simple, automatable accessibility and layout checks. They do not replace a screen-reader review. */
async function audit(page: Page, name: string) {
  const result = await page.evaluate(() => {
    const visible = (el: Element) => {
      const box = (el as HTMLElement).getBoundingClientRect();
      const style = getComputedStyle(el);
      return box.width > 0 && box.height > 0 && style.visibility !== "hidden" && style.display !== "none";
    };
    const nameOf = (el: Element) =>
      (el.getAttribute("aria-label") ?? "").trim() ||
      (el.getAttribute("title") ?? "").trim() ||
      (el.textContent ?? "").trim() ||
      Array.from(el.querySelectorAll("img[alt]")).map((img) => img.getAttribute("alt")).join("").trim();
    const unnamedControls = Array.from(document.querySelectorAll("button, a[href], [role=button], [role=tab]"))
      .filter(visible)
      .filter((el) => !nameOf(el))
      .map((el) => el.outerHTML.slice(0, 120));
    const unlabelledFields = Array.from(document.querySelectorAll("input:not([type=hidden]), select, textarea"))
      .filter((el) => (el as HTMLInputElement).type !== "file" || visible(el))
      .filter((el) => {
        const id = el.getAttribute("id");
        const hasFor = id ? document.querySelector(`label[for="${CSS.escape(id)}"]`) : null;
        return !hasFor && !el.closest("label") && !el.getAttribute("aria-label") && !el.getAttribute("aria-labelledby");
      })
      .map((el) => el.outerHTML.slice(0, 120));
    const imagesWithoutAlt = Array.from(document.querySelectorAll("img")).filter((img) => !img.hasAttribute("alt")).length;
    const ids = Array.from(document.querySelectorAll("[id]")).map((el) => el.id);
    const duplicateIds = [...new Set(ids.filter((id, i) => ids.indexOf(id) !== i))];
    return {
      h1: Array.from(document.querySelectorAll("h1")).filter(visible).map((h) => h.textContent?.trim()),
      horizontalOverflowPx: document.documentElement.scrollWidth - document.documentElement.clientWidth,
      unnamedControls,
      unlabelledFields,
      imagesWithoutAlt,
      duplicateIds,
      lang: document.documentElement.lang,
    };
  });
  checks.push({ screen: name, width: page.viewportSize()?.width, ...result });
}

async function shot(page: Page, name: string, fullPage = true) {
  counter += 1;
  await page.waitForTimeout(300); // let layout and images settle
  await page.screenshot({ path: path.join(OUT, `${String(counter).padStart(2, "0")}-${name}.png`), fullPage });
  await audit(page, name);
}

/** The same screen at desktop and phone width. */
async function both(page: Page, name: string, ready?: () => Promise<void>) {
  await page.setViewportSize(DESKTOP);
  if (ready) await ready();
  await shot(page, `${name}-desktop`);
  await page.setViewportSize(NARROW);
  await page.waitForTimeout(300);
  await shot(page, `${name}-narrow`);
  await page.setViewportSize(DESKTOP);
}

function watch(page: Page) {
  page.on("console", (message) => {
    if (message.type() === "error" || message.type() === "warning") {
      consoleProblems.push(`[${message.type()}] ${new URL(page.url()).pathname} :: ${message.text()}`);
    }
  });
  page.on("pageerror", (error) => consoleProblems.push(`[pageerror] ${new URL(page.url()).pathname} :: ${error.message}`));
  page.on("response", (response) => {
    if (response.status() >= 400) {
      failedRequests.push(`${response.status()} ${response.request().method()} ${new URL(response.url()).pathname}`);
    }
  });
  page.on("requestfailed", (request) => failedRequests.push(`FAILED ${request.method()} ${request.url()}`));
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.protocol.startsWith("http") && url.hostname !== "127.0.0.1") external.push(request.url());
  });
}

const nav = (page: Page) => page.getByRole("navigation", { name: "Main" });
const queueRow = (page: Page, name: string) =>
  page.getByRole("list", { name: "Upload queue" }).getByRole("listitem", { name, exact: true });

async function upload(page: Page, tab: string, files: ReturnType<typeof file>[]) {
  await page.getByRole("tab", { name: tab }).click();
  await page.locator("#asset-files").setInputFiles(files);
}

test("walk the twelve screens", async ({ page }) => {
  mkdirSync(OUT, { recursive: true });
  watch(page);

  // 1. Login ----------------------------------------------------------------------------------------------
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Choose a profile" })).toBeVisible();
  await expect(page.getByRole("list", { name: "Service health" })).toContainText("Background worker: running", { timeout: 60_000 });
  await both(page, "01-login");
  await page.getByRole("button", { name: "Create profile and continue" }).click();
  await expect(page.getByRole("alert")).toContainText("Enter a name for the profile.");
  await shot(page, "01-login-validation", false);
  await page.getByLabel("New profile name").fill(PROFILE);
  await page.getByRole("button", { name: "Create profile and continue" }).click();
  await expect(page).toHaveURL(/\/dashboard$/);

  // keyboard: the skip link is the first stop and is visible when focused
  await page.keyboard.press("Tab");
  const skip = page.getByRole("link", { name: "Skip to content" });
  await expect(skip).toBeFocused();
  await shot(page, "02-dashboard-skip-link-focused", false);

  // 2. Project Dashboard (empty) ----------------------------------------------------------------------------
  await expect(page.getByRole("main").getByText("No projects yet")).toBeVisible();
  await both(page, "02-dashboard-empty");

  // 3. New Project & Location Setup ---------------------------------------------------------------------------
  await nav(page).getByRole("link", { name: "New Project & Location" }).click();
  await both(page, "03-new-project-blank");
  await page.getByLabel("Name").fill(PROJECT);
  await page.getByLabel("Description (optional)").fill("Ground photographs, two overhead scenes and a short clip.");
  await page.getByLabel("Tags (optional)").fill("review, coast");
  await page.getByLabel("Add an area of interest").check();
  await page.getByLabel("X (easting / longitude)").fill("500000");
  await page.getByLabel("Y (northing / latitude)").fill("3000000");
  await page.getByRole("button", { name: "Create project" }).click();
  await expect(page.getByText("A location without a CRS is not accepted")).toBeVisible();
  await shot(page, "03-new-project-crs-required");
  const search = page.getByPlaceholder("e.g. EPSG:4326");
  await search.fill("WGS84 + LCC");
  await search.press("Enter");
  await expect(page.getByText("No matching CRS in the local registry")).toBeVisible();
  await shot(page, "03-new-project-crs-no-match", false);
  await search.fill("utm zone 43n");
  await search.press("Enter");
  await page.getByRole("list", { name: "CRS search results" }).getByRole("button", { name: /EPSG:32643/ }).click();
  await shot(page, "03-new-project-filled");
  await page.getByRole("button", { name: "Create project" }).click();
  await expect(page.getByRole("heading", { name: PROJECT, level: 1 })).toBeVisible();
  const projectUrl = page.url();
  await both(page, "03b-project-overview");

  // 4. Data Upload & Ingestion --------------------------------------------------------------------------------
  await nav(page).getByRole("link", { name: "Upload & Ingestion" }).click();
  await expect(page.getByText("No files added yet.")).toBeVisible();
  await both(page, "04-upload-empty");
  const photo = file("astronaut.png");
  await upload(page, "Ground photographs", [photo, file("coffee.png")]);
  await expect(queueRow(page, "coffee.png")).toContainText("Stored and validated.", { timeout: 60_000 });
  await upload(page, "Ground photographs", [{ ...photo, name: "astronaut copy.png" }]);
  await expect(queueRow(page, "astronaut copy.png")).toContainText("Duplicate");
  await upload(page, "Ground photographs", [{ ...photo, name: "renamed_png.jpg" }]);
  await expect(queueRow(page, "renamed_png.jpg")).toContainText("SIGNATURE_MISMATCH");
  await upload(page, "Ground photographs", [{ name: "notes.gif", mimeType: "image/gif", buffer: Buffer.from("GIF89a") }]);
  await expect(queueRow(page, "notes.gif")).toContainText("This file type is not supported here");
  await upload(page, "Satellite / aerial raster (GeoTIFF)", [file("harbour_scene_utm43n.tif"), file("scan_without_crs.tif")]);
  await expect(queueRow(page, "scan_without_crs.tif")).toContainText("Stored", { timeout: 60_000 });
  await upload(page, "Video", [file("walkthrough_h264.mp4")]);
  await expect(queueRow(page, "walkthrough_h264.mp4")).toContainText("Stored", { timeout: 60_000 });
  await both(page, "04-upload-states");

  await page.getByRole("link", { name: "View all assets" }).click();
  const table = page.getByRole("table", { name: "Assets in this project" });
  await expect(table.getByRole("row")).toHaveCount(7); // header + 6 stored files
  await expect(table.locator("img")).toHaveCount(6, { timeout: 120_000 });
  await both(page, "04b-assets");
  await table.getByRole("link", { name: "astronaut.png", exact: true }).click();
  await expect(page.getByRole("img", { name: "Preview of astronaut.png" })).toBeVisible();
  // the original is unchanged: its checksum matches the uploaded bytes
  const assetId = page.url().split("/").pop()!;
  const original = await page.request.get(`/api/v1/assets/${assetId}/content`);
  const { createHash } = await import("node:crypto");
  checks.push({
    screen: "upload-integrity",
    original_sha256_matches: createHash("sha256").update(await original.body()).digest("hex") === createHash("sha256").update(photo.buffer).digest("hex"),
  });
  await both(page, "04c-asset-detail");

  // 6. Ground Image Analysis -----------------------------------------------------------------------------------
  await nav(page).getByRole("link", { name: "Ground Image Analysis" }).click();
  await expect(page.getByRole("heading", { name: "Ground Image Analysis", level: 1 })).toBeVisible();
  await page.goto(`${projectUrl}/assets/${assetId}/ground`);
  await expect(page.getByText("This photograph has not been analysed yet")).toBeVisible();
  await both(page, "06-ground-not-analysed");
  await page.getByRole("button", { name: "Run analysis" }).click();
  const job = page.getByRole("list", { name: "Latest analysis job" }).getByRole("listitem").first();
  await expect(job).toBeVisible();
  await shot(page, "06-ground-job-running", false);
  await expect(page.getByTestId("detection-box").first()).toBeVisible({ timeout: 180_000 });
  await both(page, "06-ground-results");
  const findings = page.getByRole("table", { name: "Model findings in this photograph" });
  await findings.getByRole("row").nth(1).click();
  await expect(page.getByText(/^Finding 1:/)).toBeVisible();
  await shot(page, "06-ground-finding-selected");
  await page.getByRole("button", { name: "Zoom in" }).click();
  await page.getByRole("button", { name: "Zoom in" }).click();
  await shot(page, "06-ground-zoomed", false);
  await page.getByRole("button", { name: "Fit to view" }).click();
  await page.getByText("Advanced technical details").click();
  await shot(page, "06-ground-advanced-details");

  // 5. Map & Analysis View (planned) -----------------------------------------------------------------------------
  await nav(page).getByRole("link", { name: "Map & Analysis planned" }).click();
  await expect(page.getByText("No map is drawn")).toBeVisible();
  await both(page, "05-map");

  // 7. Satellite Image Analysis -----------------------------------------------------------------------------------
  await nav(page).getByRole("link", { name: "Satellite Image Analysis" }).click();
  await expect(page.getByRole("heading", { name: "Satellite Image Analysis", level: 1 })).toBeVisible();
  await page.getByRole("link", { name: /harbour_scene_utm43n\.tif/ }).first().click();
  await expect(page.getByRole("img", { name: "Preview of harbour_scene_utm43n.tif" })).toBeVisible();
  await both(page, "07-satellite-georeferenced");
  await page.getByRole("link", { name: /scan_without_crs\.tif/ }).first().click();
  await expect(page.getByRole("img", { name: "Preview of scan_without_crs.tif" })).toBeVisible();
  await shot(page, "07-satellite-without-crs");

  // 8. Video Analysis -----------------------------------------------------------------------------------------------
  await nav(page).getByRole("link", { name: "Video Analysis" }).click();
  await expect(page.getByRole("heading", { name: "Video Analysis", level: 1 })).toBeVisible();
  const video = page.getByLabel(/^Video player:/);
  await expect(video).toBeVisible();
  await expect.poll(() => video.evaluate((v: HTMLVideoElement) => v.readyState), { timeout: 30_000 }).toBeGreaterThanOrEqual(1);
  await page.getByRole("button", { name: "Next frame" }).click();
  await page.getByRole("button", { name: "Next frame" }).click();
  checks.push({ screen: "video-playback", currentTime: await video.evaluate((v: HTMLVideoElement) => v.currentTime), error: await video.evaluate((v: HTMLVideoElement) => v.error?.code ?? null) });
  await both(page, "08-video");

  // 9. 3D View (planned) -------------------------------------------------------------------------------------------
  await nav(page).getByRole("link", { name: "3D View planned" }).click();
  await expect(page.getByText("No compatible 3D data")).toBeVisible();
  await both(page, "09-3d");

  // 10. Observations ------------------------------------------------------------------------------------------------
  await nav(page).getByRole("link", { name: "Observations", exact: true }).click();
  const observations = page.getByRole("table", { name: "Observations in this project" });
  await expect(observations).toBeVisible();
  await both(page, "10-observations");
  await observations.getByRole("row").nth(1).click();
  await expect(page.getByRole("region", { name: "Provenance" })).toBeVisible();
  await shot(page, "10-observations-detail");
  await page.getByLabel("Review state").selectOption("verified");
  await expect(page.getByText("No observations match these filters")).toBeVisible();
  await shot(page, "10-observations-filtered-empty", false);
  await page.getByLabel("Review state").selectOption("");

  // 11. Reports ------------------------------------------------------------------------------------------------------
  await nav(page).getByRole("link", { name: "Reports", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Reports", level: 1 })).toBeVisible();
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download JSON" }).click();
  const exported = await download;
  const exportedPath = path.join(OUT, "..", "observations-export.json");
  await exported.saveAs(exportedPath);
  const parsed = JSON.parse(readFileSync(exportedPath, "utf-8")) as { observations: unknown[] };
  checks.push({ screen: "reports-export", file: exported.suggestedFilename(), records: parsed.observations.length });
  await both(page, "11-reports");

  // 12. Settings -------------------------------------------------------------------------------------------------------
  for (const tab of ["models", "crs", "sources", "network", "diagnostics"]) {
    await page.goto(`/settings/${tab}`);
    await page.waitForTimeout(700);
    if (tab === "models" || tab === "network") await both(page, `12-settings-${tab}`);
    else await shot(page, `12-settings-${tab}`);
  }

  // Dashboard with content, collapsed sidebar, mobile drawer, edge pages ------------------------------------------------
  await page.goto("/dashboard");
  await expect(page.getByRole("list", { name: "Project list" })).toBeVisible();
  await expect(page.getByRole("list", { name: "Recent activity" })).toBeVisible();
  await both(page, "02-dashboard-with-project");
  await page.getByRole("button", { name: "Collapse sidebar" }).click();
  await page.goto(`${projectUrl}/observations`);
  await shot(page, "shell-sidebar-collapsed", false);
  await page.getByRole("button", { name: "Expand sidebar" }).click();
  await page.setViewportSize(NARROW);
  await page.getByRole("button", { name: "Open navigation" }).click();
  await expect(page.getByRole("dialog", { name: "Navigation" })).toBeVisible();
  checks.push({ screen: "drawer-focus-on-open", focused: await page.evaluate(() => document.activeElement?.getAttribute("aria-label") ?? document.activeElement?.tagName) });
  await shot(page, "shell-narrow-drawer-open", false);
  await page.keyboard.press("Escape");
  checks.push({ screen: "drawer-escape-closes", closed: (await page.getByRole("dialog", { name: "Navigation" }).count()) === 0 });
  await page.setViewportSize(DESKTOP);
  await page.goto("/projects/00000000-0000-4000-8000-000000000000");
  await expect(page.getByText("This project does not exist")).toBeVisible();
  await shot(page, "edge-project-not-found", false);
  await page.goto("/no/such/page");
  await shot(page, "edge-page-not-found", false);

  writeFileSync(
    path.join(OUT, "..", "ui-foundation-browser-log.json"),
    JSON.stringify(
      {
        captured_at: new Date().toISOString(),
        screenshots: counter,
        console_problems: consoleProblems,
        failed_requests: failedRequests,
        external_requests: external,
        checks,
      },
      null,
      2,
    ),
  );
});
