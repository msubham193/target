import { execFileSync } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import path from "node:path";

import { expect, type Locator, type Page, test } from "@playwright/test";

// A captioned screen recording of what the application does today, for the client. It runs on the scratch review
// stack (its own database and data folder), never the working one:
//   $env:GEOPOC_REVIEW_SPEC = "demo-recording.spec.ts"; pnpm exec playwright test --config playwright.ui-review.config.ts
// Inputs: scikit-image sample photographs and synthetic test files from D:\geopoc-cache\review, plus synthetic
// rasters, a world file and a .prj written here. No real-world imagery. The ground detector is the installed one.

const OUT = process.env.GEOPOC_DEMO_OUT ?? "D:\\geopoc-cache\\demo-recording";
const FILES = process.env.GEOPOC_REVIEW_FILES ?? "D:\\geopoc-cache\\review";
const SPEED = Number(process.env.GEOPOC_DEMO_SPEED ?? "1"); // >1 = longer pauses

// Recorded with the browser's own screencast (DevTools protocol) and encoded with the FFmpeg already installed on
// this computer, so no extra recorder needs to be downloaded.
test.use({ launchOptions: { slowMo: 120 }, acceptDownloads: true, actionTimeout: 90_000 });

async function startRecording(page: Page, folder: string) {
  rmSync(folder, { recursive: true, force: true });
  mkdirSync(folder, { recursive: true });
  const frames: { file: string; at: number }[] = [];
  const cdp = await page.context().newCDPSession(page);
  cdp.on("Page.screencastFrame", (frame) => {
    const file = path.join(folder, `f${String(frames.length).padStart(6, "0")}.jpg`);
    writeFileSync(file, Buffer.from(frame.data, "base64"));
    frames.push({ file, at: frame.metadata.timestamp ?? Date.now() / 1000 });
    void cdp.send("Page.screencastFrameAck", { sessionId: frame.sessionId }).catch(() => undefined);
  });
  await cdp.send("Page.startScreencast", { format: "jpeg", quality: 88, maxWidth: 1440, maxHeight: 900, everyNthFrame: 1 });
  return async (target: string) => {
    await cdp.send("Page.stopScreencast").catch(() => undefined);
    const end = Date.now() / 1000;
    // Each frame is held until the next one arrives, so the video plays in real time.
    const lines = frames.map((f, i) => `file '${f.file.replaceAll("\\", "/")}'\nduration ${Math.max(0.001, (frames[i + 1]?.at ?? end) - f.at).toFixed(3)}`);
    lines.push(`file '${frames[frames.length - 1]!.file.replaceAll("\\", "/")}'`);
    const list = path.join(folder, "frames.txt");
    writeFileSync(list, lines.join("\n"));
    execFileSync("ffmpeg", [
      "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", list,
      "-vf", "scale=1440:900:force_original_aspect_ratio=decrease,pad=1440:900:(ow-iw)/2:(oh-ih)/2,fps=30,format=yuv420p",
      "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-movflags", "+faststart", target,
    ]);
    return frames.length;
  };
}

function python(expression: string): Buffer {
  const code =
    "import base64, sys; from pyproj import CRS; from pyproj.enums import WktVersion; " +
    "from tests.test_raster_tools import tif, gradient; " +
    `sys.stdout.write(base64.b64encode(${expression}).decode())`;
  const api = path.resolve(process.cwd(), "../../services/api");
  return Buffer.from(execFileSync("uv", ["run", "--no-sync", "python", "-c", code], { cwd: api, maxBuffer: 256 * 1024 * 1024 }).toString(), "base64");
}

function file(name: string, mimeType = "application/octet-stream") {
  const full = path.join(FILES, name);
  if (!existsSync(full)) throw new Error(`missing ${full}`);
  return { name, mimeType, buffer: readFileSync(full) };
}

// --- captions, title cards and a visible pointer, drawn into the page -------------------------------------------------

