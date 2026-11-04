import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Outlet, Route, Routes } from "react-router-dom";
import { vi } from "vitest";

import * as client from "../api/client";
import { renderPage } from "../test/render";
import { FindingsReportPage, ReportView } from "./FindingsReportPage";
import { ReportsPage } from "./ReportsPage";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, getFindingsReport: vi.fn(), getProjectSummary: vi.fn(), downloadExport: vi.fn() };
});

const HOSTILE = '<script>window.__pwned = 1</script><img src="x" onerror="window.__pwned = 2">';
const SHA_A = "a1".repeat(32);
const SHA_B = "b2".repeat(32);
const PROJECT = { id: "p1", name: "Survey", archived_at: null } as client.Project;

type Report = client.FindingsReport;
type Finding = Report["observations"][number];
type Revision = Finding["revisions"][number];
type Annotation = Report["annotations"]["items"][number];
type AnnotationVersion = Annotation["versions"][number];

const NO_ANNOTATIONS: Report["annotations"] = {
  available: true,
  note: "An annotation is a mark a person drew on the map, with a note. It is not a finding.",
  active: 0,
  deleted: 0,
  items: [],
};

function annotationVersion(no: number, over: Partial<AnnotationVersion> = {}): AnnotationVersion {
  return {
    version_no: no,
    action: no === 1 ? "create" : "update",
    status: "active",
    note: "gate",
    reason: null,
    actor_label: "Asha",
    created_at: "2026-10-14T08:00:00Z",
    geometry_changed: no === 1,
    ...over,
  } as AnnotationVersion;
}

function annotation(id: string, over: Partial<Annotation> = {}): Annotation {
  return {
    feature_type: "user_annotation",
    annotation_id: id,
    project_id: "p1",
    kind: "point",
    status: "active",
    version_no: 1,
    geometry: { type: "Point", coordinates: [75.0, 27.0] },
    crs_id: "EPSG:4326",
    note: "gate",
    measurement: null,
    created_by: "Asha",
    created_at: "2026-10-14T08:00:00Z",
    updated_by: "Asha",
    updated_at: "2026-10-14T08:00:00Z",
    deleted_by: null,
    deleted_at: null,
    versions: [annotationVersion(1)],
    ...over,
  } as Annotation;
}

/** One active point, one active line (edited twice), one deleted polygon. */
function annotationSet(): Report["annotations"] {
  const line = annotation("11111111-1111-4111-8111-111111111111", {
    kind: "line",
    version_no: 3,
    geometry: { type: "LineString", coordinates: [[75.0, 27.0], [75.02, 27.0], [75.02, 27.01]] },
    note: "fence, east side",
    updated_by: "Ravi",
    updated_at: "2026-10-15T09:30:00Z",
    measurement: { length_m: 2500, area_m2: null, perimeter_m: null, method: "geodesic on the WGS 84 ellipsoid", limitations: [] },
    versions: [
      annotationVersion(1, { note: "fence" }),
      annotationVersion(2, { note: "fence, east side", reason: "clarified", actor_label: "Ravi", geometry_changed: false }),
      annotationVersion(3, { note: "fence, east side", actor_label: "Ravi", geometry_changed: true }),
    ],
  });
  const field = annotation("22222222-2222-4222-8222-222222222222", {
    kind: "polygon",
    status: "deleted",
    version_no: 2,
    geometry: { type: "Polygon", coordinates: [[[75.0, 27.0], [75.001, 27.0], [75.001, 27.001], [75.0, 27.001], [75.0, 27.0]]] },
    note: "field",
    deleted_by: "Ravi",
    deleted_at: "2026-10-16T10:00:00Z",
    updated_by: "Ravi",
    updated_at: "2026-10-16T10:00:00Z",
    versions: [
      annotationVersion(1, { note: "field" }),
      annotationVersion(2, { action: "delete", status: "deleted", note: "field", reason: "drawn in error", actor_label: "Ravi", geometry_changed: false }),
    ],
  });
  const point = annotation("00000000-0000-4000-8000-000000000000");
  return { ...NO_ANNOTATIONS, active: 2, deleted: 1, items: [point, line, field] };
}

