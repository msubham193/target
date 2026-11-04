import { expect, type Page, test } from "@playwright/test";

// A project area of interest in a CRS that needs a datum transformation, against the real API and database. The planner
// is the same one every other conversion uses: several registered shifts need a choice, a ballpark-only CRS is refused,
// and nothing is stored in either case until the area can be converted. Synthetic coordinates; no ESM or Everest-56
// definition is involved or implied.
test.describe.configure({ mode: "serial" });

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

async function startProject(page: Page, name: string, crs: string, x: string, y: string) {
  await page.getByRole("link", { name: "Create project", exact: true }).click();
  await page.getByLabel("Name").fill(name);
  await page.getByLabel("Add an area of interest").check();
  await page.getByLabel("X (easting / longitude)").fill(x);
  await page.getByLabel("Y (northing / latitude)").fill(y);
  await page.getByPlaceholder(/EPSG:4326/).fill(crs.replace("EPSG:", ""));
  await page.getByPlaceholder(/EPSG:4326/).press("Enter");
  await page.getByRole("list", { name: "CRS search results" }).getByRole("button", { name: new RegExp(crs) }).click();
  await page.getByRole("button", { name: "Create project" }).click();
}

test("an area of interest that needs a datum choice asks for one, and a ballpark-only CRS is refused", async ({ page }) => {
  await enterWorkspace(page);

  // Indian 1975 / UTM 47N near Thailand: two registered shifts, none chosen for the person.
  await startProject(page, "Thailand survey", "EPSG:24047", "619000", "1558000");
  const choice = page.getByTestId("aoi-transformation-choice");
  await expect(choice).toBeVisible({ timeout: 15_000 });
  const select = choice.getByLabel("Datum transformation to WGS 84");
  await expect(select).toHaveValue("");
  await expect(select.locator("option")).toHaveCount(3); // "Choose…" and the two registered shifts
  await select.selectOption("EPSG:1812");
  await page.getByRole("button", { name: "Create project" }).click();
  await expect(page).toHaveURL(/\/projects\/[0-9a-f-]{36}$/, { timeout: 15_000 });

  const id = page.url().split("/").pop();
  const project = (await (await page.request.get(`/api/v1/projects/${id}`)).json()) as {
    aoi: { source: { transformation: { operation: { id: string; classification: string; chosen_by: string } } } };
  };
  expect(project.aoi.source.transformation.operation).toMatchObject({ id: "EPSG:1812", classification: "approximate", chosen_by: "selection" });

  // Kalianpur 1880 / India zone I: only a ballpark fallback exists. Refused, with no choice offered and nothing stored.
  await page.goto("/dashboard");
  const before = ((await (await page.request.get("/api/v1/projects")).json()) as { items: unknown[] }).items.length;
  await startProject(page, "India ballpark", "EPSG:24371", "1000000", "1000000");
  await expect(page.getByText("Only a rough ballpark transformation is available")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("aoi-transformation-choice")).toHaveCount(0);
  const after = ((await (await page.request.get("/api/v1/projects")).json()) as { items: unknown[] }).items.length;
  expect(after).toBe(before);
});