async function overlay(page: Page) {
  await page.addInitScript(() => {
    const install = () => {
      if (document.getElementById("demo-caption")) return;
      const caption = document.createElement("div");
      caption.id = "demo-caption";
      caption.setAttribute("aria-hidden", "true");
      Object.assign(caption.style, {
        position: "fixed", left: "50%", bottom: "22px", transform: "translateX(-50%)", maxWidth: "980px",
        width: "max-content", background: "rgba(8,12,20,0.93)", color: "#f4f6fb", font: "15px/1.45 'Segoe UI', system-ui, sans-serif",
        padding: "12px 20px", borderRadius: "10px", border: "1px solid rgba(255,255,255,0.18)", zIndex: "2147483646",
        pointerEvents: "none", boxShadow: "0 8px 30px rgba(0,0,0,0.45)", display: "none",
      });
      const card = document.createElement("div");
      card.id = "demo-card";
      card.setAttribute("aria-hidden", "true");
      Object.assign(card.style, {
        position: "fixed", inset: "0", background: "rgba(8,12,20,0.97)", color: "#f4f6fb", display: "none",
        zIndex: "2147483647", pointerEvents: "none", alignItems: "center", justifyContent: "center", textAlign: "center",
        font: "18px/1.6 'Segoe UI', system-ui, sans-serif", padding: "0 12%",
      });
      const dot = document.createElement("div");
      dot.id = "demo-pointer";
      Object.assign(dot.style, {
        position: "fixed", width: "18px", height: "18px", borderRadius: "50%", border: "2px solid #ffd23f",
        background: "rgba(255,210,63,0.35)", zIndex: "2147483647", pointerEvents: "none", left: "-40px", top: "-40px",
        transition: "transform 0.12s",
      });
      document.addEventListener("mousemove", (e) => { dot.style.left = `${e.clientX - 9}px`; dot.style.top = `${e.clientY - 9}px`; }, true);
      document.addEventListener("mousedown", () => { dot.style.transform = "scale(1.8)"; }, true);
      document.addEventListener("mouseup", () => { dot.style.transform = "scale(1)"; }, true);
      document.body.append(caption, card, dot);
    };
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", install);
    else install();
  });
}

const pause = (page: Page, ms: number) => page.waitForTimeout(ms * SPEED);

async function say(page: Page, title: string, text: string, ms = 4500) {
  await page.evaluate(
    ([t, x]) => {
      const box = document.getElementById("demo-caption");
      if (!box) return;
      box.innerHTML = "";
      const head = document.createElement("div");
      head.textContent = t!;
      Object.assign(head.style, { color: "#8cc8ff", fontWeight: "600", fontSize: "13px", letterSpacing: "0.04em", textTransform: "uppercase", marginBottom: "2px" });
      const body = document.createElement("div");
      body.textContent = x!;
      box.append(head, body);
      box.style.display = "block";
    },
    [title, text],
  );
  await pause(page, ms);
}

async function card(page: Page, heading: string, lines: string[], ms = 6000) {
  await page.evaluate(
    ([h, l]) => {
      const el = document.getElementById("demo-card");
      if (!el) return;
      el.innerHTML = "";
      const box = document.createElement("div");
      const title = document.createElement("div");
      title.textContent = h as string;
      Object.assign(title.style, { fontSize: "34px", fontWeight: "600", marginBottom: "18px", color: "#ffffff" });
      box.append(title);
      for (const line of l as string[]) {
        const p = document.createElement("div");
        p.textContent = line;
        Object.assign(p.style, { color: "#c9d3e6", margin: "6px 0" });
        box.append(p);
      }
      el.append(box);
      el.style.display = "flex";
    },
    [heading, lines] as const,
  );
  await pause(page, ms);
  await page.evaluate(() => {
    const el = document.getElementById("demo-card");
    if (el) el.style.display = "none";
  });
}

async function show(page: Page, target: Locator, ms = 1500) {
  await target.scrollIntoViewIfNeeded();
  await pause(page, ms);
}