const REGION = {
  type: "bbox_xyxy",
  coordinates: [10, 20, 50, 60],
  coordinate_space: "source_image_pixels",
  origin: "top_left",
  pixel_reference: "corner",
  image_width: 100,
  image_height: 80,
} as const;

function revision(no: number, note: string | null, extra: Partial<Revision> = {}): Revision {
  return {
    revision_no: no,
    action: "note",
    status_before: "needs_review",
    status_after: "needs_review",
    category_before: "coco:car",
    category_after: "coco:car",
    note,
    actor_kind: "reviewer",
    actor_label: "Asha",
    created_at: "2026-10-12T10:00:00Z",
    ...extra,
  } as Revision;
}

function finding(id: string, status: string, over: Partial<Finding> = {}): Finding {
  const reviewed = ["verified", "rejected", "inconclusive"].includes(status);
  return {
    observation_id: id,
    asset_id: "a1",
    asset_filename: "street.jpg",
    pipeline: "ground_image",
    claim_description: "Model-predicted feature.",
    claim_type: "inferred",
    model: { name: "detector", version: "v1", run_id: "r1" },
    model_category: "coco:car",
    current_category: "coco:car",
    relabelled: false,
    score_value: 0.91,
    score_semantics: "Class score after NMS.",
    score_calibrated: false,
    score_range: [0, 1],
    review_status: status,
    review_group: reviewed ? "reviewed" : "unreviewed",
    revision: 0,
    evidence: [
      {
        asset_id: "a1",
        asset_filename: "street.jpg",
        region: REGION,
        frame_index: null,
        timestamp_ms: null,
        source_excerpt_or_metadata_key: null,
      },
    ],
    quality_flags: ["UNCALIBRATED_SCORE"],
    geographically_located: false,
    open_conflict_ids: [],
    reviewer_notes: [],
    revisions: [],
    created_at: "2026-10-12T09:00:00Z",
    unavailable_fields: [],
    ...over,
  } as Finding;
}

function report(over: Partial<Report> = {}): Report {
  const note = revision(1, "Matches the site visit");
  const observations = [
    finding("o1", "verified", { revision: 1, reviewer_notes: [note], revisions: [note] }),
    finding("o2", "needs_review"),
  ];
  const step = (status: string, lat: number | null, lon: number | null, withheld: boolean, version: number) => ({
    version_no: version,
    status,
    source: "exif",
    latitude: lat,
    longitude: lon,
    coordinates_withheld: withheld,
    crs_id: "EPSG:4326",
    candidate_version_no: version === 2 ? 1 : null,
    reason: null,
    actor: "Asha",
    at: "2026-10-12T00:00:00Z",
  });
  return {
    schema_version: "1.1",
    generated_at: "2026-10-29T12:00:00Z",
    generated_by: "Asha",
    read_only: true,
    project: {
      id: "p1",
      name: "Survey",
      description: null,
      created_at: "2026-10-01T00:00:00Z",
      created_by: "Asha",
      archived_at: null,
    },
    summary: {
      assets_total: 1,
      runs_total: 1,
      observations_total: 2,
      observations_by_review_status: {
        proposed: 0,
        needs_review: 1,
        verified: 1,
        inconclusive: 0,
        disputed: 0,
        rejected: 0,
      },
      observations_reviewed: 1,
      observations_unreviewed: 1,
      observations_located: 0,
      observations_unlocated: 2,
      observations_with_reviewer_notes: 1,
      scores_calibrated: 0,
      scores_not_calibrated: 2,
      scores_unavailable: 0,
      conflicts_open: 0,
      conflicts_resolved: 0,
      photos_total: 3,
      photos_confirmed_location: 1,
      photos_unconfirmed_candidate: 1,
      photos_without_location: 1,
      annotations_active: 0,
      annotations_deleted: 0,
    },
    counting_rules: ["Reviewed: the current status is verified, rejected or inconclusive."],
    assets: [
      {
        asset_id: "a1",
        filename: "street.jpg",
        modality: "ground_image",
        media_type: "image/jpeg",
        detected_format: "jpeg",
        size_bytes: 12345,
        sha256: SHA_A,
        status: "validated",
        ingested_at: "2026-10-02T00:00:00Z",
        uploaded_by: "Asha",
        duplicate_of_asset_id: null,
        quality_flags: [],
        thumbnail_available: true,
        observation_count: 2,
      },
    ],
    runs: [
      {
        run_id: "r1",
        asset_id: "a1",
        asset_filename: "street.jpg",
        pipeline: "ground_image",
        model_name: "detector",
        model_version: "v1",
        weights_sha256: SHA_B,
        device: "cuda",
        settings: { threshold: 0.5 },
        started_at: "2026-10-12T09:00:00Z",
        finished_at: "2026-10-12T09:01:00Z",
        observation_count: 2,
        unavailable_fields: [],
      },
    ],
    observations,
    photo_locations: [
      {
        asset_id: "a1",
        filename: "confirmed.jpg",
        sha256: SHA_A,
        thumbnail_available: false,
        state: "confirmed",
        confirmed: {
          latitude: 28.6,
          longitude: 77.2,
          crs_id: "EPSG:4326",
          source: "exif",
          source_label: "EXIF GPS position, confirmed by a person",
          confirmed_by: "Asha",
          confirmed_at: "2026-10-13T00:00:00Z",
          version_no: 2,
          reason: "Checked on site",
        },
        history: [step("candidate", null, null, true, 1), step("confirmed", 28.6, 77.2, false, 2)],
        observation_count: 0,
      },
      {
        asset_id: "a2",
        filename: "candidate.jpg",
        sha256: SHA_B,
        thumbnail_available: false,
        state: "unconfirmed_candidate",
        confirmed: null,
        history: [step("candidate", null, null, true, 1)],
        observation_count: 0,
      },
      {
        asset_id: "a3",
        filename: "plain.jpg",
        sha256: SHA_B,
        thumbnail_available: false,
        state: "none",
        confirmed: null,
        history: [],
        observation_count: 0,
      },
    ],
    conflicts: [],
    annotations: NO_ANNOTATIONS,
    limitations: [
      "Model results require human review.",
      "A camera GPS position is not the precise location of an object in the photograph.",
    ],
    ...over,
  } as Report;
}

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

