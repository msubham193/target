import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { vi } from "vitest";

import * as client from "../api/client";
import type { Drawing } from "../lib/annotations";
import { renderPage } from "../test/render";
import { AnnotationPanel } from "./AnnotationPanel";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    listAnnotations: vi.fn(),
    createAnnotation: vi.fn(),
    updateAnnotation: vi.fn(),
    deleteAnnotation: vi.fn(),
    getAnnotationHistory: vi.fn(),
  };
});

const listAnnotations = vi.mocked(client.listAnnotations);
const createAnnotation = vi.mocked(client.createAnnotation);
const updateAnnotation = vi.mocked(client.updateAnnotation);
const deleteAnnotation = vi.mocked(client.deleteAnnotation);
const getAnnotationHistory = vi.mocked(client.getAnnotationHistory);

const LINE = { type: "LineString", coordinates: [[75, 27], [75.01, 27]] };
const SQUARE = { type: "Polygon", coordinates: [[[75, 27], [75.001, 27], [75.001, 27.001], [75, 27.001], [75, 27]]] };
const METHOD = "geodesic on the WGS 84 ellipsoid (PROJ geodesic, Karney)";

function annotation(over: Record<string, unknown> = {}): client.Annotation {
  return {
    feature_type: "user_annotation",
    annotation_id: "n1",
    project_id: "p1",
    kind: "line",
    status: "active",
    version_no: 1,
    geometry: LINE,
    crs_id: "EPSG:4326",
    note: "fence",
    measurement: { length_m: 991.9, area_m2: null, perimeter_m: null, method: METHOD, limitations: ["Computed from the vertices exactly as drawn, so it is only as accurate as the drawing: not a survey measurement."] },
    created_by: "Asha",
    created_at: "2026-10-28T10:00:00Z",
    updated_by: "Asha",
    updated_at: "2026-10-28T10:00:00Z",
    deleted_by: null,
    deleted_at: null,
    ...over,
  } as unknown as client.Annotation;
}

function listing(items: client.Annotation[], deleted = 0): client.Annotations {
  return { items, active: items.filter((i) => i.status === "active").length, deleted, include_deleted: false, note: "An annotation is a mark a person drew." } as client.Annotations;
}

const zoomed: unknown[] = [];

// Stands in for the map page: it owns the drawing, and "click the map" adds a position as the map would.
function Harness({ initial = null }: { initial?: Drawing | null }) {
  const [drawing, setDrawing] = useState<Drawing | null>(initial);
  const [selected, setSelected] = useState<string | null>(null);
  const [show, setShow] = useState(true);
  return (
    <>
      <AnnotationPanel
        projectId="p1"
        drawing={drawing}
        onDrawing={setDrawing}
        selectedId={selected}
        onSelect={setSelected}
        showOnMap={show}
        onShowOnMap={setShow}
        onZoom={(bounds) => zoomed.push(bounds)}
      />
      <button
        type="button"
        onClick={() =>
          setDrawing((d) =>
            d && d.active
              ? { ...d, points: d.kind === "point" ? [[75, 27]] : [...d.points, [75 + 0.001 * d.points.length, 27 + (d.points.length % 2) * 0.001]] }
              : d,
          )
        }
      >
        click the map
      </button>
      <output data-testid="selected">{selected ?? ""}</output>
      <output data-testid="shown">{String(show)}</output>
      <output data-testid="drawing">{drawing ? JSON.stringify({ kind: drawing.kind, n: drawing.points.length, active: drawing.active }) : ""}</output>
    </>
  );
}

function render(initial?: Drawing | null) {
  return renderPage(<Harness initial={initial} />);
}

beforeEach(() => {
  vi.clearAllMocks();
  zoomed.length = 0;
  listAnnotations.mockResolvedValue(listing([]));
});