async function scrollTop(page: Page) {
  await page.evaluate(() => window.scrollTo({ top: 0, behavior: "smooth" }));
  await pause(page, 600);
}

async function glide(page: Page, target: Locator, click = true) {
  await target.scrollIntoViewIfNeeded();
  const box = await target.boundingBox();
  if (box) await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2, { steps: 12 });
  if (click) await target.click();
}

const nav = (page: Page) => page.getByRole("navigation", { name: "Main" });
const go = (page: Page, name: string | RegExp) =>
  glide(page, nav(page).getByRole("link", typeof name === "string" ? { name, exact: true } : { name }));
const queueRow = (page: Page, name: string) => page.getByRole("list", { name: "Upload queue" }).getByRole("listitem", { name, exact: true });

async function clickImage(page: Page, fx: number, fy: number) {
  await page.getByTestId("raster-click-layer").scrollIntoViewIfNeeded();
  const box = (await page.getByTestId("raster-click-layer").boundingBox())!;
  await page.mouse.move(box.x + box.width * fx, box.y + box.height * fy, { steps: 8 });
  await page.mouse.click(box.x + box.width * fx, box.y + box.height * fy);
}

test("captioned walkthrough of the application so far", async ({ page }) => {
  test.setTimeout(30 * 60_000);
  mkdirSync(OUT, { recursive: true });
  await overlay(page);
  // The worker checks in the background whether PyTorch can run. Record only once the detector is ready, and stop
  // with the reason if it is not, rather than recording a disabled button.
  let detector: { available: boolean; reason_unavailable: string | null } | undefined;
  for (let i = 0; i < 90; i++) {
    const health = await (await page.request.get("http://127.0.0.1:8011/health/models")).json();
    detector = health.models.find((m: { name: string }) => m.name === "fasterrcnn_resnet50_fpn_v2");
    if (detector?.available || (detector?.reason_unavailable && !/still checking/.test(detector.reason_unavailable))) break;
    await page.waitForTimeout(1000);
  }
  if (!detector?.available) throw new Error(`The detector is not available on the review stack: ${detector?.reason_unavailable}`);
  await page.goto("/");
  const finish = await startRecording(page, path.join(OUT, "frames"));

  const before16 = python("tif(gradient(bands=4, height=600, width=900, scale=3), descriptions=['', '', '', 'declared: band 4'])");
  const after16 = python(
    "tif((lambda d: (d.__setitem__((3, slice(200, 320), slice(300, 450)), (d[3, 200:320, 300:450].astype('int64') + 900).clip(0, 65535).astype('uint16')), d)[1])(gradient(bands=4, height=600, width=900, scale=3)), descriptions=['', '', '', 'declared: band 4'])",
  );
  const scan = python("tif(gradient(bands=3, height=450, width=600, scale=2), crs=None, transform=None)");
  const prj = python("CRS('EPSG:32643').to_wkt(WktVersion.WKT1_ESRI).encode()");
  const world = Buffer.from("10\n0\n0\n-10\n510005\n2999995\n"); // centre of the top-left pixel; next to the other rasters

  // --- 0. opening ------------------------------------------------------------------------------------------------------
  await expect(page.getByRole("list", { name: "Service health" })).toContainText("Background worker: running", { timeout: 120_000 });
  await card(page, "Geospatial PoC — progress walkthrough", [
    "Everything shown runs locally on this computer: no cloud services, no online maps, no external AI.",
    "Data in this recording: sample photographs and synthetic test rasters. No real-world imagery.",
    "Covered: projects, upload and validation, ground-photo analysis with review, satellite raster tools,",
    "georeferencing and sidecar files, the map, observations, exports, and what is planned next.",
  ], 9000);

  // --- 1. start ------------------------------------------------------------------------------------------------------
  await say(page, "Start", "The start screen checks the local services first: database, storage and the background worker.", 5000);
  await say(page, "Local profiles", "A local profile names who did what in the audit history. It is attribution, not a login.");
  await page.getByLabel("New profile name").pressSequentially("Analyst", { delay: 60 });
  await glide(page, page.getByRole("button", { name: "Create profile and continue" }));
  await say(page, "Dashboard", "Projects, recent background jobs and service status. Status badges at the top: services OK, local only.", 5000);

  // --- 2. project ----------------------------------------------------------------------------------------------------
  await glide(page, page.getByRole("link", { name: "Create project", exact: true }));
  await say(page, "New project", "A project can hold an area of interest in any coordinate system from the local PROJ registry.", 3500);
  await page.getByLabel("Name").pressSequentially("Client demo", { delay: 50 });
  await page.getByLabel("Add an area of interest").check();
  await page.getByLabel("X (easting / longitude)").fill("504500");
  await page.getByLabel("Y (northing / latitude)").fill("2997000");
  const search = page.getByPlaceholder("e.g. EPSG:4326");
  await search.pressSequentially("32643", { delay: 80 });
  await search.press("Enter");
  await say(page, "CRS search", "Coordinate systems are searched offline. Free-text datum names are never guessed into a CRS.", 3500);
  await glide(page, page.getByRole("list", { name: "CRS search results" }).getByRole("button", { name: /EPSG:32643/ }));
  await pause(page, 1500);
  await glide(page, page.getByRole("button", { name: "Create project" }));
  await expect(page.getByRole("heading", { name: "Client demo", level: 1 })).toBeVisible();
  await say(page, "Project overview", "Each project lists its inputs, findings and history.", 4000);

  // --- 3. upload ------------------------------------------------------------------------------------------------------
  await go(page, "Upload & Ingestion");
  await say(page, "Upload & ingestion", "Three kinds of input: ground photographs, satellite or aerial rasters (GeoTIFF), and video.", 4500);
  await page.locator("#asset-files").setInputFiles([file("astronaut.png", "image/png"), file("chelsea.png", "image/png")]);
  await expect(queueRow(page, "chelsea.png")).toContainText("Stored", { timeout: 60_000 });
  await say(page, "Checked by content", "Each file is checked by its content, not its name, read in full, checksummed (SHA-256) and stored unmodified.", 5500);

  await glide(page, page.getByRole("tab", { name: "Satellite / aerial raster (GeoTIFF)" }));
  await page.locator("#asset-files").setInputFiles([
    file("harbour_scene_utm43n.tif"),
    { name: "multiband_before.tif", mimeType: "image/tiff", buffer: before16 },
    { name: "multiband_after.tif", mimeType: "image/tiff", buffer: after16 },
    { name: "scan.tif", mimeType: "image/tiff", buffer: scan },
    { name: "scan.tfw", mimeType: "application/octet-stream", buffer: world },
    { name: "scan.prj", mimeType: "application/octet-stream", buffer: prj },
  ]);
  await expect(queueRow(page, "scan.prj")).toContainText("Kept for review", { timeout: 90_000 });
  await say(page, "Rasters and sidecar files", "GeoTIFFs are validated and their CRS and geotransform read. The world file and .prj that came with scan.tif are kept for review — never applied on their own.", 6500);
  await show(page, queueRow(page, "scan.tfw"), 2500);

  await glide(page, page.getByRole("tab", { name: "Video" }));
  await page.locator("#asset-files").setInputFiles([file("walkthrough_h264.mp4", "video/mp4")]);
  await expect(queueRow(page, "walkthrough_h264.mp4")).toContainText("Stored", { timeout: 90_000 });
  await say(page, "Video", "Videos are probed locally with FFmpeg: codec, duration, frame rate. A preview is made in the background.", 4500);

  await go(page, "Upload & Ingestion");
  await glide(page, page.getByRole("link", { name: "Assets", exact: true }).first());
  await say(page, "Assets", "All inputs in the project, with their validation status and quality flags.", 4500);
  await glide(page, page.getByRole("link", { name: /^harbour_scene_utm43n\.tif/ }).first());
  await say(page, "Asset details", "What the file declares — size, bands, data types, CRS, extent — and what is NOT known. Unknown values are shown as unknown, never filled in.", 6000);
  await page.mouse.wheel(0, 700);
  await pause(page, 2500);
  await scrollTop(page);

  // --- 4. ground photographs -----------------------------------------------------------------------------------------
  await go(page, "Ground Image Analysis");
  await glide(page, page.getByRole("link", { name: /^astronaut\.png/ }).first());
  await expect(page.getByText("This photograph has not been analysed yet")).toBeVisible({ timeout: 30_000 });
  await say(page, "Ground image analysis", "A local object detector (torchvision Faster R-CNN, COCO classes) runs on this computer's GPU as a background job.", 5000);
  await glide(page, page.getByRole("button", { name: "Run analysis" }));
  await expect(page.getByTestId("detection-box").first()).toBeVisible({ timeout: 180_000 });
  await say(page, "Proposed findings", "Every detection is a proposed finding that needs a person's review. Scores are model rankings, not calibrated probabilities.", 6000);
  const findings = page.getByRole("table", { name: "Model findings in this photograph" });
  await glide(page, findings.getByRole("row").nth(1));
  await glide(page, page.getByRole("group", { name: "Review actions" }).getByRole("button", { name: "Confirm" }));
  await page.getByLabel("Note (optional)").pressSequentially("Person clearly visible.", { delay: 40 });
  await glide(page, page.getByRole("button", { name: "Save: Confirm" }));
  await expect(page.getByText("Confirm saved as revision 1.")).toBeVisible();
  await say(page, "Review history", "Each review is a new, append-only revision with the reviewer's name and time. Earlier revisions are never overwritten.", 5500);

  // --- 5. observations ------------------------------------------------------------------------------------------------
  await go(page, "Observations");
  await expect(page.getByRole("table", { name: "Observations in this project" })).toBeVisible();
  await say(page, "Observations", "All findings in the project in one table, with their review state, source and evidence.", 5000);

  // --- 6. satellite raster tools ---------------------------------------------------------------------------------------
  await go(page, "Satellite Image Analysis");
  await glide(page, page.getByRole("complementary", { name: "Rasters" }).getByRole("link", { name: /^multiband_after\.tif/ }));
  await expect(page.getByRole("list", { name: "Statistics per band" })).toBeVisible({ timeout: 60_000 });
  await say(page, "Satellite image analysis", "Non-AI raster tools on a 16-bit, 4-band synthetic raster. Band statistics are computed in the background from the original.", 5500);
  const controls = page.getByRole("region", { name: "Bands and contrast" });
  await controls.getByLabel("Shown as red").selectOption("4");
  await controls.getByLabel("Contrast stretch").selectOption("p2-98");
  await glide(page, controls.getByRole("button", { name: "Apply" }));
  await expect(page.getByRole("status", { name: "Display in use" })).toContainText("Showing bands 4, 2, 3");
  await say(page, "Bands and contrast", "Choose which bands to show and how to stretch them. Display only — stored values are never changed. Bands are labelled only as the file declares them.", 6000);
  await clickImage(page, 0.42, 0.43);
  await expect(page.getByRole("status", { name: "Pixel values" })).toBeVisible();
  await show(page, page.getByRole("status", { name: "Pixel values" }), 500);
  await say(page, "Pixel values", "Click the image to read the stored value of every band at that pixel, with its map position.", 5000);

  await go(page, "Satellite Image Analysis");
  await glide(page, page.getByRole("complementary", { name: "Rasters" }).getByRole("link", { name: /^harbour_scene_utm43n\.tif/ }));
  await glide(page, page.getByRole("button", { name: "Measure area" }));
  for (const [x, y] of [[0.2, 0.2], [0.6, 0.2], [0.6, 0.7], [0.2, 0.7]] as const) await clickImage(page, x, y);
  await glide(page, page.getByRole("button", { name: "Measure", exact: true }));
  await expect(page.getByRole("status", { name: "Measurement result" })).toContainText("valid");
  await show(page, page.getByRole("status", { name: "Measurement result" }), 500);
  await say(page, "Measurement", "Distances and areas are geodesic on the CRS's ellipsoid, with the method stated. Without valid georeferencing a ground measurement is refused, not estimated.", 6500);

  await glide(page, page.getByRole("region", { name: "Display copy" }).getByRole("button", { name: "Build display copy" }));
  await expect(page.getByRole("region", { name: "Display copy" })).toContainText("identical to the original", { timeout: 90_000 });
  await show(page, page.getByRole("region", { name: "Display copy" }), 500);
  await say(page, "Display copies", "For very large rasters, a lossless copy with overviews makes the map fast. It is kept only after it is verified identical to the original.", 6000);

  await go(page, "Satellite Image Analysis");
  await glide(page, page.getByRole("complementary", { name: "Rasters" }).getByRole("link", { name: /^multiband_after\.tif/ }));
  const compare = page.getByRole("region", { name: "Pixel comparison" });
  await show(page, compare, 500);
  await compare.getByLabel(/Raster A/).selectOption({ label: "multiband_before.tif" });
  await compare.getByLabel("Band of A").selectOption("4");
  await compare.getByLabel("Band of B (this raster)").selectOption("4");
  await compare.getByLabel(/Purpose/).pressSequentially("Demo: difference between two dates.", { delay: 30 });
  await say(page, "Pixel comparison", "Two rasters on exactly the same grid can be compared pixel by pixel (B − A). Nothing is resampled; misaligned rasters are refused.", 5500);
  await glide(page, compare.getByRole("button", { name: "Compute B − A" }));
  await glide(page, compare.getByRole("link", { name: "Open the result" }));
  await expect(page.getByRole("list", { name: "Statistics per band" })).toBeVisible({ timeout: 60_000 });
  await say(page, "Derived raster with lineage", "The difference is saved as a new derived raster that records exactly which files, bands and versions it came from. It is a pixel difference, not an interpretation of change.", 7000);

  // --- 7. sidecar files and georeferencing ---------------------------------------------------------------------------------
  await go(page, "Satellite Image Analysis");
  await glide(page, page.getByRole("complementary", { name: "Rasters" }).getByRole("link", { name: /^scan\.tif/ }));
  await say(page, "Not georeferenced", "scan.tif has no coordinates of its own, so it cannot be placed on the map. Nothing is assumed about where it is.", 5000);
  await glide(page, page.getByRole("link", { name: /Record georeferencing|See the record|Spatial placement/ }).first()).catch(() => undefined);
  const sidecars = page.getByRole("region", { name: "Sidecar files", exact: true });
  await expect(sidecars).toContainText("A change can be reviewed", { timeout: 30_000 });
  await show(page, sidecars, 500);
  await say(page, "Sidecar files", "Its world file (.tfw) and CRS file (.prj) are shown with what they say. The CRS is accepted only because the local registry matches it exactly (EPSG:32643).", 7000);
  const review = sidecars.getByRole("region", { name: "Review the change from sidecar files" });
  await review.getByLabel("Reason (required)").pressSequentially("World file and .prj supplied with the scan.", { delay: 30 });
  await glide(page, review.getByRole("button", { name: "Check this change" }));
  await expect(review).toContainText("Passed validation", { timeout: 30_000 });
  await show(page, review.getByRole("list", { name: "Validation checks" }), 500);
  await say(page, "Checked before recording", "The same checks as georeferencing entered by hand: the files' and the original's checksums, the CRS, where the corners fall, and a test map tile.", 7000);
  await review.getByRole("checkbox").check();
  await glide(page, review.getByRole("button", { name: "Record as version 1" }));
  await expect(sidecars).toContainText("Recorded as version 1");
  await show(page, page.getByRole("list", { name: "Georeferencing history" }), 500);
  await say(page, "Versioned and audited", "Recorded as version 1 with the reason, the person and the files used. The original file is unchanged; this placement is labelled user-asserted.", 6500);

  // --- 8. map --------------------------------------------------------------------------------------------------------------
  await go(page, "Map & Analysis");
  await expect(page.getByText("Loading the map…")).toHaveCount(0, { timeout: 30_000 });
  await pause(page, 2500);
  await say(page, "Map & Analysis", "Every georeferenced raster is drawn from its own CRS on a local map. There is no online basemap.", 5500);
  await glide(page, page.getByRole("button", { name: "Pin a point" }));
  const canvas = page.getByRole("region", { name: "Map of project layers" }).locator("canvas");
  const box = (await canvas.boundingBox())!;
  await page.mouse.move(box.x + box.width * 0.5, box.y + box.height * 0.5, { steps: 10 });
  await page.mouse.click(box.x + box.width * 0.5, box.y + box.height * 0.5);
  await expect(page.getByRole("list", { name: "Raster values at the pinned point" })).toBeVisible({ timeout: 15_000 });
  await say(page, "Coordinates and values", "A pinned point shows its coordinates in WGS 84 and any other CRS, and the stored value of each raster under it.", 6000);
  await glide(page, page.getByRole("button", { name: "Measure distance" }));
  await page.mouse.click(box.x + box.width * 0.3, box.y + box.height * 0.45);
  await page.mouse.move(box.x + box.width * 0.7, box.y + box.height * 0.55, { steps: 20 });
  await page.mouse.click(box.x + box.width * 0.7, box.y + box.height * 0.55);
  await glide(page, page.getByRole("button", { name: "Measure", exact: true }));
  await expect(page.getByRole("status", { name: "Measurement result" })).toContainText("valid");
  await say(page, "Map measurement", "Distances on the map are geodesic on the WGS 84 ellipsoid.", 4500);

  // --- 9. exports, video, 3D and settings -----------------------------------------------------------------------------------
  await go(page, "Reports");
  await say(page, "Exports", "Observations can be exported as CSV, GeoJSON and JSON, with their review state and provenance.", 4000);
  const download = page.waitForEvent("download");
  await glide(page, page.getByRole("button", { name: "Download GEOJSON" }));
  await download;
  await say(page, "Reports", "Versioned summary reports are the next major phase (planned, not started).", 4000);

  await go(page, "Video Analysis");
  await glide(page, page.getByRole("link", { name: /walkthrough_h264\.mp4/ }).first()).catch(() => undefined);
  await say(page, "Video", "Videos can be played and inspected. Frame sampling is agreed (1 frame/s by default); video analysis is not implemented yet.", 6000);

  await go(page, /^3D View/);
  await say(page, "3D view", "Planned. Clearly marked as not yet available.", 3500);

  await go(page, "Settings");
  await say(page, "Settings — models", "The model registry: each model's source, checksum, licence note and status. Only local models are allowed.", 5500);
  await page.mouse.wheel(0, 600);
  await pause(page, 2500);

  // --- 10. close -----------------------------------------------------------------------------------------------------------
  await card(page, "Status and next steps", [
    "Done: projects, validated ingestion, ground-photo detection with review, non-AI raster tools,",
    "georeferencing with sidecar files, the map, observations, review history and exports.",
    "Next gate: SAM 2.1 person-guided outlining — compatibility test awaiting download approval.",
    "Not yet: AI analysis of satellite imagery, video analysis, versioned reports (Phase 5), 3D.",
    "No accuracy is claimed: real, representative imagery has not been evaluated yet.",
  ], 11000);

  const frames = await finish(path.join(OUT, "geopoc-walkthrough.mp4"));
  console.log(`Recorded ${frames} frames to ${path.join(OUT, "geopoc-walkthrough.mp4")}`);
});
