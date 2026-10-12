import { createHash } from "node:crypto";
import { crc32, deflateSync } from "node:zlib";

import { expect, type Page, test } from "@playwright/test";

test.describe.configure({ mode: "serial" });

const PROFILE = "E2E Reviewer";

/** A valid 2x2 PNG of one colour, built here so no fixture file is stored in the repository. */
function png(red: number, green: number, blue: number): Buffer {
  const chunk = (type: string, data: Buffer) => {
    const body = Buffer.concat([Buffer.from(type, "ascii"), data]);
    const length = Buffer.alloc(4);
    length.writeUInt32BE(data.length);
    const checksum = Buffer.alloc(4);
    checksum.writeUInt32BE(crc32(body) >>> 0);
    return Buffer.concat([length, body, checksum]);
  };
  const header = Buffer.alloc(13);
  header.writeUInt32BE(2, 0);
  header.writeUInt32BE(2, 4);
  header.set([8, 2, 0, 0, 0], 8); // 8-bit RGB
  const row = [0, red, green, blue, red, green, blue];
  const pixels = deflateSync(Buffer.from([...row, ...row]));
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

const row = (page: Page, name: string) => page.getByRole("list", { name: "Upload queue" }).getByRole("listitem", { name });

test("upload, duplicate, rejection and retrieval against the real backend", async ({ page }) => {
  const external: string[] = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.protocol.startsWith("http") && url.hostname !== "127.0.0.1") external.push(request.url());
  });

  const photo = png(200, 30, 30);
  const sha256 = createHash("sha256").update(photo).digest("hex");

  await enterWorkspace(page);
  await page.getByRole("link", { name: "Create project", exact: true }).click();
  await page.getByLabel("Name").fill("Ingestion demo");
  await page.getByRole("button", { name: "Create project" }).click();
  await expect(page.getByRole("heading", { name: "Ingestion demo", level: 1 })).toBeVisible();

  await page.getByRole("navigation", { name: "Project" }).getByRole("link", { name: "Upload", exact: true }).click();
  await expect(page.getByText(".jpg, .jpeg, .png, .webp")).toBeVisible();
  await expect(page.getByText("50 MB")).toBeVisible();
  await expect(page.getByText("No files added yet.")).toBeVisible();
  const input = page.locator("#asset-files");

  // success
  await input.setInputFiles({ name: "photo.png", mimeType: "image/png", buffer: photo });
  await expect(row(page, "photo.png")).toContainText("Stored and validated.");
  await expect(row(page, "photo.png")).toContainText(`SHA-256 ${sha256.slice(0, 16)}`);
  await expect(row(page, "photo.png")).toContainText("Not georeferenced");

  // duplicate: accepted, linked, flagged
  await input.setInputFiles({ name: "photo copy.png", mimeType: "image/png", buffer: photo });
  await expect(row(page, "photo copy.png")).toContainText("Duplicate — linked");
  await expect(row(page, "photo copy.png")).toContainText("flagged as a duplicate");
  await expect(row(page, "photo copy.png").getByRole("link", { name: "View the first copy" })).toBeVisible();

  // content contradicts the name: refused by the server
  await input.setInputFiles({ name: "really_png.jpg", mimeType: "image/jpeg", buffer: photo });
  await expect(row(page, "really_png.jpg")).toContainText("The file's content does not match its name");
  await expect(row(page, "really_png.jpg")).toContainText("Code: SIGNATURE_MISMATCH");

  // corrupt: right signature, unreadable content
  await input.setInputFiles({ name: "broken.png", mimeType: "image/png", buffer: photo.subarray(0, 40) });
  await expect(row(page, "broken.png")).toContainText("The file could not be read");
  await expect(row(page, "broken.png")).toContainText("Nothing was stored.");

  // unsupported type: refused before any upload
  await input.setInputFiles({ name: "notes.gif", mimeType: "image/gif", buffer: Buffer.from("GIF89a") });
  await expect(row(page, "notes.gif")).toContainText("This file type is not supported here");

  // listing shows only what was actually stored
  await page.getByRole("link", { name: "View all assets" }).click();
  const table = page.getByRole("table", { name: "Assets in this project" });
  await expect(table.getByRole("row")).toHaveCount(3); // header + 2 assets
  await expect(table).toContainText("photo copy.png");
  await expect(table).toContainText("Duplicate content");
  await expect(table).not.toContainText("broken.png");

  // survives a reload, and the original comes back byte-for-byte
  await page.reload();
  await page.getByRole("table", { name: "Assets in this project" }).getByRole("link", { name: "photo.png", exact: true }).click();
  await expect(page.getByRole("heading", { name: "photo.png", level: 1 })).toBeVisible();
  await expect(page.getByText(sha256)).toBeVisible();
  await expect(page.getByText("no location is inferred")).toBeVisible();
  const assetId = page.url().split("/").pop()!;
  const downloaded = await page.request.get(`/api/v1/assets/${assetId}/content`);
  expect(createHash("sha256").update(await downloaded.body()).digest("hex")).toBe(sha256);

  expect(external).toEqual([]);
});
