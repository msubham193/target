import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";

import * as client from "../api/client";
import { renderPage } from "../test/render";
import { CustomModelUpload, runtimeOf, suggestName } from "./CustomModelUpload";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, uploadCustomModel: vi.fn() };
});

const uploadCustomModel = vi.mocked(client.uploadCustomModel);

function added(overrides: Partial<client.ModelInfo> = {}): client.ModelInfo {
  return {
    name: "best",
    version: "v1",
    pipeline: "ground_image",
    task: "object_detection",
    runtime: "onnx",
    licence: "Not stated by the uploader.",
    score_semantics: "The uploaded model's own confidence.",
    score_calibrated: false,
    settings: { classes: ["tank", "truck", "tent"] },
    weights_sha256: "ab".repeat(32),
    weights_size_bytes: 10,
    available: true,
    reason_unavailable: null,
    custom: true,
    original_filename: "best.onnx",
    added_at: "2026-11-07T10:00:00+00:00",
    ...overrides,
  };
}

const file = (name: string) => new File(["model"], name, { type: "application/octet-stream" });

beforeEach(() => {
  uploadCustomModel.mockReset(); // in a block: a function returned from beforeEach would be run as a cleanup step
});

describe("runtimeOf and suggestName", () => {
  it("follows the extension unless a format is chosen", () => {
    expect(runtimeOf("a.onnx", "auto")).toBe("onnx");
    expect(runtimeOf("A.PT", "auto")).toBe("ultralytics");
    expect(runtimeOf("a.pth", "auto")).toBe("torchvision");
    expect(runtimeOf("a.pt", "torchvision")).toBe("torchvision");
    expect(runtimeOf("a.h5", "auto")).toBeNull();
  });

  it("makes a valid model name from a file name", () => {
    expect(suggestName("Best Model (2).onnx")).toBe("best-model-2");
    expect(suggestName("x.pt")).toBe("my-model");
  });
});

describe("CustomModelUpload", () => {
  it("uploads an ONNX model with its settings and reports what was added", async () => {
    uploadCustomModel.mockResolvedValue(added());
    renderPage(<CustomModelUpload />);
    const user = userEvent.setup();
    await user.upload(screen.getByLabelText("Model file"), file("best.onnx"));
    expect(screen.getByLabelText("Name")).toHaveValue("best");
    await user.type(screen.getByLabelText("Minimum score"), "0.4");
    await user.click(screen.getByRole("button", { name: "Add model" }));

    await screen.findByText("Added best v1");
    expect(screen.getByText(/It finds 3 classes/)).toBeInTheDocument();
    const [sent, options] = uploadCustomModel.mock.calls[0]!;
    expect(sent.name).toBe("best.onnx");
    expect(options).toMatchObject({ name: "best", version: "v1", format: "auto", minScore: 0.4 });
    expect(options.trustCode).toBeUndefined();
    expect(screen.queryByLabelText("Architecture")).toBeNull();
  });

  it("asks the user to trust a YOLO .pt file before it can be uploaded", async () => {
    uploadCustomModel.mockResolvedValue(added({ runtime: "ultralytics" }));
    renderPage(<CustomModelUpload />);
    const user = userEvent.setup();
    await user.upload(screen.getByLabelText("Model file"), file("weights.pt"));
    expect(screen.getByText("A .pt file can run code on this computer")).toBeInTheDocument();
    const add = screen.getByRole("button", { name: "Add model" });
    expect(add).toBeDisabled();
    await user.click(screen.getByLabelText("I trust the source of this file."));
    expect(add).toBeEnabled();
    await user.click(add);
    await waitFor(() => expect(uploadCustomModel).toHaveBeenCalled());
    expect(uploadCustomModel.mock.calls[0]![1].trustCode).toBe(true);
  });

  it("offers an architecture choice for torchvision weights", async () => {
    uploadCustomModel.mockResolvedValue(added({ runtime: "torchvision" }));
    renderPage(<CustomModelUpload />);
    const user = userEvent.setup();
    await user.upload(screen.getByLabelText("Model file"), file("frcnn.pth"));
    await user.selectOptions(screen.getByLabelText("Architecture"), "fasterrcnn_mobilenet_v3_large_320_fpn");
    await user.type(screen.getByLabelText("Class names"), "tank{enter}truck");
    await user.click(screen.getByRole("button", { name: "Add model" }));
    await waitFor(() => expect(uploadCustomModel).toHaveBeenCalled());
    expect(uploadCustomModel.mock.calls[0]![1]).toMatchObject({
      architecture: "fasterrcnn_mobilenet_v3_large_320_fpn",
      classes: "tank\ntruck",
    });
  });

  it("refuses files that are not models and invalid scores before uploading", async () => {
    renderPage(<CustomModelUpload />);
    const user = userEvent.setup({ applyAccept: false }); // a browser lets the user pick "All files"
    await user.upload(screen.getByLabelText("Model file"), file("notes.txt"));
    expect(screen.getByText("A model file must end in .onnx, .pt or .pth.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add model" })).toBeDisabled();
    await user.upload(screen.getByLabelText("Model file"), file("best.onnx"));
    await user.type(screen.getByLabelText("Minimum score"), "2");
    expect(screen.getByText("Use a number from 0.01 to 0.99.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add model" })).toBeDisabled();
  });

  it("shows why the server refused the model", async () => {
    uploadCustomModel.mockImplementation(async () => {
      throw new client.ApiError(
        422,
        "MODEL_CHECK_FAILED",
        "The model could not be opened and run",
        "The file does not contain class names. Nothing was stored.",
      );
    });
    renderPage(<CustomModelUpload />);
    const user = userEvent.setup();
    await user.upload(screen.getByLabelText("Model file"), file("best.onnx"));
    await user.click(screen.getByRole("button", { name: "Add model" }));
    expect(await screen.findByText("The model was not added")).toBeInTheDocument();
    expect(screen.getByText(/does not contain class names/)).toBeInTheDocument();
  });
});
