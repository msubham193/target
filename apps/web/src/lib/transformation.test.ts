import type { TransformationOperation } from "../api/client";
import { acknowledgementFromContext, acknowledgementFromPlan, acknowledgementText, accuracyText, describeOperation } from "./transformation";

describe("accuracyText", () => {
  it("never shows a missing accuracy as zero", () => {
    expect(accuracyText("ballpark", null)).toBe("not stated");
    expect(accuracyText("ballpark", 0)).toBe("not stated");
    expect(accuracyText("approximate", null)).toBe("not stated");
    expect(accuracyText("exact", 0)).toBe("exact (no datum change)");
  });

  it("states the documented figure without false precision", () => {
    expect(accuracyText("approximate", 999)).toBe("about 999 m");
    expect(accuracyText("approximate", 3)).toBe("about 3 m");
    expect(accuracyText("approximate", 1.5)).toBe("about 1.5 m");
    expect(accuracyText("unavailable", 0.03)).toBe("about 0.03 m, if its grid were installed");
  });
});

describe("describeOperation", () => {
  it("names the class, the operation and the accuracy", () => {
    expect(describeOperation({ id: "EPSG:1247", name: "Kalianpur 1962 to WGS 84 (1)", classification: "approximate", accuracy_m: 999 })).toBe(
      "Approximate: Kalianpur 1962 to WGS 84 (1) (EPSG:1247), accuracy about 999 m",
    );
    expect(describeOperation({ id: "EPSG:1247", classification: "approximate", accuracy_m: 999 })).toBe("Approximate: EPSG:1247, accuracy about 999 m");
    expect(describeOperation({ id: "datum-unchanged", classification: "exact", accuracy_m: 0 })).toBe("Exact: no datum change");
    expect(describeOperation({ id: "ballpark", classification: "ballpark", accuracy_m: null })).toBe("Ballpark — blocked: ballpark, accuracy not stated");
  });
});

describe("acknowledgements", () => {
  const context = {
    operation_id: "EPSG:1247",
    operation_name: "Kalianpur 1962 to WGS 84 (1)",
    accuracy_m: 999,
    threshold_m: 10,
    reason: "accuracy_above_threshold",
    stale_acknowledgement: false,
  };

  it("reads what has to be acknowledged from the API's 409 context", () => {
    expect(acknowledgementFromContext(context)).toEqual({
      operationId: "EPSG:1247",
      operationName: "Kalianpur 1962 to WGS 84 (1)",
      accuracyM: 999,
      thresholdM: 10,
      reason: "accuracy_above_threshold",
      stale: false,
    });
    expect(acknowledgementFromContext(null)).toBeNull();
    expect(acknowledgementFromContext({ accuracy_m: 5 })).toBeNull();
    expect(acknowledgementFromContext({ ...context, stale_acknowledgement: true })?.stale).toBe(true);
  });

  it("states the figure and the limit, and says plainly when no accuracy is stated", () => {
    expect(acknowledgementText(acknowledgementFromContext(context)!)).toBe(
      "The datum shift Kalianpur 1962 to WGS 84 (1) (EPSG:1247) is documented to about 999 m, worse than the 10 m limit. I accept that positions placed with it can be off by at least that much.",
    );
    const unstated = acknowledgementFromContext({ ...context, accuracy_m: null, reason: "accuracy_not_stated" })!;
    expect(acknowledgementText(unstated)).toContain("has no stated accuracy, so its error could be any size");
    expect(acknowledgementText(unstated)).not.toContain("0 m");
  });

  it("finds the operation to acknowledge in a plan: the chosen one, or the usable one that needs it", () => {
    const base = { name: "n", classification: "approximate", accuracy_m: 3, authority_code: null, area_of_use: null, grids: [], summary: "", usable: true, recommended: false, selected: false, acknowledgement_threshold_m: 10 };
    const fine = { ...base, id: "A", requires_acknowledgement: false } as TransformationOperation;
    const coarse = { ...base, id: "B", accuracy_m: 120, requires_acknowledgement: true, acknowledgement_reason: "accuracy_above_threshold" } as TransformationOperation;
    expect(acknowledgementFromPlan({ operations: [fine] })).toBeNull();
    expect(acknowledgementFromPlan({ operations: [fine, coarse] })?.operationId).toBe("B");
    expect(acknowledgementFromPlan({ operations: [fine, coarse] }, "A")).toBeNull();
    expect(acknowledgementFromPlan({ operations: [fine, coarse] }, "B")?.accuracyM).toBe(120);
  });
});
