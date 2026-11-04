import type { Asset, Observation, PhotoLocationItem } from "../api/client";
import { view } from "../test/fixtures";
import {
  cameraPosition,
  captureTime,
  comparePhotos,
  distanceMetres,
  formatInterval,
  latestRun,
  summarizeFindings,
} from "./compare";

function asset(id: string, over: Record<string, unknown> = {}): Asset {
  return {
    id,
    project_id: "p1",
    original_filename: `${id}.jpg`,
    modality: "ground_image",
    acquisition_time: null,
    unknowns: { acquisition_time: "The file carries no capture time." },
    metadata: {},
    quality_flags: [],
    preview: { status: "ready", variants: ["thumb", "view"], job_id: null, error_code: null, error_message: null },
    ...over,
  } as unknown as Asset;
}

function place(id: string, over: Partial<PhotoLocationItem> = {}): PhotoLocationItem {
  return {
    asset_id: id,
    filename: `${id}.jpg`,
    state: "confirmed",
    source: "exif",
    latitude: 28.6,
    longitude: 77.2,
    version_no: 2,
    confirmed_by: "Asha",
    confirmed_at: "2026-10-28T10:05:00Z",
    observations: 0,
    ...over,
  };
}

function found(id: string, run: string, at: string, label: string, status = "needs_review") {
  const observation = {
    observation_id: id,
    model: { name: "d", version: "1", run_id: run },
    claim: { category: `coco:${label}`, description: "x", claim_type: "inferred" },
    review: { status: "needs_review" },
    provenance: { created_at: at },
  } as unknown as Observation;
  return view(observation, { current: { status: status as never } });
}

describe("capture time", () => {
  it("uses the stored time and otherwise states the stored reason, never a guess", () => {
    expect(captureTime(asset("a", { acquisition_time: "2026-10-01T10:00:00Z" }))).toEqual({ state: "known", iso: "2026-10-01T10:00:00Z" });
    expect(captureTime(asset("a"))).toEqual({ state: "unknown", reason: "The file carries no capture time." });
    expect(captureTime(asset("a", { unknowns: {} }))).toEqual({ state: "unknown", reason: "No capture time is stored for this photograph." });
  });
});

describe("camera position", () => {
  it("is confirmed only for a confirmed item with coordinates", () => {
    expect(cameraPosition(place("a")).state).toBe("confirmed");
    expect(cameraPosition(place("a", { state: "confirmed", latitude: null, longitude: null }))).toEqual({ state: "none" });
  });

  it("keeps an unconfirmed GPS candidate a candidate and carries no coordinates", () => {
    const candidate = cameraPosition(place("a", { state: "candidate", latitude: null, longitude: null }));
    expect(candidate).toEqual({ state: "candidate" });
    // even if a response wrongly carried numbers, a candidate is not turned into a position
    expect(cameraPosition(place("a", { state: "candidate" }))).toEqual({ state: "candidate" });
  });

  it("has no position for a photograph the listing does not know or that has none", () => {
    expect(cameraPosition(undefined)).toEqual({ state: "none" });
    expect(cameraPosition(place("a", { state: "none", latitude: null, longitude: null }))).toEqual({ state: "none" });
  });
});

describe("findings summary", () => {
  it("describes only the latest run and counts earlier runs without adding them", () => {
    const views = [
      found("o1", "r1", "2026-10-01T00:00:00Z", "car"),
      found("o2", "r2", "2026-10-02T00:00:00Z", "car", "verified"),
      found("o3", "r2", "2026-10-02T00:00:00Z", "dog"),
    ];
    expect(latestRun(views)?.runId).toBe("r2");
    const summary = summarizeFindings(views);
    expect(summary).toMatchObject({ total: 2, reviewed: 1, unreviewed: 1, earlierRuns: 1 });
    expect(summary?.byStatus).toEqual({ verified: 1, needs_review: 1 });
    expect(summary?.categories).toEqual([
      { category: "car", count: 1 },
      { category: "dog", count: 1 },
    ]);
  });

  it("is null, not zero, for a photograph that was never analysed", () => {
    expect(summarizeFindings([])).toBeNull();
  });
});

