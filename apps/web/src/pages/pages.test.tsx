import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";

import * as client from "../api/client";
import { renderPage } from "../test/render";
import { DashboardPage } from "./DashboardPage";
import { NewProjectPage } from "./NewProjectPage";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    listProjects: vi.fn(),
    listRecentJobs: vi.fn(),
    getProjectSummary: vi.fn(),
    createProject: vi.fn(),
    searchCrs: vi.fn(),
  };
});

const listProjects = vi.mocked(client.listProjects);
const createProject = vi.mocked(client.createProject);

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(client.listRecentJobs).mockResolvedValue({ items: [] });
  vi.mocked(client.getProjectSummary).mockResolvedValue({
    project_id: "p1",
    assets_by_modality: { ground_image: 3, overhead_raster: 1, video: 0 },
    assets_total: 4,
    jobs_by_status: { completed: 4 },
    observations_by_review_status: { needs_review: 2 },
    observations_total: 2,
    last_activity_at: "2026-10-12T00:00:00Z",
  });
});

const HARBOUR = {
  id: "p1",
  name: "Harbour",
  description: null,
  tags: ["coast"],
  aoi: null,
  source_preference: "local_only",
  created_by: { id: "u1", display_name: "Asha", created_at: "2026-10-12T00:00:00Z" },
  created_at: "2026-10-12T00:00:00Z",
  updated_at: "2026-10-12T00:00:00Z",
  archived_at: null,
} as client.Project;

describe("DashboardPage", () => {
  it("shows the first-use empty state and no invented counts", async () => {
    listProjects.mockResolvedValue({ items: [], next_cursor: null });
    renderPage(<DashboardPage />);
    expect(await screen.findByText("No projects yet")).toBeInTheDocument();
    expect(screen.queryByRole("list", { name: "Project list" })).not.toBeInTheDocument();
    // Recent activity comes from the API; with none, the panel says so rather than showing invented activity.
    expect(await screen.findByText(/Nothing yet/)).toBeInTheDocument();
    expect(screen.queryByText(/not part of this phase/)).not.toBeInTheDocument();
    expect(client.getProjectSummary).not.toHaveBeenCalled();
  });

  it("shows an actionable error with its diagnostic code when the API is unreachable", async () => {
    listProjects.mockRejectedValue(new client.ApiError(0, "API_UNREACHABLE", "The local API is not reachable"));
    renderPage(<DashboardPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("The local API is not reachable");
    expect(screen.getByRole("alert")).toHaveTextContent("Code: API_UNREACHABLE");
  });

  it("lists projects and states when no location was entered", async () => {
    listProjects.mockResolvedValue({
      items: [
        {
          id: "p1",
          name: "Harbour",
          description: null,
          tags: ["coast"],
          aoi: null,
          source_preference: "local_only",
          created_by: { id: "u1", display_name: "Asha", created_at: "2026-10-12T00:00:00Z" },
          created_at: "2026-10-12T00:00:00Z",
          updated_at: "2026-10-12T00:00:00Z",
          archived_at: null,
        },
      ],
      next_cursor: null,
    });
    renderPage(<DashboardPage />);
    expect(await screen.findByRole("link", { name: "Harbour" })).toHaveAttribute("href", "/projects/p1");
    expect(screen.getByText(/Location: none entered/)).toBeInTheDocument();
  });

  it("shows counts only as the summary endpoint reports them", async () => {
    listProjects.mockResolvedValue({ items: [HARBOUR], next_cursor: null });
    renderPage(<DashboardPage />);
    await waitFor(() => expect(client.getProjectSummary).toHaveBeenCalledWith("p1"));
    const row = within(await screen.findByRole("list", { name: "Project list" })).getAllByRole("listitem")[0]!;
    await waitFor(() => expect(row).toHaveTextContent(/2\s*findings?/));
  });
});

describe("NewProjectPage", () => {
  it("creates a project with no location when none is entered", async () => {
    createProject.mockResolvedValue({ id: "p1" } as client.Project);
    const user = userEvent.setup();
    renderPage(<NewProjectPage />);
    await user.type(screen.getByLabelText("Name"), "Survey A");
    await user.click(screen.getByRole("button", { name: "Create project" }));
    await waitFor(() => expect(createProject).toHaveBeenCalledTimes(1));
    expect(createProject.mock.calls[0]![0]).toEqual({
      name: "Survey A",
      description: null,
      tags: [],
      source_preference: "local_only",
    });
  });

  it("refuses a location without a CRS and sends nothing", async () => {
    const user = userEvent.setup();
    renderPage(<NewProjectPage />);
    await user.type(screen.getByLabelText("Name"), "Survey B");
    await user.click(screen.getByLabelText("Add an area of interest"));
    await user.type(screen.getByLabelText("X (first coordinate)"), "77.2");
    await user.type(screen.getByLabelText("Y (second coordinate)"), "28.6");
    await user.click(screen.getByRole("button", { name: "Create project" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("A location without a CRS is not accepted");
    expect(createProject).not.toHaveBeenCalled();
  });

  it("removes a form error as soon as the form is changed", async () => {
    const user = userEvent.setup();
    renderPage(<NewProjectPage />);
    await user.click(screen.getByRole("button", { name: "Create project" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Enter a project name.");
    await user.type(screen.getByLabelText("Name"), "S");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("requires a name", async () => {
    const user = userEvent.setup();
    renderPage(<NewProjectPage />);
    await user.click(screen.getByRole("button", { name: "Create project" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Enter a project name.");
    expect(createProject).not.toHaveBeenCalled();
  });

  it("keeps place search and online imagery sources disabled when no Mapbox token is configured", () => {
    vi.stubEnv("VITE_MAPBOX_ACCESS_TOKEN", "");
    renderPage(<NewProjectPage />);
    expect(screen.getByLabelText("Place search")).toBeDisabled();
    expect(screen.getByLabelText(/Allow configured online imagery sources/)).toBeDisabled();
    vi.unstubAllEnvs();
  });
});

