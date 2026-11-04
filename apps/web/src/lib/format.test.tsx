import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { vi } from "vitest";

import * as client from "../api/client";
import { AppShell } from "../components/AppShell";
import { renderPage } from "../test/render";
import { describeGeometry, formatDateTime } from "./format";
import { flagLabel, formatCount, formatLabel } from "./ingestion";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, listProjects: vi.fn(), getHealth: vi.fn() };
});

describe("display formatting", () => {
  it("writes dates so that day and month cannot be confused", () => {
    const text = formatDateTime("2026-10-12T09:05:00Z");
    expect(text).toMatch(/^\d{2} Oct 2026, \d{2}:\d{2}$/);
    expect(formatDateTime("not a date")).toBe("unknown");
  });

  it("groups digits the same way whatever the computer's regional settings are", () => {
    expect(formatCount(791555)).toBe("791,555");
    expect(formatCount(4323960)).toBe("4,323,960");
  });

  it("never shows a raw flag code", () => {
    expect(flagLabel("UNCALIBRATED_SCORE")).toBe("Score not calibrated");
    expect(flagLabel("SOME_FUTURE_FLAG")).toBe("Some future flag");
    expect(formatLabel("GTiff")).toBe("TIFF / GeoTIFF");
    expect(formatLabel("PNG")).toBe("PNG");
  });

  it("describes a point and an extent in words, rounding only for display", () => {
    expect(describeGeometry({ type: "Point", coordinates: [500000, 3000000] }, { x: "X", y: "Y" })).toBe("X 500000, Y 3000000");
    expect(
      describeGeometry({ type: "Point", coordinates: [75.00000000000001, 27.1224696415507] }, { x: "longitude", y: "latitude" }, 6),
    ).toBe("longitude 75.000000, latitude 27.122470");
    const square = { type: "Polygon", coordinates: [[[0, 1], [2, 1], [2, 3], [0, 3], [0, 1]]] };
    expect(describeGeometry(square, { x: "X", y: "Y" })).toBe("X 0 to 2, Y 1 to 3");
    expect(describeGeometry({ type: "Point", coordinates: [] }, { x: "X", y: "Y" })).toBeNull();
  });
});

describe("main navigation", () => {
  const project = { id: "p1", name: "Survey", archived_at: null } as client.Project;

  async function render(route: string, projects: client.Project[] = [project]) {
    vi.mocked(client.listProjects).mockResolvedValue({ items: projects, next_cursor: null });
    vi.mocked(client.getHealth).mockResolvedValue({
      status: "ok",
      worker: { running: true },
      online: { enabled: false },
    } as unknown as client.Health);
    renderPage(
      <Routes>
        <Route element={<AppShell />}>
          <Route path="*" element={<div />} />
        </Route>
      </Routes>,
      route,
    );
    if (projects.length > 0) await screen.findByRole("option", { name: "Survey" });
    return screen.getAllByRole("navigation", { name: "Main" })[0]!;
  }

  async function current(route: string): Promise<string[]> {
    const nav = await render(route);
    return Array.from(nav.querySelectorAll('[aria-current="page"]')).map((link) => link.textContent ?? "");
  }

  it.each([
    ["/dashboard", "Project Dashboard"],
    ["/projects/new", "New Project"],
    ["/projects/p1/assets/new", "Add Files"],
    ["/projects/p1/assets", "Add Files"],
    ["/projects/p1/assets/a1", "Add Files"],
    ["/projects/p1/ground", "Photo Analysis"],
    ["/projects/p1/assets/a1/ground", "Photo Analysis"],
    ["/projects/p1/assets/a1/overhead", "Satellite Images"],
    ["/projects/p1/assets/a1/video", "Videos"],
    ["/projects/p1/observations", "Findings"],
    ["/projects/p1/reports", "Reports"],
    ["/projects/p1/map", "Map"],
    ["/settings/crs", "Settings"],
  ])("marks exactly one screen as current on %s", async (route, label) => {
    expect(await current(route)).toEqual([label]);
  });

  it("groups the screens and labels the ones that are planned", async () => {
    const nav = await render("/projects/p1/map");
    for (const group of ["Start", "Your files", "Explore and check", "Setup"]) expect(nav).toHaveTextContent(group);
    expect(nav).not.toHaveTextContent("Mapplanned"); // the map exists since Phase 4
    expect(nav).not.toHaveTextContent("3D Terrainplanned"); // the 3D terrain view exists
    expect(nav).not.toHaveTextContent("Photo Analysisplanned");
  });

  it("disables project screens, with a reason, until a project is open", async () => {
    const nav = await render("/dashboard", []);
    const ground = within(nav).getByText("Photo Analysis").closest("[aria-disabled]");
    expect(ground).toHaveAttribute("aria-disabled", "true");
    expect(ground).toHaveAttribute("title", "Photo Analysis — open a project first");
    expect(within(nav).getByRole("link", { name: /Project Dashboard/ })).toHaveAttribute("href", "/dashboard");
  });

  it("collapses the sidebar and remembers it", async () => {
    await render("/dashboard");
    await userEvent.click(screen.getByRole("button", { name: "Collapse sidebar" }));
    expect(screen.getByRole("button", { name: "Expand sidebar" })).toBeInTheDocument();
    expect(localStorage.getItem("geopoc.sidebarCollapsed")).toBe("true");
  });
});
