import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Outlet, Route, Routes } from "react-router-dom";
import { vi } from "vitest";

import * as client from "../api/client";
import { detail, view } from "../test/fixtures";
import { renderPage } from "../test/render";
import { DEMO_EMAIL, DEMO_PASSWORD, signIn, signOut } from "../lib/auth";
import { LoginPage } from "./LoginPage";
import { ObservationsPage } from "./ObservationsPage";
import { ReportsPage } from "./ReportsPage";
import { SettingsPage } from "./SettingsPage";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    listProfiles: vi.fn(),
    createProfile: vi.fn(),
    getHealth: vi.fn(),
    getModelHealth: vi.fn(),
    listObservations: vi.fn(),
    listAssets: vi.fn(),
    getProjectSummary: vi.fn(),
    listConflicts: vi.fn(),
    getObservation: vi.fn(),
    reviewObservation: vi.fn(),
    downloadExport: vi.fn(),
  };
});

const PROJECT = { id: "p1", name: "Survey", archived_at: null } as client.Project;

function observation(id: string, assetId: string, label: string, status: string): client.Observation {
  return {
    schema_version: "3.0",
    observation_id: id,
    project_id: "p1",
    asset_id: assetId,
    pipeline: "ground_image",
    model: { name: "detector", version: "v1", run_id: "r1" },
    claim: { category: `coco:${label}`, description: "Model-predicted feature.", claim_type: "inferred" },
    score: { raw_value: 0.9, semantics: "Class score after NMS.", calibrated: false, value_range: [0, 1] },
    evidence: [],
    geospatial: { geometry: null, crs_id: null, coordinate_units: null, derivation_method: null, uncertainty: null },
    measurements: [],
    quality_flags: ["UNCALIBRATED_SCORE"],
    review: { status, reviewer_note: null, reviewed_at: null, reviewed_by: null },
    provenance: { source_checksum: "ab".repeat(32), ingested_at: "2026-10-12T00:00:00Z", transformations: [], created_at: "2026-10-12T00:00:00Z" },
  } as client.Observation;
}

const HEALTH = {
  status: "ok",
  bind_host: "127.0.0.1",
  database: { reachable: true, postgis_version: "3.5", schema_revision: "0003" },
  storage: { data_root: "D:\\geopoc-data", data_root_ok: true, free_bytes: 10 * 1024 ** 3 },
  worker: { running: true },
  online: { enabled: false, proj_network: false },
  versions: { api: "0.1.0" },
} as unknown as client.Health;

function inProject(element: React.ReactElement, path: string) {
  return renderPage(
    <Routes>
      <Route path="/projects/:projectId" element={<Outlet context={PROJECT} />}>
        <Route path="*" element={element} />
      </Route>
    </Routes>,
    `/projects/p1/${path}`,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(client.getHealth).mockResolvedValue(HEALTH);
});

