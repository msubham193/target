import { describe, expect, it } from "vitest";

import { checkPosition, parseDegrees, toInputValue } from "./photoLocation";

describe("parseDegrees", () => {
  it.each([
    ["28.6139", 28.6139],
    ["-33.8688", -33.8688],
    ["+77.2", 77.2],
    [" 10 ", 10],
    [".5", 0.5],
    ["12.", 12],
  ])("accepts %j", (text, value) => expect(parseDegrees(text)).toBe(value));

  it.each([["", "empty"], ["abc", "text"], ["NaN", "NaN"], ["Infinity", "Infinity"], ["1e3", "exponent"], ["10,5", "comma"], ["28°36′", "DMS"], ["1.2.3", "two points"]])(
    "rejects %j (%s)",
    (text) => expect(parseDegrees(text)).toBeNull(),
  );
});

describe("checkPosition", () => {
  it("accepts a valid position", () => {
    expect(checkPosition("28.6", "77.2")).toEqual({ ok: true, position: { lat: 28.6, lon: 77.2 } });
    expect(checkPosition("-90", "180")).toEqual({ ok: true, position: { lat: -90, lon: 180 } });
  });

  it.each([
    ["91", "10", /Latitude must be between -90 and 90/, null],
    ["-90.5", "10", /Latitude must be between/, null],
    ["10", "180.5", null, /Longitude must be between -180 and 180/],
    ["10", "-181", null, /Longitude must be between/],
    ["", "10", /Enter a latitude/, null],
    ["10", "", null, /Enter a longitude/],
    ["north", "10", /Latitude must be a number/, null],
    ["10", "east", null, /Longitude must be a number/],
  ])("refuses %j, %j with a message for the field", (lat, lon, latMessage, lonMessage) => {
    const result = checkPosition(lat, lon);
    expect(result.ok).toBe(false);
    if (result.ok) return;
    if (latMessage) expect(result.latError).toMatch(latMessage);
    else expect(result.latError).toBeNull();
    if (lonMessage) expect(result.lonError).toMatch(lonMessage);
    else expect(result.lonError).toBeNull();
  });

  it("refuses 0, 0 as a missing value, as the server does", () => {
    const result = checkPosition("0", "0");
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.latError).toMatch(/0, 0 is almost always a missing value/);
  });

  it("accepts a zero on one axis only", () => {
    expect(checkPosition("0", "10").ok).toBe(true);
    expect(checkPosition("10", "0").ok).toBe(true);
  });
});

describe("toInputValue", () => {
  it("writes six places so a picked point can be typed back unchanged", () => {
    expect(toInputValue(27.1222)).toBe("27.122200");
    expect(checkPosition(toInputValue(-33.8688), toInputValue(151.2093))).toEqual({ ok: true, position: { lat: -33.8688, lon: 151.2093 } });
  });
});
