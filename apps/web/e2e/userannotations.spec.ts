import { expect, type Page, test } from "@playwright/test";

// Map annotations in a real browser, against the real API and database (scratch database geopoc_e2e): draw a point, a line
// and a polygon with real mouse clicks, see them listed with measurements, select one by clicking it on the map, reload,
// edit, delete (soft) and see the history. Nothing else in the project is created, so any annotation text found in the
// findings would be a leak.
test.describe.configure({ mode: "serial" });

// The same profile as the other specs. This file is named to sort after them: the browser tests share one scratch
// database, and the earlier specs expect a fresh one (no projects yet, no other profiles).
const PROFILE = "E2E Reviewer";

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
const panel = (page: Page) => page.getByRole("region", { name: "Annotations", exact: true });
const list = (page: Page) => page.getByRole("list", { name: "Annotations in this project" });
const mapRegion = (page: Page) => page.getByRole("region", { name: "Map of project layers" });

function watchExternalRequests(page: Page): string[] {
  const external: string[] = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.protocol.startsWith("http") && url.hostname !== "127.0.0.1") external.push(request.url());
  });
  return external;
}

/** A real mouse click at an offset (in pixels) from the centre of the map canvas. */
async function clickMap(page: Page, dx = 0, dy = 0) {
  const canvas = mapRegion(page).locator("canvas");
  await expect(canvas).toBeVisible();
  await canvas.scrollIntoViewIfNeeded();
  const box = (await canvas.boundingBox())!;
  await page.mouse.click(box.x + box.width / 2 + dx, box.y + box.height / 2 + dy);
}

async function openMap(page: Page) {
  await nav(page).getByRole("link", { name: "Map & Analysis", exact: true }).click();
  await expect(panel(page)).toBeVisible();
  await expect(panel(page).getByRole("group", { name: "Draw an annotation" })).toBeVisible();
}

test("points, lines and polygons are drawn, measured, listed, selected on the map and still there after a reload", async ({
  page,
}, testInfo) => {
  const external = watchExternalRequests(page);
  await enterWorkspace(page);
  await page.getByRole("link", { name: "Create project", exact: true }).click();
  await page.getByLabel("Name").fill("Annotations");
  await page.getByRole("button", { name: "Create project" }).click();
  await openMap(page);
  await expect(panel(page)).toContainText("No annotations yet");
  await expect(panel(page)).toContainText("Your own notes on the map");

  // A point: nothing can be saved before it is placed.
  await panel(page).getByRole("button", { name: "Add point" }).click();
  const form = panel(page).getByRole("form", { name: "New annotation" });
  await expect(form.getByRole("button", { name: "Save annotation" })).toBeDisabled();
  await clickMap(page);
  await expect(form.getByRole("button", { name: "Save annotation" })).toBeEnabled();
  await form.getByLabel("Note").fill("Gate <b>not bold</b>");
  await form.getByRole("button", { name: "Save annotation" }).click();
  await expect(panel(page)).toContainText("The annotation was saved.");
  await expect(list(page).getByRole("listitem").first()).toContainText("Gate <b>not bold</b>"); // plain text, not markup
  await expect(list(page).locator("b")).toHaveCount(0);

  // A real click on the saved point (the map fits to it, so it is at the centre) selects it.
  await expect(async () => {
    await clickMap(page);
    await expect(list(page).locator("> li").first()).toHaveAttribute("aria-current", "true", { timeout: 2_000 });
  }).toPass({ timeout: 20_000 });

  // A line, then a polygon, each drawn with real clicks.
  await panel(page).getByRole("button", { name: "Add line" }).click();
  await clickMap(page, -120, 40);
  await clickMap(page, 120, 40);
  await panel(page).getByLabel("Note").fill("Fence");
  await panel(page).getByRole("button", { name: "Save annotation" }).click();
  const fence = list(page).locator("> li", { hasText: "Fence" });
  await expect(fence).toContainText(/Length \(approximate\): [\d.,]+ (m|km)/);
  await expect(fence).toContainText("Method: geodesic on the WGS 84 ellipsoid");

  await panel(page).getByRole("button", { name: "Add polygon" }).click();
  await clickMap(page, -100, -60);
  await clickMap(page, 100, -60);
  await expect(panel(page).getByRole("button", { name: "Save annotation" })).toBeDisabled(); // two positions are not an area
  await clickMap(page, 0, -140);
  await panel(page).getByLabel("Note").fill("Field");
  await panel(page).getByRole("button", { name: "Save annotation" }).click();
  const field = list(page).locator("> li", { hasText: "Field" });
  await expect(field).toContainText(/Area \(approximate\): [\d.,]+ (m²|ha|km²)/);
  await expect(field).toContainText(/Outline length \(approximate\): [\d.,]+ (m|km)/);
  await expect(panel(page)).toContainText("3 active · 0 deleted");
  await mapRegion(page).screenshot({ path: testInfo.outputPath("annotations-drawn.png") });

  // They are saved: a reload still shows all three, with the same notes.
  await page.reload();
  await expect(panel(page)).toContainText("3 active · 0 deleted");
  for (const note of ["Gate", "Fence", "Field"]) await expect(list(page)).toContainText(note);

  // They are not findings: the observations list does not know them.
  await nav(page).getByRole("link", { name: "Observations" }).click();
  await expect(page.getByText("Gate")).toHaveCount(0);
  await expect(page.getByText("Fence")).toHaveCount(0);
  expect(external).toEqual([]);
});

