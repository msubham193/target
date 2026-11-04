import { expect, type Page, test } from "@playwright/test";

// The tests share one scratch database and run in order.
test.describe.configure({ mode: "serial" });

const PROFILE = "E2E Reviewer";

async function enterWorkspace(page: Page) {
  await page.goto("/");
  await expect(page).toHaveURL(/\/login$/);
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

test("workspace entry shows service health and the first-use empty dashboard", async ({ page }) => {
  await page.goto("/login");
  const health = page.getByRole("list", { name: "Service health" });
  await expect(health).toContainText("OK · Database (PostgreSQL + PostGIS): PostGIS 3.");
  await expect(health).toContainText("OK · Local storage");
  await expect(page.getByText("It is not a login and gives no access control")).toBeVisible();

  await enterWorkspace(page);
  await expect(page.getByRole("main").getByText("No projects yet")).toBeVisible();
  await expect(page.getByTitle("Local profile (not a login)")).toHaveText(PROFILE);
  await expect(page.getByRole("link", { name: "Local only" })).toBeVisible();
});

test("create a project without a location, then reopen it (Phase 1 exit criterion)", async ({ page }) => {
  await enterWorkspace(page);
  await page.getByRole("link", { name: "Create project", exact: true }).click();
  await page.getByLabel("Name").fill("Harbour survey");
  await page.getByLabel("Description (optional)").fill("Ground photos only");
  await page.getByLabel("Tags (optional)").fill("coast, demo");
  await page.getByRole("button", { name: "Create project" }).click();

  await expect(page.getByRole("heading", { name: "Harbour survey", level: 1 })).toBeVisible();
  await expect(page.getByText("No location entered. A project does not need one.")).toBeVisible();
  const projectUrl = page.url();

  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Project Dashboard" }).click();
  const list = page.getByRole("list", { name: "Project list" });
  await expect(list.getByRole("link", { name: "Harbour survey" })).toBeVisible();
  await expect(list).toContainText("Location: none entered");

  await page.reload();
  await page.getByRole("list", { name: "Project list" }).getByRole("link", { name: "Harbour survey" }).click();
  await expect(page).toHaveURL(projectUrl);
  await expect(page.getByRole("heading", { name: "Harbour survey", level: 1 })).toBeVisible();
  await expect(page.getByText("coast, demo")).toBeVisible();
});

test("a location needs a complete CRS; 'WGS84 + LCC' is not one", async ({ page }) => {
  await enterWorkspace(page);
  await page.goto("/projects/new");
  await page.getByLabel("Name").fill("With location");
  await page.getByLabel("Add an area of interest").check();
  await page.getByLabel("X (easting / longitude)").fill("77.2");
  await page.getByLabel("Y (northing / latitude)").fill("28.6");

  await page.getByRole("button", { name: "Create project" }).click();
  await expect(page.getByText("A location without a CRS is not accepted")).toBeVisible();

  const search = page.getByPlaceholder("e.g. EPSG:4326");
  await search.fill("WGS84 + LCC");
  await search.press("Enter");
  await expect(page.getByText("No matching CRS in the local registry")).toBeVisible();

  await search.fill("EPSG:4326");
  await search.press("Enter");
  await page.getByRole("list", { name: "CRS search results" }).getByRole("button", { name: /EPSG:4326 — WGS 84/ }).click();
  await expect(page.getByText("authority axis order: Lat (north, degree), Lon (east, degree)")).toBeVisible();

  await page.getByRole("button", { name: "Create project" }).click();
  await expect(page.getByRole("heading", { name: "With location", level: 1 })).toBeVisible();
  // The location is shown in words, as entered and in the CRS it was entered in.
  await expect(page.getByText("X 77.2, Y 28.6")).toBeVisible();
  await expect(page.getByText("in EPSG:4326", { exact: true })).toBeVisible();
  await expect(page.getByText("It does not show where any uploaded file was captured")).toBeVisible();
});

test("search, edit and archive a project", async ({ page }) => {
  await enterWorkspace(page);
  await page.getByLabel("Search projects").fill("harbour");
  await page.getByRole("button", { name: "Apply" }).click();
  const list = page.getByRole("list", { name: "Project list" });
  await expect(list.getByRole("listitem").filter({ hasText: "Created" })).toHaveCount(1);
  await list.getByRole("link", { name: "Harbour survey" }).click();

  await page.getByRole("button", { name: "Edit details" }).click();
  await page.getByLabel("Name").fill("Harbour survey 2");
  await page.getByRole("button", { name: "Save" }).click();
  await expect(page.getByRole("heading", { name: "Harbour survey 2", level: 1 })).toBeVisible();

  await page.getByRole("button", { name: "Archive project" }).click();
  await expect(page.getByRole("button", { name: "Restore project" })).toBeVisible();
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Project Dashboard" }).click();
  await expect(page.getByRole("list", { name: "Project list" })).not.toContainText("Harbour survey 2");
  await page.getByLabel("Show archived").check();
  await expect(page.getByRole("list", { name: "Project list" })).toContainText("Harbour survey 2");
});

test("later-phase screens say so, settings state the DSM position, and nothing leaves the machine", async ({ page }) => {
  const external: string[] = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.protocol.startsWith("http") && url.hostname !== "127.0.0.1") external.push(request.url());
  });

  await enterWorkspace(page);
  await page.getByRole("list", { name: "Project list" }).getByRole("link", { name: "With location" }).click();
  const nav = page.getByRole("navigation", { name: "Main" });
  // The 3D terrain view exists; a project with no rasters says so plainly and draws nothing.
  const threeD = nav.getByRole("link", { name: "3D View" });
  await expect(threeD).toBeVisible();
  await threeD.click();
  await expect(page.getByRole("heading", { name: "3D View", level: 1 })).toBeVisible();
  await expect(page.getByRole("status").filter({ hasText: "This project has no rasters" })).toBeVisible();
  // The map exists (Phase 4). A project with only a location shows that location and places no files.
  await nav.getByRole("link", { name: "Map & Analysis", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Map & Analysis View", level: 1 })).toBeVisible();
  await expect(page.getByRole("checkbox", { name: "Project area of interest" })).toBeChecked();
  await expect(page.getByText("No georeferenced rasters in this project.")).toBeVisible();
  for (const [link, title] of [
    ["Observations", "Observations"],
    ["Reports", "Reports"],
  ] as const) {
    await nav.getByRole("link", { name: link, exact: true }).click();
    await expect(page.getByRole("heading", { name: title, level: 1 })).toBeVisible();
  }
  await expect(page.getByText("PDF report")).toBeVisible();

  await page.goto("/settings/diagnostics");
  await expect(page.getByText("API bound to")).toBeVisible();
  await expect(page.getByText("127.0.0.1", { exact: true })).toBeVisible();
  const sections = page.getByRole("navigation", { name: "Settings sections" });
  await sections.getByRole("link", { name: "Models" }).click();
  // The end-to-end stack runs with the test-only stub models; a working installation never lists them.
  await expect(page.getByRole("list", { name: "Models" })).toContainText("Not calibrated and not evaluated");
  await sections.getByRole("link", { name: "Coordinate systems" }).click();
  await expect(page.getByText("DSM (Defence Series Map) compatibility is not claimed")).toBeVisible();
  await sections.getByRole("link", { name: "Data sources & storage" }).click();
  await expect(page.getByText("No online sources are configured")).toBeVisible();

  await page.goto("/projects/00000000-0000-4000-8000-000000000000");
  await expect(page.getByText("This project does not exist")).toBeVisible();

  expect(external).toEqual([]);
});