describe("LoginPage sign-in", () => {
  beforeEach(() => signOut());
  afterEach(() => signOut());

  it("asks for the demo email and password first, and says it protects nothing", async () => {
    vi.mocked(client.listProfiles).mockResolvedValue({ items: [], notice: "" });
    renderPage(<LoginPage />, "/login");
    expect(await screen.findByRole("form", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.getByTestId("demo-credentials")).toHaveTextContent(DEMO_EMAIL);
    expect(screen.getByTestId("demo-credentials")).toHaveTextContent("protects nothing");
    expect(screen.queryByRole("button", { name: "Create profile and continue" })).toBeNull();
  });

  it("refuses a wrong password without saying which part was wrong", async () => {
    vi.mocked(client.listProfiles).mockResolvedValue({ items: [], notice: "" });
    renderPage(<LoginPage />, "/login");
    await userEvent.type(await screen.findByLabelText("Email"), DEMO_EMAIL);
    await userEvent.type(screen.getByLabelText("Password"), "nope");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("The email or password is not correct.");
    expect(screen.queryByRole("button", { name: "Create profile and continue" })).toBeNull();
  });

  it("asks for both fields before checking anything", async () => {
    vi.mocked(client.listProfiles).mockResolvedValue({ items: [], notice: "" });
    renderPage(<LoginPage />, "/login");
    await userEvent.click(await screen.findByRole("button", { name: "Sign in" }));
    expect(screen.getByText("Enter your email.")).toBeInTheDocument();
    expect(screen.getByText("Enter your password.")).toBeInTheDocument();
  });

  it("accepts the demo account (any letter case in the email) and then offers the profiles", async () => {
    vi.mocked(client.listProfiles).mockResolvedValue({ items: [{ id: "u1", display_name: "Asha", created_at: "2026-10-12T00:00:00Z" }], notice: "" });
    renderPage(<LoginPage />, "/login");
    await userEvent.type(await screen.findByLabelText("Email"), ` ${DEMO_EMAIL.toUpperCase()} `);
    await userEvent.type(screen.getByLabelText("Password"), DEMO_PASSWORD);
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("button", { name: "Continue as Asha" })).toBeInTheDocument();
    expect(screen.queryByRole("form", { name: "Sign in" })).toBeNull();
  });

  it("fills in the demo details on request, and a wrong-case password is still refused", async () => {
    expect(signIn(DEMO_EMAIL, DEMO_PASSWORD.toLowerCase())).toBe(false);
    vi.mocked(client.listProfiles).mockResolvedValue({ items: [], notice: "" });
    renderPage(<LoginPage />, "/login");
    await userEvent.click(await screen.findByRole("button", { name: "Fill in the demo details" }));
    expect(screen.getByLabelText("Email")).toHaveValue(DEMO_EMAIL);
    expect(screen.getByLabelText("Password")).toHaveValue(DEMO_PASSWORD);
  });
});

