import { formatDecimalDegrees, formatDms, graticule, parseDms, toDms } from "./geo";

describe("coordinate display conversion (GEO-002)", () => {
  it("formats a known point in degrees, minutes and seconds", () => {
    expect(formatDms(27.12247, "lat")).toBe("27°07′20.89″N");
    expect(formatDms(75, "lon")).toBe("75°00′00.00″E");
    expect(formatDms(-33.8568, "lat")).toBe("33°51′24.48″S");
    expect(formatDms(-0.5, "lon", 0)).toBe("0°30′00″W");
  });

  it("carries rounded seconds into minutes and degrees", () => {
    expect(toDms(10.999999999, "lat")).toEqual({ degrees: 11, minutes: 0, seconds: 0, hemisphere: "N" });
    expect(formatDms(59.9999999, "lon")).toBe("60°00′00.00″E");
  });

  it("round-trips within the precision shown", () => {
    // Two decimals of a second is 1/360000 degree; the round trip must stay within half of that.
    for (const value of [0, 27.12247, -122.4194155, 89.9999, -179.123456, 45.5]) {
      for (const axis of ["lat", "lon"] as const) {
        if (axis === "lat" && Math.abs(value) > 90) continue;
        expect(Math.abs(parseDms(formatDms(value, axis)) - value)).toBeLessThanOrEqual(0.5 / 360000 + 1e-12);
      }
    }
  });

  it("refuses values that are not coordinates", () => {
    expect(() => toDms(91, "lat")).toThrow(RangeError);
    expect(() => toDms(Number.NaN, "lon")).toThrow(RangeError);
    expect(() => parseDms("27.1")).toThrow(SyntaxError);
  });

  it("always states the hemisphere in decimal degrees", () => {
    expect(formatDecimalDegrees(-33.85680012, "lat")).toBe("33.856800° S");
    expect(formatDecimalDegrees(151.2153, "lon", 4)).toBe("151.2153° E");
  });

  it("builds a graticule of meridians and parallels", () => {
    const lines = graticule(30);
    expect(lines.features.filter((f) => f.properties.kind === "meridian")).toHaveLength(13);
    expect(lines.features.filter((f) => f.properties.kind === "parallel")).toHaveLength(6);
  });
});

describe("measurement display", () => {
  it("chooses units without changing the value", async () => {
    const { formatArea, formatLength } = await import("./geo");
    expect(formatLength(1000.4)).toBe("1000.4 m");
    expect(formatLength(5.123)).toBe("5.12 m");
    expect(formatLength(111319.49)).toBe("111.319 km");
    expect(formatArea(1000.8)).toBe("1000.8 m²");
    expect(formatArea(1_000_800)).toBe("100.08 ha");
    expect(formatArea(12_308_800_000)).toBe("12308.80 km²");
  });
});
