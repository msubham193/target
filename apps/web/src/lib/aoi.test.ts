import { buildAoiGeometry, emptyAoiFields, parseTags } from "./aoi";

describe("buildAoiGeometry", () => {
  it("builds a point in x, y order exactly as typed", () => {
    const result = buildAoiGeometry("point", { ...emptyAoiFields, x: "77.2", y: "28.6" });
    expect(result).toEqual({ ok: true, geometry: { type: "Point", coordinates: [77.2, 28.6] } });
  });

  it("rejects missing or non-numeric coordinates instead of guessing", () => {
    expect(buildAoiGeometry("point", { ...emptyAoiFields, x: "77.2" }).ok).toBe(false);
    expect(buildAoiGeometry("point", { ...emptyAoiFields, x: "abc", y: "1" }).ok).toBe(false);
    expect(buildAoiGeometry("point", { ...emptyAoiFields, x: " ", y: "1" }).ok).toBe(false);
  });

  it("builds a closed bounding-box polygon", () => {
    const result = buildAoiGeometry("bbox", { ...emptyAoiFields, minX: "0", minY: "1", maxX: "2", maxY: "3" });
    expect(result).toEqual({
      ok: true,
      geometry: {
        type: "Polygon",
        coordinates: [
          [
            [0, 1],
            [2, 1],
            [2, 3],
            [0, 3],
            [0, 1],
          ],
        ],
      },
    });
  });

  it("rejects an inverted bounding box", () => {
    const result = buildAoiGeometry("bbox", { ...emptyAoiFields, minX: "5", minY: "1", maxX: "2", maxY: "3" });
    expect(result.ok).toBe(false);
  });
});

describe("parseTags", () => {
  it("trims, drops blanks and removes duplicates", () => {
    expect(parseTags(" coast, demo ,,coast ")).toEqual(["coast", "demo"]);
  });
});