describe("the annotation panel before anything is drawn", () => {
  it("says what annotations are and are not, and that the author is attribution only", async () => {
    render();
    expect(await screen.findByText(/No annotations yet/)).toBeInTheDocument();
    expect(screen.getByText(/Your own notes on the map/)).toBeInTheDocument();
    expect(screen.getByText(/separate from the findings found in photos/)).toBeInTheDocument();
    expect(screen.getByText(/Profiles are not a sign-in/)).toBeInTheDocument();
    for (const name of ["Add point", "Add line", "Add polygon"]) expect(screen.getByRole("button", { name })).toBeEnabled();
    expect(listAnnotations).toHaveBeenCalledWith("p1", false); // deleted ones are not asked for by default
  });

  it("says plainly when the database has not been updated, and offers no way to draw", async () => {
    listAnnotations.mockRejectedValue(new client.ApiError(503, "ANNOTATIONS_NOT_MIGRATED", "Annotations are not available yet", "The database has not been updated for map annotations."));
    render();
    expect(await screen.findByText("Annotations are not available yet")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Add line" })).not.toBeInTheDocument();
  });

  it("reports any other failure with its code instead of an empty list", async () => {
    listAnnotations.mockRejectedValue(new client.ApiError(500, "INTERNAL_ERROR", "Something failed"));
    render();
    expect(await screen.findByText("Annotations could not be loaded")).toBeInTheDocument();
    expect(screen.getByText(/INTERNAL_ERROR/)).toBeInTheDocument();
    expect(screen.queryByText(/No annotations yet/)).not.toBeInTheDocument();
  });
});

describe("drawing and saving", () => {
  it("cannot be saved until the shape is complete, and sends a closed polygon in GeoJSON with the note", async () => {
    createAnnotation.mockResolvedValue(annotation({ annotation_id: "n2", kind: "polygon", geometry: SQUARE, note: "field", measurement: { length_m: null, area_m2: 10_980, perimeter_m: 420, method: METHOD, limitations: [] } }));
    render();
    await userEvent.click(await screen.findByRole("button", { name: "Add polygon" }));
    const form = screen.getByRole("form", { name: "New annotation" });
    const save = within(form).getByRole("button", { name: "Save annotation" });
    expect(save).toBeDisabled();
    expect(within(form).getByText(/first of at least 3 positions/)).toBeInTheDocument();
    // the tool buttons are locked while a shape is open
    expect(screen.getByRole("button", { name: "Add line" })).toBeDisabled();

    await userEvent.click(screen.getByRole("button", { name: "click the map" }));
    await userEvent.click(screen.getByRole("button", { name: "click the map" }));
    expect(save).toBeDisabled();
    expect(within(form).getByText("2 positions placed; add 1 more.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "click the map" }));
    expect(save).toBeEnabled();
    await userEvent.type(within(form).getByLabelText("Note"), "field");
    await userEvent.click(save);

    await waitFor(() => expect(createAnnotation).toHaveBeenCalledTimes(1));
    const [projectId, body] = createAnnotation.mock.calls[0]!;
    expect(projectId).toBe("p1");
    expect(body.kind).toBe("polygon");
    expect(body.crs_id).toBe("EPSG:4326");
    expect(body.note).toBe("field");
    const ring = (body.geometry as { coordinates: number[][][] }).coordinates[0]!;
    expect(ring).toHaveLength(4);
    expect(ring[0]).toEqual(ring[3]); // closed
    expect(await screen.findByText("The annotation was saved.")).toBeInTheDocument();
    expect(screen.getByTestId("drawing")).toHaveTextContent(""); // the draft is cleared
    expect(screen.getByTestId("selected")).toHaveTextContent("n2");
  });

  it("places a point by typed coordinates as an alternative to clicking, and refuses values that are not coordinates", async () => {
    createAnnotation.mockResolvedValue(annotation({ kind: "point", geometry: { type: "Point", coordinates: [75.5, 27.25] }, measurement: null }));
    render();
    await userEvent.click(await screen.findByRole("button", { name: "Add point" }));
    await userEvent.type(screen.getByLabelText("Latitude"), "95");
    await userEvent.type(screen.getByLabelText("Longitude"), "75.5");
    await userEvent.click(screen.getByRole("button", { name: "Place point" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/within ±90°/);
    expect(screen.getByRole("button", { name: "Save annotation" })).toBeDisabled();

    await userEvent.clear(screen.getByLabelText("Latitude"));
    await userEvent.type(screen.getByLabelText("Latitude"), "27.25");
    await userEvent.click(screen.getByRole("button", { name: "Place point" }));
    expect(screen.getByText("27.250000°, 75.500000°")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Save annotation" }));
    await waitFor(() => expect(createAnnotation).toHaveBeenCalled());
    expect(createAnnotation.mock.calls[0]![1].geometry).toEqual({ type: "Point", coordinates: [75.5, 27.25] });
  });

  it("can undo and clear positions, and cancelling sends nothing", async () => {
    render();
    await userEvent.click(await screen.findByRole("button", { name: "Add line" }));
    await userEvent.click(screen.getByRole("button", { name: "click the map" }));
    await userEvent.click(screen.getByRole("button", { name: "click the map" }));
    expect(screen.getByTestId("drawing")).toHaveTextContent('"n":2');
    await userEvent.click(screen.getByRole("button", { name: "Undo last point" }));
    expect(screen.getByTestId("drawing")).toHaveTextContent('"n":1');
    await userEvent.click(screen.getByRole("button", { name: "Clear points" }));
    expect(screen.getByTestId("drawing")).toHaveTextContent('"n":0');
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("form", { name: "New annotation" })).not.toBeInTheDocument();
    expect(createAnnotation).not.toHaveBeenCalled();
  });

  it("keeps the drawing and the note and shows the refusal when the server refuses the shape", async () => {
    createAnnotation.mockRejectedValue(new client.ApiError(422, "ANNOTATION_GEOMETRY_INVALID", "The shape is not valid", "Self-intersection. Nothing was saved."));
    render();
    await userEvent.click(await screen.findByRole("button", { name: "Add line" }));
    await userEvent.click(screen.getByRole("button", { name: "click the map" }));
    await userEvent.click(screen.getByRole("button", { name: "click the map" }));
    await userEvent.type(screen.getByLabelText("Note"), "keep me");
    await userEvent.click(screen.getByRole("button", { name: "Save annotation" }));
    expect(await screen.findByText("The annotation was not saved")).toBeInTheDocument();
    expect(screen.getByText(/ANNOTATION_GEOMETRY_INVALID/)).toBeInTheDocument();
    expect(screen.getByLabelText("Note")).toHaveValue("keep me");
    expect(screen.getByTestId("drawing")).toHaveTextContent('"n":2');
  });
});

describe("listing annotations", () => {
  it("shows the note as plain text, never as markup", async () => {
    listAnnotations.mockResolvedValue(listing([annotation({ note: '<img src=x onerror="window.__hacked=1"> <b>bold</b>' })]));
    const { container } = render();
    expect(await screen.findByText(/<img src=x onerror=/)).toBeInTheDocument();
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("b")).toBeNull();
  });

  it("gives a line its length and a polygon its area and outline, each with a unit and a stated method and limits", async () => {
    listAnnotations.mockResolvedValue(
      listing([
        annotation(),
        annotation({ annotation_id: "n2", kind: "polygon", geometry: SQUARE, note: "field", measurement: { length_m: null, area_m2: 10_980, perimeter_m: 420.4, method: METHOD, limitations: ["A polygon's area is the area of its outline only; it says nothing about what is inside."] } }),
        annotation({ annotation_id: "n3", kind: "point", geometry: { type: "Point", coordinates: [75, 27] }, note: "gate", measurement: null }),
      ]),
    );
    render();
    const list = await screen.findByRole("list", { name: "Annotations in this project" });
    // the limitations are list items too, so take the annotations as the list's own children
    const [line, polygon, point] = Array.from(list.children) as [HTMLElement, HTMLElement, HTMLElement];
    expect(line).toHaveTextContent("Length (approximate): 992 m");
    expect(polygon).toHaveTextContent("Area (approximate): 1.10 ha (10,980 m²)");
    expect(polygon).toHaveTextContent("Outline length (approximate): 420 m");
    expect(line).toHaveTextContent(`Method: ${METHOD}`);
    expect(line).toHaveTextContent("not a survey measurement");
    expect(polygon).toHaveTextContent("says nothing about what is inside");
    expect(point).not.toHaveTextContent(/Length|Area/); // a point has no measurement
    expect(line).toHaveTextContent("Drawn by Asha");
  });

  it("selects an annotation, zooms to it, and can hide annotations from the map", async () => {
    listAnnotations.mockResolvedValue(listing([annotation()]));
    render();
    await userEvent.click(await screen.findByRole("button", { name: "Show line on the map" }));
    expect(screen.getByTestId("selected")).toHaveTextContent("n1");
    expect(zoomed).toEqual([[75, 27, 75.01, 27]]);
    expect(screen.getByRole("list", { name: "Annotations in this project" }).children[0]).toHaveAttribute("aria-current", "true");
    await userEvent.click(screen.getByLabelText("Show on the map"));
    expect(screen.getByTestId("shown")).toHaveTextContent("false");
  });

  it("lists deleted annotations only when asked, marked as deleted and without edit or delete buttons", async () => {
    listAnnotations.mockImplementation(async (_project, includeDeleted) =>
      includeDeleted
        ? { ...listing([annotation(), annotation({ annotation_id: "n9", status: "deleted", version_no: 2, note: "old fence", measurement: null, deleted_by: "Asha", deleted_at: "2026-10-29T09:00:00Z" })], 1), include_deleted: true }
        : listing([annotation()], 1),
    );
    render();
    await screen.findByText("fence");
    expect(screen.queryByText("old fence")).not.toBeInTheDocument();
    expect(screen.getByText("1 active · 1 deleted")).toBeInTheDocument();

    await userEvent.click(screen.getByLabelText("List deleted annotations"));
    const gone = (await screen.findByText("old fence")).closest("li")!;
    expect(within(gone).getByText("Deleted")).toBeInTheDocument();
    expect(gone).toHaveTextContent("Deleted by Asha");
    expect(within(gone).queryByRole("button", { name: "Edit line" })).not.toBeInTheDocument();
    expect(within(gone).queryByRole("button", { name: "Delete line" })).not.toBeInTheDocument();
    expect(within(gone).getByRole("button", { name: "Show line on the map" })).toBeDisabled();
    expect(listAnnotations).toHaveBeenCalledWith("p1", true);
  });
});

describe("editing", () => {
  it("changes only the note when the shape is left alone, on top of the version that was opened", async () => {
    listAnnotations.mockResolvedValue(listing([annotation({ version_no: 3 })]));
    updateAnnotation.mockResolvedValue(annotation({ version_no: 4, note: "fence, east side" }));
    render();
    await userEvent.click(await screen.findByRole("button", { name: "Edit line" }));
    const form = screen.getByRole("form", { name: "Edit annotation" });
    expect(within(form).getByLabelText("Note")).toHaveValue("fence");
    expect(within(form).getByText("The shape is unchanged. Redraw it to move it.")).toBeInTheDocument();
    // clicking the map does not disturb a shape that has not been reopened for drawing
    await userEvent.click(screen.getByRole("button", { name: "click the map" }));
    expect(screen.getByTestId("drawing")).toHaveTextContent('"n":2');
    await userEvent.type(within(form).getByLabelText("Note"), ", east side");
    await userEvent.click(within(form).getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(updateAnnotation).toHaveBeenCalledTimes(1));
    expect(updateAnnotation).toHaveBeenCalledWith("p1", "n1", { expected_version: 3, crs_id: "EPSG:4326", note: "fence, east side" });
    expect(await screen.findByText("The annotation was saved as a new version.")).toBeInTheDocument();
  });

  it("sends the new shape only after it was redrawn", async () => {
    listAnnotations.mockResolvedValue(listing([annotation()]));
    updateAnnotation.mockResolvedValue(annotation({ version_no: 2 }));
    render();
    await userEvent.click(await screen.findByRole("button", { name: "Edit line" }));
    await userEvent.click(screen.getByRole("button", { name: "Redraw shape" }));
    expect(screen.getByTestId("drawing")).toHaveTextContent('"n":0');
    const save = screen.getByRole("button", { name: "Save changes" });
    expect(save).toBeDisabled(); // a line needs two positions again
    await userEvent.click(screen.getByRole("button", { name: "click the map" }));
    await userEvent.click(screen.getByRole("button", { name: "click the map" }));
    await userEvent.click(save);
    await waitFor(() => expect(updateAnnotation).toHaveBeenCalled());
    const body = updateAnnotation.mock.calls[0]![2];
    expect(body.expected_version).toBe(1);
    expect((body.geometry as { type: string }).type).toBe("LineString");
  });

  it("explains a conflicting change instead of overwriting it, and keeps the person's edit", async () => {
    listAnnotations.mockResolvedValue(listing([annotation()]));
    updateAnnotation.mockRejectedValue(new client.ApiError(409, "ANNOTATION_VERSION_CONFLICT", "The annotation changed since you opened it", "You edited version 1; the current version is 2. Nothing was saved."));
    render();
    await userEvent.click(await screen.findByRole("button", { name: "Edit line" }));
    await userEvent.type(screen.getByLabelText("Note"), " mine");
    await userEvent.click(screen.getByRole("button", { name: "Save changes" }));
    expect(await screen.findByText("The annotation was not saved")).toBeInTheDocument();
    expect(screen.getByText(/the current version is 2/)).toBeInTheDocument();
    expect(screen.getByText(/ANNOTATION_VERSION_CONFLICT/)).toBeInTheDocument();
    expect(screen.getByLabelText("Note")).toHaveValue("fence mine");
  });
});

describe("deleting", () => {
  it("asks first, and a soft delete sends the version that was seen", async () => {
    listAnnotations.mockResolvedValue(listing([annotation({ version_no: 2 })]));
    deleteAnnotation.mockResolvedValue(annotation({ status: "deleted", version_no: 3, measurement: null }));
    render();
    await userEvent.click(await screen.findByRole("button", { name: "Delete line" }));
    const confirm = screen.getByRole("alertdialog", { name: "Confirm delete" });
    expect(confirm).toHaveTextContent("hidden from the map. Its history stays");
    expect(deleteAnnotation).not.toHaveBeenCalled(); // nothing happens until it is confirmed

    await userEvent.click(within(confirm).getByRole("button", { name: "Keep it" }));
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(deleteAnnotation).not.toHaveBeenCalled();

    await userEvent.click(screen.getByRole("button", { name: "Delete line" }));
    await userEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Delete annotation" }));
    await waitFor(() => expect(deleteAnnotation).toHaveBeenCalledWith("p1", "n1", 2));
    expect(await screen.findByText(/The annotation was deleted\. It is hidden from the map; its history is kept\./)).toBeInTheDocument();
  });

  it("reports a refused delete and leaves the annotation listed", async () => {
    listAnnotations.mockResolvedValue(listing([annotation()]));
    deleteAnnotation.mockRejectedValue(new client.ApiError(409, "ANNOTATION_VERSION_CONFLICT", "The annotation changed since you opened it"));
    render();
    await userEvent.click(await screen.findByRole("button", { name: "Delete line" }));
    await userEvent.click(screen.getByRole("button", { name: "Delete annotation" }));
    expect(await screen.findByText("The annotation was not deleted")).toBeInTheDocument();
    expect(screen.getByText("fence")).toBeInTheDocument();
  });
});

describe("history", () => {
  it("shows every version with who made it and why, and the note as text", async () => {
    listAnnotations.mockResolvedValue(listing([annotation({ version_no: 2 })]));
    getAnnotationHistory.mockResolvedValue({
      annotation_id: "n1",
      project_id: "p1",
      kind: "line",
      note: "n",
      versions: [
        { version_no: 1, action: "create", status: "active", geometry: LINE, note: "first", reason: null, actor_label: "Asha", created_at: "2026-10-28T10:00:00Z" },
        { version_no: 2, action: "update", status: "active", geometry: LINE, note: "<b>second</b>", reason: "clarified", actor_label: "Ben", created_at: "2026-10-28T11:00:00Z" },
      ],
    } as client.AnnotationHistory);
    const { container } = render();
    await userEvent.click(await screen.findByRole("button", { name: "Show history of line" }));
    const history = await screen.findByRole("region", { name: "Annotation history" });
    expect(getAnnotationHistory).toHaveBeenCalledWith("p1", "n1");
    expect(history).toHaveTextContent("Version 1 · drawn");
    expect(history).toHaveTextContent("Version 2 · changed");
    expect(history).toHaveTextContent("by Ben");
    expect(history).toHaveTextContent("reason: clarified");
    expect(history).toHaveTextContent("<b>second</b>");
    expect(container.querySelector("section b")).toBeNull();
  });
});