describe("comparePhotos", () => {
  it("orders by stored capture time, leaves untimed photographs out of the timeline, and invents no time", () => {
    const result = comparePhotos([
      { asset: asset("late", { acquisition_time: "2026-10-01T10:05:00Z" }), location: place("late"), observations: [] },
      { asset: asset("none"), location: place("none"), observations: [] },
      { asset: asset("early", { acquisition_time: "2026-10-01T10:00:00Z" }), location: place("early"), observations: [] },
    ]);
    expect(result.timeline.map((t) => t.assetId)).toEqual(["early", "late"]);
    expect(result.intervals).toEqual([{ fromId: "early", toId: "late", seconds: 300 }]);
    const untimed = result.photos.find((p) => p.assetId === "none")!;
    expect(untimed.time.state).toBe("unknown");
    expect(untimed.gaps.join(" ")).toMatch(/No capture time: The file carries no capture time/);
  });

  it("has no interval when fewer than two photographs have a time", () => {
    const result = comparePhotos([
      { asset: asset("a", { acquisition_time: "2026-10-01T10:00:00Z" }), location: undefined, observations: [] },
      { asset: asset("b"), location: undefined, observations: [] },
    ]);
    expect(result.intervals).toEqual([]);
    expect(result.timeline).toHaveLength(1);
  });

  it("measures distances only between confirmed camera positions", () => {
    const result = comparePhotos([
      { asset: asset("a"), location: place("a", { latitude: 28.6, longitude: 77.2 }), observations: [] },
      { asset: asset("b"), location: place("b", { latitude: 28.601, longitude: 77.2 }), observations: [] },
      { asset: asset("c"), location: place("c", { state: "candidate", latitude: null, longitude: null }), observations: [] },
      { asset: asset("d"), location: undefined, observations: [] },
    ]);
    expect(result.distances).toHaveLength(1);
    expect(result.distances[0]).toMatchObject({ aId: "a", bId: "b" });
    expect(result.distances[0]!.metres).toBeGreaterThan(105);
    expect(result.distances[0]!.metres).toBeLessThan(117);
  });

  it("lists what is missing and unresolved for each photograph", () => {
    const result = comparePhotos([
      {
        asset: asset("a", { acquisition_time: "2026-10-01T10:00:00Z" }),
        location: place("a", { state: "candidate", latitude: null, longitude: null }),
        observations: [found("o1", "r1", "2026-10-02T00:00:00Z", "car")],
      },
      { asset: asset("b"), location: place("b", { state: "none", latitude: null, longitude: null }), observations: [] },
      { asset: asset("c"), location: place("c"), observations: undefined },
    ]);
    const [a, b, c] = result.photos as [(typeof result.photos)[number], (typeof result.photos)[number], (typeof result.photos)[number]];
    expect(a.gaps).toEqual(["A GPS position in the file has not been confirmed, so no location is used.", "1 finding(s) not yet reviewed."]);
    expect(b.gaps).toContain("No location is recorded.");
    expect(b.gaps).toContain("Not analysed yet.");
    expect(c.findings).toBeUndefined(); // still loading is not "not analysed"
    expect(c.gaps).not.toContain("Not analysed yet.");
  });

  it("creates no relationship between findings of different photographs, even with the same label and place", () => {
    const same = [found("o1", "r1", "2026-10-02T00:00:00Z", "car")];
    const result = comparePhotos([
      { asset: asset("a"), location: place("a"), observations: same },
      { asset: asset("b"), location: place("b"), observations: [found("o2", "r2", "2026-10-02T00:00:00Z", "car")] },
    ]);
    expect(Object.keys(result).sort()).toEqual(["distances", "intervals", "photos", "timeline"]);
    expect(JSON.stringify(result)).not.toMatch(/same object|duplicate|match|link|related/i);
    // the camera distance is a fact about the two photographs, and is 0 here because the positions are identical
    expect(result.distances[0]!.metres).toBe(0);
  });
});

describe("helpers", () => {
  it("computes great-circle distance and formats intervals", () => {
    expect(distanceMetres({ lat: 0, lon: 0 }, { lat: 0, lon: 1 })).toBeGreaterThan(111_000);
    expect(distanceMetres({ lat: 10, lon: 10 }, { lat: 10, lon: 10 })).toBe(0);
    expect(formatInterval(0)).toBe("0 s");
    expect(formatInterval(200)).toBe("3 min 20 s");
    expect(formatInterval(7500)).toBe("2 h 5 min");
    expect(formatInterval(-60)).toBe("1 min 0 s");
  });
});
