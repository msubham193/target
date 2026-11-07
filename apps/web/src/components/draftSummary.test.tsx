import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";

import * as client from "../api/client";
import { renderPage } from "../test/render";
import { DraftSummary } from "./DraftSummary";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, getReportSummaryStatus: vi.fn(), writeReportSummary: vi.fn() };
});

const READY = { enabled: true, reachable: true, model: "qwen2.5vl:latest", model_installed: true, note: "Ready." };
const DRAFT: client.ReportSummaryDraft = {
  machine_written: true,
  disclaimer: "Written by a language model on this computer from the stored data in this report. It is a draft: no person has reviewed it.",
  model: "qwen2.5vl:latest",
  generated_at: "2026-11-05T10:00:00Z",
  attempts: 1,
  overview: "55 detections were stored; 52 are waiting for review.",
  photos: [{ filename: "harbour.jpg", text: "12 detections were marked." }],
  notes: ["Scores are the model's own values."],
  facts_used: { detections_total: 55 },
};

beforeEach(() => vi.clearAllMocks());

describe("DraftSummary", () => {
  it("offers a draft, and shows it clearly marked as machine-written and unreviewed", async () => {
    vi.mocked(client.getReportSummaryStatus).mockResolvedValue(READY);
    vi.mocked(client.writeReportSummary).mockResolvedValue(DRAFT);
    renderPage(<DraftSummary projectId="p1" />);
    expect(screen.getByText("Machine-written · not reviewed")).toBeInTheDocument();
    expect(screen.getByText(/does not say what anything means or what to do/)).toBeInTheDocument();
    const button = screen.getByRole("button", { name: "Write a draft summary" });
    await waitFor(() => expect(button).toBeEnabled());
    expect(client.writeReportSummary).not.toHaveBeenCalled(); // nothing is written until it is asked for

    await userEvent.click(button);
    const text = await screen.findByTestId("draft-text");
    expect(client.writeReportSummary).toHaveBeenCalledWith("p1");
    expect(text).toHaveTextContent("55 detections were stored; 52 are waiting for review.");
    expect(text).toHaveTextContent("harbour.jpg: 12 detections were marked.");
    expect(text).toHaveTextContent("It is a draft: no person has reviewed it.");
    expect(text).toHaveTextContent("by qwen2.5vl:latest on this computer");
    expect(screen.getByText("The exact facts the model was given")).toBeInTheDocument();
  });

  it("can be discarded, which removes the text", async () => {
    vi.mocked(client.getReportSummaryStatus).mockResolvedValue(READY);
    vi.mocked(client.writeReportSummary).mockResolvedValue(DRAFT);
    renderPage(<DraftSummary projectId="p1" />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Write a draft summary" })).toBeEnabled());
    await userEvent.click(screen.getByRole("button", { name: "Write a draft summary" }));
    await screen.findByTestId("draft-text");
    await userEvent.click(screen.getByRole("button", { name: "Discard" }));
    expect(screen.queryByTestId("draft-text")).not.toBeInTheDocument();
    expect(screen.queryByText(/55 detections were stored/)).not.toBeInTheDocument();
  });

  it("says why it is unavailable and keeps the button off", async () => {
    vi.mocked(client.getReportSummaryStatus).mockResolvedValue({
      ...READY,
      reachable: false,
      model_installed: false,
      note: "Ollama is not reachable on this computer.",
    });
    renderPage(<DraftSummary projectId="p1" />);
    expect(await screen.findByTestId("draft-unavailable")).toHaveTextContent("Ollama is not reachable on this computer.");
    expect(screen.getByRole("button", { name: "Write a draft summary" })).toBeDisabled();
  });

  it("shows a refused or failed draft as an error with its code, and no text", async () => {
    vi.mocked(client.getReportSummaryStatus).mockResolvedValue(READY);
    vi.mocked(client.writeReportSummary).mockRejectedValue(
      new client.ApiError(502, "LLM_OUTPUT_REJECTED", "The draft contained the number 56, which is not in the stored data."),
    );
    renderPage(<DraftSummary projectId="p1" />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Write a draft summary" })).toBeEnabled());
    await userEvent.click(screen.getByRole("button", { name: "Write a draft summary" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Code: LLM_OUTPUT_REJECTED");
    expect(screen.queryByTestId("draft-text")).not.toBeInTheDocument();
  });
});
