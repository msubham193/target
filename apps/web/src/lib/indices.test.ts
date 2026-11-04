import { describe, expect, it } from "vitest";

import {
  formatAcquisition,
  formatBytes,
  formatCount,
  formatValue,
  isRunActive,
  latestResult,
  legendGradient,
  legendPosition,
  ndviTileQuery,
  parseLegend,
  REQUIRED_SCHEMA_REVISION,
  runPhase,
  setupState,
  shortHash,
  tileUrl,
} from "./indices";

const DISPLAY = {
  colormap: "ndvi",
  title: "NDVI",
  low: -0.2,
  high: 0.9,
  stops: [
    { value: -0.2, color: "#3b5b92" },
    { value: 0, color: "#e8dfc8" },
    { value: 0.9, color: "#0b5d2a" },
  ],
};

describe("run phases", () => {
  it("maps the job's own status to the four states the Studio shows", () => {
    expect(runPhase("queued")).toBe("queued");
    expect(runPhase("validating")).toBe("running");
    expect(runPhase("processing")).toBe("running");
    expect(runPhase("completed")).toBe("completed");
    expect(runPhase("failed")).toBe("failed");
    expect(runPhase("cancelled")).toBe("cancelled");
  });

  it("treats an unknown status as a failure rather than inventing progress", () => {
    expect(runPhase("something-new")).toBe("failed");
  });

  it("knows which runs are still going", () => {
    expect(isRunActive({ status: "queued" })).toBe(true);
    expect(isRunActive({ status: "processing" })).toBe(true);
    expect(isRunActive({ status: "completed" })).toBe(false);
    expect(isRunActive({ status: "failed" })).toBe(false);
  });
});

describe("the legend", () => {
  it("is read from the API's description", () => {
    const legend = parseLegend(DISPLAY);
    expect(legend?.low).toBe(-0.2);
    expect(legend?.high).toBe(0.9);
    expect(legend?.stops).toHaveLength(3);
  });

  it("is refused when it is missing or malformed", () => {
    expect(parseLegend(null)).toBeNull();
    expect(parseLegend(undefined)).toBeNull();
    expect(parseLegend("ndvi")).toBeNull();
    expect(parseLegend({ ...DISPLAY, low: 1, high: 1 })).toBeNull();
    expect(parseLegend({ ...DISPLAY, stops: [DISPLAY.stops[0]] })).toBeNull();
    expect(parseLegend({ ...DISPLAY, stops: [{ value: "x", color: 1 }, { value: 0, color: "#000" }] })).toBeNull();
    expect(parseLegend({ ...DISPLAY, colormap: 3 })).toBeNull();
  });

  it("places values along the bar and holds them inside it", () => {
    const legend = parseLegend(DISPLAY)!;
    expect(legendPosition(-0.2, legend)).toBe(0);
    expect(legendPosition(0.9, legend)).toBe(100);
    expect(legendPosition(0, legend)).toBeCloseTo((0.2 / 1.1) * 100, 6);
    expect(legendPosition(-5, legend)).toBe(0);
    expect(legendPosition(5, legend)).toBe(100);
  });

  it("builds its gradient from the same stops the map uses", () => {
    const gradient = legendGradient(parseLegend(DISPLAY)!);
    expect(gradient).toMatch(/^linear-gradient\(to right, #3b5b92 0\.0%, #e8dfc8 18\.2%, #0b5d2a 100\.0%\)$/);
  });
});

describe("tile addresses", () => {
  it("adds only a colour ramp and its range to the existing tile route", () => {
    expect(ndviTileQuery(parseLegend(DISPLAY)!)).toBe("colormap=ndvi&colormap_range=-0.2,0.9");
    expect(tileUrl("a1", 3)).toBe("/api/v1/assets/a1/tiles/{z}/{x}/{y}.png?v=3");
  });

  it("never builds an address from a path or a URL", () => {
    expect(tileUrl("a1", 0)).not.toMatch(/https?:|\\|file:/);
  });
});

describe("number formatting", () => {
  it("shows values to four places and never negative zero", () => {
    expect(formatValue(0.123456)).toBe("0.1235");
    expect(formatValue(-0.00001)).toBe("0.0000");
    expect(formatValue(-0.5)).toBe("-0.5000");
    expect(formatValue(null)).toBe("not available");
    expect(formatValue(Number.NaN)).toBe("not available");
  });

  it("groups pixel counts and says when one is missing", () => {
    expect(formatCount(117496393)).toBe("117,496,393");
    expect(formatCount(undefined)).toBe("not available");
  });

  it("states times in UTC, as the product metadata does", () => {
    expect(formatAcquisition("2024-01-15T23:52:21.024Z")).toBe("2024-01-15 23:52:21 UTC");
    expect(formatAcquisition(null)).toBe("unknown");
    expect(formatAcquisition("not a date")).toBe("unknown");
  });

  it("shortens a hash and sizes in binary units", () => {
    expect(shortHash("9ec59e43956e73cd096702a48fbc2fed")).toBe("9ec59e43956e");
    expect(shortHash(null)).toBe("not recorded");
    expect(formatBytes(523771724)).toBe("499.5 MiB");
    expect(formatBytes(2 * 1024 ** 3)).toBe("2.00 GiB");
  });
});

describe("the newest result", () => {
  const run = (status: string, result: unknown) => ({ status, result }) as never;
  it("is the newest completed run that has a stored result", () => {
    const runs = [run("processing", null), run("failed", null), run("completed", { id: "new" }), run("completed", { id: "old" })];
    expect(latestResult(runs)).toBe(runs[2]);
  });

  it("is absent when nothing has completed", () => {
    expect(latestResult([run("queued", null), run("cancelled", null)])).toBeUndefined();
    expect(latestResult([])).toBeUndefined();
  });
});

describe("whether the database can accept a run", () => {
  it("is pending while the schema is older than the revision that added the job kind", () => {
    expect(REQUIRED_SCHEMA_REVISION).toBe(10);
    expect(setupState("0009")).toBe("pending");
    expect(setupState("0001")).toBe("pending");
  });

  it("is ready from that revision on", () => {
    expect(setupState("0010")).toBe("ready");
    expect(setupState("0011")).toBe("ready");
    expect(setupState("0123")).toBe("ready");
  });

  it("is unknown, and so never blocks, when the revision cannot be read", () => {
    expect(setupState(null)).toBe("unknown");
    expect(setupState(undefined)).toBe("unknown");
    expect(setupState("")).toBe("unknown");
    expect(setupState("head")).toBe("unknown");
    expect(setupState("0010a")).toBe("unknown");
  });
});