test("a note can be edited, an old edit is refused, and a delete hides the annotation but keeps its history", async ({
  page,
}) => {
  const external = watchExternalRequests(page);
  await enterWorkspace(page);
  await page.getByRole("list", { name: "Project list" }).getByRole("link", { name: "Annotations" }).click();
  await openMap(page);
  const fence = () => list(page).locator("> li", { hasText: "Fence" });

  await fence().getByRole("button", { name: "Edit line" }).click();
  const form = panel(page).getByRole("form", { name: "Edit annotation" });
  await expect(form.getByLabel("Note")).toHaveValue("Fence");
  await expect(form).toContainText("The shape is unchanged");
  await form.getByLabel("Note").fill("Fence, east side");
  await form.getByRole("button", { name: "Save changes" }).click();
  await expect(panel(page)).toContainText("The annotation was saved as a new version.");
  await expect(list(page).locator("> li", { hasText: "Fence, east side" })).toContainText("version 2");

  // History shows both versions, with who and what.
  await list(page).locator("> li", { hasText: "Fence, east side" }).getByRole("button", { name: "Show history of line" }).click();
  const history = panel(page).getByRole("region", { name: "Annotation history" });
  await expect(history).toContainText("Version 1 · drawn");
  await expect(history).toContainText("Version 2 · changed");
  await expect(history).toContainText(`by ${PROFILE}`);

  // A soft delete asks first, hides it, and keeps the history reachable.
  const field = () => list(page).locator("> li", { hasText: "Field" });
  await field().getByRole("button", { name: "Delete polygon" }).click();
  await field().getByRole("button", { name: "Keep it" }).click();
  await expect(field()).toHaveCount(1);
  await field().getByRole("button", { name: "Delete polygon" }).click();
  await field().getByRole("button", { name: "Delete annotation" }).click();
  await expect(panel(page)).toContainText("The annotation was deleted.");
  await expect(field()).toHaveCount(0);
  await expect(panel(page)).toContainText("2 active · 1 deleted");

  await panel(page).getByLabel("List deleted annotations").check();
  await expect(field()).toContainText("Deleted");
  await expect(field().getByRole("button", { name: "Edit polygon" })).toHaveCount(0);
  await field().getByRole("button", { name: "Show history of polygon" }).click();
  await expect(history).toContainText("Version 2 · deleted");
  await expect(history).toContainText("Version 1 · drawn");

  // After a reload the deleted one is still hidden by default.
  await page.reload();
  await expect(panel(page)).toContainText("2 active · 1 deleted");
  await expect(list(page)).not.toContainText("Field");
  expect(external).toEqual([]);
});
