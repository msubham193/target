import { axisHeading, coordinateHeadings, xyAxes } from "./axes";

const E = { abbreviation: "E", direction: "east", unit: "metre" };
const N = { abbreviation: "N", direction: "north", unit: "metre" };
const WEST = { abbreviation: "Y", direction: "west", unit: "metre" };
const SOUTH = { abbreviation: "X", direction: "south", unit: "metre" };
const LAT = { abbreviation: "Lat", direction: "north", unit: "degree" };
const LON = { abbreviation: "Lon", direction: "east", unit: "degree" };

describe("xyAxes", () => {
  it("keeps east/north order", () => {
    expect(xyAxes([E, N])).toEqual([E, N]);
  });

  it("swaps a latitude-first pair, as the registry lists it the other way round from how it is entered", () => {
    expect(xyAxes([LAT, LON])).toEqual([LON, LAT]);
    expect(xyAxes([N, E])).toEqual([E, N]);
  });

  it("does not assume x is east: a west/south CRS keeps its own order", () => {
    expect(xyAxes([WEST, SOUTH])).toEqual([WEST, SOUTH]);
  });

  it("gives nothing for a CRS with fewer than two axes", () => {
    expect(xyAxes([E])).toBeNull();
  });
});

describe("coordinateHeadings", () => {
  it("is neutral before a CRS is chosen", () => {
    expect(coordinateHeadings(null)).toEqual({ x: "X (first coordinate)", y: "Y (second coordinate)" });
    expect(coordinateHeadings([])).toEqual({ x: "X (first coordinate)", y: "Y (second coordinate)" });
  });

  it("names the axis and the way it runs", () => {
    expect(coordinateHeadings([E, N])).toEqual({ x: "X (E, towards the east)", y: "Y (N, towards the north)" });
    expect(coordinateHeadings([LAT, LON])).toEqual({ x: "X (Lon, towards the east)", y: "Y (Lat, towards the north)" });
    expect(coordinateHeadings([WEST, SOUTH])).toEqual({ x: "X (Y, towards the west)", y: "Y (X, towards the south)" });
  });

  it("does not claim a direction for a grid-direction axis", () => {
    expect(axisHeading({ abbreviation: "E", direction: "North along 90°E" }, "X")).toBe("X (E)");
    expect(axisHeading(undefined, "X")).toBe("X");
  });
});
