import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Outlet, Route, Routes } from "react-router-dom";
import { vi } from "vitest";

import * as client from "../api/client";
import { checkFile, formatBytes } from "../lib/ingestion";
import { renderPage } from "../test/render";
import { AssetsPage } from "./AssetsPage";
import { IngestionPage } from "./IngestionPage";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, getIngestionRules: vi.fn(), uploadAsset: vi.fn(), listAssets: vi.fn() };
});

const getIngestionRules = vi.mocked(client.getIngestionRules);
const uploadAsset = vi.mocked(client.uploadAsset);
const listAssets = vi.mocked(client.listAssets);

const MB = 1024 ** 2;
const RULES: client.IngestionRules = {
  rules: [
    { modality: "ground_image", label: "Ground photographs", extensions: [".jpg", ".jpeg", ".png", ".webp"], max_bytes: 50 * MB },
    { modality: "overhead_raster", label: "Satellite / aerial raster (GeoTIFF)", extensions: [".tif", ".tiff"], max_bytes: 1024 * MB },
    { modality: "video", label: "Video", extensions: [".mp4", ".mov", ".mkv"], max_bytes: 1024 * MB },
  ],
  notes: ["A file is checked by its content and by actually reading it, not by its name."],
};

const PROJECT = { id: "p1", name: "Survey", archived_at: null } as client.Project;

function asset(overrides: Partial<client.Asset> = {}): client.Asset {
  return {
    id: "a1",
    project_id: "p1",
    original_filename: "photo.jpg",
    modality: "ground_image",
    detected_format: "JPEG",
    media_type: "image/jpeg",
    size_bytes: 2048,
    sha256: "ab".repeat(32),
    status: "validated",
    duplicate_of_asset_id: null,
    quality_flags: ["NOT_GEOREFERENCED"],
    unknowns: {},
    metadata: {},
    acquisition_time: null,
    uploaded_by: { id: "u1", display_name: "Asha", created_at: "2026-10-12T00:00:00Z" },
    ingested_at: "2026-10-12T00:00:00Z",
    raster: null,
    video: null,
    ...overrides,
  };
}

function renderInProject(page: React.ReactElement, project: client.Project = PROJECT) {
  return renderPage(
    <Routes>
      <Route element={<Outlet context={project} />}>
        <Route path="/" element={page} />
      </Route>
    </Routes>,
  );
}

function file(name: string, size = 10): File {
  const created = new File(["x".repeat(10)], name);
  Object.defineProperty(created, "size", { value: size });
  return created;
}

// `accept` filtering is switched off so the page's own validation is what gets exercised.
const user = () => userEvent.setup({ applyAccept: false });

beforeEach(() => {
  vi.clearAllMocks();
  getIngestionRules.mockResolvedValue(RULES);
});