function first<T>(items: T[]): T {
  const item = items[0];
  if (item === undefined) throw new Error("fixture list is empty");
  return item;
}

const pwned = () => (window as unknown as { __pwned?: number }).__pwned;

beforeEach(() => {
  vi.clearAllMocks();
  delete (window as unknown as { __pwned?: number }).__pwned;
});

describe("FindingsReportPage", () => {
  it("shows the identity, summary counts, counting rules, limitations, checksums and run provenance", async () => {
    vi.mocked(client.getFindingsReport).mockResolvedValue(report());
    inProject(<FindingsReportPage />, "reports/findings");
    expect(await screen.findByRole("heading", { level: 1, name: "Survey" })).toBeInTheDocument();
    expect(client.getFindingsReport).toHaveBeenCalledWith("p1");
    expect(screen.getByText(/Report generated/)).toBeInTheDocument();
    expect(screen.getByTestId("count-Reviewed")).toHaveTextContent("1");
    expect(screen.getByTestId("count-Unreviewed")).toHaveTextContent("1");
    expect(screen.getByTestId("count-Photos: confirmed location")).toHaveTextContent("1");
    expect(screen.getByTestId("count-Photos: unconfirmed GPS candidate")).toHaveTextContent("1");
    expect(screen.getByText(/Reviewed: the current status is verified/)).toBeInTheDocument();
    const limitations = screen.getByRole("region", { name: "3. Limitations" });
    expect(within(limitations).getByText(/not the precise location of an object/)).toBeInTheDocument();
    expect(screen.getAllByText(SHA_A, { selector: "code" }).length).toBeGreaterThan(0);
    expect(screen.getAllByText(SHA_B, { selector: "code" }).length).toBeGreaterThan(0);
    expect(within(screen.getByRole("region", { name: "5. Methods and models" })).getByText("detector v1")).toBeInTheDocument();
    expect(screen.getByText(/threshold/)).toBeInTheDocument();
  });

  it("separates reviewed from unreviewed findings, shows the raw score as uncalibrated, and the note with its author", async () => {
    vi.mocked(client.getFindingsReport).mockResolvedValue(report());
    inProject(<FindingsReportPage />, "reports/findings");
    expect(await screen.findByText("Reviewed findings (1)")).toBeInTheDocument();
    expect(screen.getByText("Unreviewed findings (1)")).toBeInTheDocument();
    const cards = screen.getAllByTestId("finding") as [HTMLElement, HTMLElement];
    expect(cards).toHaveLength(2);
    expect(within(cards[0]).getByText("Verified")).toBeInTheDocument();
    expect(within(cards[0]).getByText("Matches the site visit")).toBeInTheDocument();
    expect(within(cards[0]).getByText(/Asha \(reviewer\)/)).toBeInTheDocument();
    expect(within(cards[0]).getByText(/not calibrated: the model's own value, not a probability/)).toBeInTheDocument();
    expect(within(cards[0]).getByText(/box x 10–50, y 20–60 px of a 100×80 px image/)).toBeInTheDocument();
    expect(within(cards[1]).getByText("Needs review")).toBeInTheDocument();
    expect(within(cards[1]).getByText("No reviewer notes are stored for this finding.")).toBeInTheDocument();
  });

  it("draws an evidence thumbnail only for an asset that has one", async () => {
    vi.mocked(client.getFindingsReport).mockResolvedValue(report());
    const { unmount } = inProject(<FindingsReportPage />, "reports/findings");
    const images = await screen.findAllByAltText("Thumbnail of street.jpg");
    expect(images[0]).toHaveAttribute("src", "/api/v1/assets/a1/preview?variant=thumb");
    unmount();
    const without = report();
    first(without.assets).thumbnail_available = false;
    vi.mocked(client.getFindingsReport).mockResolvedValue(without);
    inProject(<FindingsReportPage />, "reports/findings");
    await screen.findByRole("heading", { level: 1, name: "Survey" });
    expect(screen.queryByAltText("Thumbnail of street.jpg")).not.toBeInTheDocument();
  });

  it("labels missing provenance, score, region and model as unavailable instead of filling them in", async () => {
    const data = report();
    data.runs[0] = { ...first(data.runs), weights_sha256: null, device: null, unavailable_fields: ["weights_sha256", "device"] };
    data.observations[1] = finding("o2", "needs_review", {
      model: null,
      score_value: null,
      evidence: [
        {
          asset_id: "a1",
          asset_filename: "street.jpg",
          region: null,
          frame_index: null,
          timestamp_ms: null,
          source_excerpt_or_metadata_key: null,
        },
      ],
      unavailable_fields: ["model", "score", "evidence_region"],
    });
    vi.mocked(client.getFindingsReport).mockResolvedValue(data);
    inProject(<FindingsReportPage />, "reports/findings");
    await screen.findByRole("heading", { level: 1, name: "Survey" });
    const methods = screen.getByRole("region", { name: "5. Methods and models" });
    expect(within(methods).getAllByText("Unavailable (not stored)")).toHaveLength(2);
    const unreviewed = screen.getAllByTestId("finding")[1] as HTMLElement;
    expect(within(unreviewed).getByText("score unavailable (not stored)")).toBeInTheDocument();
    expect(within(unreviewed).getByText("model unavailable (not stored)")).toBeInTheDocument();
    expect(within(unreviewed).getByText("image region unavailable (not stored)")).toBeInTheDocument();
    expect(within(unreviewed).queryByText(/0\.91/)).not.toBeInTheDocument();
  });

  it("shows coordinates only for a confirmed location and never for an unconfirmed candidate", async () => {
    vi.mocked(client.getFindingsReport).mockResolvedValue(report());
    inProject(<FindingsReportPage />, "reports/findings");
    await screen.findByRole("heading", { level: 1, name: "Survey" });
    const [confirmed, candidate, plain] = screen.getAllByTestId("photo-location") as [HTMLElement, HTMLElement, HTMLElement];
    expect(within(confirmed).getByText("Confirmed location")).toBeInTheDocument();
    expect(within(confirmed).getByText("28.6, 77.2", { selector: "td" })).toBeInTheDocument();
    expect(within(confirmed).getByText(/confirmed by Asha/)).toBeInTheDocument();
    expect(within(confirmed).getByText(/not the position of anything shown in it/)).toBeInTheDocument();
    expect(within(confirmed).getByText("not shown (unconfirmed)")).toBeInTheDocument();
    expect(within(candidate).getByText("Unconfirmed GPS candidate")).toBeInTheDocument();
    expect(within(candidate).getByText(/not treated as a location/)).toBeInTheDocument();
    expect(candidate.textContent).not.toMatch(/\d+\.\d+/);
    expect(within(plain).getByText("No location")).toBeInTheDocument();
  });

  it("shows conflicts with their stored status", async () => {
    const data = report();
    data.conflicts = [
      {
        id: "c1",
        attribute: "category",
        status: "open",
        rule: "pixel_overlap_iou_0.5_v1",
        details: {},
        created_by_run_id: "r1",
        created_at: "2026-10-12T09:02:00Z",
        resolved_at: null,
        resolution_note: null,
        members: [
          {
            observation_id: "o1",
            asset_id: "a1",
            asset_filename: "street.jpg",
            value: { category: "coco:car" },
            current_status: "disputed",
            current_category: "coco:car",
          },
          {
            observation_id: "o2",
            asset_id: "a1",
            asset_filename: "street.jpg",
            value: { category: "coco:truck" },
            current_status: "disputed",
            current_category: "coco:truck",
          },
        ],
      },
    ] as Report["conflicts"];
    vi.mocked(client.getFindingsReport).mockResolvedValue(data);
    inProject(<FindingsReportPage />, "reports/findings");
    const conflict = await screen.findByTestId("conflict");
    expect(within(conflict).getByText("Open")).toBeInTheDocument();
    expect(within(conflict).getByText(/coco:truck — currently Disputed/)).toBeInTheDocument();
  });

  it("says plainly what an empty project has", async () => {
    const empty = report({ assets: [], runs: [], observations: [], photo_locations: [], conflicts: [] });
    empty.summary = {
      ...empty.summary,
      assets_total: 0,
      runs_total: 0,
      observations_total: 0,
      observations_reviewed: 0,
      observations_unreviewed: 0,
      observations_located: 0,
      observations_unlocated: 0,
      observations_with_reviewer_notes: 0,
      scores_not_calibrated: 0,
      photos_total: 0,
      photos_confirmed_location: 0,
      photos_unconfirmed_candidate: 0,
      photos_without_location: 0,
    };
    vi.mocked(client.getFindingsReport).mockResolvedValue(empty);
    inProject(<FindingsReportPage />, "reports/findings");
    expect(await screen.findByText("No files have been added to this project.")).toBeInTheDocument();
    expect(screen.getByText(/No analysis has been run in this project/)).toBeInTheDocument();
    expect(screen.getByText("There are no findings in this project.")).toBeInTheDocument();
    expect(screen.getByText("This project has no photographs.")).toBeInTheDocument();
    expect(screen.getByText("No conflicts are stored for this project.")).toBeInTheDocument();
    expect(screen.getByText("No map annotations are stored for this project.")).toBeInTheDocument();
    expect(screen.queryByTestId("annotation")).not.toBeInTheDocument();
    expect(screen.getByTestId("count-Annotations: active")).toHaveTextContent("0");
  });

  it("renders untrusted text as text: no script or image element is created from it", async () => {
    const data = report();
    data.project = { ...data.project, name: HOSTILE, description: HOSTILE };
    data.assets[0] = { ...first(data.assets), filename: HOSTILE, uploaded_by: HOSTILE };
    data.observations[0] = finding("o1", "verified", {
      asset_filename: HOSTILE,
      claim_description: HOSTILE,
      current_category: HOSTILE,
      reviewer_notes: [revision(1, HOSTILE, { actor_label: HOSTILE })],
      revisions: [revision(1, HOSTILE)],
    });
    data.photo_locations[0] = { ...first(data.photo_locations), filename: HOSTILE };
    data.annotations = {
      ...NO_ANNOTATIONS,
      active: 1,
      items: [
        annotation("33333333-3333-4333-8333-333333333333", {
          note: HOSTILE,
          created_by: HOSTILE,
          updated_by: HOSTILE,
          versions: [annotationVersion(1, { note: HOSTILE, reason: HOSTILE, actor_label: HOSTILE })],
        }),
      ],
    };
    vi.mocked(client.getFindingsReport).mockResolvedValue(data);
    const { container } = inProject(<FindingsReportPage />, "reports/findings");
    await screen.findByRole("heading", { level: 1 });
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector('img[src="x"]')).toBeNull();
    expect(container.querySelector("[onerror]")).toBeNull();
    expect(pwned()).toBeUndefined();
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(HOSTILE);
    expect(screen.getAllByText(HOSTILE, { exact: false }).length).toBeGreaterThan(3);
    expect(screen.getByTestId("annotation-note")).toHaveTextContent(HOSTILE);
  });

  it("explains an unknown project with the API's code", async () => {
    vi.mocked(client.getFindingsReport).mockRejectedValue(
      new client.ApiError(404, "PROJECT_NOT_FOUND", "Project not found"),
    );
    inProject(<FindingsReportPage />, "reports/findings");
    expect(await screen.findByRole("alert")).toHaveTextContent("Code: PROJECT_NOT_FOUND");
    expect(screen.queryByRole("article")).not.toBeInTheDocument();
  });

  it("regenerates on request and prints through the browser", async () => {
    vi.mocked(client.getFindingsReport).mockResolvedValue(report());
    const print = vi.spyOn(window, "print").mockImplementation(() => undefined);
    inProject(<FindingsReportPage />, "reports/findings");
    await screen.findByRole("heading", { level: 1, name: "Survey" });
    expect(client.getFindingsReport).toHaveBeenCalledTimes(1);
    await userEvent.click(screen.getByRole("button", { name: "Regenerate" }));
    await waitFor(() => expect(client.getFindingsReport).toHaveBeenCalledTimes(2));
    await userEvent.click(screen.getByRole("button", { name: "Print report" }));
    expect(print).toHaveBeenCalledTimes(1);
    print.mockRestore();
  });

  it("claims no PDF file", async () => {
    vi.mocked(client.getFindingsReport).mockResolvedValue(report());
    inProject(<FindingsReportPage />, "reports/findings");
    expect(await screen.findByText(/no PDF is\s+created by the application/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /pdf/i })).not.toBeInTheDocument();
  });
});