describe("LoginPage", () => {
  beforeEach(() => {
    signIn(DEMO_EMAIL, DEMO_PASSWORD);
  });
  afterEach(() => signOut());

  it("says plainly that a profile is not authentication and asks for no password", async () => {
    vi.mocked(client.listProfiles).mockResolvedValue({ items: [{ id: "u1", display_name: "Asha", created_at: "2026-10-12T00:00:00Z" }], notice: "" });
    renderPage(<LoginPage />, "/login");
    expect(await screen.findByRole("button", { name: "Continue as Asha" })).toBeInTheDocument();
    expect(screen.getByText(/It is not a login and gives no access control/)).toBeInTheDocument();
    expect(document.querySelector('input[type="password"]')).toBeNull();
    expect(await screen.findByRole("list", { name: "Service health" })).toBeInTheDocument();
  });

  it("validates the new profile name before sending anything", async () => {
    vi.mocked(client.listProfiles).mockResolvedValue({ items: [], notice: "" });
    renderPage(<LoginPage />, "/login");
    await userEvent.click(await screen.findByRole("button", { name: "Create profile and continue" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Enter a name for the profile.");
    expect(client.createProfile).not.toHaveBeenCalled();
  });

  it("explains a name that is already taken", async () => {
    vi.mocked(client.listProfiles).mockResolvedValue({ items: [], notice: "" });
    vi.mocked(client.createProfile).mockRejectedValue(new client.ApiError(409, "PROFILE_EXISTS", "exists"));
    renderPage(<LoginPage />, "/login");
    await userEvent.type(await screen.findByLabelText("New profile name"), "Asha");
    await userEvent.click(screen.getByRole("button", { name: "Create profile and continue" }));
    expect(await screen.findByText("A profile with this name already exists")).toBeInTheDocument();
  });
});

describe("ObservationsPage", () => {
  const boat = view(observation("o1", "a1", "boat", "needs_review"), { asset_filename: "harbour.jpg" });
  beforeEach(() => {
    vi.mocked(client.listConflicts).mockResolvedValue([]);
    vi.mocked(client.listObservations).mockResolvedValue([
      boat,
      view(observation("o2", "a2", "car", "needs_review"), { asset_filename: "road.jpg", open_conflict_ids: ["c1"] }),
      view(observation("o3", "a2", "truck", "proposed"), {
        asset_filename: "road.jpg",
        current: { category: "coco:lorry", relabelled: true, revision: 1 },
      }),
    ]);
    vi.mocked(client.getObservation).mockResolvedValue(detail(boat));
  });

  it("filters by current state, conflicts and text, and offers all six review states", async () => {
    inProject(<ObservationsPage />, "observations");
    const table = await screen.findByRole("table", { name: "Findings in this project" });
    expect(within(table).getAllByRole("row")).toHaveLength(4);
    // A corrected category is shown, with the model's own label beside it.
    expect(table).toHaveTextContent("lorrycorrected; model: truck");

    const states = within(screen.getByLabelText("Review state")).getAllByRole("option").map((o) => o.textContent);
    expect(states).toHaveLength(7); // "All" plus the six states
    await userEvent.selectOptions(screen.getByLabelText("Review state"), "proposed");
    expect(within(table).getAllByRole("row")).toHaveLength(2);
    expect(table).toHaveTextContent("lorry");

    await userEvent.selectOptions(screen.getByLabelText("Review state"), "");
    await userEvent.selectOptions(screen.getByLabelText("Conflicts"), "open");
    expect(within(table).getAllByRole("row")).toHaveLength(2);
    expect(table).toHaveTextContent("car");
    await userEvent.selectOptions(screen.getByLabelText("Conflicts"), "");

    await userEvent.type(screen.getByLabelText("Search class or file"), "harbour");
    await waitFor(() => expect(within(table).getAllByRole("row")).toHaveLength(2));
    expect(table).toHaveTextContent("boat");
    expect(client.listObservations).toHaveBeenCalledWith("p1", { limit: 500 });
  });

  it("says which video frame a finding is in, and opens the video screen rather than the photograph screen", async () => {
    const base = observation("o9", "v1", "person", "needs_review");
    const frameFinding = view(
      {
        ...base,
        pipeline: "video",
        evidence: [
          {
            asset_id: "v1",
            region_px: { type: "bbox_xyxy", coordinates: [10, 10, 50, 40], coordinate_space: "source_image_pixels", origin: "top_left", pixel_reference: "corner", image_width: 96, image_height: 72 },
            frame_index: 20,
            timestamp_ms: 2000,
            source_excerpt_or_metadata_key: `video_frame_sha256:${"ab".repeat(32)}`,
          },
        ],
      } as client.Observation,
      { asset_filename: "gate.mp4" },
    );
    vi.mocked(client.listObservations).mockResolvedValue([frameFinding]);
    vi.mocked(client.getObservation).mockResolvedValue(detail(frameFinding));
    inProject(<ObservationsPage />, "observations");
    const table = await screen.findByRole("table", { name: "Findings in this project" });
    await userEvent.click(within(table).getAllByRole("row")[1]!);
    const evidence = await screen.findByRole("region", { name: "Evidence" });
    expect(evidence).toHaveTextContent("Frame 20 at 0:02.000");
    expect(evidence).toHaveTextContent("The region below is in that frame, not in the whole video.");
    expect(screen.getByRole("link", { name: "Open in Videos" })).toHaveAttribute("href", "/projects/p1/assets/v1/video?run=r1");
    expect(screen.queryByRole("link", { name: "Open in Photo Analysis" })).not.toBeInTheDocument();
  });

  it("shows evidence and provenance and offers the review actions the state allows", async () => {
    inProject(<ObservationsPage />, "observations");
    const table = await screen.findByRole("table", { name: "Findings in this project" });
    await userEvent.click(within(table).getAllByRole("row")[1]!);
    expect(await screen.findByRole("region", { name: "Provenance" })).toHaveTextContent("detector v1");
    expect(screen.getByRole("link", { name: "Open in Photo Analysis" })).toHaveAttribute(
      "href",
      "/projects/p1/assets/a1/ground?run=r1",
    );
    const actions = await screen.findByRole("group", { name: "Review actions" });
    expect(within(actions).getByRole("button", { name: "Confirm" })).toBeInTheDocument();
    expect(within(actions).queryByRole("button", { name: "Reopen for review" })).not.toBeInTheDocument();
    expect(screen.getByText(/No review yet/)).toBeInTheDocument();
  });
});

describe("ReviewPanel", () => {
  const boat = view(observation("o1", "a1", "boat", "needs_review"));

  async function openReview() {
    vi.mocked(client.listObservations).mockResolvedValue([boat]);
    vi.mocked(client.listConflicts).mockResolvedValue([]);
    vi.mocked(client.getObservation).mockResolvedValue(detail(boat));
    inProject(<ObservationsPage />, "observations");
    const table = await screen.findByRole("table", { name: "Findings in this project" });
    await userEvent.click(within(table).getAllByRole("row")[1]!);
    return screen.findByRole("group", { name: "Review actions" });
  }

  it("requires a reason before a rejection can be saved, and sends the revision that was reviewed", async () => {
    const actions = await openReview();
    vi.mocked(client.reviewObservation).mockResolvedValue(
      detail(view(boat.observation, { current: { status: "rejected", revision: 1, last_note: "Driftwood." } }), {
        revisions: [
          {
            revision_no: 1,
            action: "reject",
            status_before: "needs_review",
            status_after: "rejected",
            category_before: "coco:boat",
            category_after: "coco:boat",
            note: "Driftwood.",
            actor_kind: "reviewer",
            actor_label: "Asha",
            details: {},
            created_at: "2026-10-21T10:00:00Z",
          },
        ],
      }),
    );
    await userEvent.click(within(actions).getByRole("button", { name: "Reject" }));
    const form = screen.getByRole("form", { name: "Reject" });
    expect(within(form).getByText(/The state becomes/)).toHaveTextContent("Rejected");
    const save = within(form).getByRole("button", { name: "Save: Reject" });
    expect(save).toBeDisabled();
    await userEvent.type(within(form).getByLabelText("Reason (required)"), "Driftwood.");
    await userEvent.click(save);
    expect(client.reviewObservation).toHaveBeenCalledWith("o1", {
      action: "reject",
      expected_revision: 0,
      note: "Driftwood.",
      category: null,
    });
    expect(await screen.findByText("Reject saved as revision 1.")).toBeInTheDocument();
    const history = screen.getByRole("list", { name: "Review history" });
    expect(history).toHaveTextContent("Needs review → Rejected");
    expect(history).toHaveTextContent("“Driftwood.”");
    expect(history).toHaveTextContent("Asha");
  });

  it("explains a revision conflict instead of overwriting another review", async () => {
    const actions = await openReview();
    vi.mocked(client.reviewObservation).mockRejectedValue(
      new client.ApiError(409, "REVISION_CONFLICT", "changed", "You reviewed revision 0; it is now at revision 1."),
    );
    await userEvent.click(within(actions).getByRole("button", { name: "Confirm" }));
    await userEvent.click(screen.getByRole("button", { name: "Save: Confirm" }));
    expect(await screen.findByText("Someone else changed this finding")).toBeInTheDocument();
    expect(screen.getByText(/it is now at revision 1/)).toBeInTheDocument();
    await waitFor(() => expect(client.getObservation).toHaveBeenCalledTimes(2)); // the latest version is reloaded
  });

  it("relabels with a new category and a reason", async () => {
    const actions = await openReview();
    vi.mocked(client.reviewObservation).mockResolvedValue(
      detail(view(boat.observation, { current: { category: "kayak", relabelled: true, revision: 1 } })),
    );
    await userEvent.click(within(actions).getByRole("button", { name: "Correct the category" }));
    const input = screen.getByLabelText("Correct category");
    expect(input).toHaveValue("boat");
    await userEvent.clear(input);
    await userEvent.type(input, "kayak");
    await userEvent.type(screen.getByLabelText("Reason (required)"), "Single paddler.");
    await userEvent.click(screen.getByRole("button", { name: "Save: Correct the category" }));
    expect(client.reviewObservation).toHaveBeenCalledWith("o1", {
      action: "relabel",
      expected_revision: 0,
      note: "Single paddler.",
      category: "kayak",
    });
  });
});

describe("ReportsPage", () => {
  beforeEach(() => {
    vi.mocked(client.getProjectSummary).mockResolvedValue({
      project_id: "p1",
      assets_by_modality: { ground_image: 2, overhead_raster: 0, video: 0 },
      assets_total: 2,
      jobs_by_status: {},
      observations_by_review_status: { needs_review: 1 },
      observations_total: 1,
      last_activity_at: "2026-10-12T00:00:00Z",
    });
  });

  it("offers the three server exports and no report download", async () => {
    vi.mocked(client.downloadExport).mockResolvedValue({ filename: "observations-p1.csv", bytes: 2048 });
    inProject(<ReportsPage />, "reports");
    for (const format of ["CSV", "GEOJSON", "JSON"]) {
      expect(await screen.findByRole("button", { name: `Download ${format}` })).toBeEnabled();
    }
    await userEvent.click(screen.getByRole("button", { name: "Download CSV" }));
    expect(client.downloadExport).toHaveBeenCalledWith("p1", "csv");
    expect(await screen.findByRole("status")).toHaveTextContent("Saved observations-p1.csv (2.0 KB).");
    expect(screen.queryByRole("button", { name: /pdf|html|report/i })).not.toBeInTheDocument();
    expect(screen.getByText("PDF report")).toBeInTheDocument();
    expect(screen.getByText(/positions in an image are counted and left out/)).toBeInTheDocument();
  });

  it("shows an export failure with its code", async () => {
    vi.mocked(client.downloadExport).mockRejectedValue(
      new client.ApiError(401, "PROFILE_REQUIRED", "A local workspace profile is required"),
    );
    inProject(<ReportsPage />, "reports");
    await userEvent.click(await screen.findByRole("button", { name: "Download JSON" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Code: PROFILE_REQUIRED");
  });
});

describe("SettingsPage", () => {
  function renderSettings(tab: string) {
    return renderPage(
      <Routes>
        <Route path="/settings/:tab" element={<SettingsPage />} />
      </Routes>,
      `/settings/${tab}`,
    );
  }

  it("reports network use from the API as read-only and claims no enforcement", async () => {
    renderSettings("network");
    expect(await screen.findByText("Network use")).toBeInTheDocument();
    expect(screen.getByText("Read-only")).toBeInTheDocument();
    expect(screen.getByText("127.0.0.1")).toBeInTheDocument();
    expect(screen.getByText(/It does not block the network at the\s+operating-system level/)).toBeInTheDocument();
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
    expect(screen.queryByRole("switch")).not.toBeInTheDocument();
  });

  it("stores the sidebar preference in this browser only", async () => {
    renderSettings("diagnostics");
    await userEvent.click(screen.getByRole("checkbox", { name: /Start with the sidebar collapsed/ }));
    expect(localStorage.getItem("geopoc.sidebarCollapsed")).toBe("true");
    expect(await screen.findByText("API bound to")).toBeInTheDocument();
  });

  it("lists uncalibrated models without accuracy claims", async () => {
    vi.mocked(client.getModelHealth).mockResolvedValue({
      manifest_found: true,
      errors: [],
      worker: { running: true, capabilities: { cuda_available: false } },
      llm_runtime: { note: "Not configured." },
      models: [
        {
          name: "detector",
          version: "v1",
          pipeline: "ground_image",
          task: "object_detection",
          runtime: "torchvision",
          licence: "BSD",
          score_semantics: "Class score after NMS.",
          score_calibrated: false,
          settings: {},
          weights_sha256: null,
          weights_size_bytes: null,
          available: true,
          reason_unavailable: null,
        },
      ],
    } as client.ModelHealth);
    renderSettings("models");
    const list = await screen.findByRole("list", { name: "Models" });
    expect(list).toHaveTextContent("Not calibrated and not evaluated on representative data: no accuracy is claimed");
  });
});
