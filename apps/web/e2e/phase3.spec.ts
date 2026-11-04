import { crc32, deflateSync } from "node:zlib";

import { expect, type Page, test } from "@playwright/test";

// Runs against the real API and a real worker process. Analysis uses the test-only stub detector, so this
// covers the job, preview and results integration, not any real model.
test.describe.configure({ mode: "serial" });

const PROFILE = "E2E Reviewer";

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
  for (let x = 0; x < width; x++) row.set([(x * 7) % 256, 90, 160], 1 + x * 3);
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

test("preview appears, an analysis job runs on the worker, and its results are shown with their caveats", async ({ page }) => {
  await enterWorkspace(page);
  await page.getByRole("link", { name: "Create project", exact: true }).click();
  await page.getByLabel("Name").fill("Jobs demo");
  await page.getByRole("button", { name: "Create project" }).click();
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Upload & Ingestion" }).click();

  await page.locator("#asset-files").setInputFiles({ name: "street.png", mimeType: "image/png", buffer: png(400, 300) });
  const queued = page.getByRole("list", { name: "Upload queue" }).getByRole("listitem", { name: "street.png" });
  await expect(queued).toContainText("Stored and validated.");
  await queued.getByRole("link", { name: "View details" }).click();

  // The preview is produced by the worker in the background and shows up without a reload.
  const preview = page.getByRole("img", { name: "Preview of street.png" });
  await expect(preview).toBeVisible({ timeout: 30_000 });
  await expect.poll(() => preview.evaluate((img: HTMLImageElement) => [img.naturalWidth, img.naturalHeight])).toEqual([400, 300]);
  await expect(page.getByRole("listitem", { name: "Preview job" })).toContainText("Completed");

  // The worker is visible to the UI.
  await expect(page.getByText("No worker is running")).toHaveCount(0);

  // Run the analysis and watch the job reach its final state.
  await page.getByLabel("Model").selectOption({ label: "stub-detector test" });
  await page.getByRole("button", { name: "Run analysis" }).click();
  const analysis = page.getByRole("listitem", { name: "Ground-image analysis job" });
  await expect(analysis).toContainText("Needs review", { timeout: 30_000 });
  await expect(analysis).toContainText("2 findings stored · 1 below the model's cutoff, not stored");
  await analysis.getByRole("link", { name: "View results" }).click();

  await expect(page.getByRole("heading", { name: "Ground Image Analysis", level: 1 })).toBeVisible();
  await expect(page.getByText("Model predictions, not confirmed findings")).toBeVisible();
  await expect(page.getByTestId("detection-box")).toHaveCount(2);
  // The stub reports a box at 10%-50% x 20%-80% of a 400 x 300 image, in the original's pixels.
  const first = page.getByTestId("detection-box").first();
  await expect(first).toHaveAttribute("x", "40");
  await expect(first).toHaveAttribute("y", "60");
  await expect(first).toHaveAttribute("width", "160");
  await expect(first).toHaveAttribute("height", "180");

  const table = page.getByRole("table", { name: "Model findings in this photograph" });
  await expect(table.getByRole("row")).toHaveCount(3);
  await expect(table).toContainText("thing a");
  await expect(table).toContainText("0.900");
  await expect(table).toContainText("uncalibrated");
  await expect(table).toContainText("Needs review");
  await expect(page.getByText("an operating setting, not a validated threshold")).toBeVisible();
  await expect(page.getByText("no geographic location is established")).toBeVisible();

  // Thumbnail in the asset list.
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Upload & Ingestion" }).click();
  await page.getByRole("navigation", { name: "Ingestion" }).getByRole("link", { name: "Assets", exact: true }).click();
  const thumb = page.getByRole("table", { name: "Assets in this project" }).locator("img");
  await expect(thumb).toHaveCount(1);
  await expect.poll(() => thumb.evaluate((img: HTMLImageElement) => img.naturalWidth)).toBe(256);
});

test("satellite and video screens show stored files and do not offer analysis that does not exist", async ({ page }) => {
  await enterWorkspace(page);
  await page.getByRole("list", { name: "Project list" }).getByRole("link", { name: "Jobs demo" }).click();
  const nav = page.getByRole("navigation", { name: "Main" });
  for (const [link, empty] of [
    ["Satellite Image Analysis", "No rasters in this project"],
    ["Video Analysis", "No videos in this project"],
  ] as const) {
    await nav.getByRole("link", { name: link }).click();
    await expect(page.getByRole("heading", { name: link, level: 1 })).toBeVisible();
    await expect(page.getByText(empty)).toBeVisible();
    await expect(page.getByRole("button", { name: "Run analysis" })).toHaveCount(0);
  }
});