describe("IngestionPage", () => {
  it("shows a loading state, then the limits the server enforces", async () => {
    renderInProject(<IngestionPage />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading accepted formats");
    expect(await screen.findByText(".jpg, .jpeg, .png, .webp")).toBeInTheDocument();
    expect(screen.getByText("50 MB")).toBeInTheDocument();
    expect(screen.getByText("No files added yet.")).toBeInTheDocument();

    await user().click(screen.getByRole("tab", { name: "Video" }));
    expect(screen.getByText(".mp4, .mov, .mkv")).toBeInTheDocument();
    expect(screen.getByText("1 GB")).toBeInTheDocument();
  });

  it("reports when the accepted formats cannot be loaded", async () => {
    getIngestionRules.mockRejectedValue(new client.ApiError(0, "API_UNREACHABLE", "The local API is not reachable"));
    renderInProject(<IngestionPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Code: API_UNREACHABLE");
  });

  it("uploads a file and shows the stored, validated result", async () => {
    uploadAsset.mockResolvedValue({
      asset: asset(),
      duplicate: false,
      duplicate_of_asset_id: null,
      stored_original_reused: false,
      message: "Stored and validated.",
    });
    renderInProject(<IngestionPage />);
    await user().upload(await screen.findByLabelText("Choose files"), file("photo.jpg"));

    const row = await screen.findByRole("listitem", { name: "photo.jpg" });
    expect(await within(row).findByText("Stored")).toBeInTheDocument();
    expect(within(row).getByText("Stored and validated.")).toBeInTheDocument();
    expect(within(row).getByText(/Flags: Not georeferenced/)).toBeInTheDocument();
    expect(within(row).getByRole("link", { name: "View details" })).toHaveAttribute("href", "/projects/p1/assets/a1");
    expect(uploadAsset).toHaveBeenCalledWith("p1", "ground_image", expect.any(File), expect.any(Function));
  });

  it("shows a duplicate as linked to the first copy, not as a new source", async () => {
    uploadAsset.mockResolvedValue({
      asset: asset({ id: "a2", duplicate_of_asset_id: "a1", quality_flags: ["NOT_GEOREFERENCED", "DUPLICATE_ASSET"] }),
      duplicate: true,
      duplicate_of_asset_id: "a1",
      stored_original_reused: true,
      message: "Identical content is already in this project. This upload was linked to it and flagged as a duplicate.",
    });
    renderInProject(<IngestionPage />);
    await user().upload(await screen.findByLabelText("Choose files"), file("copy.jpg"));

    const row = await screen.findByRole("listitem", { name: "copy.jpg" });
    expect(await within(row).findByText("Duplicate — linked")).toBeInTheDocument();
    expect(within(row).getByText(/flagged as a duplicate/)).toBeInTheDocument();
    expect(within(row).getByRole("link", { name: "View the first copy" })).toHaveAttribute("href", "/projects/p1/assets/a1");
  });

  it("refuses an unsupported format immediately, without uploading", async () => {
    renderInProject(<IngestionPage />);
    await user().upload(await screen.findByLabelText("Choose files"), file("animation.gif"));

    const row = await screen.findByRole("listitem", { name: "animation.gif" });
    expect(within(row).getByText("Not accepted")).toBeInTheDocument();
    expect(within(row).getByRole("alert")).toHaveTextContent("This file type is not supported here");
    expect(within(row).getByRole("alert")).toHaveTextContent("accepts .jpg, .jpeg, .png, .webp");
    expect(within(row).getByRole("alert")).toHaveTextContent("Code: UNSUPPORTED_FILE_TYPE");
    expect(uploadAsset).not.toHaveBeenCalled();
  });

  it("refuses an oversized file immediately, stating the limit", async () => {
    renderInProject(<IngestionPage />);
    await user().upload(await screen.findByLabelText("Choose files"), file("huge.jpg", 60 * MB));

    const alert = within(await screen.findByRole("listitem", { name: "huge.jpg" })).getByRole("alert");
    expect(alert).toHaveTextContent("The file is too large");
    expect(alert).toHaveTextContent("This file is 60 MB; the limit for ground photographs is 50 MB.");
    expect(uploadAsset).not.toHaveBeenCalled();
  });

  it("shows the server's reason when the content does not match the name", async () => {
    uploadAsset.mockRejectedValue(
      new client.ApiError(
        415,
        "SIGNATURE_MISMATCH",
        "The file's content does not match its name",
        "The name ends in .jpg, which should be a JPEG image, but the content is PNG image.",
        "req-1",
      ),
    );
    renderInProject(<IngestionPage />);
    await user().upload(await screen.findByLabelText("Choose files"), file("really_png.jpg"));

    const row = await screen.findByRole("listitem", { name: "really_png.jpg" });
    const alert = await within(row).findByRole("alert");
    expect(within(row).getByText("Not accepted")).toBeInTheDocument();
    expect(alert).toHaveTextContent("The file's content does not match its name");
    expect(alert).toHaveTextContent("but the content is PNG image");
    expect(alert).toHaveTextContent("Code: SIGNATURE_MISMATCH · Request: req-1");
  });

  it("shows a corrupt file as not accepted with nothing stored", async () => {
    uploadAsset.mockRejectedValue(
      new client.ApiError(422, "ASSET_UNREADABLE", "The file could not be read", "The file is corrupt or not a readable JPEG image. Nothing was stored."),
    );
    renderInProject(<IngestionPage />);
    await user().upload(await screen.findByLabelText("Choose files"), file("broken.jpg"));
    const alert = await within(await screen.findByRole("listitem", { name: "broken.jpg" })).findByRole("alert");
    expect(alert).toHaveTextContent("Nothing was stored.");
    expect(alert).toHaveTextContent("Code: ASSET_UNREADABLE");
  });

  it("distinguishes a system failure from a rejected file and continues with the next file", async () => {
    uploadAsset
      .mockRejectedValueOnce(new client.ApiError(503, "DATABASE_UNAVAILABLE", "The local database is not available"))
      .mockResolvedValueOnce({
        asset: asset({ id: "a3", original_filename: "second.png" }),
        duplicate: false,
        duplicate_of_asset_id: null,
        stored_original_reused: false,
        message: "Stored and validated.",
      });
    renderInProject(<IngestionPage />);
    await user().upload(await screen.findByLabelText("Choose files"), [file("first.jpg"), file("second.png")]);

    const first = await screen.findByRole("listitem", { name: "first.jpg" });
    expect(await within(first).findByText("Failed")).toBeInTheDocument();
    expect(within(first).getByRole("alert")).toHaveTextContent("Code: DATABASE_UNAVAILABLE");
    const second = await screen.findByRole("listitem", { name: "second.png" });
    expect(await within(second).findByText("Stored")).toBeInTheDocument();
    await waitFor(() => expect(uploadAsset).toHaveBeenCalledTimes(2));
  });

  it("blocks uploads to an archived project", async () => {
    renderInProject(<IngestionPage />, { ...PROJECT, archived_at: "2026-10-12T00:00:00Z" });
    expect(await screen.findByText("This project is archived")).toBeInTheDocument();
    expect(screen.getByLabelText("Choose files")).toBeDisabled();
  });
});

describe("AssetsPage", () => {
  it("shows the empty state with a way to upload", async () => {
    listAssets.mockResolvedValue({ items: [], next_cursor: null });
    renderInProject(<AssetsPage />);
    expect(await screen.findByText("No assets yet")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Upload the first file" })).toHaveAttribute("href", "/projects/p1/assets/new");
  });

  it("lists assets from the backend with status and flags", async () => {
    listAssets.mockResolvedValue({
      items: [
        asset(),
        asset({
          id: "a2",
          original_filename: "scene.tif",
          modality: "overhead_raster",
          detected_format: "GTiff",
          status: "validated_with_warnings",
          quality_flags: ["MISSING_CRS", "NOT_GEOREFERENCED"],
        }),
      ],
      next_cursor: null,
    });
    renderInProject(<AssetsPage />);
    const row = (await screen.findByRole("link", { name: "scene.tif" })).closest("tr")!;
    expect(within(row).getByText("Satellite / aerial raster")).toBeInTheDocument();
    expect(within(row).getByText("Validated with warnings")).toBeInTheDocument();
    expect(within(row).getByText("No CRS in file, Not georeferenced")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "photo.jpg" })).toHaveAttribute("href", "/projects/p1/assets/a1");
    expect(listAssets).toHaveBeenCalledWith("p1", { modality: undefined, limit: 25, cursor: undefined });
  });

  it("shows an error when the list cannot be loaded", async () => {
    listAssets.mockRejectedValue(new client.ApiError(503, "DATABASE_UNAVAILABLE", "The local database is not available"));
    renderInProject(<AssetsPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Code: DATABASE_UNAVAILABLE");
  });
});

describe("checkFile", () => {
  const rule = RULES.rules[0]!;
  it("accepts a supported, non-empty file within the limit", () => {
    expect(checkFile({ name: "A.JPG", size: 50 * MB }, rule)).toBeNull();
  });
  it("rejects wrong extension, empty and oversized files", () => {
    expect(checkFile({ name: "a.tif", size: 10 }, rule)?.code).toBe("UNSUPPORTED_FILE_TYPE");
    expect(checkFile({ name: "noext", size: 10 }, rule)?.code).toBe("UNSUPPORTED_FILE_TYPE");
    expect(checkFile({ name: "a.jpg", size: 0 }, rule)?.code).toBe("EMPTY_FILE");
    expect(checkFile({ name: "a.jpg", size: 50 * MB + 1 }, rule)?.code).toBe("FILE_TOO_LARGE");
  });
  it("formats sizes", () => {
    expect([formatBytes(512), formatBytes(2048), formatBytes(50 * MB), formatBytes(1024 ** 3)]).toEqual([
      "512 bytes",
      "2.0 KB",
      "50 MB",
      "1 GB",
    ]);
  });
});
