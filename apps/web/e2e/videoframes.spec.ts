import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import { expect, type Page, test } from "@playwright/test";

// Video-frame analysis in a real browser, against the real API, database and worker (scratch database geopoc_e2e), with the
// test-only stub detector and a short clip written by the locally installed ffmpeg. Named to sort after the other specs: they
// share one scratch database and expect it fresh.
test.describe.configure({ mode: "serial" });

const PROFILE = "E2E Reviewer";

function clip(): Buffer {
  const folder = mkdtempSync(path.join(tmpdir(), "geopoc-e2e-video-"));
  const file = path.join(folder, "gate.mp4");
  execFileSync("ffmpeg", [
    "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10:duration=6",
    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-bf", "2", file,
  ]); // prettier-ignore
  return readFileSync(file);
}

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

function watchExternalRequests(page: Page): string[] {
  const external: string[] = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.protocol.startsWith("http") && url.hostname !== "127.0.0.1") external.push(request.url());
  });
  return external;
}

test("a video is sampled on request, its frames and findings are shown, reviewed, and reopened without running anything again", async ({
  page,
}, testInfo) => {
  const external = watchExternalRequests(page);
  await enterWorkspace(page);
  await page.getByRole("link", { name: "Create project", exact: true }).click();
  await page.getByLabel("Name").fill("Video frames");
  await page.getByRole("button", { name: "Create project" }).click();

  await nav(page).getByRole("link", { name: "Upload & Ingestion" }).click();
  await page.getByRole("tab", { name: "Video" }).click();
  await page.locator("#asset-files").setInputFiles({ name: "gate.mp4", mimeType: "video/mp4", buffer: clip() });
  await expect(page.getByRole("list", { name: "Upload queue" }).getByRole("listitem", { name: "gate.mp4", exact: true })).toContainText("Stored", { timeout: 30_000 });

  // Uploading analysed nothing: the video screen asks for a choice and runs nothing by itself.
  await nav(page).getByRole("link", { name: "Video Analysis" }).click();
  await expect(page.getByText("This video has not been analysed yet")).toBeVisible();
  await expect(page.getByText("Sampled frames, model predictions, not confirmed findings")).toBeVisible();
  const form = page.getByRole("form", { name: "Analyse frames" });
  await form.getByLabel("Model").selectOption({ label: "stub-detector test" });
  await form.getByLabel("Take a frame every (seconds)").fill("1");
  await form.getByLabel("At most this many frames").fill("10");
  await expect(form.getByText(/6 frames would be analysed: one frame every 1 s/)).toBeVisible();
  // an over-large request is refused before it can be started
  await form.getByLabel("Take a frame every (seconds)").fill("0.2");
  await expect(form.getByText("This sampling cannot be used")).toBeVisible();
  await expect(form.getByRole("button", { name: "Analyse these frames" })).toBeDisabled();
  await form.getByLabel("Take a frame every (seconds)").fill("1");
  await expect(form.getByText(/6 frames would be analysed/)).toBeVisible();

  await form.getByRole("button", { name: "Analyse these frames" }).click();
  const latest = page.getByRole("list", { name: "Latest analysis job" });
  await expect(latest).toContainText("Needs review", { timeout: 60_000 });
  await expect(latest).toContainText("12 findings stored · 6 frames analysed");

  // the sampled frames, their timestamps and the findings in each
  const strip = page.getByRole("list", { name: "Sampled frames" });
  await expect(strip.getByRole("listitem")).toHaveCount(6);
  await expect(strip).toContainText("0:05.000");
  const timeline = page.getByRole("group", { name: "Sampled frames on the video timeline" });
  await expect(timeline.getByRole("button")).toHaveCount(6);
  await expect(page.getByTestId("detection-box")).toHaveCount(2); // frame 0, the first with findings

  // a click on the timeline selects that frame and moves the real player to that time
  await timeline.getByRole("button", { name: "Frame 30 at 0:03.000, 2 findings" }).click();
  await expect(page.getByRole("heading", { name: "Frame 30 · 0:03.000" })).toBeVisible();
  await expect(async () => {
    expect(await page.locator("video").evaluate((el) => (el as HTMLVideoElement).currentTime)).toBeCloseTo(3, 1);
  }).toPass({ timeout: 10_000 });
  await expect(page.getByTestId("detection-box")).toHaveCount(2);
  await expect(page.getByTestId("frame-viewer").getByRole("img", { name: /Frame 30 of the video at 0:03.000/ })).toBeVisible();
  await page.getByTestId("frame-viewer").screenshot({ path: testInfo.outputPath("frame-with-boxes.png") });

  // a finding is linked to its frame and is reviewed with the existing review panel
  const group = page.getByRole("region", { name: "Findings in frame 30" });
  await group.getByRole("button", { name: /^Finding thing a in frame 30/ }).click();
  await expect(page.getByRole("heading", { name: "Finding: thing a" })).toBeVisible();
  await expect(page.getByText(/video_frame_sha256:[0-9a-f]{64}/)).toBeVisible();
  await page.getByRole("group", { name: "Review actions" }).getByRole("button", { name: "Confirm" }).click();
  await page.getByRole("button", { name: "Save: Confirm" }).click();
  await expect(page.getByText(/Confirm saved as revision 1/)).toBeVisible();
  await expect(group.getByRole("button", { name: /^Finding thing a in frame 30/ })).toContainText("Verified");

  // reopening reads the stored result: no second job exists, and the same frames are there
  const projectId = page.url().match(/\/projects\/([0-9a-f-]{36})\//)![1];
  await page.reload();
  await expect(page.getByRole("list", { name: "Sampled frames" }).getByRole("listitem")).toHaveCount(6);
  await expect(page.getByRole("region", { name: "Findings in frame 30" })).toContainText("Verified");
  const jobs = await (await page.request.get(`/api/v1/projects/${projectId}/jobs?kind=analyze`)).json();
  expect(jobs.items).toHaveLength(1);

  // the video findings are in the project's observations, labelled with their frame
  await nav(page).getByRole("link", { name: "Observations", exact: true }).click();
  const table = page.getByRole("table", { name: "Observations in this project" });
  await expect(table.getByRole("row")).toHaveCount(13); // header + 12 findings
  await table.getByRole("row").filter({ hasText: "thing a" }).first().click();
  await expect(page.getByRole("region", { name: "Evidence" })).toContainText(/Frame \d+ at 0:0\d\.000/);
  await expect(page.getByRole("link", { name: "Open in Video Analysis" })).toBeVisible();
  expect(external).toEqual([]);
});