describe("map annotations in the report", () => {
  function withAnnotations(over: Partial<Report["annotations"]> = {}): Report {
    const data = report();
    data.annotations = { ...annotationSet(), ...over };
    data.summary = { ...data.summary, annotations_active: data.annotations.active, annotations_deleted: data.annotations.deleted };
    return data;
  }

  it("lists annotations in their own section, apart from the findings, with the counts in the summary", async () => {
    vi.mocked(client.getFindingsReport).mockResolvedValue(withAnnotations());
    inProject(<FindingsReportPage />, "reports/findings");
    const section = await screen.findByRole("region", { name: "9. Map annotations" });
    expect(screen.getByTestId("count-Annotations: active")).toHaveTextContent("2");
    expect(screen.getByTestId("count-Annotations: deleted")).toHaveTextContent("1");
    expect(within(section).getByText("Active annotations (2)")).toBeInTheDocument();
    expect(within(section).getByText("Deleted annotations (1)")).toBeInTheDocument();
    expect(within(section).getByText(/not a finding/)).toBeInTheDocument();
    // not mixed into the findings, the photographs or the counts of findings
    const findings = screen.getByRole("region", { name: "6. Findings" });
    expect(within(findings).queryByText(/fence|gate|field/)).not.toBeInTheDocument();
    expect(screen.getByTestId("count-Findings")).toHaveTextContent("2");
    expect(screen.getAllByTestId("finding")).toHaveLength(2);
    expect(screen.getAllByTestId("annotation")).toHaveLength(3);
  });

  it("shows id, kind, shape, note, author, version and times for an active annotation, and its measurement with the word approximate", async () => {
    vi.mocked(client.getFindingsReport).mockResolvedValue(withAnnotations());
    inProject(<FindingsReportPage />, "reports/findings");
    await screen.findByRole("region", { name: "9. Map annotations" });
    const [point, line] = screen.getAllByTestId("annotation") as [HTMLElement, HTMLElement, HTMLElement];

    expect(point).toHaveAttribute("data-status", "active");
    expect(within(point).getByText("Point")).toBeInTheDocument();
    expect(within(point).getByText("Active")).toBeInTheDocument();
    expect(within(point).getByText("00000000-0000-4000-8000-000000000000", { selector: "code" })).toBeInTheDocument();
    expect(within(point).getByTestId("annotation-note")).toHaveTextContent("gate");
    expect(within(point).getByText("27.000000°, 75.000000°")).toBeInTheDocument();
    expect(within(point).getByText(/Coordinate system EPSG:4326/)).toBeInTheDocument();
    expect(within(point).getAllByText(/Asha on 14 Oct 2026/)).toHaveLength(2); // drawn by, and last changed by
    expect(within(point).getByText("version 1")).toBeInTheDocument();
    expect(within(point).queryByText(/Measurement/)).not.toBeInTheDocument(); // a point has none

    expect(within(line).getByText("Line")).toBeInTheDocument();
    expect(within(line).getByText("version 3")).toBeInTheDocument();
    expect(within(line).getByText(/Ravi on 15 Oct 2026/)).toBeInTheDocument(); // last changed by (drawn by Asha)
    expect(within(line).getByText(/Asha on 14 Oct 2026/)).toBeInTheDocument();
    expect(within(line).getByText("Length (approximate): 2.50 km")).toBeInTheDocument();
    expect(within(line).getByText(/not a survey measurement/)).toBeInTheDocument();
    expect(within(line).getByText(/LineString, 3 positions/)).toBeInTheDocument();
  });

  it("marks a deleted annotation as deleted in words, shows who deleted it, and gives it no measurement", async () => {
    vi.mocked(client.getFindingsReport).mockResolvedValue(withAnnotations());
    inProject(<FindingsReportPage />, "reports/findings");
    await screen.findByRole("region", { name: "9. Map annotations" });
    const deleted = screen.getAllByTestId("annotation")[2] as HTMLElement;
    expect(deleted).toHaveAttribute("data-status", "deleted");
    expect(within(deleted).getByText("Deleted: not an active mark")).toBeInTheDocument();
    expect(within(deleted).queryByText("Active")).not.toBeInTheDocument();
    expect(within(deleted).getByText("Last shape (before deletion)")).toBeInTheDocument();
    const deletedBy = within(deleted).getByText("Deleted by").nextElementSibling as HTMLElement;
    expect(deletedBy).toHaveTextContent(/Ravi on 16 Oct 2026/);
    expect(within(deleted).queryByText(/Area \(approximate\)/)).not.toBeInTheDocument();
    // and it sits under the deleted heading, not the active one
    const headings = screen.getAllByRole("heading", { level: 3 }).map((h) => h.textContent);
    expect(headings.indexOf("Deleted annotations (1)")).toBeGreaterThan(headings.indexOf("Active annotations (2)"));
    const lists = screen.getAllByTestId("annotation").map((a) => a.getAttribute("data-status"));
    expect(lists).toEqual(["active", "active", "deleted"]);
  });

  it("shows the whole version history, what each version changed, and the reason", async () => {
    vi.mocked(client.getFindingsReport).mockResolvedValue(withAnnotations());
    inProject(<FindingsReportPage />, "reports/findings");
    await screen.findByRole("region", { name: "9. Map annotations" });
    const [, line, deleted] = screen.getAllByTestId("annotation") as [HTMLElement, HTMLElement, HTMLElement];
    const lineRows = within(within(line).getByRole("table", { name: "Version history of this annotation" })).getAllByRole("row").slice(1);
    expect(lineRows.map((r) => within(r).getAllByRole("cell")[1]?.textContent)).toEqual([
      "created",
      "changed (note)",
      "changed (shape)",
    ]);
    expect(within(lineRows[1]!).getByText("clarified")).toBeInTheDocument();
    const deletedRows = within(within(deleted).getByRole("table", { name: "Version history of this annotation" })).getAllByRole("row").slice(1);
    expect(deletedRows.map((r) => within(r).getAllByRole("cell")[1]?.textContent)).toEqual(["created", "deleted"]);
    expect(within(deletedRows[1]!).getByText("drawn in error")).toBeInTheDocument();
  });

  it("lists only the first positions of a long shape and says where the rest is", async () => {
    const data = withAnnotations();
    const coordinates = Array.from({ length: 30 }, (_, i) => [75 + i * 0.001, 27 + (i % 2) * 0.001]);
    data.annotations.items = [annotation("44444444-4444-4444-8444-444444444444", { kind: "line", geometry: { type: "LineString", coordinates } })];
    vi.mocked(client.getFindingsReport).mockResolvedValue(data);
    inProject(<FindingsReportPage />, "reports/findings");
    const item = await screen.findByTestId("annotation");
    expect(within(item).getByText(/LineString, 30 positions/)).toBeInTheDocument();
    expect(within(item).getByText(/and 18 more\. All positions are in the JSON and GeoJSON exports\./)).toBeInTheDocument();
  });

  it("says plainly when a project has annotations that were all deleted", async () => {
    const data = withAnnotations();
    data.annotations = { ...data.annotations, active: 0, deleted: 1, items: [annotationSet().items[2] as Annotation] };
    vi.mocked(client.getFindingsReport).mockResolvedValue(data);
    inProject(<FindingsReportPage />, "reports/findings");
    expect(await screen.findByText("There are no active annotations.")).toBeInTheDocument();
    expect(screen.getByText("Deleted annotations (1)")).toBeInTheDocument();
  });

  it("says annotations cannot be listed, rather than showing zero, when the database has no annotation table", async () => {
    const data = report();
    data.annotations = {
      available: false,
      note: "This database has not been updated for map annotations, so none can be listed. That does not mean the project has none.",
      active: null,
      deleted: null,
      items: [],
    };
    data.summary = { ...data.summary, annotations_active: null, annotations_deleted: null };
    vi.mocked(client.getFindingsReport).mockResolvedValue(data);
    inProject(<FindingsReportPage />, "reports/findings");
    const section = await screen.findByRole("region", { name: "9. Map annotations" });
    expect(within(section).getByText(/That does not mean the project has none/)).toBeInTheDocument();
    expect(within(section).getByText("Map annotations cannot be listed from this database.")).toBeInTheDocument();
    expect(within(section).queryByText("No map annotations are stored for this project.")).not.toBeInTheDocument();
    expect(screen.getByTestId("count-Annotations: active")).toHaveTextContent("Unavailable (not stored)");
    expect(screen.getByTestId("count-Annotations: deleted")).not.toHaveTextContent(/^0$/);
  });

  it("shows an annotation with an empty note as having no note", async () => {
    const data = withAnnotations();
    data.annotations.items = [annotation("55555555-5555-4555-8555-555555555555", { note: "", versions: [annotationVersion(1, { note: "" })] })];
    vi.mocked(client.getFindingsReport).mockResolvedValue(data);
    inProject(<FindingsReportPage />, "reports/findings");
    expect(await screen.findByTestId("annotation-note")).toHaveTextContent("No note");
  });
});

describe("ReportView", () => {
  it("is a pure view of the report it is given", () => {
    renderPage(<ReportView report={report()} />);
    expect(screen.getByRole("article", { name: "Findings report" })).toBeInTheDocument();
  });
});

describe("ReportsPage entry point", () => {
  it("links to the findings report and still offers no PDF button", async () => {
    vi.mocked(client.getProjectSummary).mockResolvedValue({
      project_id: "p1",
      assets_by_modality: { ground_image: 1, overhead_raster: 0, video: 0 },
      assets_total: 1,
      jobs_by_status: {},
      observations_by_review_status: {},
      observations_total: 0,
      last_activity_at: null,
    } as unknown as Awaited<ReturnType<typeof client.getProjectSummary>>);
    inProject(<ReportsPage />, "reports");
    const link = await screen.findByRole("link", { name: /Open findings report/ });
    expect(link).toHaveAttribute("href", "/projects/p1/reports/findings");
    expect(screen.queryByRole("button", { name: /pdf/i })).not.toBeInTheDocument();
    expect(screen.getByText("PDF report")).toBeInTheDocument();
  });
});
